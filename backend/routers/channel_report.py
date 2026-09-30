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
    price_table: bool = False        # 발주 데이터에 금액이 없어 별도 단가표(상품공급정보 등)로 매입금액을 계산하는 채널(컬리)
    price_vat_included: bool = True  # 단가표 단가에 부가세 포함 여부 기본값 (컬리 '일반단가'는 부가세 별도 → 컬리 화면 공급가 합계 = 단가×1.1)
    supply_includes_vat: bool = False  # True(컬리): 채널이 부가세 포함 금액을 '공급가'로 부름 → 보고 금액을 ÷1.1 하지 않고 채널 화면과 같은 금액으로 표시
    exec_name: str = ""              # 경영 요약(3채널 묶음) 표기 ('쿠팡 로켓배송' 등). 비면 display
    exec_metric: str = "order"       # 경영 요약 월별 추이 지표: 'recv'(납품) | 'order'(발주)
    exec_metric_word: str = ""       # 경영 요약 첫 줄 지표 표기. 비면 recv→'납품', order→'발주' (컬리: 값은 납품(출고)인데 '발주'로 적는 관행)
    history_by_order_date: bool = False  # 월별 추이의 '발주 수량'을 납기월 대신 발주일 월로 집계 (올리브영: 납품확인서 '발주일자' 기준이 보고 관행)
    exclude_item: Optional[Callable[[str, str], bool]] = None  # (품목명, SKU/바코드) → True 면 이 채널 집계에서 제외 (컬리: 이알하나)
    cancel_memo_kw: str = ""         # BH '입고' 중 메모에 이 키워드 + '취소' 가 있으면 출고 취소로 보고 월별 출고에서 상계 (컬리: '# 컬리 김포취소')
    bh_month_basis: str = "ship"     # 월별 BH 출고 귀속: 'ship'(출고일 월) | 'due'(납기월 — 메모 납기일, 없으면 다음 서류 납기일). 컬리는 'due'
    unit_basis: bool = False         # True(쿠팡): 세트 상품(3입·2입·혼합세트) 행을 구성 단품 행으로 풀어 수량을 단품 단위로 집계 (금액은 행 합계를 나눠 붙임 → 발주서 합계 불변)
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
    st.setdefault("prices", _empty_prices(sp))   # 매입 단가표 (price_table 채널)
    st.setdefault("monthly", {})    # {YYYY-MM: {order_qty, delivered}} 월별 추이 수기 보정 (저장소에 없는 지난 달)
    st.setdefault("bh_monthly", {})  # {YYYY-MM: {qty, fetched_at}} BH 채널 거래처 월별 출고 합계 캐시 (지난 달, 3일 TTL)
    return st


def _empty_prices(sp: Optional["ChannelSpec"] = None) -> dict:
    # items: {label: {unit, event, name, barcode, code, box}} — unit = 매입단가(원). vat_included: 단가에 부가세 포함 여부
    return {"items": {}, "vat_included": (sp.price_vat_included if sp else True), "source": "", "updated_at": ""}


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
    "DJ&A 머쉬룸 스모크칠리&갈릭 크리스프 30g": "스모크칠리 30g",
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

# 세트 라벨 → [(구성 단품 상품명, 세트당 개수)]. unit_basis 채널(쿠팡)에서 발주서 세트 행을 단품 행으로 풀 때 사용.
# 구성 단품 상품명은 COMMON_ALIASES/COMMON_BARCODES 에 있는 쿠팡 단품명 그대로 (라벨·바코드가 그대로 붙게)
_MUSH30 = "디제이앤에이 표고버섯 크리스프 오리지널 30g 1봉"
_SMOKE65 = "표고버섯 크리스프 스모크 칠리&갈릭 65g"
_MUSH65 = "표고버섯 크리스프 오리지널 65g"
COMMON_BUNDLES = {
    "머쉬룸 30g 3입": [(_MUSH30, 3)],
    "머쉬룸 30g 4입": [(_MUSH30, 4)],
    "스모크칠리 30g 3입": [("DJ&A 머쉬룸 스모크칠리&갈릭 크리스프 30g", 3)],
    "스모크칠리 2입": [(_SMOKE65, 2)],
    "스모크칠리 3입": [(_SMOKE65, 3)],
    "베지크리스프 3입": [("디제이앤에이 베지 크리스프 오리지널 90g", 3)],
    "포테이토 웨지 3입": [("디제이앤에이 포테이토 웨지 오리지널 100g", 3)],
    "포테이토 웨지 스위트칠리 3입": [("디제이앤에이 포테이토 웨지 스위트칠리 100g", 3)],
    "저키 오리지널 3입": [("디제이앤에이 표고버섯 저키 오리지널 60g", 3)],
    "저키 스모크칠리 3입": [("디제이앤에이 표고버섯 저키 스모크칠리&갈릭 60g", 3)],
    "저키 텍사스BBQ 3입": [("디제이앤에이 표고버섯 저키 텍사스 BBQ 60g", 3)],
    "머쉬룸 혼합세트(30g×4+칠리×2)": [(_MUSH30, 4), (_SMOKE65, 2)],
    "머쉬룸 혼합세트(30g×2+칠리×1)": [(_MUSH30, 2), (_SMOKE65, 1)],
    "머쉬룸 혼합세트(65g×3+칠리×1)": [(_MUSH65, 3), (_SMOKE65, 1)],
    "머쉬룸 혼합세트(65g×1+칠리×3)": [(_MUSH65, 1), (_SMOKE65, 3)],
}

