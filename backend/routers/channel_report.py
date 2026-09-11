"""채널 발주·납품 보고 (쿠팡 / 올리브영 공용 엔진).

발주 캘린더에 누적된 채널 발주서를 기준으로 납기별 발주 현황 · 확정/입고 실적 · 자사 재고(아워박스 가용 → BH) ·
채널 판매/재고 추이(업로드 파일)를 한 화면에 모으고, 의장 보고용 텍스트를 자동 생성한다.

채널별 차이는 `ChannelSpec`으로 주입한다 (coupang_report.py / oliveyoung_report.py 가 스펙을 만들어 라우터 생성).

데이터 출처
  - 발주/확정/입고: routers.order_plan 저장소(order_plan_store.json) 中 채널 = spec.channel 행
      qty = 발주수량, conf = 확정수량(쿠팡 업체납품가능수량, 없으면 발주수량), recv = 채널 입고(확정)수량, amt = 발주금액(매입가·VAT포함)
  - 자사 재고: 아워박스 가용재고(할당·불량 제외) 우선, 없으면 BoxHero /v1/items. BH SKU = 상품 바코드.
  - 출고 실적: BoxHero 출고 거래 中 거래처명이 spec.partner_match 인 것. 메모의 'N일'은 납기 힌트로 사용.
  - 판매/재고 추이: 채널별 업로드 파일 (쿠팡: rocket csv + forecast xlsx / 올리브영: 상품별 매출_재고 조회 xlsx, 월 단위)
  - 특이사항·입고 예정·별칭: 화면 입력 (data/<key>_report_state.json)
"""
from __future__ import annotations

import io, os, re, sys, json, csv, time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, Callable
from collections import defaultdict

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
from routers import order_plan as OP
from routers.inventory import _fetch_bh_items

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


@dataclass
class ChannelSpec:
    key: str                         # 'coupang' | 'oliveyoung' (파일명·URL)
    channel: str                     # order_plan 저장소의 채널명 ('쿠팡' / '올리브영')
    display: str                     # 보고문 표기 ('쿠팡' / '올리브영')
    partner_match: Callable[[str], bool]
    aliases: dict                    # 채널 상품명 → 짧은 라벨
    barcodes: dict = field(default_factory=dict)   # 채널 상품명 → 바코드(BH SKU) 기본표
    has_conf: bool = False           # 발주서에 확정(납품예정)수량 컬럼이 있는 채널
    conf_word: str = "확정"           # 확정수량 표기 ('확정' / '납품예정')
    qty_is_conf: bool = False        # True면 발주잔량·금액을 확정(납품예정)수량 기준으로 본다 (올리브영: 납품예정수량만 납품)
    derive_conf_from_amt: bool = False  # 구버전 저장 행: conf 없으면 원가금액/단가로 역산 (올리브영 원가금액 = 납품예정수량×원단가)
    amt_includes_vat: bool = True    # 발주금액이 부가세 포함(쿠팡 매입가)이면 True → 공급가 = ÷1.1. 올리브영 원가금액은 이미 공급가액
    recv_word: str = "입고등록"       # 채널의 '입고 확정' 표현
    recv_hint: str = "발주서 재다운로드 시 갱신"
    po_word: str = "발주서"
    upload_mode: str = "daily"       # 'daily'(쿠팡 rocket/forecast) | 'monthly'(올리브영 월별 매출·재고) | 'none'(컬리: 업로드 없음)
    has_amt: bool = True             # 발주 데이터에 금액이 있는 채널. False(컬리 거래명세서)면 보고문·화면에서 금액을 생략
    has_recv: bool = True            # 채널 입고확정 수량이 발주 데이터에 있는 채널. False(컬리)면 입고등록 관련 문구 생략
    alias_fallback: Optional[Callable[[str], Optional[str]]] = None    # 정규화 상품명 → 라벨 (별칭표에 없을 때, OCR 오인식 흡수용)
    barcode_fallback: Optional[Callable[[str], Optional[str]]] = None  # 정규화 상품명 → 바코드 (바코드표에 없을 때)
    upload_hint: str = ""
    state_path: str = ""
    upload_path: str = ""

    def __post_init__(self):
        self.state_path = self.state_path or os.path.join(_DATA_DIR, f"{self.key}_report_state.json")
        self.upload_path = self.upload_path or os.path.join(_DATA_DIR, f"{self.key}_report_uploads.json")


# ─────────────────────────────────────────────────────────────────
# 저장소
# ─────────────────────────────────────────────────────────────────
def _load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_state(sp: ChannelSpec) -> dict:
    st = _load_json(sp.state_path, {})
    st.setdefault("notes", "")
    st.setdefault("aliases", {})
    st.setdefault("incoming", [])   # [{id, label, qty, date, memo}] 입고 예정(항공분 등)
    return st


def _empty_uploads() -> dict:
    return {"rocket": {}, "forecast": None, "monthly": {}, "files": []}


def _load_uploads(sp: ChannelSpec) -> dict:
    up = _load_json(sp.upload_path, _empty_uploads())
    for k, v in _empty_uploads().items():
        up.setdefault(k, v)
    return up


# ─────────────────────────────────────────────────────────────────
# 라벨(짧은 이름) — 채널 공통. 라벨이 같으면 같은 상품으로 취급(쿠팡명·올리브영명·BH명 모두 라벨로 수렴)
# ─────────────────────────────────────────────────────────────────
COMMON_ALIASES = {
    # 쿠팡 상품명
    "표고버섯 크리스프 오리지널 65g": "머쉬룸 오리지널",
    "표고버섯 크리스프 스모크 칠리&갈릭 65g": "스모크칠리",
    "DJ&A 믹스드 버섯 크리스프 65g": "믹스드",
    "디제이앤에이 베지 크리스프 오리지널 90g": "베지크리스프",
    "디제이앤에이 베지 크리스프 핫앤스파이시 스낵 90g": "베지크리스프 핫앤스파이시",
    "디제이앤에이 스냅피 크리스프 30g 1봉": "스냅피",
    "DJ&A 크리스피 갈릭 클로브스 45g": "갈릭 클로브스",
    "디제이앤에이 포테이토 웨지 오리지널 100g": "포테이토 웨지",
    "디제이앤에이 포테이토 웨지 스위트칠리 100g": "포테이토 웨지 스위트칠리",
    "디제이앤에이 크리스피 브로콜리 플로렛 45g": "브로콜리 플로렛",
    "디제이앤에이 피클리셔스 50g 1봉": "피클리셔스",
    "DJ&A 크런치 타임 오리지널 50g": "크런치 타임",
    "디제이앤에이 표고버섯 저키 오리지널 60g": "저키 오리지널",
    "디제이앤에이 표고버섯 저키 스모크칠리&갈릭 60g": "저키 스모크칠리",
    "디제이앤에이 표고버섯 저키 텍사스 BBQ 60g": "저키 텍사스BBQ",
    "DJ&A 머쉬룸 크리스프 30g 3ea": "머쉬룸 30g 3입",
    "DJ&A 머쉬룸 스모크칠리&갈릭 크리스프 30g 3ea": "스모크칠리 30g 3입",
    "디제이앤에이 표고버섯 크리스프 오리지널 30g 1봉": "머쉬룸 30g",
    "디제이앤에이 표고버섯 저키 오리지널 60g x 3봉": "저키 오리지널 3입",
    "디제이앤에이 표고버섯 저키 스모크칠리&갈릭 60g x 3봉": "저키 스모크칠리 3입",
    "디제이앤에이 표고버섯 저키 텍사스 BBQ 60g x 3봉": "저키 텍사스BBQ 3입",
    "디제이앤에이 포테이토 웨지 오리지널 100g 3ea": "포테이토 웨지 3입",
    "디제이앤에이 포테이토 웨지 스위트칠리 100g, 3개입": "포테이토 웨지 스위트칠리 3입",
    "DJ&A 베지크리스프 오리지널 90g 3ea": "베지크리스프 3입",
    "디제이앤에이 표고버섯 스모크칠리&갈릭 65g, 3개입": "스모크칠리 3입",
    "DJ&A 표고버섯과자 시타케 머쉬룸 오리지널 30g 4개 + 스모크 칠리&갈릭 65g 2개": "머쉬룸 혼합세트(30g×4+칠리×2)",
    "DJ&A 표고버섯과자 시타케 머쉬룸 오리지널 30g 2개 스모크 칠리&갈릭 65g 1개": "머쉬룸 혼합세트(30g×2+칠리×1)",
    "DJ&A 표고버섯과자 시타케 머쉬룸 오리지널 65g 3개 + 스모크 칠리&갈릭 65g 1개": "머쉬룸 혼합세트(65g×3+칠리×1)",
    "DJ&A 표고버섯과자 시타케 머쉬룸 오리지널 65g 1개 + 스모크 칠리&갈릭 65g 3개": "머쉬룸 혼합세트(65g×1+칠리×3)",
    "DJ&A 표고버섯과자 시타케 머쉬룸 스모크 칠리&갈릭 65g 2개": "스모크칠리 2입",
    "DJ&A 표고버섯과자 시타케 머쉬룸 오리지널 30g 4개": "머쉬룸 30g 4입",
    # 올리브영 상품명 (납품확인서 · 상품별 매출_재고 조회)
    "DJ&A 시타케 머쉬룸 크리스프 65g 오리지널": "머쉬룸 오리지널",
    "DJ&A 시타케 머쉬룸 크리스프 65g 스모크드칠리앤갈릭 (온)": "스모크칠리",
    "DJ&A 시타케 머쉬룸 크리스프 65g 스모크드칠리앤갈릭": "스모크칠리",
    "DJ&A 베지 크리스프 90g 오리지널": "베지크리스프",
    "DJ&A 브로콜리 플로렛츠 45g": "브로콜리 플로렛",
    "DJ&A 포테이토 웨지 100g 오리지널": "포테이토 웨지",
    "DJ&A 포테이토 웨지 100g 스위트칠리": "포테이토 웨지 스위트칠리",
    "DJ&A 피클리셔스 50g 오리지널": "피클리셔스",
    "DJ&A 표고버섯 저키 60g 오리지널": "저키 오리지널",
    "DJ&A 시타케 표고버섯 크리스프 30g": "머쉬룸 30g",
    "DJ&A 포테이토 웨지스 오리지널 감자 65g": "포테이토 웨지 65g",
    "DJ&A 프루티 크리스프스 스트로우베리 25G": "프루티 크리스프 스트로베리",
    "(온/증) 2503_DJ&A_시타케 머쉬룸 크리스프 15g 오리지널": "머쉬룸 15g 증정",
    "크리스프파워 프로틴 프레첼 50g 씨솔트": "프로틴 프레첼 씨솔트",
    "크리스프파워 프로틴 프레첼 50g 참깨": "프로틴 프레첼 참깨",
    "크리스프파워 프로틴 프레첼 50g 에브리띵": "프로틴 프레첼 에브리띵",
    "크리스프파워 프로틴 프레첼 50g 체다": "프로틴 프레첼 체다",
    "팝타임 팝콘 42g 히말라야 핑크솔트 할라피뇨": "팝타임 42g 할라피뇨",
    "팝타임 팝콘 42g 히말라야 핑크솔트 스윗앤솔티": "팝타임 42g 스윗앤솔티",
    "팝타임 팝콘 28g 씨솔트": "팝타임 28g 씨솔트",
    "팝타임 팝콘 28g 화이트 체다치즈향": "팝타임 28g 체다치즈",
    "팝타임 팝콘 128g 씨솔트 (온)": "팝타임 128g 씨솔트",
    "팝타임 팝콘 198g 히말라야 핑크솔트 스윗앤솔티 (온)": "팝타임 198g 스윗앤솔티",
    "팝타임 팝콘 135g 화이트 체다치즈향 (온)": "팝타임 135g 체다치즈",
    "팝타임 팝콘 198g 히말라야 핑크솔트 할라피뇨 (온)": "팝타임 198g 할라피뇨",
    "트윈픽스 프로틴 퍼프 60g 할라피뇨체다": "트윈픽스 할라피뇨체다",
    "트윈픽스 프로틴 퍼프 60g 나초치즈맛": "트윈픽스 나초치즈",
    "트윈픽스 프로틴 퍼프 60g 사워크림&어니언": "트윈픽스 사워크림어니언",
}