# 라벨 → 바코드(BH SKU) — 채널 공통 (쿠팡 운영리포트 바코드 · 올리브영 상품코드가 모두 이 바코드)
COMMON_BARCODES = {
    "디제이앤에이 표고버섯 크리스프 오리지널 30g 1봉": "9345544001580",   # 세트 해체 단품 (BH: 시타케 머쉬룸 오리지널 30g)
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
# 매입 단가표 (발주 데이터에 금액이 없는 채널 — 컬리 상품공급정보의 '일반단가')
# ─────────────────────────────────────────────────────────────────
def _to_int(v) -> int:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return 0
        return int(round(float(str(v).replace(",", "").strip() or 0)))
    except Exception:
        return 0


def _parse_supply_info_xlsx(df: pd.DataFrame) -> list:
    """컬리 파트너 '상품공급정보 다운로드' xlsx → [{name, unit, event, barcode, code, box}].
    헤더 행(상품명·일반단가 포함)을 찾아 읽는다. 단가 '1,600' 문자열도 처리."""
    hdr = None
    for i in range(min(len(df), 10)):
        vals = [str(x).strip() for x in df.iloc[i].tolist()]
        if "상품명" in vals and any("일반단가" in v for v in vals):
            hdr = i
            break
    if hdr is None:
        raise ValueError("상품공급정보 형식이 아닙니다 (상품명·일반단가 컬럼 없음)")
    cols = [str(x).strip() for x in df.iloc[hdr].tolist()]
    idx = {c: k for k, c in enumerate(cols)}

    def col(row, *names):
        for nm in names:
            if nm in idx:
                v = row[idx[nm]]
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    return v
        return None

    out = []
    for r in range(hdr + 1, len(df)):
        row = df.iloc[r].tolist()
        name = str(col(row, "상품명") or "").strip()
        if not name:
            continue
        bc = col(row, "대체코드", "바코드")
        bc_s = ""
        if bc is not None:
            bc_s = str(int(bc)) if isinstance(bc, float) else str(bc).strip()
        out.append({
            "name": name,
            "unit": _to_int(col(row, "일반단가")),
            "event": _to_int(col(row, "이벤트단가")),
            "barcode": bc_s,
            "code": str(col(row, "마스터코드") or "").strip(),
            "box": _to_int(col(row, "박스당 입수", "박스입수")),
        })
    return out


def _apply_prices(sp: ChannelSpec, rows: list, amap: dict, prices: dict) -> tuple[int, list]:
    """금액 없는 발주 행에 단가표 × 수량으로 매입금액(amt)을 채운다.
    amt는 채널 스펙의 amt_includes_vat 기준(컬리 True → 부가세 포함 금액)으로 맞춘다.
    반환: (금액 채운 행 수, 단가표에 없는 라벨 목록)"""
    items = (prices or {}).get("items") or {}
    if not items:
        return 0, []
    vat_inc = bool((prices or {}).get("vat_included", True))
    # 단가 부가세 포함 여부와 채널 amt 기준이 다르면 ×1.1 / ÷1.1
    if sp.amt_includes_vat:
        scale = 1.0 if vat_inc else 1.1
    else:
        scale = (1 / 1.1) if vat_inc else 1.0
    filled, missing = 0, []
    for r in rows:
        if r.get("amt") is not None:
            continue
        lb = _label_of(r["name"], amap)
        it = items.get(lb)
        if it is None or it.get("unit") is None:
            if lb not in missing:
                missing.append(lb)
            continue
        r["amt"] = int(round(r["qty"] * int(it["unit"]) * scale))
        r["amt_from_price"] = True
        filled += 1
    return filled, missing


# ─────────────────────────────────────────────────────────────────
# 발주 데이터 (order_plan 누적 저장소의 채널 행)
# ─────────────────────────────────────────────────────────────────
def _channel_rows(sp: ChannelSpec) -> tuple[list, str]:
    """[{name, qty, conf, recv, amt, date, po, fname}], 마지막 반영 시각"""
    store = OP._load_plan_store()
    rows, last_ingest = [], ""
    amap = _alias_map(sp) if sp.unit_basis else None
    for fname, f in store.get("files", {}).items():
        if (f.get("channel") or "") != sp.channel:
            continue
        last_ingest = max(last_ingest, f.get("ingested_at") or "")
        for r in f.get("rows", []):
            if not r.get("date"):
                continue
            if sp.exclude_item and sp.exclude_item(str(r.get("name") or ""), str(r.get("barcode") or "")):
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
            name = str(r.get("name") or "").strip()
            recv = None if r.get("recv") is None else int(r.get("recv"))
            base = {"date": r.get("date"), "po": str(r.get("po") or ""), "fname": fname,
                    "order_date": (r.get("order_date") or None)}   # 발주일 (일반 양식 '발주일자' 컬럼, 구버전 저장 행은 None)
            # 세트 상품 → 구성 단품 행 (unit_basis 채널). 수량·확정·입고는 세트당 개수를 곱하고,
            # 금액은 행 합계를 구성 비율로 나눠 붙인다 (합계 = 발주서 금액 그대로). BH 출고는 단품 SKU 로 나가므로 이렇게 맞춘다.
            comps = COMMON_BUNDLES.get(_label_of(name, amap)) if sp.unit_basis else None
            if comps:
                tot_k = sum(k for _, k in comps) or 1
                for cname, k in comps:
                    share = k / tot_k
                    rows.append({**base, "name": cname, "bundle_of": name, "bundle_k": k,
                                 "qty": qty * k, "order_qty": order_qty * k,
                                 "conf": (None if conf is None else conf * k),
                                 "recv": (None if recv is None else recv * k),
                                 "amt": (None if amt is None else int(round(amt * share))),
                                 "recv_amt": (None if recv_amt is None else int(round(recv_amt * share)))})
            else:
                rows.append({**base, "name": name, "qty": qty, "order_qty": order_qty, "conf": conf, "recv": recv,
                             "amt": amt, "recv_amt": recv_amt})
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


def _tx_detail_strict(token: str, tx_id) -> list:
    """거래 상세 items — strict 용. routers.channel._fetch_tx_detail 은 실패를 [] 로 삼켜 월합계 캐시가 과소로 굳을 수 있어,
    여기서는 HTTP 오류를 예외로 올린다. 429/5xx 는 1·2·4초 간격으로 3번까지 재시도."""
    import requests as _rq
    from routers.channel import BH_BASE, _bh_headers
    last = None
    for i in range(3):
        try:
            r = _rq.get(f"{BH_BASE}/v1/location-txs/{tx_id}", headers=_bh_headers(token), timeout=15)
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"BH 상세 {tx_id}: HTTP {r.status_code}")
                time.sleep(2 ** i)
                continue
            r.raise_for_status()
            return (r.json().get("item") or {}).get("items") or []
        except _rq.RequestException as e:
            last = e
            time.sleep(2 ** i)
    raise last or RuntimeError(f"BH 상세 {tx_id} 조회 실패")


def _channel_outbound(sp: ChannelSpec, token: str, from_date: str, to_date: str, strict: bool = False) -> list:
    """채널 거래처 출고 → [{date, created, sku, name, qty, partner, due}]
    strict=False(기본): BH 조회 실패는 [] (화면 요약이 죽지 않게).
    strict=True: 조회(목록·거래 상세) 실패를 그대로 올린다 — 월별 출고 캐시처럼 '실패 = 출고 0' 으로 저장되면 안 되는 호출용."""
    if not token:
        return []
    try:
        txs = U.fetch_transactions(token, "out", from_date, to_date)
    except Exception:
        if strict:
            raise
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
            if strict:
                items = _tx_detail_strict(token, tx.get("id"))   # 실패는 예외로 — 상세 누락분이 빠진 월합계가 캐시되면 3일간 과소 집계
            else:
                try:
                    from routers.channel import _fetch_tx_detail
                    items = _fetch_tx_detail(token, tx.get("id"))
                except Exception:
                    items = []
        for it in items or []:
            sku = str(it.get("sku") or (it.get("item") or {}).get("sku") or "").strip()
            name = it.get("name") or (it.get("item") or {}).get("name") or ""
            if sp.exclude_item and sp.exclude_item(str(name), sku):
                continue
            q = abs(int(it.get("quantity") or 0))
            if q:
                out.append({"date": d, "created": created, "sku": sku, "name": name, "qty": q,
                            "partner": partner, "due": due, "tx": tx.get("id")})
    return out


def _channel_cancels(sp: ChannelSpec, token: str, from_date: str, to_date: str, strict: bool = False) -> list:
    """출고 취소 입고 → [{date, sku, name, qty}]. BH 에 출고 취소가 거래처 없는 '입고'(메모 '# 컬리 김포취소')로 기록되는 경우용.
    sp.cancel_memo_kw 가 비면 []. 메모에 키워드와 '취소' 가 모두 있어야 한다."""
    kw = sp.cancel_memo_kw
    if not kw or not token:
        return []
    try:
        txs = U.fetch_transactions(token, "in", from_date, to_date)
    except Exception:
        if strict:
            raise
        return []
    out = []
    for tx in txs:
        memo = str(tx.get("memo") or "")
        if kw not in memo or "취소" not in memo:
            continue
        d = str(tx.get("transaction_time") or tx.get("created_at") or "")[:10]
        items = tx.get("items")
        if items is None:
            if strict:
                items = _tx_detail_strict(token, tx.get("id"))
            else:
                try:
                    from routers.channel import _fetch_tx_detail
                    items = _fetch_tx_detail(token, tx.get("id"))
                except Exception:
                    items = []
        for it in items or []:
            sku = str(it.get("sku") or (it.get("item") or {}).get("sku") or "").strip()
            name = it.get("name") or (it.get("item") or {}).get("name") or ""
            if sp.exclude_item and sp.exclude_item(str(name), sku):
                continue
            q = abs(int(it.get("quantity") or 0))
            if q:
                out.append({"date": d, "sku": sku, "name": name, "qty": q})
    return out


_BH_MONTHLY_TTL_DAYS = 3
_BH_MONTHLY_VER = 3   # 캐시 형식 버전 — 제외 품목·취소 상계(v2), 납기월 귀속(v3). 버전이 다르면 다시 조회


def _ship_due(sp: ChannelSpec, sh: dict, due_dates: Optional[list] = None) -> str:
    """BH 출고 1건의 귀속 납기일(YYYY-MM-DD). 'ship' 기준이면 출고일.
    'due' 기준이면 메모의 납기('# 컬리 배차 8월 4일')가 있으면 그 날, 없으면('# 컬리 배차')
    출고일 이후 7일 안의 가장 가까운 서류 납기일(거래명세서 입고일), 그것도 없으면 출고일.
    예: 8/26 '# 컬리 배차' 1,608개 → 9/1 거래명세서(1,608개) → 9/1."""
    d = str(sh.get("date") or "")[:10]
    if sp.bh_month_basis != "due":
        return d
    due = str(sh.get("due") or "")[:10]
    if due:
        return due
    if due_dates and d:
        import bisect
        i = bisect.bisect_left(due_dates, d)
        if i < len(due_dates):
            try:
                if (datetime.fromisoformat(due_dates[i]) - datetime.fromisoformat(d)).days <= 7:   # 컬리 배차·택배는 출고 후 1주 안에 입고
                    return due_dates[i]
            except ValueError:
                pass
    return d


def _ship_month(sp: ChannelSpec, sh: dict, due_dates: Optional[list] = None) -> str:
    """BH 출고 1건의 귀속 월 (_ship_due 의 월)."""
    return _ship_due(sp, sh, due_dates)[:7]