# 라벨 → 바코드(BH SKU) — 채널 공통 (쿠팡 운영리포트 바코드 · 올리브영 상품코드가 모두 이 바코드)
COMMON_BARCODES = {
    "표고버섯 크리스프 오리지널 65g": "9345544003454",
    "표고버섯 크리스프 스모크 칠리&갈릭 65g": "9345544005977",
    "디제이앤에이 베지 크리스프 오리지널 90g": "9345544006868",
    "디제이앤에이 피클리셔스 50g 1봉": "9345544009395",
    "DJ&A 믹스드 버섯 크리스프 65g": "9345544007117",
    "디제이앤에이 크리스피 브로콜리 플로렛 45g": "9345544004970",
    "디제이앤에이 베지 크리스프 핫앤스파이시 스낵 90g": "9345544001214",
    "디제이앤에이 포테이토 웨지 오리지널 100g": "9345544006714",
    "DJ&A 크리스피 갈릭 클로브스 45g": "9345544007025",
    "디제이앤에이 스냅피 크리스프 30g 1봉": "9345544001535",
    "디제이앤에이 포테이토 웨지 스위트칠리 100g": "9345544006707",
    "DJ&A 크런치 타임 오리지널 50g": "9345544009289",
    "디제이앤에이 표고버섯 저키 오리지널 60g": "9345544005014",
    # 올리브영 상품명 → 상품코드(=바코드)
    "DJ&A 시타케 머쉬룸 크리스프 65g 오리지널": "9345544003454",
    "DJ&A 시타케 머쉬룸 크리스프 65g 스모크드칠리앤갈릭 (온)": "9345544005977",
    "DJ&A 베지 크리스프 90g 오리지널": "9345544006868",
    "DJ&A 브로콜리 플로렛츠 45g": "9345544004970",
    "DJ&A 포테이토 웨지 100g 오리지널": "9345544006714",
    "DJ&A 포테이토 웨지 100g 스위트칠리": "9345544006707",
    "DJ&A 피클리셔스 50g 오리지널": "9345544009395",
    "DJ&A 표고버섯 저키 60g 오리지널": "9345544005014",
    "DJ&A 시타케 표고버섯 크리스프 30g": "9345544001580",
    "DJ&A 포테이토 웨지스 오리지널 감자 65g": "9345544007964",
    "DJ&A 프루티 크리스프스 스트로우베리 25G": "9345544001207",
    "크리스프파워 프로틴 프레첼 50g 씨솔트": "850047451170",
    "크리스프파워 프로틴 프레첼 50g 참깨": "850047451187",
    "크리스프파워 프로틴 프레첼 50g 에브리띵": "850047451194",
    "크리스프파워 프로틴 프레첼 50g 체다": "850047451354",
    "팝타임 팝콘 42g 히말라야 핑크솔트 할라피뇨": "850053118319",
    "팝타임 팝콘 28g 씨솔트": "850053118326",
    "팝타임 팝콘 42g 히말라야 핑크솔트 스윗앤솔티": "850053118340",
    "팝타임 팝콘 28g 화이트 체다치즈향": "850053118364",
    "팝타임 팝콘 128g 씨솔트 (온)": "850053118234",
    "팝타임 팝콘 198g 히말라야 핑크솔트 스윗앤솔티 (온)": "850053118241",
    "팝타임 팝콘 135g 화이트 체다치즈향 (온)": "850053118258",
    "팝타임 팝콘 198g 히말라야 핑크솔트 할라피뇨 (온)": "850053118272",
    "트윈픽스 프로틴 퍼프 60g 할라피뇨체다": "850000796300",
    "트윈픽스 프로틴 퍼프 60g 나초치즈맛": "850000796324",
    "트윈픽스 프로틴 퍼프 60g 사워크림&어니언": "850000796348",
}

_BRAND_RE = re.compile(r"(DJ&A|디제이앤에이|표고버섯과자\s*시타케)", re.IGNORECASE)
_SIZE_RE = re.compile(r"\d+(\.\d+)?\s*(g|ml|kg|l)\b", re.IGNORECASE)