def _month_end(ym: str) -> str:
    """'2026-09' → '2026-09-30'"""
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = datetime(y + (m // 12), (m % 12) + 1, 1)
    return (nxt - timedelta(days=1)).strftime("%Y-%m-%d")


def _bh_monthly_shipped(sp: ChannelSpec, token: str, months: list, as_of: str,
                        current_ship: Optional[list] = None, current_from: str = "",
                        current_cancels: Optional[list] = None, due_dates: Optional[list] = None) -> dict:
    """채널 거래처(sp.partner_match) BH 출고의 월별 합계 → {ym: qty|None}. 출고일 월 기준, SKU 매핑 필터 없이 전체.
    (컬리 6·7·8월 1,974/7,893/8,343 = BH '마켓컬리' 출고 월합계 — 사용자 보고 수치와 일치 확인)
      - 이달(as_of[:7]): _build_summary 가 이미 조회한 raw 출고(current_ship, current_from~as_of)를 합산. current_from 이 월초보다
        늦으면 월초~current_from 전날만 추가 조회 (보통 ship_from ≤ 월초-15일이라 추가 조회 없음).
      - 지난 달: state 'bh_monthly' 캐시({ym: {qty, fetched_at}}). 없거나 fetched_at 이 3일 이상 지난 달만 한 번의 조회
        (빠진 달 최소 월초 ~ 최대 월말)로 채워 저장. token 없으면 캐시값 또는 None.
      - 어느 단계든 예외면 그 달은 None (전체 요약이 죽지 않게). BH 조회는 strict 로 호출해 실패를 구분한다 —
        실패를 '출고 0' 으로 캐시하면 3일간 경영 요약 추이가 0 으로 나가므로, 실패한 달은 None 으로 두고 캐시 저장도 건너뛴다."""
    cur_ym = as_of[:7]
    out: dict = {ym: None for ym in months}
    if not months:
        return out
    now = datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M")

    # ── 이달: 이미 조회한 출고 재사용 (token 없으면 조회 자체가 안 된 것이므로 0 이 아니라 None) ──
    if cur_ym in out and token:
        try:
            m_start = cur_ym + "-01"
            # 납기월 기준이면 지난 달 말 출고(이달 납기분)도 봐야 하므로 월초-15일부터
            need_from = ((datetime.fromisoformat(m_start) - timedelta(days=15)).strftime("%Y-%m-%d")
                         if sp.bh_month_basis == "due" else m_start)
            # 납기월 기준: 귀속 납기가 기준일 이후인 출고(선출고)는 아직 납품이 아니므로 이달 합계에서 제외 — 발주잔량으로 본다
            def key(sh):
                due = _ship_due(sp, sh, due_dates)
                if sp.bh_month_basis == "due" and due > as_of:
                    return ""
                return due[:7]
            if current_ship is not None:
                qty = sum(int(sh.get("qty") or 0) for sh in current_ship if key(sh) == cur_ym)
                if current_from and current_from > need_from:
                    gap_to = (datetime.fromisoformat(current_from) - timedelta(days=1)).strftime("%Y-%m-%d")
                    qty += sum(int(sh.get("qty") or 0) for sh in _channel_outbound(sp, token, need_from, gap_to, strict=True)
                               if key(sh) == cur_ym)
                out[cur_ym] = qty
            else:
                out[cur_ym] = sum(int(sh.get("qty") or 0) for sh in _channel_outbound(sp, token, need_from, as_of, strict=True)
                                  if key(sh) == cur_ym)
            if sp.cancel_memo_kw and out[cur_ym] is not None:
                cc = current_cancels if current_cancels is not None else _channel_cancels(sp, token, m_start, as_of, strict=True)
                out[cur_ym] = max(0, out[cur_ym] - sum(int(c["qty"]) for c in cc if str(c["date"])[:7] == cur_ym))
        except Exception:
            out[cur_ym] = None   # 조회 실패 = 미확인 (0 아님)

    # ── 지난 달: 캐시 → 빠진 달만 한 번에 조회 ──
    past = [ym for ym in months if ym < cur_ym]
    if not past:
        return out
    try:
        st = _load_state(sp)
        cache = dict(st.get("bh_monthly") or {})
        missing = []
        for ym in past:
            c = cache.get(ym) or {}
            fresh = False
            if c.get("qty") is not None and c.get("fetched_at") and c.get("v") == _BH_MONTHLY_VER:
                try:
                    fresh = (now - datetime.strptime(c["fetched_at"], "%Y-%m-%d %H:%M")).days < _BH_MONTHLY_TTL_DAYS
                except ValueError:
                    fresh = False
            if fresh or not token:
                out[ym] = (None if c.get("qty") is None else int(c["qty"]))
            else:
                missing.append(ym)
        if missing and token:
            f_from, f_to = min(missing) + "-01", _month_end(max(missing))
            if sp.bh_month_basis == "due":   # 첫 달 납기분이 그 전달 말에 출고된 경우까지
                f_from = (datetime.fromisoformat(f_from) - timedelta(days=15)).strftime("%Y-%m-%d")
            try:
                # BH 거래 목록 캐시는 완결된 달을 영구 보관한다 → 사후 수정(올리브영 6월 7,510 → 8,010 등)이 안 보이므로
                # 월별 합계를 새로 받는 시점에는 그 달 캐시를 지우고 다시 받는다
                try:
                    U.invalidate_bh_txlist_cache(f_from, f_to)
                except Exception:
                    pass
                shipped = _channel_outbound(sp, token, f_from, f_to, strict=True)
                cancels = _channel_cancels(sp, token, f_from, f_to, strict=True) if sp.cancel_memo_kw else []
            except Exception:
                # BH 조회 실패: 빠진 달은 None 그대로, 캐시도 저장하지 않는다 (다음 호출에서 다시 시도).
                # 만료된 옛 캐시값이 있으면 그거라도 보여준다 (0 으로 굳히는 것보다 낫다)
                for ym in missing:
                    c = cache.get(ym) or {}
                    out[ym] = (None if c.get("qty") is None else int(c["qty"]))
                return out
            agg: dict = defaultdict(int)
            for sh in shipped:
                agg[_ship_month(sp, sh, due_dates)] += int(sh.get("qty") or 0)
            for c in cancels:
                agg[str(c.get("date") or "")[:7]] -= int(c.get("qty") or 0)
            for ym in missing:
                out[ym] = max(0, int(agg.get(ym, 0)))
                cache[ym] = {"qty": out[ym], "fetched_at": now_s, "v": _BH_MONTHLY_VER}
            st = _load_state(sp)   # 저장 직전 다시 읽어 다른 필드(notes·prices 등) 덮어쓰기 방지
            st["bh_monthly"] = cache
            _save_json(sp.state_path, st)
    except Exception:
        for ym in past:
            out.setdefault(ym, None)
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
                vals.append(0.0 if pd.isna(v) else round(float(v), 1))   # 빈 칸(NaN)은 0 — NaN 이 남으면 보고문 round() 에서 터진다
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
    has_amt = sp.has_amt
    price_meta = None
    if sp.price_table:
        pr = _load_state(sp).get("prices") or _empty_prices(sp)
        filled, missing = _apply_prices(sp, rows, amap, pr)
        has_amt = has_amt or filled > 0
        price_meta = {"count": len(pr.get("items") or {}), "vat_included": bool(pr.get("vat_included", True)),
                      "source": pr.get("source", ""), "updated_at": pr.get("updated_at", ""),
                      "filled_rows": filled, "unpriced_rows": sum(1 for r in rows if r.get("amt") is None),
                      "missing_labels": missing}
    skumap = _sku_map(sp, rows)
    cfg = U.load_config()
    token = cfg.get("api_token") or ""
    month_start = month + "-01"
    month_label = f"{int(month[5:7])}월"

    def label(name):
        return _label_of(name, amap)

    # 공급가 환산 배수. 채널 화면이 부가세 포함 금액을 공급가로 쓰면(컬리) 나누지 않는다
    vat = 1.1 if (sp.amt_includes_vat and not sp.supply_includes_vat) else 1.0

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
    # 조회는 월초-15일부터 넓게 받고, 직전 납기 이전 출고는 메모 납기가 이달 첫 납기 이후인 것만 남긴다
    # (예: 8/27 '# 쿠팡 밀크런 1일' 4,629개 → 9/1 납기분. 직전 납기 8/28 다음날부터만 보면 빠진다)
    fetch_from = ship_from
    if past_month or sp.bh_month_basis == "due":
        fetch_from = min(ship_from, (datetime.fromisoformat(month_start) - timedelta(days=15)).strftime("%Y-%m-%d"))
    first_due = past_month[0]["date"] if past_month else month_start
    # 거래처 출고 전체 (월별 추이 BH 합계용). 조회 실패는 빈 목록이 아니라 '미확인'으로 구분해야 이달 값이 0 으로 나가지 않는다
    try:
        ship_raw_all = _channel_outbound(sp, token, fetch_from, as_of, strict=True)
        ship_fetch_ok = True
    except Exception:
        ship_raw_all, ship_fetch_ok = [], False
    ship_raw = [sh for sh in ship_raw_all if sh["date"] >= ship_from or (sh.get("due") and sh["due"] >= first_due)]
    ship = [sh for sh in ship_raw if sku_to_label.get(sh["sku"])]   # 채널 발주 품목으로 매핑되는 출고만
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
            # 미납 금액: 발주(또는 납품예정) 대비 미납 수량 × 공급단가. 올리브영은 발주 대비 납품예정 미달분도 미납
            it["unfilled_supply"] = int(round((it["unfilled"] + (it.get("short_vs_order", 0) if sp.qty_is_conf else 0)) * u))
            g_ship += it["shipped"]; g_deliv += it["delivered"]
            g_billed += it["billed_supply"]; g_unbilled += it["unbilled_supply"]
        # 그룹 품목에 없는 라벨의 출고(발주 외 품목)도 납품·미반영에 포함
        g["_extra_ship"] = {}
        for lb, q in shipped_lb.items():
            if lb not in {it["label"] for it in g["items"]} and q:
                g_ship += q; g_deliv += q
                g_unbilled += int(round(q * unit_supply.get(lb, 0)))
                g["_extra_ship"][lb] = q
        g["shipped"] = g_ship
        g["delivered"] = g_deliv
        g["billed_supply"] = g_billed
        g["unbilled_supply"] = g_unbilled
        g["unfilled"] = sum(it["unfilled"] for it in g["items"])
        g["unfilled_supply"] = sum(it["unfilled_supply"] for it in g["items"])

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
    # 예정 납기(기준일 이후)로 귀속된 선출고 — 채널은 아직 입고 전이므로 '납품 완료'에서 빼고 납품 대기 쪽에서만 본다
    early_qty = sum(v["qty"] for k, v in extra_by_due.items() if k)
    early_supply = int(round(sum(v["supply"] for k, v in extra_by_due.items() if k)))

    # 매입 미반영 = 라벨별로 '이달 출고 합계 − 채널 입고확정 합계'를 월 단위로 상계.
    # (한 출고 거래가 두 납기분을 합쳐 나가면 납기 귀속이 어긋나 한 납기는 초과·다른 납기는 미달로 보이는데,
    #  월 합계로 보면 이미 입고확정된 수량이므로 미반영이 아니다 — 올리브영 9/3 출고 = 9/5+9/8 납기분 사례)
    if sp.has_recv:
        net_ship: dict = defaultdict(int); net_recv: dict = defaultdict(int)
        for g in past_month:
            # 출고 기록이 전혀 없고 납기가 출고 집계 시작일 직후인 그룹(예: 9/1 납기 → 8월 말 출고)은 출고가 집계 범위 밖이라
            # 입고확정만 있다. 이걸 상계에 넣으면 다른 납기의 '입고 진행 중'을 부당하게 깎으므로 제외한다.
            if not g.get("shipped") and (datetime.fromisoformat(g["date"]) - datetime.fromisoformat(ship_from)).days <= 7:
                continue
            for it in g["items"]:
                net_ship[it["label"]] += it["shipped"]; net_recv[it["label"]] += (it["recv"] or 0)
            for lb, q in g.get("_extra_ship", {}).items():
                net_ship[lb] += q
        net_unbilled = {lb: max(0, q - net_recv.get(lb, 0)) for lb, q in net_ship.items()}
        month_unbilled = int(round(sum(q * unit_supply.get(lb, 0) for lb, q in net_unbilled.items())))
        for g in past_month:
            g["unbilled_supply"] = 0
            for it in g["items"]:
                it["unbilled_supply"] = 0
        # 상계 후 남는 미반영은 가장 늦은 납기 그룹에 표시
        if month_unbilled and past_month:
            past_month[-1]["unbilled_supply"] = month_unbilled
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
    pending_by_group = [{"md": g["md"], "date": g["date"], "qty": q}
                        for g in past_month for q in [sum(max(0, it["shipped"] - (it["recv"] or 0)) for it in g["items"])] if q]
    unbilled_by_group = [{"md": g["md"], "date": g["date"],
                          "qty": (sum(net_unbilled.values()) if sp.has_recv else sum(max(0, it["shipped"] - (it["recv"] or 0)) for it in g["items"])),
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
        elif sp.qty_is_conf and r["amt"] and r["qty"]:
            # 행 단가(원가금액 ÷ 납품예정수량) × 납품수량 — 품목별 가중평균 단가를 쓰면 납품확인서 합계와 어긋난다
            recv_supply += r["recv"] * (r["amt"] / r["qty"]) / vat
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

    # 월별 추이: BH 거래처 출고 월합계(이달은 위 ship_raw 재사용, 지난 달은 state 캐시)
    cancels_cur: Optional[list] = []
    if sp.cancel_memo_kw:
        try:
            cancels_cur = _channel_cancels(sp, token, month_start, as_of, strict=True)
        except Exception:
            cancels_cur = None   # 취소 조회 실패 → 월별 합계 쪽에서 다시 시도(실패면 이달 None)
    bh_monthly = _bh_monthly_shipped(sp, token, _prev_months(month), as_of,
                                     current_ship=(ship_raw_all if ship_fetch_ok else None), current_from=fetch_from,
                                     current_cancels=cancels_cur, due_dates=all_dates)
    # 입고확정 없는 단가표 채널(컬리): 이달 출고일 기준 공급가를 BH SKU(바코드) → 단가표로 직접 계산.
    # 발주 품목 매핑(ship_by_date)은 이달 거래명세서에 없는 품목을 빼 버려 수량(bh_shipped)과 모집단이 어긋난다
    month_ship_supply = None; month_ship_unpriced = 0
    if sp.price_table and not sp.has_recv:
        _pr = _load_state(sp).get("prices") or {}
        _mult = 1.0 if _pr.get("vat_included", True) else 1.1
        _bc_unit = {str(v.get("barcode") or "").strip(): int(v.get("unit") or 0)
                    for v in (_pr.get("items") or {}).values() if v.get("barcode") and v.get("unit")}
        _tot = 0.0
        for sh in ship_raw_all:
            _due = _ship_due(sp, sh, all_dates)
            if _due[:7] != month or (sp.bh_month_basis == "due" and _due > as_of):   # 수량(bh_shipped)과 같은 귀속 기준, 선출고 제외
                continue
            u = _bc_unit.get(sh["sku"])
            if u:
                _tot += sh["qty"] * u * _mult
            else:
                month_ship_unpriced += sh["qty"]
        for c in (cancels_cur or []):
            if str(c["date"])[:7] == month:
                _tot -= c["qty"] * _bc_unit.get(c["sku"], 0) * _mult
        month_ship_supply = int(round(max(0.0, _tot))) if ship_fetch_ok else None

    core = sorted(pend_by_label.items(), key=lambda kv: -kv[1]["qty"])[:2]
    summary = {
        "channel": sp.key, "channel_name": sp.display, "has_conf": sp.has_conf, "conf_word": sp.conf_word,
        "amt_includes_vat": (sp.amt_includes_vat and not sp.supply_includes_vat), "has_amt": has_amt, "has_recv": sp.has_recv, "prices": price_meta,
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
            "early_total": early_qty, "early_supply": early_supply,   # 예정 납기 선출고 (납품 완료에 포함된 채널 미입고분)
            "month_ship_supply": month_ship_supply, "month_ship_unpriced": month_ship_unpriced,   # 이달 출고일 기준 공급가(단가표 채널)
            "cancel_qty": sum(int(c["qty"]) for c in (cancels_cur or []) if str(c["date"])[:7] == month),   # 이달 출고 취소 입고(상계분)
            "bh_fetch_ok": ship_fetch_ok,   # False 면 이달 BH 출고 조회 실패(경영 요약 이달 값 미확인)
            "bh_ship_total": ship_total, "bh_ship_supply": ship_supply, "ship_from": ship_from,
            "delivered_by_label": [{"label": k, "qty": v} for k, v in sorted(delivered_by_label.items(), key=lambda kv: -kv[1])],
            "recv_supply": recv_supply,
            "unbilled_supply": unbilled_supply,
            "billed_supply": billed_supply,
            "unbilled_by_group": unbilled_by_group,
            "pending_by_group": pending_by_group,   # 납기별 출고 완료·채널 입고등록 대기 수량 (상계 전)
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
        "history": _build_history(sp, rows, month, bh_monthly=bh_monthly),
        "history_order_basis": ("order_date" if sp.history_by_order_date else "due"),   # 월별 추이 발주 수량 귀속 기준
        "unit_basis": sp.unit_basis,   # 세트 상품을 단품으로 풀어 집계했는지 (쿠팡)
        "exec_metric": sp.exec_metric,
        "exec_metric_word": sp.exec_metric_word or ("납품" if sp.exec_metric == "recv" else "발주"),
    }
    if not sp.has_recv:
        # 채널 입고확정 데이터가 없는 채널(컬리): 별도 입고확정 절차가 없어 출고분 = 입고 확정으로 본다
        dvx = summary.get("delivery") or {}
        for g in dvx.get("groups") or []:
            g["recv"] = int(g.get("delivered") or 0)
            g["billed_supply"] = int(g.get("billed_supply") or 0) + int(g.get("unbilled_supply") or 0)
            g["unbilled_supply"] = 0
            for it in g.get("items") or []:
                it["recv"] = int(it.get("delivered") or 0)
                it["billed_supply"] = int(it.get("billed_supply") or 0) + int(it.get("unbilled_supply") or 0)
                it["unbilled_supply"] = 0
        dvx["recv_month"] = int(dvx.get("ship_total") or 0)
        # 금액도 출고분 전체를 매입 확정으로 (단가표로 계산한 채널)
        dvx["billed_supply"] = int(dvx.get("ship_supply") or 0)
        dvx["recv_supply"] = int(dvx.get("ship_supply") or 0)
        dvx["unbilled_supply"] = 0
        dvx["unbilled_by_group"] = []
    summary["text"] = _render_text(sp, summary)
    summary["text_detail"] = _render_text_detail(sp, summary)
    summary["text_exec"] = _render_text_exec(sp, summary)
    # 후처리(금액·입고 문구 정리)는 상세 양식에만. 간결 양식은 렌더러가 has_amt / has_recv 를 직접 반영한다
    if not has_amt:
        summary["text_detail"] = _strip_money(summary["text_detail"])
    if not sp.has_recv:
        summary["text_detail"] = _strip_recv_lines(summary["text_detail"])
        if has_amt:
            # 입고확정 절차가 없는 채널: '매입 확정'은 출고분 기준임을 문구에 드러낸다
            summary["text_detail"] = (summary["text_detail"].replace(" — 전량 매입 확정", " — 출고분 전량 매입 반영(출고 = 입고 확정)")
                                      .replace("(매입 확정)", "(출고 완료 = 입고 확정)"))
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
            "forecast_weekly": fcv, "forecast_next": (fcv[0] if fcv and fcv[0] == fcv[0] else None),   # NaN 방어 (NaN != NaN)
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
        L.append(f"* 발주 {_n(o['order_qty'])}개 중 {sp.conf_word} {_n(o['qty'])}개 — 미납 {_n(o['short_vs_order'])}개는 납품예정에서 제외")
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
    return f"{round(v)}%" if abs(v - round(v)) < 0.05 else f"{v:.1f}%"


def _pct_txt(tp) -> str:
    """증감을 (+80%) / (-6%) 처럼 — 사람이 손으로 쓰던 표기"""
    if tp is None:
        return ""
    return f"({'+' if tp > 0 else ''}{tp:g}%)"


def _bar(ratio: float, width: int = 10) -> str:
    """0~1 비율 → ▰▰▰▱▱ 막대. 슬랙 본문은 고정폭이 아니라서 같은 글자만 써 칸 수로 비교되게 한다"""
    ratio = max(0.0, min(1.0, ratio or 0.0))
    filled = width if ratio >= 1 else min(width - 1, int(ratio * width))   # 100% 만 가득 — 96% 가 만칸으로 보이지 않게
    return "▰" * filled + "▱" * (width - filled)


def _render_text(sp: ChannelSpec, s: dict) -> str:
    """슬랙 본문용 보고문 — 담당자가 직접 쓰던 양식(인사 1줄 · ■ 요약 · * 항목 · 마무리 1줄)을 그대로 따른다.
    이모지·특수 기호 없이, 요약 3줄(납품 / 남은 발주 / 진행률)과 채널 판매(막대 포함)만. 상세는 '상세' 양식으로."""
    o = s["orders"]; dv = s["delivery"]; t = s.get("trend") or {}
    ch = sp.display; ml = s["month_label"]
    has_amt = s.get("has_amt", True)   # 금액 없는 채널(단가표 없는 컬리)은 수량만

    def amt_qty(supply, qty):
        return f"{_won(supply)} ({_n(qty)}개)" if has_amt else f"{_n(qty)}개"

    L = [f"[{ch}] 안녕하세요, {s['as_of_md']} 기준 {ch} 발주·납품 현황 공유드립니다.", "", "■ 요약"]

    # 1) 이번 달 납품 (예정 납기 선출고분은 아직 채널 미입고 → 남은 발주 쪽)
    done_q = done_s = 0
    if s["bh_connected"]:
        done_q = dv["ship_total"] - (dv.get("early_total") or 0)
        done_s = (dv.get("ship_supply") or 0) - (dv.get("early_supply") or 0)
        if done_q > 0:
            line = f"* {ml} 납품 {amt_qty(done_s, done_q)}"
            pend_s = (dv.get("unbilled_supply") or 0) - (dv.get("early_supply") or 0)
            pend_q = sum(u["qty"] for u in (dv.get("unbilled_by_group") or [])) - (dv.get("early_total") or 0)   # 금액과 같은 모집단
            if sp.has_recv and pend_s > 0 and pend_q > 0:
                line += f" — 이 중 {sp.recv_word} 대기 {amt_qty(pend_s, pend_q)}"
            L.append(line)
        else:
            L.append(f"* {ml} 납품 실적 없음")

    # 2) 남은 발주
    if o["groups"]:
        rng = f"{o['groups'][0]['md']}~{o['groups'][-1]['md']}" if len(o["groups"]) > 1 else o["groups"][0]["md"]
        by_lb: dict = defaultdict(int)
        for g in o["groups"]:
            for it in g["items"]:
                by_lb[it["label"]] += it.get("qty") or 0
        top = _top([{"label": k, "qty": v} for k, v in sorted(by_lb.items(), key=lambda kv: -kv[1])], "qty", 1)
        L.append(f"* 남은 발주 {amt_qty(o['supply'], o['qty'])} · {rng} 납기 · {o['po_count']}건" + (f" — {top}" if top else ""))
    else:
        L.append("* 남은 발주 없음")

    # 3) 이번 달 진행률 — 납품 완료 ÷ (납품 완료 + 남은 발주). 금액이 없는 채널은 수량으로
    if s["bh_connected"] and done_q > 0:
        done, rest = (done_s, o["supply"] or 0) if has_amt else (done_q, o["qty"] or 0)
        total = done + rest
        if total > 0:
            L.append(f"* {ml} 진행률 {_pct1(done, total)} {_bar(done / total)}")

    # 단가표가 일부만 등록된 채널(컬리): 금액이 일부 품목만 합산된 것임을 표시
    pm_meta = s.get("prices") or {}
    if has_amt and pm_meta.get("unpriced_rows"):
        miss = pm_meta.get("missing_labels") or []
        L.append(f"* 단가 미등록 {len(miss) or pm_meta['unpriced_rows']}종은 금액에서 제외" + (f" ({' · '.join(miss[:3])})" if miss else ""))

    # ── 채널 판매: 품목별 한 줄, 끝에 재고 소진일 막대 (1칸 = 5일)
    if (t.get("has_rocket") or t.get("has_forecast")) and t.get("rows"):
        per = t.get("period_label", "")

        def stock_tail(r):
            dos = r.get("days_of_supply")
            if dos is None:
                return f" · 재고 {_n(r.get('stock_last'))}개" if r.get("stock_last") is not None else ""
            return f" · 재고 {dos:g}일분" + (" — 부족" if dos < 7 else "") + f"  {_bar(dos / 50)}"

        if t.get("mode") == "monthly":
            pv = t.get("total_sales_prev"); el = t.get("elapsed_days") or 0; pm = t.get("prev") or ""
            head = f"■ {ch} 판매 ({per}"
            if pv and el and t.get("total_sales") and len(pm) >= 7:
                import calendar
                pdays = calendar.monthrange(int(pm[:4]), int(pm[5:7]))[1]
                pct = round(((t['total_sales'] / el) / (pv / pdays) - 1) * 100)
                head += f" · 일평균 전월 대비 {'+' if pct > 0 else ''}{pct}%"
            L += ["", head + ")"]
            L.append(f"* 합계 {_n(t.get('total_sales'))}개" + (f" · 소비자가 {_won(t.get('total_retail'))}" if has_amt else "") + (f" (전월 {_n(pv)}개)" if pv else ""))
            for r in [x for x in t["rows"] if x["sales_7d"] or x["stock_last"]][:4]:
                L.append(f"* {r['label']} — {_n(r['sales_7d'])}개 (매장 {_n(r['sales_store'])} · 온라인 {_n(r['sales_online'])})" + stock_tail(r))
        else:
            L += ["", f"■ {ch} 판매 ({per} 일평균 · 전주 대비 · {ch} 재고)"]
            for r in [x for x in t["rows"] if x["avg_7d"] or x.get("forecast_next")][:3]:
                if r["avg_7d"]:
                    L.append(f"* {r['label']} — {_n(round(r['avg_7d']))}개/일 {_pct_txt(r['trend_pct'])}".rstrip() + stock_tail(r))
                elif r.get("forecast_next") is not None:
                    L.append(f"* {r['label']} — 다음주 예측 {_n(round(r['forecast_next']))}개")
        if any(r.get("days_of_supply") is not None for r in t["rows"][:4]):
            L.append("  (재고 막대 1칸 = 5일, 가득이면 50일 이상)")

    # ── 특이사항 메모 (있을 때만)
    notes = [ln.strip().lstrip("*-•■ ").strip() for ln in (s.get("notes") or "").splitlines() if ln.strip()]
    if notes:
        L += ["", "■ 특이사항"] + [f"* {ln}" for ln in notes]

    L += ["", "참고로만 공유드립니다. 감사합니다."]
    return "\n".join(L)



# ─────────────────────────────────────────────────────────────────
# 월별 추이 · 경영 요약(3채널 묶음) 보고문
# ─────────────────────────────────────────────────────────────────
def _prev_months(month: str, n: int = 4) -> list:
    """'2026-09', 4 → ['2026-06','2026-07','2026-08','2026-09']"""
    y, m = int(month[:4]), int(month[5:7])
    out = []
    for k in range(n - 1, -1, -1):
        yy, mm = y, m - k
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        out.append(f"{yy:04d}-{mm:02d}")
    return out


def _build_history(sp: ChannelSpec, rows: list, month: str, n: int = 4, bh_monthly: Optional[dict] = None) -> list:
    """최근 n개월 월별 발주·납품 수량 (납기일 기준). 저장소에 없는 달은 화면에서 입력한 수기 값(state.monthly)으로 보정.
    bh_monthly({ym: BH 거래처 출고 월합계}) 가 있으면 각 행에 bh_shipped 로 붙이고, 채널 입고확정 데이터가 없는 채널(컬리)은
    출고 = 입고 확정이므로 그 값을 auto_delivered 로 쓴다. has_recv 채널은 저장소 입고 합계가 auto_delivered, bh_shipped 는 참고값.
    [{ym, label, order_qty, delivered, auto_order_qty, auto_delivered, bh_shipped, override}]"""
    agg: dict = defaultdict(lambda: {"order_qty": 0, "recv": 0, "recv_n": 0})
    for r in rows:
        ym = (r["date"] or "")[:7]
        if not ym:
            continue
        # 발주 수량의 귀속 월: 기본은 납기월. history_by_order_date 채널(올리브영)은 발주일 월 (없는 행은 납기월로 폴백)
        oym = ((r.get("order_date") or "")[:7] if sp.history_by_order_date else "") or ym
        agg[oym]["order_qty"] += int(r.get("order_qty", r["qty"]) or 0)
        a = agg[ym]
        if r.get("recv") is not None:
            a["recv"] += int(r["recv"]); a["recv_n"] += 1
    ov = _load_state(sp).get("monthly") or {}
    bh_monthly = bh_monthly or {}
    out = []
    for ym in _prev_months(month, n):
        a = agg.get(ym) or {"order_qty": 0, "recv": 0, "recv_n": 0}
        o = ov.get(ym) or {}
        bh_q = bh_monthly.get(ym)
        bh_q = None if bh_q is None else int(bh_q)
        if sp.has_recv:
            auto_recv = a["recv"] if a["recv_n"] else None
        else:
            auto_recv = bh_q   # 입고확정 절차가 없는 채널: BH 출고 = 납품
        row = {"ym": ym, "label": f"{int(ym[5:7])}월",
               "auto_order_qty": a["order_qty"] or None, "auto_delivered": auto_recv, "bh_shipped": bh_q,
               "order_qty": a["order_qty"] or None, "delivered": auto_recv, "override": False}
        for k in ("order_qty", "delivered"):
            if o.get(k) is not None:
                row[k] = int(o[k]); row["override"] = True
        out.append(row)
    return out


def _render_text_exec(sp: ChannelSpec, s: dict, idx: Optional[int] = None) -> str:
    """경영 요약 한 채널 블록 —
        1. 쿠팡 로켓배송 — 납품 6월 32,069 → 7월 96,035 → 8월 68,661개 → 9월(78,719)
        9월 납품 3억 5,791만원 (78,719개)
        미납품 발주잔량 1,489만원 (3,008개 · 9/22 납기)"""
    o = s["orders"]; dv = s["delivery"]
    hist = s.get("history") or []
    ml = s["month_label"]; month = s["month"]
    has_amt = s.get("has_amt", True)
    name = sp.exec_name or sp.display
    metric_word = s.get("exec_metric_word") or sp.exec_metric_word or ("납품" if sp.exec_metric == "recv" else "발주")

    # 이달 납품(진행중): 채널 입고확정이 있으면 그 수량(이달 납기 전체), 없으면 출고분
    if sp.has_recv:
        cur_deliv = max(int(dv.get("recv_month") or 0), sum(h["delivered"] or 0 for h in hist if h["ym"] == month))
        cur_supply = int(dv.get("recv_supply") or 0)
    else:
        # 입고확정 없는 채널(컬리): 지난 달 추이(bh_shipped = BH 출고일 월합계)와 같은 '출고일 월' 기준으로 이달을 잡는다.
        # dv.ship_total 은 납기 귀속 기준이라 지난 달 말 출고(예: 8/26~31 출고 → 9월 납기)가 이달로 넘어와
        # '8월 8,343 → 9월(9,573)' 처럼 두 달에 중복 집계되고, 다음 달이 되면 9월 값이 다시 내려앉는다.
        cur_row = next((h for h in hist if h["ym"] == month), None)
        bh_cur = cur_row.get("bh_shipped") if cur_row else None
        cur_deliv = int(bh_cur) if bh_cur is not None else int(dv.get("ship_total") or 0)
        # 금액도 같은 기준: 이달 출고일자 출고의 공급가 합계(ship_by_date). 없으면 납기 귀속 금액(ship_supply)으로 폴백.
        # (ship_by_date 는 발주 품목으로 매핑된 출고만이라 bh_shipped(거래처 전체)와 미매핑 SKU 만큼 차이 날 수 있음)
        month_ship = [x for x in (dv.get("ship_by_date") or []) if str(x.get("date") or "")[:7] == month]
        cur_supply = (int(sum(int(x.get("supply") or 0) for x in month_ship)) if month_ship and bh_cur is not None
                      else int(dv.get("ship_supply") or 0))
        if dv.get("month_ship_supply") is not None and bh_cur is not None:
            cur_supply = int(dv["month_ship_supply"])   # BH SKU → 단가표 (수량과 같은 모집단)
    # 수기 보정이 이달에도 있으면 그 값 우선
    for h in hist:
        if h["ym"] == month and h["override"] and h["delivered"] is not None:
            cur_deliv = h["delivered"]

    past = [h for h in hist if h["ym"] != month]
    vals = [(h["label"], (h["delivered"] if sp.exec_metric == "recv" else h["order_qty"])) for h in past]
    parts = [f"{lb} {_n(v)}" for lb, v in vals if v is not None]
    if parts:
        parts[-1] += "개"   # 자료 없는 달을 건너뛴 뒤 마지막 값에 단위
    prev_lv = next(((lb, v) for lb, v in reversed(vals) if v is not None), None)   # 값이 있는 가장 최근 지난 달
    # 첫 줄 이달 괄호는 지난 달과 같은 지표로: 발주 채널(올리브영)은 이달 납기 발주 수량, 납품 채널은 이달 납품
    cur_row = next((h for h in hist if h["ym"] == month), None)
    cur_metric = int((cur_row or {}).get("order_qty") or 0) if sp.exec_metric == "order" else cur_deliv
    cur = f"{ml}({_n(cur_metric)}"
    prev_v = prev_lv[1] if prev_lv else None
    prev_label = prev_lv[0] if prev_lv else ""
    pend_q = int(o.get("qty") or 0)
    early_q = 0
    shipped_early = min(pend_q, int((s.get("fulfil") or {}).get("shipped_early") or 0))
    if not sp.has_recv and sp.bh_month_basis != "due":
        # 이달 값이 출고일 기준이면 예정 납기분 선출고는 이미 이달 값에 들어 있으므로 잔량에서 뺀다 (이중 계산 방지)
        early_q = shipped_early
        pend_q = pend_q - early_q
    if prev_v and cur_metric < prev_v:
        if sp.exec_metric == "recv" and cur_metric + pend_q >= prev_v:
            cur += f" / 진행중, {prev_label} 실적 초과예상"
        elif (prev_v - cur_metric) / prev_v <= 0.05:
            cur += f" / 진행중, {prev_label} 실적 근접"
        else:
            cur += " / 진행중"
    cur += ")"
    head = f"{name} — {metric_word} " + " → ".join(parts + [cur]) if parts else f"{name} — {ml} {metric_word} {cur}"
    L = [f"{idx}. {head}" if idx else head, ""]

    if cur_deliv:
        L.append(f"{ml} 납품 " + (f"{_won(cur_supply)} ({_n(cur_deliv)}개)" if has_amt and cur_supply else f"{_n(cur_deliv)}개"))
    # 잔량 그룹이 있어도 납품예정 수량이 0이면(올리브영: 발주 30 중 납품예정 0) '없음'으로.
    # 출고 = 납품인 채널(컬리)은 선출고분을 잔량에서 뺀다 — 이미 이달 납품에 들어 있다
    left_groups = [g for g in o["groups"] if (g.get("qty") or 0) - (0 if early_q == 0 else (g.get("shipped_early") or 0)) > 0]
    tot_q = int(o.get("qty") or 0)
    if left_groups and pend_q > 0:
        due = "·".join(g["md"] for g in left_groups)
        left_supply = int(round((o.get("supply") or 0) * pend_q / tot_q)) if tot_q else 0
        # 납기 전인데 이미 출고한 물량은 잔량에 두되 '선출고 완료·입고 대기' 로 표시
        early_note = f" · 선출고 {_n(shipped_early)}개 입고 대기" if (shipped_early and early_q == 0) else ""
        L.append("미납품 발주잔량 " + (f"{_won(left_supply)} ({_n(pend_q)}개 · {due} 납기{early_note})" if has_amt and left_supply
                                  else f"{_n(pend_q)}개 ({due} 납기{early_note})"))
    elif early_q:
        due = "·".join(g["md"] for g in o["groups"] if g.get("shipped_early"))
        L.append(f"미납품 발주잔량 없음 ({due} 납기분 {_n(early_q)}개 선출고 완료)")
    else:
        L.append("미납품 발주잔량 없음")
    return "\n".join(L)


def build_exec_text(specs: list, as_of: str, month: str = "", extra_blocks: Optional[list] = None) -> dict:
    """3채널(쿠팡·올리브영·컬리) 경영 요약을 한 번에. 채널별 summary 를 만들어 블록을 번호 붙여 이어 붙인다.
    extra_blocks: 발주 엔진을 쓰지 않는 채널(네이버 등)의 블록 생성기 목록. 각 callable(as_of, month, idx) →
    {key, name, text, history, error} 를 specs 뒤에 번호를 이어 붙인다. 실패는 '집계 실패' 블록."""
    month = (month or as_of[:7])[:7]
    blocks, channels = [], []
    for i, sp in enumerate(specs, 1):
        try:
            sm = _build_summary(sp, as_of, month)
            txt = _render_text_exec(sp, sm, idx=i)
            channels.append({"key": sp.key, "name": sp.exec_name or sp.display, "text": txt, "history": sm.get("history"), "error": ""})
        except Exception as e:  # 한 채널 실패가 전체를 막지 않게
            txt = f"{i}. {sp.exec_name or sp.display} — 집계 실패 ({str(e)[:80]})"
            channels.append({"key": sp.key, "name": sp.exec_name or sp.display, "text": txt, "history": None, "error": str(e)[:200]})
        blocks.append(txt)
    for k, fn in enumerate(extra_blocks or [], len(specs) + 1):
        key = getattr(fn, "exec_key", f"extra{k}")
        name = getattr(fn, "exec_name", key)
        try:
            blk = dict(fn(as_of, month, k) or {})
            blk.setdefault("key", key); blk.setdefault("name", name)
            blk.setdefault("history", None); blk.setdefault("error", "")
            if not blk.get("text"):
                blk["text"] = f"{k}. {blk['name']} — 집계 실패 (보고문 없음)"
        except Exception as e:
            blk = {"key": key, "name": name, "text": f"{k}. {name} — 집계 실패 ({str(e)[:80]})", "history": None, "error": str(e)[:200]}
        channels.append(blk)
        blocks.append(blk["text"])
    return {"as_of": as_of, "as_of_md": _md(as_of), "month": month, "text": "\n\n".join(blocks), "channels": channels}


# ─────────────────────────────────────────────────────────────────
# 라우터
# ─────────────────────────────────────────────────────────────────
class MonthlyIn(BaseModel):
    monthly: dict   # {YYYY-MM: {order_qty?: int|None, delivered?: int|None}} — None/빈 dict 이면 보정 삭제


class NotesIn(BaseModel):
    notes: str


class IncomingIn(BaseModel):
    label: str
    qty: int
    date: str
    memo: str = ""
    id: Optional[str] = None


class PricesIn(BaseModel):
    items: dict = {}                 # {label: unit(int) | {unit, ...} | None(삭제)}
    vat_included: Optional[bool] = None


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

    @router.get("/sales-trend")
    def get_sales_trend(as_of: str = ""):
        """SKU 출고 추이 화면용 — 업로드된 판매/재고/예측 리포트를 라벨 단위로 정리해 내려준다.
        labels: 발주 캘린더 raw 상품명 → 라벨 (프론트에서 발주 SKU 행과 판매 행을 잇는 키)."""
        as_of = (as_of or date.today().isoformat())[:10]
        up = _load_uploads(sp)
        amap = _alias_map(sp)
        t = _build_trend(sp, up, amap, as_of, as_of[:7])
        labels = {}
        store = OP._load_plan_store()
        for f in store.get("files", {}).values():
            if (f.get("channel") or "") != sp.channel:
                continue
            for r in f.get("rows", []):
                n = str(r.get("name") or "").strip()
                if n and n not in labels:
                    labels[n] = _label_of(n, amap)
        keep = ("label", "names", "sales_series", "stock_series", "sales_7d", "avg_7d", "avg_prev_7d", "trend_pct",
                "stock_last", "stock_center", "stock_store", "days_of_supply", "forecast_weekly", "forecast_next",
                "sales_store", "sales_online", "amount_series", "promo")
        rows = [{k: r.get(k) for k in keep if k in r} for r in t.get("rows", [])]
        return {"channel": sp.channel, "mode": t.get("mode"), "days": t.get("days", []), "months": t.get("months", []),
                "weeks": t.get("weeks", []), "period_label": t.get("period_label", ""),
                "latest": t.get("latest"), "elapsed_days": t.get("elapsed_days"),
                "rows": rows, "labels": labels, "uploads": _upload_meta(sp, up)}

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

    @router.get("/prices")
    def get_prices():
        st = _load_state(sp)
        return {"prices": st.get("prices") or _empty_prices(sp)}

    @router.put("/prices")
    def put_prices(body: PricesIn):
        """단가표 수정. items 값이 정수면 unit만 바꾸고 원상품명·바코드 등 메타는 유지. None이면 삭제."""
        st = _load_state(sp)
        pr = st.get("prices") or _empty_prices(sp)
        items = dict(pr.get("items") or {})
        for lb, v in (body.items or {}).items():
            lb = str(lb).strip()
            if not lb:
                continue
            if v is None:
                items.pop(lb, None)
                continue
            cur = dict(items.get(lb) or {})
            if isinstance(v, dict):
                cur.update({k: v[k] for k in ("unit", "event", "name", "barcode", "code", "box") if k in v})
            else:
                cur["unit"] = v
            cur["unit"] = _to_int(cur.get("unit"))
            items[lb] = cur
        pr["items"] = items
        if body.vat_included is not None:
            pr["vat_included"] = bool(body.vat_included)
        pr["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        st["prices"] = pr
        _save_json(sp.state_path, st)
        return {"prices": pr}

    @router.post("/prices/upload")
    async def upload_prices(files: list[UploadFile] = File(...)):
        """컬리 '상품공급정보 다운로드' xlsx → 라벨별 매입단가(일반단가). 같은 라벨이 여러 행이면 마지막 행."""
        st = _load_state(sp)
        pr = st.get("prices") or _empty_prices(sp)
        amap = _alias_map(sp)
        items = dict(pr.get("items") or {})
        added, errors, skipped, src = 0, [], [], []
        for f in files:
            fname = f.filename or "file"
            data = await f.read()
            try:
                df = pd.read_excel(io.BytesIO(data), header=None)
                for it in _parse_supply_info_xlsx(df):
                    lb = _label_of(it["name"], amap)
                    if it["unit"] <= 0:
                        # 증정품 등 단가 0 행: 같은 라벨의 정상 단가를 덮어쓰지 않는다
                        skipped.append(f"{lb} (단가 0)")
                        if (items.get(lb) or {}).get("unit"):
                            continue
                    items[lb] = it
                    added += 1
                src.append(fname)
            except Exception as e:
                errors.append(f"{fname}: {e}")
        pr["items"] = items
        if src:
            pr["source"] = ", ".join(src)
            pr["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        st["prices"] = pr
        _save_json(sp.state_path, st)
        return {"added": added, "skipped": skipped, "errors": errors, "prices": pr}

    @router.post("/prices/clear")
    def clear_prices():
        st = _load_state(sp)
        st["prices"] = _empty_prices(sp)
        _save_json(sp.state_path, st)
        return {"prices": st["prices"]}

    @router.get("/monthly")
    def get_monthly():
        return {"monthly": _load_state(sp).get("monthly") or {}}

    @router.put("/monthly")
    def put_monthly(body: MonthlyIn):
        """월별 추이 수기 보정 저장. 값 None 이면 해당 항목 삭제, 항목이 모두 비면 그 달 삭제."""
        st = _load_state(sp)
        cur = dict(st.get("monthly") or {})
        for ym, v in (body.monthly or {}).items():
            ym = str(ym)[:7]
            if not re.match(r"^\d{4}-\d{2}$", ym):
                raise HTTPException(400, f"월 형식 오류: {ym}")
            if not v:
                cur.pop(ym, None); continue
            row = dict(cur.get(ym) or {})
            for k in ("order_qty", "delivered"):
                if k in v:
                    if v[k] is None or v[k] == "":
                        row.pop(k, None)
                    else:
                        row[k] = _to_int(v[k])
            if row:
                cur[ym] = row
            else:
                cur.pop(ym, None)
        st["monthly"] = cur
        _save_json(sp.state_path, st)
        return {"monthly": cur}

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