def _nkey(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _fallback_label(name: str) -> str:
    s = _BRAND_RE.sub(" ", str(name or ""))
    s = _SIZE_RE.sub(" ", s)
    s = re.sub(r"[,/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or str(name)


class _FallbackDict(dict):
    """dict.get 미스 시 fn(key)로 한 번 더 시도 — 채널 스펙의 키워드 기반 라벨/바코드 추정용."""
    def __init__(self, data: dict, fn=None):
        super().__init__(data)
        self._fn = fn

    def get(self, key, default=None):
        v = dict.get(self, key)
        if v:
            return v
        if self._fn:
            try:
                r = self._fn(key)
            except Exception:
                r = None
            if r:
                return r
        return default


def _alias_map(sp: ChannelSpec) -> dict:
    """정규화 키 → 라벨 (공통 + 채널 기본 + 사용자 덧씌움)."""
    m = {_nkey(k): v for k, v in COMMON_ALIASES.items()}
    m.update({_nkey(k): v for k, v in sp.aliases.items()})
    for k, v in (_load_state(sp).get("aliases") or {}).items():
        if str(v).strip():
            m[_nkey(k)] = str(v).strip()
    return _FallbackDict(m, sp.alias_fallback)


def _label_of(name: str, amap: dict) -> str:
    return amap.get(_nkey(name)) or _fallback_label(name)


def _barcode_map(sp: ChannelSpec, up: Optional[dict] = None) -> dict:
    """정규화 상품명 → 바코드 (공통표 + 채널표 + 업로드 리포트)."""
    m = {_nkey(k): v for k, v in COMMON_BARCODES.items()}
    m.update({_nkey(k): v for k, v in sp.barcodes.items()})
    up = up if up is not None else _load_uploads(sp)
    for _d, day in (up.get("rocket") or {}).items():
        for name, v in day.items():
            bc = str(v.get("barcode") or "").strip()
            if bc and bc.isdigit():
                m[_nkey(name)] = bc
    for _p, month in (up.get("monthly") or {}).items():
        for name, v in month.items():
            bc = str(v.get("code") or "").strip()
            if bc and bc.isdigit() and len(bc) >= 8:
                m[_nkey(name)] = bc
    return _FallbackDict(m, sp.barcode_fallback)


# ─────────────────────────────────────────────────────────────────
# 발주 데이터 (order_plan 누적 저장소의 채널 행)
# ─────────────────────────────────────────────────────────────────
def _channel_rows(sp: ChannelSpec) -> tuple[list, str]:
    """[{name, qty, conf, recv, amt, date, po, fname}], 마지막 반영 시각"""
    store = OP._load_plan_store()
    rows, last_ingest = [], ""
    for fname, f in store.get("files", {}).items():
        if (f.get("channel") or "") != sp.channel:
            continue
        last_ingest = max(last_ingest, f.get("ingested_at") or "")
        for r in f.get("rows", []):
            if not r.get("date"):
                continue
            qty = int(r.get("qty") or 0)
            conf = None if r.get("conf") is None else int(r.get("conf"))
            amt = None if r.get("amt") is None else int(r.get("amt"))
            recv_amt = None if r.get("recv_amt") is None else int(r.get("recv_amt"))
            if conf is None and sp.derive_conf_from_amt and amt and qty and recv_amt is not None:
                # 올리브영 납품확인서: 원가금액 = 납품예정수량 × 원단가, 발주금액 = 발주수량 × 원단가
                conf = int(round(recv_amt / (amt / qty)))
            if sp.qty_is_conf and conf is not None:
                # 납품예정수량만 납품하므로 발주잔량·금액을 그 기준으로 (발주수량은 order_qty에 보관)
                order_qty, qty = qty, conf
                if recv_amt is not None:
                    amt = recv_amt
            else:
                order_qty = qty
            rows.append({
                "name": str(r.get("name") or "").strip(),
                "qty": qty, "order_qty": order_qty,
                "conf": conf,
                "recv": (None if r.get("recv") is None else int(r.get("recv"))),
                "amt": amt, "recv_amt": recv_amt,
                "date": r.get("date"), "po": str(r.get("po") or ""), "fname": fname,
            })
    return rows, last_ingest


def _sku_map(sp: ChannelSpec, rows: list) -> dict:
    """채널 상품명(raw) → {sku, master_name} (발주 캘린더와 동일한 매칭 로직, 번들 미분리)."""
    try:
        agg = OP._aggregate_rows(
            [{"name": r["name"], "qty": r["qty"], "date": r["date"], "channel": sp.channel, "fname": r["fname"]}
             for r in rows], threshold=70, use_master=1, split_bundles=0)
    except Exception:
        return {}
    out = {}
    for it in agg.get("items", []):
        for raw in (it.get("raw_breakdown") or {}).keys():
            out[raw] = {"sku": it.get("sku") or "", "master_name": it.get("name") or raw,
                        "matched": bool(it.get("matched"))}
    return out


# ─────────────────────────────────────────────────────────────────
# BoxHero 재고 · 아워박스 가용재고 · BH 출고 (짧은 캐시, 채널 공통)
# ─────────────────────────────────────────────────────────────────
_bh_cache: dict = {"stock": None, "stock_ts": 0.0}
_BH_TTL = 600


def _bh_stock_by_sku(token: str, force: bool = False) -> dict:
    if not token:
        return {}
    now = time.time()
    if not force and _bh_cache["stock"] is not None and now - _bh_cache["stock_ts"] < _BH_TTL:
        return _bh_cache["stock"]
    try:
        items = _fetch_bh_items(token, set())
    except Exception:
        return _bh_cache["stock"] or {}
    m = {}
    for it in items:
        if it.get("sku"):
            m[it["sku"]] = {"qty": int(it.get("qty") or 0), "name": it.get("name") or ""}
    _bh_cache["stock"], _bh_cache["stock_ts"] = m, now
    return m


_ob_cache: dict = {"stock": None, "ts": 0.0, "error": ""}


def _ob_available_by_name(force: bool = False) -> dict:
    """아워박스 재고 → 정규화 상품명 → {available, total, unavailable}. OB 상품명은 BH 품목명과 동일 표기."""
    import html as _html
    now = time.time()
    if not force and _ob_cache["stock"] is not None and now - _ob_cache["ts"] < _BH_TTL:
        return _ob_cache["stock"]
    try:
        import ourbox_api as api_mod
        cfg = U.load_config()
        client = api_mod.make_client(cfg)
        if not client:
            _ob_cache["error"] = "OurBox 미설정"
            return _ob_cache["stock"] or {}
        raw = client.fetch_stock()
    except Exception as e:
        _ob_cache["error"] = str(e)[:120]
        return _ob_cache["stock"] or {}
    m: dict = {}
    for r in raw:
        nm = _nkey(_html.unescape(str(r.get("product_name") or "")))
        if not nm:
            continue
        e = m.setdefault(nm, {"available": 0, "total": 0, "unavailable": 0,
                              "name": _html.unescape(str(r.get("product_name") or "")).strip()})
        e["available"] += int(r.get("available_stock") or 0)
        e["total"] += int(r.get("total_stock") or 0)
        e["unavailable"] += int(r.get("unavailable_stock") or 0)
    _ob_cache["stock"], _ob_cache["ts"], _ob_cache["error"] = m, now, ""
    return m


_MEMO_DAY_RE = re.compile(r"(\d{1,2})\s*일")


def _memo_due(memo: str, tx_date: str) -> str:
    """BH 출고 메모 '# 10일 올리브영' → 납기 힌트 날짜 (출고일 이후 가장 가까운 그 일자)."""
    m = _MEMO_DAY_RE.search(memo or "")
    if not m or not tx_date:
        return ""
    day = int(m.group(1))
    try:
        base = datetime.fromisoformat(tx_date[:10])
    except ValueError:
        return ""
    for k in range(0, 2):
        y, mo = base.year, base.month + k
        if mo > 12:
            y, mo = y + 1, mo - 12
        try:
            cand = datetime(y, mo, day)
        except ValueError:
            continue
        if cand.date() >= base.date():
            return cand.strftime("%Y-%m-%d")
    return ""


def _channel_outbound(sp: ChannelSpec, token: str, from_date: str, to_date: str) -> list:
    """채널 거래처 출고 → [{date, created, sku, name, qty, partner, due}]"""
    if not token:
        return []
    try:
        txs = U.fetch_transactions(token, "out", from_date, to_date)
    except Exception:
        return []
    out = []
    for tx in txs:
        partner = ((tx.get("partner") or {}).get("name") or "") if isinstance(tx.get("partner"), dict) else ""
        if not sp.partner_match(partner):
            continue
        d = str(tx.get("transaction_time") or tx.get("created_at") or "")[:10]
        created = str(tx.get("created_at") or "")[:10]
        due = _memo_due(str(tx.get("memo") or ""), d)
        items = tx.get("items")
        if items is None:
            try:
                from routers.channel import _fetch_tx_detail
                items = _fetch_tx_detail(token, tx.get("id"))
            except Exception:
                items = []
        for it in items or []:
            sku = str(it.get("sku") or (it.get("item") or {}).get("sku") or "").strip()
            name = it.get("name") or (it.get("item") or {}).get("name") or ""
            q = abs(int(it.get("quantity") or 0))
            if q:
                out.append({"date": d, "created": created, "sku": sku, "name": name, "qty": q,
                            "partner": partner, "due": due, "tx": tx.get("id")})
    return out


# ─────────────────────────────────────────────────────────────────
# 업로드 파일 파서
# ─────────────────────────────────────────────────────────────────
def _parse_rocket_csv(data: bytes) -> dict:
    """쿠팡 basic_operation_rocket_*.csv → {date: {sku_name: {sales, stock, inbound, sku_id, barcode}}}"""
    text = None
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        raise ValueError("CSV 인코딩을 알 수 없습니다")
    rd = csv.DictReader(io.StringIO(text))
    cols = rd.fieldnames or []
    need = {"날짜", "SKU 명", "출고수량", "현재재고수량"}
    if not need.issubset(set(cols)):
        raise ValueError(f"쿠팡 운영 리포트 컬럼이 아닙니다: {cols[:8]}")
    out: dict = {}
    for r in rd:
        d = str(r.get("날짜") or "").strip()
        if len(d) == 8 and d.isdigit():
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        name = str(r.get("SKU 명") or "").strip()
        if not d or not name:
            continue
        brand = str(r.get("브랜드") or "")
        if brand and brand not in ("디제이앤에이", "DJ&A", "N_A"):
            continue
        if brand == "N_A" and "DJ&A" not in name and "디제이앤에이" not in name and "표고버섯" not in name:
            continue
        e = out.setdefault(d, {}).setdefault(name, {"sales": 0, "stock": 0, "inbound": 0,
                                                      "sku_id": str(r.get("SKU ID") or ""),
                                                      "barcode": str(r.get("바코드") or ""), "centers": 0})
        e["sales"] += int(float(r.get("출고수량") or 0))
        e["stock"] += int(float(r.get("현재재고수량") or 0))
        e["inbound"] += int(float(r.get("입고수량") or 0))
        e["centers"] += 1
    return out


def _parse_forecast_xlsx(df: pd.DataFrame) -> dict:
    """쿠팡 *-forecast-*.xlsx → {"weeks": [{from,to}], "items": {sku_name: {sku_id, values[]}}}"""
    hdr = None

    def _row(i):
        return ["" if pd.isna(v) else str(v) for v in df.iloc[i].tolist()]
    for i in range(min(10, len(df))):
        row = _row(i)
        if any("SKU NAME" in c.upper() for c in row) and any("Forecast" in c or "예상판매" in c for c in row):
            hdr = i
            break
    if hdr is None or hdr < 2:
        raise ValueError("주간 판매 예측 양식(SKU NAME / Weekly Sales Forecast)이 아닙니다")
    header = _row(hdr)
    name_idx = next(k for k, c in enumerate(header) if "SKU NAME" in c.upper())
    id_idx = next((k for k, c in enumerate(header) if c.strip().upper() == "SKU"), None)
    wk_idx = [k for k, c in enumerate(header) if "Forecast" in c or "예상판매" in c]

    def _d(v):
        if pd.isna(v):
            return ""
        s = str(v).strip().split(".")[0]
        return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else s

    weeks = [{"from": _d(df.iat[hdr - 2, k]), "to": _d(df.iat[hdr - 1, k])} for k in wk_idx]
    items = {}
    for i in range(hdr + 1, len(df)):
        name = df.iat[i, name_idx]
        if pd.isna(name) or not str(name).strip():
            continue
        vals = []
        for k in wk_idx:
            v = df.iat[i, k]
            try:
                vals.append(round(float(v), 1))
            except (TypeError, ValueError):
                vals.append(0.0)
        items[str(name).strip()] = {"sku_id": ("" if id_idx is None else str(df.iat[i, id_idx]).split(".")[0]),
                                    "values": vals}
    return {"weeks": weeks, "items": items}


def _is_oy_monthly(df: pd.DataFrame) -> bool:
    row0 = ["" if pd.isna(v) else str(v) for v in df.iloc[0].tolist()] if len(df) else []
    return "상품코드" in row0 and "상품명" in row0 and "재고" in row0 and "매출" in row0


def _parse_oy_monthly(df: pd.DataFrame) -> dict:
    """올리브영 '상품별 매출_재고 조회' xlsx (한 달치) →
    {name: {code, cost, price, stock_off, stock_on, stock_store, sales_store, sales_online, sales_global, sales_total, promo}}
    헤더 2행: 0행 대분류(재고/매출), 1행 소분류(오프라인센터/온라인센터/매장 · 매장/온라인/글로벌/계)."""
    row0 = ["" if pd.isna(v) else str(v).strip() for v in df.iloc[0].tolist()]
    row1 = ["" if pd.isna(v) else str(v).strip() for v in df.iloc[1].tolist()]
    col = {}
    for j, c in enumerate(row0):
        if c in ("상품코드", "상품명", "행사원가", "행사매가", "금주행사", "차주행사"):
            col[c] = j
    grp = ""
    for j in range(len(row0)):
        if row0[j] in ("재고", "매출"):
            grp = row0[j]
        sub = row1[j] if j < len(row1) else ""
        if grp and sub:
            col[f"{grp}:{sub}"] = j
    need = ["상품코드", "상품명", "재고:오프라인센터", "재고:온라인센터", "재고:매장", "매출:매장", "매출:온라인", "매출:계"]
    miss = [k for k in need if k not in col]
    if miss:
        raise ValueError(f"올리브영 매출_재고 양식 컬럼 누락: {miss}")

    def _i(row, key):
        j = col.get(key)
        if j is None or j >= len(row):
            return 0
        v = row[j]
        try:
            return int(float(str(v).replace(",", "")))
        except (TypeError, ValueError):
            return 0

    out = {}
    for i in range(2, len(df)):
        row = df.iloc[i].tolist()
        name = row[col["상품명"]] if col["상품명"] < len(row) else None
        if pd.isna(name) or not str(name).strip() or str(name).startswith("Total Count"):
            continue
        code = str(row[col["상품코드"]]).split(".")[0].strip() if pd.notna(row[col["상품코드"]]) else ""
        out[str(name).strip()] = {
            "code": code, "cost": _i(row, "행사원가"), "price": _i(row, "행사매가"),
            "stock_off": _i(row, "재고:오프라인센터"), "stock_on": _i(row, "재고:온라인센터"), "stock_store": _i(row, "재고:매장"),
            "sales_store": _i(row, "매출:매장"), "sales_online": _i(row, "매출:온라인"), "sales_global": _i(row, "매출:글로벌"),
            "sales_total": _i(row, "매출:계"),
            "promo": (str(row[col["금주행사"]]).strip() if "금주행사" in col and col["금주행사"] < len(row) and pd.notna(row[col["금주행사"]]) else ""),
        }
    if not out:
        raise ValueError("상품 행을 찾지 못했습니다")
    return out


def _upload_meta(sp: ChannelSpec, up: dict) -> dict:
    days = sorted((up.get("rocket") or {}).keys())
    fc = up.get("forecast") or None
    months = sorted((up.get("monthly") or {}).keys())
    return {
        "mode": sp.upload_mode,
        "rocket_days": len(days), "rocket_range": [days[0], days[-1]] if days else None,
        "forecast": ({"filename": fc.get("filename"), "uploaded_at": fc.get("uploaded_at"),
                      "weeks": fc.get("weeks")} if fc else None),
        "months": months,
        "files": up.get("files", []),
    }


# ─────────────────────────────────────────────────────────────────
# 요약 계산
# ─────────────────────────────────────────────────────────────────
def _md(d: str) -> str:
    """'2026-09-15' → '9/15'"""
    try:
        return f"{int(d[5:7])}/{int(d[8:10])}"
    except Exception:
        return d


def _supply(v: Optional[int]) -> Optional[int]:
    """발주금액(매입가, 부가세 포함) → 공급가액(부가세 제외). 스낵류는 전량 과세(10%)."""
    return None if v is None else int(round(int(v) / 1.1))


def _strip_money(text: str) -> str:
    """금액 정보가 없는 채널(컬리): 렌더된 보고문에서 원 단위 금액 표기를 걷어낸다."""
    W = r"\d[\d,억만 ]*원"
    out = []
    for line in text.split("\n"):
        line = re.sub(r"\s*\((?:매입 확정|매입 미반영|공급가)[^)]*\)", "", line)
        line = re.sub(r" — 매입 확정 [^\n]*", "", line)
        line = re.sub(r" \(매입 미반영\)", "", line)
        line = re.sub(r"\s*—\s*전량 매입 확정", "", line)
        line = re.sub(r" \(출고 완료 · 매입 미반영\)", " (출고 완료 = 입고 확정)", line)
        line = re.sub(r",? 출고 완료·매입 미반영 " + r"\d[\d,억만 ]*원", "", line)
        line = re.sub(r"\s*·\s*공급가 " + W, "", line)
        line = re.sub(r"공급가 " + W + r"\s*", "", line)
        line = re.sub(W + r" \((\d[\d,]*개[^)]*)\)", r"\1", line)
        line = re.sub(r"\s*·\s*" + W + r"(?=(\s|$|·|\)))", "", line)
        line = re.sub(r"\s{2,}", " ", line).rstrip()
        out.append(line)
    return "\n".join(out)


def _strip_recv_lines(text: str) -> str:
    """채널 입고확정 데이터가 없는 채널(컬리): '입고 0개 · 입고 대기', '입고 N월 누계 0개' 줄을 뺀다."""
    out = []
    for line in text.split("\n"):
        if re.search(r"^\* .*입고 0개 · 입고 대기", line) or re.search(r"^\* .*입고 .*누계 0개", line):
            continue
        line = re.sub(r" · 입고 대기 [\d,]+개", "", line)
        out.append(line)
    return "\n".join(out)


def _won(v: Optional[int]) -> str:
    """원 → '2억 9,234만원' / '442만원' / '59만원' (만원 미만은 원)"""
    if v is None:
        return "-"
    v = int(v)
    if v < 10_000:
        return f"{v:,}원"
    man = round(v / 10_000)
    if man >= 10_000:
        eok, rem = divmod(man, 10_000)
        return f"{eok}억 {rem:,}만원" if rem else f"{eok}억원"
    return f"{man:,}만원"


def _n(v) -> str:
    return "-" if v is None else f"{int(v):,}"


def _build_summary(sp: ChannelSpec, as_of: str, month: str, refresh_bh: bool = False) -> dict:
    rows, last_ingest = _channel_rows(sp)
    amap = _alias_map(sp)
    skumap = _sku_map(sp, rows)
    cfg = U.load_config()
    token = cfg.get("api_token") or ""
    month_start = month + "-01"
    month_label = f"{int(month[5:7])}월"

    def label(name):
        return _label_of(name, amap)

    vat = 1.1 if sp.amt_includes_vat else 1.0

    def sup(v):
        """발주금액 → 공급가액 (채널별 부가세 포함 여부 반영)"""
        return None if v is None else int(round(int(v) / vat))

    # 납기(입고예정일)별 · 품목별 집계
    by_date: dict = defaultdict(lambda: {"qty": 0, "order_qty": 0, "conf": 0, "conf_n": 0, "recv": 0, "recv_n": 0,
                                         "amt": 0, "pos": set(), "files": set(), "items": defaultdict(lambda: {
                                             "qty": 0, "order_qty": 0, "conf": 0, "conf_avail": False, "recv": 0,
                                             "recv_avail": False, "amt": 0, "names": set()})})
    for r in rows:
        g = by_date[r["date"]]
        lb = label(r["name"])
        it = g["items"][lb]
        g["qty"] += r["qty"]; it["qty"] += r["qty"]; it["names"].add(r["name"])
        oq = r.get("order_qty", r["qty"])
        g["order_qty"] += oq; it["order_qty"] += oq
        if r["po"]:
            g["pos"].add(r["po"])
        g["files"].add(r["fname"])
        if r["amt"] is not None:
            g["amt"] += r["amt"]; it["amt"] += r["amt"]
        c = r["conf"] if r["conf"] is not None else r["qty"]
        g["conf"] += c; it["conf"] += c
        if r["conf"] is not None:
            g["conf_n"] += 1; it["conf_avail"] = True
        if r["recv"] is not None:
            g["recv"] += r["recv"]; it["recv"] += r["recv"]; g["recv_n"] += 1; it["recv_avail"] = True

    stock_by_sku = _bh_stock_by_sku(token, force=refresh_bh)
    up = _load_uploads(sp)
    bcmap = _barcode_map(sp, up)

    def _sku_for(name: str) -> str:
        sk = skumap.get(name, {}).get("sku", "")
        if sk and (not stock_by_sku or sk in stock_by_sku):
            return sk
        bc = bcmap.get(_nkey(name), "")
        if bc and (not stock_by_sku or bc in stock_by_sku):
            return bc
        return sk or ""

    def _items_out(items: dict) -> list:
        out = []
        for lb, v in items.items():
            names = sorted(v["names"])
            skus = sorted({_sku_for(n) for n in names} - {""})
            out.append({"label": lb, "qty": v["qty"], "order_qty": v["order_qty"], "conf": v["conf"], "conf_avail": v["conf_avail"],
                        "short_vs_order": max(0, v["order_qty"] - v["qty"]),   # 발주수량 대비 납품예정 미달(올리브영 미납)
                        "recv": v["recv"] if v["recv_avail"] else None, "amt": v["amt"],
                        "names": names, "skus": skus})
        out.sort(key=lambda x: -x["qty"])
        return out

    groups = []
    for d in sorted(by_date):
        g = by_date[d]
        groups.append({
            "date": d, "md": _md(d), "qty": g["qty"], "order_qty": g["order_qty"], "short_vs_order": max(0, g["order_qty"] - g["qty"]), "conf": g["conf"],
            "conf_avail": g["conf_n"] > 0, "recv": g["recv"] if g["recv_n"] else None,
            "amt": g["amt"], "supply": sup(g["amt"]),
            "po_count": len(g["pos"]) if g["pos"] else len(g["files"]),
            "items": _items_out(g["items"]),
            "status": "pending" if d >= as_of else "past",
        })

    pending = [g for g in groups if g["status"] == "pending"]
    past_month = [g for g in groups if g["status"] == "past" and g["date"] >= month_start]

    pend_pos = set(); pend_files = set()
    for d in by_date:
        if d >= as_of:
            pend_pos |= by_date[d]["pos"]; pend_files |= by_date[d]["files"]
    pend_qty = sum(g["qty"] for g in pending)
    pend_conf = sum(g["conf"] for g in pending)
    pend_amt = sum(g["amt"] for g in pending)
    conf_avail_pending = any(g["conf_avail"] for g in pending)

    pend_by_label: dict = defaultdict(lambda: {"qty": 0, "conf": 0, "names": set(), "skus": set()})
    for g in pending:
        for it in g["items"]:
            p = pend_by_label[it["label"]]
            p["qty"] += it["qty"]; p["conf"] += it["conf"]
            p["names"] |= set(it["names"]); p["skus"] |= set(it["skus"])

    # 자사 재고 — 이 달에 등장한 모든 라벨
    all_labels: dict = defaultdict(lambda: {"names": set(), "skus": set()})
    for g in groups:
        if g["date"] >= month_start:
            for it in g["items"]:
                all_labels[it["label"]]["names"] |= set(it["names"])
                all_labels[it["label"]]["skus"] |= set(it["skus"])
    ob_by_name = _ob_available_by_name(force=refresh_bh)
    ob_connected = bool(ob_by_name)

    def _strip_code(k: str) -> str:
        return re.sub(r"^[a-z]{2,5}\d{3,5}-?", "", k)
    ob_stripped = {_strip_code(k): v for k, v in ob_by_name.items()}

    def _ob_for_skus(skus: list):
        if not ob_by_name:
            return None
        found = False
        av = tot = un = 0
        for sk in skus:
            nm = _nkey(stock_by_sku.get(sk, {}).get("name", ""))
            e = ob_by_name.get(nm) if nm else None
            if e is None and nm:
                e = ob_by_name.get(_strip_code(nm)) or ob_stripped.get(_strip_code(nm))
            if e:
                found = True
                av += e["available"]; tot += e["total"]; un += e["unavailable"]
        return {"available": av, "total": tot, "unavailable": un} if found else None

    stock_rows = []
    for lb, v in all_labels.items():
        skus = sorted(v["skus"])
        bh_qty = sum(stock_by_sku.get(s, {}).get("qty", 0) for s in skus) if skus else None
        ob = _ob_for_skus(skus) if skus else None
        qty = ob["available"] if ob else bh_qty
        pend = pend_by_label.get(lb, {"qty": 0, "conf": 0})
        remain = pend["conf"] if conf_avail_pending else pend["qty"]
        if qty is None:
            status = "unmapped"
        elif remain <= 0:
            status = "none"
        elif qty >= remain:
            status = "ok"
        elif qty <= max(50, remain * 0.05):
            status = "out"
        else:
            status = "short"
        stock_rows.append({"label": lb, "skus": skus, "stock": qty, "basis": ("ob" if ob else ("bh" if bh_qty is not None else None)),
                           "bh_stock": bh_qty,
                           "ob_available": (ob["available"] if ob else None), "ob_total": (ob["total"] if ob else None),
                           "ob_unavailable": (ob["unavailable"] if ob else None),
                           "pending": remain, "gap": (None if qty is None else qty - remain), "status": status})
    stock_rows.sort(key=lambda x: -(x["pending"] or 0))

    # 라벨별 공급단가(부가세 제외) — 발주 행의 발주금액/수량 가중평균 ÷ 1.1
    unit_amt: dict = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["amt"] is not None and r["qty"] > 0:
            u = unit_amt[label(r["name"])]
            u[0] += r["amt"]; u[1] += r["qty"]
    unit_supply = {lb: (a / q / vat) for lb, (a, q) in unit_amt.items() if q > 0}

    sku_to_label = {}
    for lb, v in all_labels.items():
        for s in v["skus"]:
            sku_to_label[s] = lb

    # ── 출고 실적 (BH 채널 거래처) — 이달 첫 지난 납기의 직전 납기 다음날 ~ 기준일 ──
    all_dates = sorted({r["date"] for r in rows if r["date"]})
    ship_from = month_start
    if past_month:
        prevs = [d for d in all_dates if d < past_month[0]["date"]]
        if prevs:
            ship_from = (datetime.fromisoformat(prevs[-1]) + timedelta(days=1)).strftime("%Y-%m-%d")
        ship_from = max(ship_from, (datetime.fromisoformat(month_start) - timedelta(days=15)).strftime("%Y-%m-%d"))
    ship = _channel_outbound(sp, token, ship_from, as_of)
    ship = [sh for sh in ship if sku_to_label.get(sh["sku"])]   # 채널 발주 품목으로 매핑되는 출고만
    for i, sh in enumerate(ship):
        sh["_i"] = i
    ship_by_date: dict = defaultdict(lambda: defaultdict(int))
    ship_by_label: dict = defaultdict(int)
    ship_today_by_label: dict = defaultdict(int)
    for s in ship:
        lb = sku_to_label.get(s["sku"]) or s["name"] or s["sku"]
        ship_by_date[s["date"]][lb] += s["qty"]
        ship_by_label[lb] += s["qty"]
        if s["created"] == as_of or s["date"] == as_of:
            ship_today_by_label[lb] += s["qty"]
    ship_total = sum(ship_by_label.values())
    ship_today = sum(ship_today_by_label.values())
    ship_supply_by_label = {lb: int(round(q * unit_supply.get(lb, 0))) for lb, q in ship_by_label.items()}
    ship_supply = sum(ship_supply_by_label.values())
    ship_priced = sum(q for lb, q in ship_by_label.items() if lb in unit_supply)

    # ── 납기 귀속 (거래 단위) ──
    #  (1) 메모의 'N일' 힌트가 어떤 납기 날짜와 같으면 그 납기
    #  (2) 힌트 없으면: 출고일 이후 14일 내 납기 중 '품목별 수량 일치도'가 가장 좋은 납기
    #      (예: 9/3 출고 2,949개 = 9/8 납품확인서 2,949개 → 9/8 납기분). 후보가 없으면 출고일 직후 납기.
    #  납품 = max(채널 입고확정, 우리 출고). 지난 납기·예정 납기 모두 배정하며, 예정 납기 배정분은 '선출고'.
    group_by_date = {g["date"]: g for g in groups}
    sorted_dates = sorted(group_by_date)

    def _need_of(it):
        return it["conf"] if it["conf_avail"] else it["qty"]

    txs: dict = defaultdict(list)
    for sh in ship:
        txs[(sh["tx"], sh["date"], sh["due"])].append(sh)
    assigned_of_tx: dict = {}
    for (txid, d, due), lines in txs.items():
        if due and due in group_by_date:
            assigned_of_tx[txid] = due
            continue
        cands = [gd for gd in sorted_dates if gd >= d and (datetime.fromisoformat(gd) - datetime.fromisoformat(d)).days <= 14]
        if not cands:
            assigned_of_tx[txid] = None
            continue
        want: dict = defaultdict(int)
        for sh in lines:
            want[sku_to_label.get(sh["sku"])] += sh["qty"]
        # 기본은 출고일 직후 납기(창 규칙). 더 늦은 납기의 발주 수량과 '정확히'(오차 2% 이내) 일치하는 경우에만 그 납기로.
        # (거래가 여러 건으로 나뉘어 출고된 경우 근사 일치로 잘못 옮겨 붙는 것을 막기 위해 정확 일치만 허용)
        best = cands[0]
        for gd in cands:
            g = group_by_date[gd]
            have_need = {it["label"]: _need_of(it) for it in g["items"]}
            have_recv = {it["label"]: (it["recv"] or 0) for it in g["items"]}
            # 발주수량 또는 채널 입고확정수량과 품목별로 정확히 일치할 때만 그 납기로 이동
            score = min(sum(abs(q - have_need.get(lb, 0)) for lb, q in want.items()),
                        sum(abs(q - have_recv.get(lb, 0)) for lb, q in want.items()))
            if score == 0:
                best = gd
                break
        assigned_of_tx[txid] = best
    for sh in ship:
        sh["assigned"] = assigned_of_tx.get(sh["tx"])

    for g in past_month:
        shipped_lb: dict = defaultdict(int)
        for sh in ship:
            if sh["assigned"] == g["date"]:
                shipped_lb[sku_to_label.get(sh["sku"])] += sh["qty"]
        g_ship = g_deliv = g_billed = g_unbilled = 0
        for it in g["items"]:
            it["shipped"] = shipped_lb.get(it["label"], 0)
            recv = it["recv"] or 0
            base = _need_of(it)
            it["delivered"] = max(recv, it["shipped"])
            it["unfilled"] = max(0, base - it["delivered"])
            u = unit_supply.get(it["label"], 0)
            it["billed_supply"] = int(round(recv * u))
            it["unbilled_supply"] = int(round(max(0, it["shipped"] - recv) * u))
            g_ship += it["shipped"]; g_deliv += it["delivered"]
            g_billed += it["billed_supply"]; g_unbilled += it["unbilled_supply"]
        # 그룹 품목에 없는 라벨의 출고(발주 외 품목)도 납품·미반영에 포함
        for lb, q in shipped_lb.items():
            if lb not in {it["label"] for it in g["items"]} and q:
                g_ship += q; g_deliv += q
                g_unbilled += int(round(q * unit_supply.get(lb, 0)))
        g["shipped"] = g_ship
        g["delivered"] = g_deliv
        g["billed_supply"] = g_billed
        g["unbilled_supply"] = g_unbilled
        g["unfilled"] = sum(it["unfilled"] for it in g["items"])

    # 예정 납기 선출고 (+ 어느 납기에도 못 붙은 출고)
    extra_by_label: dict = defaultdict(int)
    extra_by_due: dict = defaultdict(lambda: {"qty": 0, "supply": 0.0})
    for sh in ship:
        a = sh["assigned"]
        if a and a in {g["date"] for g in past_month}:
            continue
        if sh["date"] < month_start and not (a and a >= month_start):
            continue
        lb = sku_to_label.get(sh["sku"]) or sh["name"] or sh["sku"]
        extra_by_label[lb] += sh["qty"]
        key = a if (a and a >= as_of) else ""
        extra_by_due[key]["qty"] += sh["qty"]
        extra_by_due[key]["supply"] += sh["qty"] * unit_supply.get(lb, 0)
    extra_qty = sum(extra_by_label.values())
    extra_supply = int(round(sum(q * unit_supply.get(lb, 0) for lb, q in extra_by_label.items())))

    delivered_total = sum(g["delivered"] for g in past_month) + extra_qty
    billed_supply = sum(g["billed_supply"] for g in past_month)
    unbilled_supply = sum(g["unbilled_supply"] for g in past_month) + extra_supply
    delivered_supply = billed_supply + unbilled_supply
    delivered_by_label: dict = defaultdict(int)
    for g in past_month:
        for it in g["items"]:
            if it.get("delivered"):
                delivered_by_label[it["label"]] += it["delivered"]
    for lb, q in extra_by_label.items():
        delivered_by_label[lb] += q
    unbilled_by_group = [{"md": g["md"], "date": g["date"], "qty": sum(max(0, it["shipped"] - (it["recv"] or 0)) for it in g["items"]),
                          "supply": g["unbilled_supply"]} for g in past_month if g["unbilled_supply"]]
    for key, v in sorted(extra_by_due.items()):
        if v["qty"]:
            unbilled_by_group.append({"md": (f"{_md(key)} 납기 선출고" if key else "미귀속 출고"), "date": "",
                                      "qty": v["qty"], "supply": int(round(v["supply"]))})

    # ── 납품 가능 시뮬레이션 — 납기 빠른 순으로 가용재고(+입고 예정) 배분 ──
    avail: dict = {}
    for r in stock_rows:
        avail[r["label"]] = (None if r["stock"] is None else max(0, int(r["stock"])))
    incoming = sorted(_load_state(sp).get("incoming", []), key=lambda r: r["date"])
    label_keys = {_nkey(lb): lb for lb in all_labels}
    for lb in all_labels:
        for nm in all_labels[lb]["names"]:
            label_keys[_nkey(nm)] = lb
    fulfil_total = short_total = 0
    for g in pending:
        for r in incoming:
            if r["date"] <= g["date"] and not r.get("_applied"):
                lb = label_keys.get(_nkey(r["label"]))
                if lb is None:
                    r["_unknown"] = True
                    continue
                avail[lb] = (avail.get(lb) or 0) + int(r["qty"])
                r["_applied"] = True
        # 이 납기로 이미 나간 선출고분은 납품 완료로 간주 (재고에서 이미 빠져 있음)
        early_lb: dict = defaultdict(int)
        for sh in ship:
            if sh.get("assigned") == g["date"]:
                early_lb[sku_to_label.get(sh["sku"])] += sh["qty"]
        g_ok = g_short = g_early = 0
        g_unknown = False
        for it in g["items"]:
            need = it["conf"] if it["conf_avail"] else it["qty"]
            early = min(need, early_lb.get(it["label"], 0))
            it["shipped_early"] = early
            rem = need - early
            a = avail.get(it["label"])
            if a is None:
                if early:
                    it["deliverable"] = early; it["shortage"] = rem
                    g_ok += early; g_short += rem
                else:
                    it["deliverable"] = None; it["shortage"] = None
                    g_unknown = True
                g_early += early
                continue
            d = min(a, rem)
            avail[it["label"]] = a - d
            it["deliverable"] = early + d; it["shortage"] = rem - d
            g_ok += early + d; g_short += rem - d; g_early += early
        g["deliverable"] = g_ok; g["shortage"] = g_short; g["has_unknown"] = g_unknown
        g["shipped_early"] = g_early
        g["need"] = sum(it["conf"] if it["conf_avail"] else it["qty"] for it in g["items"])
        g["fulfil_rate"] = round(g_ok / g["need"] * 100) if g["need"] else 0
        fulfil_total += g_ok; short_total += g_short
    need_total = sum(g["need"] for g in pending)
    fulfil = {
        "deliverable": fulfil_total, "shortage": short_total, "need": need_total,
        "rate": (round(fulfil_total / need_total * 100) if need_total else 0),
        "leftover": [{"label": lb, "qty": q} for lb, q in sorted(avail.items(), key=lambda kv: -(kv[1] or 0)) if q],
        "short_by_label": [],
        "incoming": [{"id": r["id"], "label": r["label"], "qty": r["qty"], "date": r["date"], "md": _md(r["date"]),
                      "memo": r.get("memo", ""), "applied": bool(r.get("_applied")), "unknown": bool(r.get("_unknown"))}
                     for r in incoming],
        "incoming_total": sum(int(r["qty"]) for r in incoming if r.get("_applied")),
        "shipped_early": sum(g.get("shipped_early", 0) for g in pending),
    }
    sbl: dict = defaultdict(int)
    for g in pending:
        for it in g["items"]:
            if it.get("shortage"):
                sbl[it["label"]] += it["shortage"]
    fulfil["short_by_label"] = [{"label": k, "qty": v} for k, v in sorted(sbl.items(), key=lambda kv: -kv[1])]

    recv_supply = 0
    for r in rows:
        if (r["date"] or "")[:7] != month or not r["recv"]:
            continue
        if r["recv_amt"] and not sp.qty_is_conf:
            recv_supply += r["recv_amt"] / vat
        else:
            recv_supply += r["recv"] * unit_supply.get(label(r["name"]), 0)
    recv_supply = int(round(recv_supply))
    recv_month = sum(g["recv"] or 0 for g in groups if g["date"] >= month_start and g["date"][:7] == month and g["date"] < as_of)
    recv_by_label: dict = defaultdict(int)
    for g in groups:
        if g["date"][:7] == month:
            for it in g["items"]:
                if it["recv"]:
                    recv_by_label[it["label"]] += it["recv"]

    trend = _build_trend(sp, up, amap, as_of, month)

    core = sorted(pend_by_label.items(), key=lambda kv: -kv[1]["qty"])[:2]
    summary = {
        "channel": sp.key, "channel_name": sp.display, "has_conf": sp.has_conf, "conf_word": sp.conf_word,
        "amt_includes_vat": sp.amt_includes_vat, "has_amt": sp.has_amt, "has_recv": sp.has_recv,
        "recv_word": sp.recv_word, "upload_mode": sp.upload_mode,
        "as_of": as_of, "as_of_md": _md(as_of), "month": month, "month_label": month_label,
        "last_ingested_at": last_ingest, "bh_connected": bool(token),
        "bh_stock_cached_at": (datetime.fromtimestamp(_bh_cache["stock_ts"]).strftime("%H:%M")
                               if _bh_cache["stock_ts"] else None),
        "orders": {
            "po_count": len(pend_pos) if pend_pos else len(pend_files), "qty": pend_qty, "conf": pend_conf,
            "order_qty": sum(g["order_qty"] for g in pending), "short_vs_order": sum(g["short_vs_order"] for g in pending),
            "amt": pend_amt, "supply": sup(pend_amt),
            "conf_avail": conf_avail_pending, "conf_diff": pend_conf - pend_qty,
            "groups": pending,
            "core": [{"label": lb, "qty": v["qty"], "conf": v["conf"]} for lb, v in core],
        },
        "delivery": {
            "groups": past_month,
            "recv_month": recv_month,
            "recv_by_label": [{"label": k, "qty": v} for k, v in sorted(recv_by_label.items(), key=lambda kv: -kv[1])],
            "ship_total": delivered_total, "ship_today": ship_today,
            "ship_supply": delivered_supply, "ship_priced_qty": ship_priced,
            "bh_ship_total": ship_total, "bh_ship_supply": ship_supply, "ship_from": ship_from,
            "delivered_by_label": [{"label": k, "qty": v} for k, v in sorted(delivered_by_label.items(), key=lambda kv: -kv[1])],
            "recv_supply": recv_supply,
            "unbilled_supply": unbilled_supply,
            "billed_supply": billed_supply,
            "unbilled_by_group": unbilled_by_group,
            "ship_by_label": [{"label": k, "qty": v, "supply": ship_supply_by_label.get(k, 0)}
                              for k, v in sorted(ship_by_label.items(), key=lambda kv: -kv[1])],
            "ship_today_by_label": [{"label": k, "qty": v} for k, v in sorted(ship_today_by_label.items(), key=lambda kv: -kv[1])],
            "ship_by_date": [{"date": d, "md": _md(d), "qty": sum(m.values()),
                              "supply": int(round(sum(q * unit_supply.get(lb, 0) for lb, q in m.items()))),
                              "items": [{"label": k, "qty": v} for k, v in sorted(m.items(), key=lambda kv: -kv[1])]}
                             for d, m in sorted(ship_by_date.items())],
        },
        "stock": stock_rows,
        "stock_basis": ("ob" if ob_connected else "bh"),
        "ob_connected": ob_connected,
        "ob_error": _ob_cache.get("error", ""),
        "fulfil": fulfil,
        "trend": trend,
        "uploads": _upload_meta(sp, up),
        "notes": _load_state(sp).get("notes", ""),
        "all_groups": [{"date": g["date"], "md": g["md"], "qty": g["qty"], "amt": g["amt"], "supply": g["supply"], "status": g["status"]}
                       for g in groups],
    }
    if not sp.has_recv:
        # 채널 입고확정 데이터가 없는 채널(컬리): 별도 입고확정 절차가 없어 출고분 = 입고 확정으로 본다
        dvx = summary.get("delivery") or {}
        for g in dvx.get("groups") or []:
            g["recv"] = int(g.get("delivered") or 0)
            for it in g.get("items") or []:
                it["recv"] = int(it.get("delivered") or 0)
        dvx["recv_month"] = int(dvx.get("ship_total") or 0)
    summary["text"] = _render_text(sp, summary)
    summary["text_detail"] = _render_text_detail(sp, summary)
    if not sp.has_amt:
        summary["text"] = _strip_money(summary["text"])
        summary["text_detail"] = _strip_money(summary["text_detail"])
    if not sp.has_recv:
        summary["text"] = _strip_recv_lines(summary["text"])
        summary["text_detail"] = _strip_recv_lines(summary["text_detail"])
    return summary


# ─────────────────────────────────────────────────────────────────
# 판매/재고 추이
# ─────────────────────────────────────────────────────────────────
def _build_trend(sp: ChannelSpec, up: dict, amap: dict, as_of: str, month: str) -> dict:
    if sp.upload_mode == "monthly":
        return _build_trend_monthly(up, amap, as_of, month)
    return _build_trend_daily(up, amap)


def _build_trend_daily(up: dict, amap: dict) -> dict:
    """쿠팡: 일별 판매/재고(rocket) + 주간 예측(forecast)."""
    rocket = up.get("rocket") or {}
    days = sorted(rocket.keys())
    per_label: dict = defaultdict(lambda: {"sales": defaultdict(int), "stock": defaultdict(int), "names": set()})
    for d in days:
        for name, v in rocket[d].items():
            lb = _label_of(name, amap)
            per_label[lb]["sales"][d] += v.get("sales", 0)
            per_label[lb]["stock"][d] += v.get("stock", 0)
            per_label[lb]["names"].add(name)
    fc = up.get("forecast") or {}
    fc_items = fc.get("items") or {}
    fc_by_label: dict = defaultdict(lambda: None)
    for name, v in fc_items.items():
        lb = _label_of(name, amap)
        vals = v.get("values") or []
        cur = fc_by_label[lb]
        fc_by_label[lb] = vals if cur is None else [a + b for a, b in zip(cur, vals)]

    last7 = days[-7:]
    rows = []
    for lb, v in per_label.items():
        sales_series = [v["sales"].get(d, 0) for d in days]
        stock_series = [v["stock"].get(d, 0) for d in days]
        s7 = sum(v["sales"].get(d, 0) for d in last7)
        avg7 = round(s7 / len(last7), 1) if last7 else 0
        prev7 = days[-14:-7]
        p7 = sum(v["sales"].get(d, 0) for d in prev7)
        avgp = round(p7 / len(prev7), 1) if prev7 else None
        last_stock = stock_series[-1] if stock_series else 0
        first_stock = stock_series[0] if stock_series else 0
        fcv = fc_by_label.get(lb)
        rows.append({
            "label": lb, "names": sorted(v["names"]),
            "sales_series": sales_series, "stock_series": stock_series,
            "sales_7d": s7, "avg_7d": avg7, "avg_prev_7d": avgp,
            "trend_pct": (round((avg7 - avgp) / avgp * 100) if avgp else None),
            "stock_last": last_stock, "stock_first": first_stock, "stock_delta": last_stock - first_stock,
            "days_of_supply": (round(last_stock / avg7, 1) if avg7 > 0 else None),
            "forecast_weekly": fcv, "forecast_next": (fcv[0] if fcv else None),
        })
    for lb, vals in fc_by_label.items():
        if lb not in per_label and vals and sum(vals) > 0:
            rows.append({"label": lb, "names": [], "sales_series": [], "stock_series": [],
                         "sales_7d": 0, "avg_7d": 0, "avg_prev_7d": None, "trend_pct": None,
                         "stock_last": None, "stock_first": None, "stock_delta": None,
                         "days_of_supply": None, "forecast_weekly": vals, "forecast_next": vals[0]})
    rows.sort(key=lambda x: -(x["sales_7d"] or 0))
    return {"mode": "daily", "days": days, "weeks": fc.get("weeks") or [], "rows": rows,
            "has_rocket": bool(days), "has_forecast": bool(fc_items),
            "period_label": (f"{_md(days[0])}~{_md(days[-1])}" if days else "")}


def _build_trend_monthly(up: dict, amap: dict, as_of: str, month: str) -> dict:
    """올리브영: 월별 매출(매장/온라인/계)·재고(센터/매장) 스냅샷. 최신 월(보통 이달 MTD) 기준 일평균·재고일수."""
    monthly = up.get("monthly") or {}
    months = sorted(monthly.keys())
    if not months:
        return {"mode": "monthly", "days": [], "months": [], "weeks": [], "rows": [],
                "has_rocket": False, "has_forecast": False, "period_label": ""}
    latest = months[-1]
    # 최신 월의 경과 일수 (이달이면 기준일까지, 지난 달이면 그 달 일수)
    if latest == as_of[:7]:
        elapsed = max(1, int(as_of[8:10]))
    else:
        y, m = int(latest[:4]), int(latest[5:7])
        nxt = datetime(y + (m // 12), (m % 12) + 1, 1)
        elapsed = (nxt - timedelta(days=1)).day
    per_label: dict = defaultdict(lambda: {"sales": defaultdict(int), "sales_store": defaultdict(int), "sales_online": defaultdict(int),
                                           "amount": defaultdict(float), "retail": defaultdict(float), "stock_center": 0, "stock_store": 0,
                                           "cost": 0, "price": 0, "names": set(), "promo": ""})
    for p in months:
        for name, v in monthly[p].items():
            lb = _label_of(name, amap)
            e = per_label[lb]
            e["sales"][p] += v.get("sales_total", 0)
            e["sales_store"][p] += v.get("sales_store", 0)
            e["sales_online"][p] += v.get("sales_online", 0)
            e["amount"][p] += v.get("sales_total", 0) * (v.get("cost") or 0)
            e["retail"][p] += v.get("sales_total", 0) * (v.get("price") or 0)
            e["names"].add(name)
            if p == latest:   # 재고는 최신 스냅샷만 의미 있음
                e["stock_center"] += v.get("stock_off", 0) + v.get("stock_on", 0)
                e["stock_store"] += v.get("stock_store", 0)
                e["cost"] = v.get("cost") or e["cost"]
                e["price"] = v.get("price") or e["price"]
                e["promo"] = v.get("promo") or e["promo"]
    rows = []
    prev = months[-2] if len(months) >= 2 else None
    for lb, e in per_label.items():
        cur = e["sales"].get(latest, 0)
        avg = round(cur / elapsed, 1) if elapsed else 0
        stock_total = e["stock_center"] + e["stock_store"]
        prev_avg = None
        if prev:
            y, m = int(prev[:4]), int(prev[5:7])
            nxt = datetime(y + (m // 12), (m % 12) + 1, 1)
            pdays = (nxt - timedelta(days=1)).day
            prev_avg = round(e["sales"].get(prev, 0) / pdays, 1)
        rows.append({
            "label": lb, "names": sorted(e["names"]),
            "sales_series": [e["sales"].get(p, 0) for p in months],
            "amount_series": [int(round(e["amount"].get(p, 0))) for p in months],
            "stock_series": [],
            "sales_7d": cur, "avg_7d": avg, "avg_prev_7d": prev_avg,
            "trend_pct": (round((avg - prev_avg) / prev_avg * 100) if prev_avg else None),
            "sales_store": e["sales_store"].get(latest, 0), "sales_online": e["sales_online"].get(latest, 0),
            "sales_amount": int(round(e["amount"].get(latest, 0))),
            "sales_retail": int(round(e["retail"].get(latest, 0))),
            "stock_last": stock_total, "stock_center": e["stock_center"], "stock_store": e["stock_store"],
            "stock_first": None, "stock_delta": None,
            "days_of_supply": (round(stock_total / avg, 1) if avg > 0 else None),
            "forecast_weekly": None, "forecast_next": None,
            "cost": e["cost"], "price": e["price"], "promo": e["promo"],
        })
    rows.sort(key=lambda x: -(x["sales_7d"] or 0))
    return {"mode": "monthly", "days": [], "months": months, "latest": latest, "elapsed_days": elapsed,
            "weeks": [], "rows": rows, "has_rocket": True, "has_forecast": False,
            "period_label": f"{int(latest[5:7])}월" + (f" 1~{elapsed}일" if latest == as_of[:7] else ""),
            "total_sales": sum(r["sales_7d"] for r in rows),
            "total_amount": sum(r["sales_amount"] for r in rows),
            "total_retail": sum(r["sales_retail"] for r in rows),
            "total_store": sum(r["sales_store"] for r in rows), "total_online": sum(r["sales_online"] for r in rows),
            "prev": prev, "total_sales_prev": (sum(r["sales_series"][-2] for r in rows) if prev else None)}


# ─────────────────────────────────────────────────────────────────
# 보고 텍스트
# ─────────────────────────────────────────────────────────────────
def _top(items, key, n=3, fmt=None):
    fmt = fmt or (lambda it: f"{it['label']} {_n(it[key])}")
    items = [it for it in items if (it.get(key) or 0) > 0]
    head = " · ".join(fmt(it) for it in items[:n])
    rest = len(items) - n
    return head + (f" 외 {rest}종" if rest > 0 else "")


def _render_summary_lines_detail(sp: ChannelSpec, s: dict) -> list:
    """상세형 요약 — 발주 잔여 / 납품 실적 / 재고·납품 가능 / 판매 추이 / 특이사항 첫 줄."""
    o = s["orders"]; dv = s["delivery"]; f = s.get("fulfil") or {}
    ch = sp.display
    L = ["", "■ 요약", ""]
    if o["groups"]:
        rng = "·".join(g["md"] for g in o["groups"])
        core = " · ".join(f"{c['label']} {_n(c['qty'])}" for c in o["core"])
        L.append(f"* 미납품 발주잔량 {_n(o['qty'])}개 · {_won(o['supply'])} ({o['po_count']}건, {rng} 납기 예정)" + (f" — {core}" if core else ""))
    else:
        L.append("* 기준일 이후 납기 예정 발주 없음")
    if s["bh_connected"]:
        wait = max(0, dv["ship_total"] - sum(min(g["recv"] or 0, g.get("delivered", 0)) for g in dv["groups"]))
        if dv["ship_total"]:
            line = f"* {s['month_label']} 납품 {_n(dv['ship_total'])}개 · 공급가 {_won(dv.get('ship_supply'))}"
            if dv.get("unbilled_supply"):
                det = " · ".join(f"{u['md']}{' 납기분' if u.get('date') else ''} {_won(u['supply'])}" for u in (dv.get("unbilled_by_group") or [])[:3])
                line += f" (매입 확정 {_won(dv.get('billed_supply'))} + 출고 완료·매입 미반영 {_won(dv.get('unbilled_supply'))}" + (f": {det}" if det else "") + ")"
            if dv["ship_today"]:
                line += f" (오늘 {_n(dv['ship_today'])}개)"
            line += " — " + _top(dv.get("delivered_by_label") or dv["ship_by_label"], "qty", 3)
            L.append(line)
            line = f"* {ch} {sp.recv_word} {_n(dv['recv_month'])}개"
            if wait:
                line += f" · {sp.recv_word} 대기 {_n(wait)}개"
        else:
            line = f"* {s['month_label']} 납품 실적 없음 · {ch} {sp.recv_word} {_n(dv['recv_month'])}개"
        unf = sum(g.get("unfilled", 0) for g in dv["groups"])
        if unf:
            line += f" · 지난 납기 미납 {_n(unf)}개"
        L.append(line)
    if s["bh_connected"] and o["groups"] and f:
        basis = "가용재고" if s.get("ob_connected") else "재고"
        inc = f.get("incoming_total") or 0
        line = f"* 현재 {basis}{' + 입고 예정분으로' if inc else '로'} 발주잔량의 {f.get('rate', 0)}% 납품 가능 ({_n(f.get('deliverable'))}/{_n(f.get('need'))}개)"
        if f.get("shortage"):
            line += " · 부족 " + " · ".join(f"{x['label']} {_n(x['qty'])}" for x in (f.get("short_by_label") or [])[:3])
        L.append(line)
        outs = [r for r in s["stock"] if r["status"] in ("out", "short") and r["pending"] > 0]
        if outs:
            L.append("* 재고 소진 품목: " + " · ".join(f"{r['label']} {_n(r['stock'])}개" for r in outs[:3])
                     + (" → 입고 예정분 도착 후 납품" if inc else " → 입고 확보 필요"))
    t = s.get("trend") or {}
    if t.get("has_rocket") and t.get("rows"):
        core_labels = [c["label"] for c in o["core"]] or [r["label"] for r in t["rows"][:2]]
        if t.get("mode") == "monthly":
            bits = []
            for lb in core_labels[:2]:
                r = next((x for x in t["rows"] if x["label"] == lb), None)
                if r and r.get("sales_7d"):
                    tp = r.get("trend_pct")
                    bits.append(f"{lb} {_n(r['sales_7d'])}개(일평균 {r['avg_7d']:g}" + (f", {'+' if tp >= 0 else ''}{tp}%" if tp is not None else "") + ")"
                                + (f" 재고 {r['days_of_supply']:g}일분" if r.get("days_of_supply") else ""))
            head = f"* {ch} 판매 {t.get('period_label', '')} {_n(t.get('total_sales'))}개 · 소비자가 {_won(t.get('total_retail'))} (공급가 {_won(t.get('total_amount'))})"
            if t.get("total_sales_prev") is not None:
                head += f" (전월 {_n(t['total_sales_prev'])}개)"
            L.append(head + (" — " + " · ".join(bits) if bits else ""))
        else:
            bits = []
            for lb in core_labels:
                r = next((x for x in t["rows"] if x["label"] == lb), None)
                if r and r.get("avg_7d"):
                    tp = r.get("trend_pct")
                    bits.append(f"{lb} 일평균 {r['avg_7d']:g}개" + (f"({'+' if tp >= 0 else ''}{tp}%)" if tp is not None else "")
                                + (f", {ch} 재고 {r['days_of_supply']:g}일분" if r.get("days_of_supply") else ""))
            if bits:
                L.append(f"* {ch} 판매: " + " · ".join(bits))
    notes = [ln.strip().lstrip("*-•■ ").strip() for ln in (s.get("notes") or "").splitlines() if ln.strip()]
    if notes:
        L.append(f"* {notes[0]}")
    return L


def _render_text_detail(sp: ChannelSpec, s: dict) -> str:
    """상세형 보고문 — 요약 + 발주 현황 + 납품 실적(납기별) + 재고·납품 가능 + 판매/재고 + 특이사항."""
    L = []
    o = s["orders"]; dv = s["delivery"]; f = s.get("fulfil") or {}
    ch = sp.display
    L.append("안녕하세요 의장님")
    L.append(f"{s['as_of_md']} 기준 {ch} 발주·납품 현황 공유드립니다.")
    L.extend(_render_summary_lines_detail(sp, s))

    L += ["", f"■ 발주 현황 — {o['po_count']}건 · 공급가 {_won(o['supply'])} ({_n(o['qty'])}개)", ""]
    if not o["groups"]:
        L.append("* 기준일 이후 납기 예정 발주가 없습니다")
    for g in o["groups"]:
        L.append(f"* {g['md']} 납기 — {_won(g['supply'])} ({_n(g['qty'])}개) · {_top(g['items'], 'qty', 2)}"
                 + (f" · 발주 {_n(g['order_qty'])} 중 미납 {_n(g['short_vs_order'])}" if sp.qty_is_conf and g.get("short_vs_order") else ""))
    if sp.qty_is_conf and o.get("short_vs_order"):
        L.append(f"* 발주 {_n(o['order_qty'])}개 중 {sp.conf_word} {_n(o['qty'])}개 — 미납 {_n(o['short_vs_order'])}개는 재고 부족으로 납품예정에서 제외")
    if sp.has_conf and o["conf_avail"] and o["conf_diff"]:
        L.append(f"* {sp.conf_word}수량은 발주 대비 {_n(abs(o['conf_diff']))}개 {'적음' if o['conf_diff'] < 0 else '많음'} (납품가능 수량 확정 조정)")
    if s["last_ingested_at"]:
        L.append(f"* {sp.po_word} 최종 반영 {s['last_ingested_at'][5:16]} · 이후 추가 발주 없음")

    L += ["", f"■ 납품 실적 ({s['month_label']})", ""]
    if s["bh_connected"]:
        if dv["ship_total"]:
            L.append(f"* {s['month_label']} 납품 {_n(dv['ship_total'])}개 · 공급가 {_won(dv.get('ship_supply'))}"
                     + (f" (매입 확정 {_won(dv.get('billed_supply'))} · 매입 미반영 {_won(dv.get('unbilled_supply'))})" if dv.get("unbilled_supply") else "")
                     + (f" · 오늘 {_n(dv['ship_today'])}개 출고" if dv["ship_today"] else ""))
            for g in dv["groups"]:
                if not g.get("delivered"):
                    continue
                recv = g["recv"] or 0
                tag = ("매입 확정" if recv >= g["delivered"] else
                       (f"매입 확정 {_n(recv)} · 미반영 {_n(g['delivered'] - recv)}" if recv else "출고 완료 · 매입 미반영"))
                L.append(f"* {g['md']} 납기분 납품 {_n(g['delivered'])}개 ({tag}) — "
                         + _top(sorted(g["items"], key=lambda it: -(it.get("delivered") or 0)), "delivered", 3))
            for u in [u for u in (dv.get("unbilled_by_group") or []) if not u.get("date")]:
                L.append(f"* {u['md']} {_n(u['qty'])}개 · {_won(u['supply'])} (매입 미반영)")
            if dv["ship_by_date"]:
                L.append("* BH 출고 기록: " + " / ".join(f"{sd['md']} {_n(sd['qty'])}개" for sd in dv["ship_by_date"]))
        else:
            L.append(f"* {s['month_label']} {ch} 납품 실적 없음")
    else:
        L.append("* 박스히어로 미연동 — 출고 실적 조회 불가")
    L.append(f"* {ch} {sp.recv_word} {s['month_label']} 누계 {_n(dv['recv_month'])}개 ({sp.recv_hint})")
    shorts = [g for g in dv["groups"] if g.get("unfilled", 0) > 0]
    if shorts:
        L.append("* 발주 대비 미납: " + ", ".join(
            f"{g['md']} {_n(g['unfilled'])}개({_top(sorted(g['items'], key=lambda it: -(it.get('unfilled') or 0)), 'unfilled', 2)})" for g in shorts)
            + " — 재고 확보 후 순차 납품")
    elif dv["groups"]:
        L.append("* 지난 납기분 미납 없음")

    basis_txt = "아워박스 가용재고" if s.get("ob_connected") else "박스히어로 재고"
    L += ["", f"■ 자사 재고 및 납품 가능 ({basis_txt} 기준)", ""]
    if not s["bh_connected"]:
        L.append("* 박스히어로 미연동 — 설정에서 토큰을 연결하면 자사 재고가 표시됩니다")
    else:
        outs = [r for r in s["stock"] if r["status"] in ("out", "short") and r["pending"] > 0]
        oks = [r for r in s["stock"] if r["status"] == "ok" and r["pending"] > 0]
        if outs:
            L.append("* 부족: " + " · ".join(f"{r['label']} 가용재고 {_n(r['stock'])}개 / 미납품 발주잔량 {_n(r['pending'])}개" for r in outs[:3])
                     + (f" 외 {len(outs) - 3}종" if len(outs) > 3 else ""))
        if oks:
            L.append("* 대응 가능(가용재고 ≥ 발주잔량): " + _top(oks, "stock", 4, lambda r: f"{r['label']} {_n(r['stock'])}개"))
        um = [r for r in s["stock"] if r["status"] == "unmapped" and r["pending"] > 0]
        if um:
            L.append(f"* 세트·미매핑 {len(um)}종은 재고 미확인 ({_n(sum(r['pending'] for r in um))}개, 계산 제외)")
        if o["groups"] and f:
            inc = [r for r in (f.get("incoming") or []) if r.get("applied")]
            for r in inc:
                L.append(f"* 입고 예정 {r['md']} {r['label']} {_n(r['qty'])}개" + (f" ({r['memo']})" if r.get("memo") else "") + " 반영")
            L.append(f"* 납품 가능 {_n(f.get('deliverable'))}/{_n(f.get('need'))}개 ({f.get('rate', 0)}%) — "
                     + " · ".join(f"{g['md']} {g['fulfil_rate']}%" + (f"(부족 {_n(g['shortage'])})" if g.get("shortage") else "") for g in o["groups"]))
            if f.get("short_by_label"):
                L.append("* 부족 합계 " + " · ".join(f"{x['label']} {_n(x['qty'])}" for x in f["short_by_label"][:3])
                         + (" → 입고 후 납품" if not inc else " → 추가 입고 필요"))
    if o["groups"] and not s["bh_connected"]:
        L.append("* " + "·".join(g["md"] for g in o["groups"]) + " 납기 건은 입고 전 상태이며 재고 확보 후 순차 납품 대상입니다")

    t = s["trend"]
    if t.get("has_rocket") or t.get("has_forecast"):
        if t.get("mode") == "monthly":
            L += ["", f"■ {ch} 판매 현황 ({t.get('period_label', '')} · {ch} 매장·온라인 소비자 판매)", ""]
            L.append(f"* 판매 {_n(t.get('total_sales'))}개 (매장 {_n(t.get('total_store'))} · 온라인 {_n(t.get('total_online'))}) · 소비자가 {_won(t.get('total_retail'))} (행사매가) · 공급가 {_won(t.get('total_amount'))} (행사원가)"
                     + (f" · 전월 {_n(t.get('total_sales_prev'))}개" if t.get("total_sales_prev") is not None else ""))
            for r in t["rows"][:6]:
                if not r["sales_7d"] and not r["stock_last"]:
                    continue
                tp = r["trend_pct"]
                L.append(f"* {r['label']} — {_n(r['sales_7d'])}개 (매장 {_n(r['sales_store'])} · 온라인 {_n(r['sales_online'])}) · 소비자가 {_won(r['sales_retail'])} · 공급가 {_won(r['sales_amount'])}"
                         + (f" · 일평균 {r['avg_7d']:g}" + (f"({'+' if tp >= 0 else ''}{tp}%)" if tp is not None else "") if r["avg_7d"] else "")
                         + f" · {ch} 재고 센터 {_n(r['stock_center'])}/매장 {_n(r['stock_store'])}" + (f" ({r['days_of_supply']:g}일분)" if r.get("days_of_supply") else ""))
        else:
            rng = f" ({t['period_label']})" if t.get("period_label") else ""
            L += ["", f"■ {ch} 판매·재고 추이{rng}", ""]
            for r in t["rows"][:3]:
                bits = []
                if t["has_rocket"] and r["avg_7d"]:
                    tp = r["trend_pct"]
                    bits.append(f"일평균 {r['avg_7d']:g}개" + (f"({'+' if tp >= 0 else ''}{tp}%)" if tp is not None else ""))
                    bits.append(f"재고 {_n(r['stock_last'])}개" + (f"({r['days_of_supply']:g}일분)" if r["days_of_supply"] else ""))
                if r.get("forecast_next") is not None:
                    bits.append(f"다음주 예측 {_n(round(r['forecast_next']))}개")
                if bits:
                    L.append(f"* {r['label']} — " + " · ".join(bits))

    notes = (s.get("notes") or "").strip()
    if notes:
        L += ["", "■ 특이사항", ""]
        for ln in notes.splitlines():
            ln = ln.strip()
            if ln:
                L.append(ln if ln.startswith(("*", "■", "-", "•")) else f"* {ln}")
    return "\n".join(L)


def _pct1(a: float, b: float) -> str:
    """99.8% 처럼 소수 1자리 (정수면 소수 생략)."""
    if not b:
        return "0%"
    v = a / b * 100
    return f"{int(v)}%" if abs(v - round(v)) < 0.05 else f"{v:.1f}%"


def _render_text(sp: ChannelSpec, s: dict) -> str:
    """보고문 — 요약 3줄 · 납기별 발주잔량 · 재고 및 납품 가능 · 채널 판매/재고 (간결 양식)."""
    o = s["orders"]; dv = s["delivery"]; f = s.get("fulfil") or {}; t = s.get("trend") or {}
    ch = sp.display
    L = [f"안녕하세요, {s['as_of_md']} 기준 {ch} 발주·납품 현황 공유드립니다.", "", "■ 요약"]

    # 1) 발주잔량
    if o["groups"]:
        core = " · ".join(f"{c['label']} {_n(c['qty'])}" for c in o["core"])
        L.append(f"* 미납품 발주잔량 {_won(o['supply'])} ({_n(o['qty'])}개 · {o['po_count']}건)" + (f" — {core}" if core else ""))
        if sp.qty_is_conf and o.get("short_vs_order"):
            shorts: dict = defaultdict(int)
            for g in o["groups"]:
                for it in g["items"]:
                    if it.get("short_vs_order"):
                        shorts[it["label"]] += it["short_vs_order"]
            L.append(f"* 발주 {_n(o['order_qty'])}개 중 {sp.conf_word} {_n(o['qty'])}개 · 미납 {_n(o['short_vs_order'])}개 ("
                     + " · ".join(f"{k} {_n(v)}" for k, v in sorted(shorts.items(), key=lambda kv: -kv[1])[:3]) + ") — 재고 부족으로 납품예정에서 제외")
    else:
        L.append("* 기준일 이후 납기 예정 발주 없음")

    # 2) 납품
    if s["bh_connected"] and dv["ship_total"]:
        line = f"* {s['month_label']} 납품 {_won(dv.get('ship_supply'))} ({_n(dv['ship_total'])}개)"
        if dv.get("unbilled_supply"):
            line += f" — 매입 확정 {_won(dv.get('billed_supply'))}, 출고 완료·매입 미반영 {_won(dv.get('unbilled_supply'))}"
        else:
            line += " — 전량 매입 확정"
        unf = sum(g.get("unfilled", 0) for g in dv["groups"])
        if sp.qty_is_conf:
            unf += sum(g.get("short_vs_order", 0) for g in dv["groups"])
        if unf:
            line += f" · 지난 납기 발주 대비 미납 {_n(unf)}개"
        L.append(line)
    elif s["bh_connected"]:
        L.append(f"* {s['month_label']} 납품 실적 없음")

    # 3) 납품 가능
    inc = [r for r in (f.get("incoming") or []) if r.get("applied")]
    if s["bh_connected"] and o["groups"] and f:
        need, deliv, short = f.get("need", 0), f.get("deliverable", 0), f.get("shortage", 0)
        early = f.get("shipped_early") or 0
        src = ("·".join(sorted({r["md"] for r in inc})) + " 입고분으로") if inc else "현 재고로"
        early_txt = f", 선출고 {_n(early)}개 포함" if early else ""
        if short == 0:
            L.append(f"* {src} 발주잔량 전량 납품 가능합니다 ({_n(deliv)}/{_n(need)}개{early_txt})")
        else:
            L.append(f"* {src} 발주잔량의 {_pct1(deliv, need)} 납품 가능합니다 ({_n(deliv)}/{_n(need)}개{early_txt}) · 부족 "
                     + " · ".join(f"{x['label']} {_n(x['qty'])}" for x in (f.get("short_by_label") or [])[:3]))

    # ── 납기별 발주잔량
    if o["groups"]:
        L += ["", "■ 납기별 발주잔량"]
        for g in o["groups"]:
            line = f"* {g['md']} — {_won(g['supply'])} ({_n(g['qty'])}개)" + (f" · {_top(g['items'], 'qty', 2)}" if len(g["items"]) > 1 or g["items"][0]["qty"] != g["qty"] else (f" · {g['items'][0]['label']}" if g["items"] else ""))
            if sp.qty_is_conf and g.get("short_vs_order"):
                sh = sorted([it for it in g["items"] if it.get("short_vs_order")], key=lambda it: -it["short_vs_order"])
                line += f" · 발주 {_n(g['order_qty'])} 중 미납 {_n(g['short_vs_order'])}(" + " · ".join(f"{it['label']} {_n(it['short_vs_order'])}" for it in sh[:2]) + ")"
            L.append(line)

    # ── 재고 및 납품 가능
    if s["bh_connected"] and o["groups"]:
        L += ["", "■ 재고 및 납품 가능"]
        inc_by_label: dict = defaultdict(list)
        for r in inc:
            inc_by_label[r["label"]].append(r)
        # 라벨 정규화 (입고 예정 라벨은 사용자가 적은 이름)
        label_of_inc = {}
        for lb, rs in inc_by_label.items():
            hit = next((x["label"] for x in s["stock"] if _nkey(x["label"]) == _nkey(lb)), lb)
            label_of_inc[hit] = label_of_inc.get(hit, []) + rs
        outs = [r for r in s["stock"] if r["status"] in ("out", "short") and r["pending"] > 0]
        short_lbl = {x["label"]: x["qty"] for x in (f.get("short_by_label") or [])}
        for r in outs:
            rs = label_of_inc.get(r["label"], [])
            early_r = sum(it.get("shipped_early", 0) for g in o["groups"] for it in g["items"] if it["label"] == r["label"])
            sh_q = short_lbl.get(r["label"], 0)
            line = f"* {r['label']} — 가용 {_n(r['stock'])}개"
            if early_r:
                line += f", 선출고 {_n(early_r)}개 완료"
            if rs:
                line += ", " + " · ".join(f"{x['md']} 입고 {_n(x['qty'])}개" for x in rs) + ("로 충당" if not sh_q else f"로 일부 충당 (부족 {_n(sh_q)}개)")
            elif sh_q:
                line += f", 미납품 발주잔량 {_n(r['pending'])}개 → 부족 {_n(sh_q)}개"
            else:
                line += f" → 발주잔량 {_n(r['pending'])}개 납품 가능"
            L.append(line)
        oks = [r for r in s["stock"] if r["status"] == "ok" and r["pending"] > 0]
        non_full = [g for g in o["groups"] if g.get("shortage")]
        if oks:
            rate_txt = ("납기별 납품률은 모두 100%입니다" if not non_full else
                        "납기별 납품률은 " + " · ".join(f"{g['md']} {_pct1(g['deliverable'], g['need'])}" for g in non_full)
                        + ("이고 나머지는 100%입니다" if len(non_full) < len(o["groups"]) else "입니다"))
            L.append(f"* 그 외 {len(oks)}종은 현 재고로 대응 가능하며, {rate_txt}")
        rest_short = [x for x in (f.get("short_by_label") or []) if x["label"] not in {r["label"] for r in outs}]
        for x in rest_short[:3]:
            L.append(f"* {x['label']} {_n(x['qty'])}개 부족 — 추가 입고 필요")
        um = [r for r in s["stock"] if r["status"] == "unmapped" and r["pending"] > 0]
        if um:
            L.append(f"* 세트·미매핑 {len(um)}종({_n(sum(r['pending'] for r in um))}개)은 재고 미확인으로 계산에서 제외")
        # 특이사항 메모는 이 섹션 끝에 그대로
        for ln in (s.get("notes") or "").splitlines():
            ln = ln.strip()
            if ln:
                L.append(ln if ln.startswith(("*", "-", "•")) else f"* {ln}")
    elif (s.get("notes") or "").strip():
        L += ["", "■ 특이사항"]
        for ln in (s.get("notes") or "").splitlines():
            ln = ln.strip()
            if ln:
                L.append(ln if ln.startswith(("*", "-", "•")) else f"* {ln}")

    # ── 채널 판매/재고
    if (t.get("has_rocket") or t.get("has_forecast")) and t.get("rows"):
        per = t.get("period_label", "")
        if t.get("mode") == "monthly":
            L += ["", f"■ {ch} 판매 현황 ({per} · {ch} 매장·온라인 소비자 판매)"]
            L.append(f"* 판매 {_n(t.get('total_sales'))}개 (매장 {_n(t.get('total_store'))} · 온라인 {_n(t.get('total_online'))}) · 소비자가 {_won(t.get('total_retail'))} · 공급가 {_won(t.get('total_amount'))}"
                     + (f" · 전월 {_n(t.get('total_sales_prev'))}개" if t.get("total_sales_prev") is not None else ""))
            for r in [x for x in t["rows"] if x["sales_7d"] or x["stock_last"]][:4]:
                tp = r["trend_pct"]
                L.append(f"* {r['label']} — {_n(r['sales_7d'])}개 (매장 {_n(r['sales_store'])} · 온라인 {_n(r['sales_online'])}) · 소비자가 {_won(r['sales_retail'])}"
                         + (f" · 일평균 {_n(round(r['avg_7d']))}개" + (f"({'+' if tp >= 0 else ''}{tp}%)" if tp is not None else "") if r["avg_7d"] else "")
                         + f" · {ch} 재고 센터 {_n(r['stock_center'])}/매장 {_n(r['stock_store'])}" + (f" ({r['days_of_supply']:g}일분)" if r.get("days_of_supply") else ""))
        else:
            L += ["", f"■ {ch} 판매·재고" + (f" ({per})" if per else "")]
            for r in [x for x in t["rows"] if x["avg_7d"] or x.get("forecast_next")][:3]:
                bits = []
                if r["avg_7d"]:
                    tp = r["trend_pct"]
                    bits.append(f"일평균 {_n(round(r['avg_7d']))}개" + (f"({'+' if tp >= 0 else ''}{tp}%)" if tp is not None else ""))
                    bits.append(f"재고 {r['days_of_supply']:g}일분" if r.get("days_of_supply") else f"재고 {_n(r['stock_last'])}개")
                if r.get("forecast_next") is not None and not r["avg_7d"]:
                    bits.append(f"다음주 예측 {_n(round(r['forecast_next']))}개")
                if bits:
                    L.append(f"* {r['label']} — " + " · ".join(bits))
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────
# 라우터
# ─────────────────────────────────────────────────────────────────
class NotesIn(BaseModel):
    notes: str


class IncomingIn(BaseModel):
    label: str
    qty: int
    date: str
    memo: str = ""
    id: Optional[str] = None


class AliasIn(BaseModel):
    aliases: dict


def make_router(sp: ChannelSpec) -> APIRouter:
    router = APIRouter()

    @router.get("/summary")
    def get_summary(as_of: str = "", month: str = "", refresh_bh: int = 0):
        as_of = (as_of or date.today().isoformat())[:10]
        try:
            datetime.fromisoformat(as_of)
        except ValueError:
            raise HTTPException(400, "as_of 형식 오류 (YYYY-MM-DD)")
        month = (month or as_of[:7])[:7]
        return _build_summary(sp, as_of, month, refresh_bh=bool(refresh_bh))

    @router.post("/upload")
    async def upload_files(files: list[UploadFile] = File(...), period: str = Form("")):
        """판매/재고 파일 업로드. 쿠팡: rocket csv / forecast xlsx. 올리브영: 상품별 매출_재고 xlsx (period=YYYY-MM 필수, 미지정 시 이달)."""
        up = _load_uploads(sp)
        added, errors = [], []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        period = (period or date.today().strftime("%Y-%m"))[:7]
        for f in files:
            fname = f.filename or "file"
            data = await f.read()
            try:
                low = fname.lower()
                kind = None
                if low.endswith(".csv"):
                    parsed = _parse_rocket_csv(data)
                    for d, m in parsed.items():
                        up["rocket"][d] = m
                    kind = "rocket"
                    added.append({"filename": fname, "type": kind, "days": len(parsed),
                                  "range": [min(parsed), max(parsed)] if parsed else None})
                elif low.endswith((".xlsx", ".xls")):
                    df = pd.read_excel(io.BytesIO(data), header=None)
                    if _is_oy_monthly(df):
                        up["monthly"][period] = _parse_oy_monthly(df)
                        kind = "monthly"
                        added.append({"filename": fname, "type": kind, "period": period, "items": len(up["monthly"][period])})
                    else:
                        up["forecast"] = {**_parse_forecast_xlsx(df), "filename": fname, "uploaded_at": now}
                        kind = "forecast"
                        added.append({"filename": fname, "type": kind, "weeks": len(up["forecast"]["weeks"])})
                else:
                    errors.append(f"{fname}: 지원하지 않는 형식(csv/xlsx만)")
                    continue
                up["files"] = [x for x in up["files"] if x.get("filename") != fname]
                up["files"].append({"filename": fname, "uploaded_at": now, "type": kind,
                                    **({"period": period} if kind == "monthly" else {})})
            except Exception as e:
                errors.append(f"{fname}: {e}")
        keep = sorted(up["rocket"].keys())[-120:]
        up["rocket"] = {d: up["rocket"][d] for d in keep}
        _save_json(sp.upload_path, up)
        return {"added": added, "errors": errors, "uploads": _upload_meta(sp, up)}

    @router.post("/upload/clear")
    def clear_uploads():
        _save_json(sp.upload_path, _empty_uploads())
        return {"ok": True}

    @router.get("/notes")
    def get_notes():
        st = _load_state(sp)
        return {"notes": st.get("notes", ""), "updated_at": st.get("notes_updated_at")}

    @router.put("/notes")
    def put_notes(body: NotesIn):
        st = _load_state(sp)
        st["notes"] = body.notes
        st["notes_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _save_json(sp.state_path, st)
        return {"notes": st["notes"], "updated_at": st["notes_updated_at"]}

    @router.get("/incoming")
    def get_incoming():
        return {"incoming": _load_state(sp).get("incoming", [])}

    @router.post("/incoming")
    def upsert_incoming(body: IncomingIn):
        try:
            datetime.fromisoformat(body.date[:10])
        except ValueError:
            raise HTTPException(400, "날짜 형식 오류 (YYYY-MM-DD)")
        st = _load_state(sp)
        rows = st.get("incoming", [])
        rid = body.id or f"{int(time.time()*1000)}"
        row = {"id": rid, "label": body.label.strip(), "qty": int(body.qty), "date": body.date[:10], "memo": body.memo.strip()}
        rows = [r for r in rows if r.get("id") != rid] + [row]
        rows.sort(key=lambda r: (r["date"], r["label"]))
        st["incoming"] = rows
        _save_json(sp.state_path, st)
        return {"incoming": rows}

    @router.delete("/incoming/{rid}")
    def delete_incoming(rid: str):
        st = _load_state(sp)
        st["incoming"] = [r for r in st.get("incoming", []) if r.get("id") != rid]
        _save_json(sp.state_path, st)
        return {"incoming": st["incoming"]}

    @router.get("/aliases")
    def get_aliases():
        st = _load_state(sp)
        return {"defaults": {**COMMON_ALIASES, **sp.aliases}, "overrides": st.get("aliases", {})}

    @router.put("/aliases")
    def put_aliases(body: AliasIn):
        st = _load_state(sp)
        st["aliases"] = {str(k): str(v) for k, v in (body.aliases or {}).items()}
        _save_json(sp.state_path, st)
        return {"ok": True, "overrides": st["aliases"]}

    return router
