# -*- coding: utf-8 -*-
"""슬랙 #물류_출고 출고요청 → 아워박스 발주등록 · BoxHero 출고등록 · 물류센터 메일 발송 자동화.

현재 수작업 흐름(실측)
  1) 요청자가 '아워박스 제품 출고양식(통합)' 엑셀(주문내역 시트)을 슬랙에 첨부
  2) 담당자가 OMS에 엑셀일괄등록(채널 선택: 샘플(정상소비기한)·쿠팡·올리브영 …)
  3) BoxHero에 거래처별 출고 1건 등록
  4) 호법센터(ccg1@naver.com)에 "[호법택배] [시나몬랩] M/D(요일) … 출고 요청" 메일 (거래명세서·부착문서 첨부)
  5) 슬랙 메시지에 ✅

이 라우터
  POST /prepare  슬랙 메시지 1건 + 첨부 엑셀을 읽어 주문/품목을 뽑고, 아워박스 상품·BH SKU·채널·거래처를
                 자동 매칭한 '검토용 초안'(메일 초안 포함)을 돌려준다. 쓰기 없음.
  POST /run      검토·수정한 초안으로 실제 실행 (단계별 ON/OFF): 아워박스 발주등록(API /api/oms/order/send)
                 → BoxHero 출고등록 → Gmail 발송(첨부) → 슬랙 ✅ + 스레드 답글. 단계별 완료는 slack_dispatch_log에
                 기록해 재실행 시 끝난 단계는 건너뛴다.
  POST /attach   메일에 붙일 파일(거래명세서 등) 업로드 (ts별 스테이징 폴더)
  GET  /refs     아워박스 판매채널·BH 거래처/위치·Gmail 상태·설정
  GET/POST /config  메일 수신자·서명·채널 규칙 등 (config.json)
"""
import sys
import os
import re
import io
import json
import html
import time
import threading
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import utils_core as U
import receiving_db as db
import state
from routers import ob_orders

router = APIRouter()

OURBOX_API = "https://api.ourbox.co.kr"
ATTACH_DIR = os.path.join(_ROOT, "backend", "data", "dispatch_attach")
WEEKDAY = "월화수목금토일"

with db._conn() as _c:
    _c.execute("""CREATE TABLE IF NOT EXISTS slack_dispatch_log (
        ts TEXT PRIMARY KEY,
        channel_id TEXT,
        title TEXT,
        ob_json TEXT,
        bh_tx_id TEXT,
        bh_json TEXT,
        mail_id TEXT,
        mail_json TEXT,
        oms_json TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')))""")
    # 실행 때 사람이 확정한 '요청 상품명 → 아워박스 코드 / BH SKU' 를 기억해 다음 매칭에 우선 적용
    _c.execute("""CREATE TABLE IF NOT EXISTS slack_dispatch_alias (
        name TEXT PRIMARY KEY, ob_code TEXT, ob_name TEXT, bh_sku TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')))""")
    # 채널(올리브영 등) 전표용 상품코드·상품명 — 납품확인서/발주서 업로드 때마다 갱신, 다음 건의 폴백으로만 사용
    _c.execute("""CREATE TABLE IF NOT EXISTS slack_dispatch_channel_product (
        name_key TEXT PRIMARY KEY, code TEXT, name TEXT, source TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime')))""")
    _c.commit()

_lock = threading.Lock()
_cache = {}  # key → (expires_at, value)


def _cached(key: str, ttl: int, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    _cache[key] = (now + ttl, val)
    return val


def _cfg() -> dict:
    return U.load_config()


# ── 설정 ─────────────────────────────────────────────────────────────────
_DEFAULTS = {
    "dispatch_mail_to": "ccg1@naver.com",
    "dispatch_mail_cc": "jeonghaesol0719@ourbox.co.kr, rlaxoans123@ourbox.co.kr, yugar250623@naver.com, "
                        "ggkk03@naver.com, tigerkku@naver.com, kr2leejt@naver.com, kkamac@naver.com, "
                        "soran2635@hanmail.net, ocean@cinnamon-lab.com, ella@cinnamon-lab.com",
    "dispatch_mail_subject": "[{tag}] [시나몬랩] {date} {title} 출고 요청의 건",
    "dispatch_mail_greeting": "안녕하세요 담당님\n\n다음건 출고 부탁드립니다",
    "dispatch_mail_signature": "",
    "dispatch_bh_memo": "# {partner} {date_kr} · 슬랙 출고요청 ({title})",
    # 키워드 → 아워박스 채널코드 / BH 거래처명 / 메일 태그. 앞에서부터 먼저 맞는 규칙 적용.
    "dispatch_rules": [
        {"kw": "올리브영", "ob": "SACH0699", "bh": "올리브영"},
        {"kw": "컬리", "ob": "SACH0700", "bh": "마켓컬리"},
        {"kw": "그로스", "ob": "SACH0333", "bh": "쿠팡그로스"},
        {"kw": "로켓", "ob": "SACH0333", "bh": "쿠팡그로스"},
        {"kw": "쿠팡", "ob": "SACH0333", "bh": "쿠팡"},
        {"kw": "W컨셉", "ob": "SACH1097", "bh": "W컨셉"},
        {"kw": "면세", "ob": "SACH0693", "bh": "면세점"},
        {"kw": "박람회", "ob": "SACH0779", "bh": "행사(박람회 등)"},
        {"kw": "행사", "ob": "SACH0779", "bh": "행사(박람회 등)"},
        {"kw": "기부", "ob": "SACH1024", "bh": "기부"},
        {"kw": "큐텐", "ob": "SACH1091", "bh": "큐텐"},
        {"kw": "원더팜", "ob": "SACH1177", "bh": "원더팜"},
        {"kw": "제니스", "ob": "SACH0744", "bh": "제니스상사"},
        {"kw": "히로인스", "ob": "SACH1085", "bh": "히로인스"},
        {"kw": "말론", "ob": "SACH1141", "bh": "말론"},
        {"kw": "수출", "ob": "SACH0747", "bh": "수출"},
        {"kw": "명절", "ob": "SACH1200", "bh": "명절선물"},
        {"kw": "코아시스", "ob": "SACH1242", "bh": "코아시스"},
        {"kw": "현대홈", "ob": "SACH1242", "bh": "현대 홈쇼핑"},
        {"kw": "무상", "ob": "SACH0707", "bh": "마케팅(무상/샘플)"},
        {"kw": "협찬", "ob": "SACH0707", "bh": "마케팅(무상/샘플)"},
        {"kw": "샘플", "ob": "SACH0707", "bh": "마케팅(무상/샘플)"},
        {"kw": "마케팅", "ob": "SACH0707", "bh": "마케팅(무상/샘플)"},
        {"kw": "PPL", "ob": "SACH0707", "bh": "마케팅(무상/샘플)"},
        {"kw": "CS", "ob": "SACH0728", "bh": "CS(무상)"},
        {"kw": "누락", "ob": "SACH0728", "bh": "CS(무상)"},
    ],
    "dispatch_tag_rules": [
        {"kw": "그로스", "tag": "쿠팡 그로스 밀크런"},
        {"kw": "쉽먼트", "tag": "쿠팡 쉽먼트 택배"},
        {"kw": "밀크런", "tag": "쿠팡 밀크런"},
        {"kw": "퀵", "tag": "호법배차"},
        {"kw": "직납", "tag": "호법배차"},
        {"kw": "배차", "tag": "호법배차"},
        {"kw": "화물", "tag": "호법배차"},
        {"kw": "용차", "tag": "호법배차"},
        {"kw": "택배", "tag": "호법택배"},
    ],
    "dispatch_default_tag": "호법택배",
}


def _settings() -> dict:
    cfg = _cfg()
    out = {}
    for k, v in _DEFAULTS.items():
        val = cfg.get(k)
        out[k] = v if val in (None, "", []) else val
    return out


def _split_mails(s) -> list:
    if isinstance(s, list):
        return [str(x).strip() for x in s if str(x).strip()]
    return [x.strip() for x in re.split(r"[,;\n]", str(s or "")) if x.strip()]


# ── 외부 참조 데이터 (캐시) ─────────────────────────────────────────────────
def _ob_headers(cfg: dict) -> dict:
    ak, sk = (cfg.get("ourbox_access_key") or "").strip(), (cfg.get("ourbox_secret_key") or "").strip()
    if not ak or not sk:
        raise HTTPException(400, "아워박스 API 키(ourbox_access_key/secret) 설정 필요")
    return {"api_access_key": ak, "api_secret_key": sk, "Content-Type": "application/json"}


def _ob_post(cfg: dict, path: str, body: dict, timeout: int = 60) -> dict:
    import requests
    r = requests.post(OURBOX_API + path, headers=_ob_headers(cfg), json=body, timeout=timeout)
    if r.status_code == 401:
        raise HTTPException(400, f"아워박스 API 인증 실패(IP 화이트리스트 확인): {r.text[:150]}")
    r.raise_for_status()
    return r.json()


def _ob_channels(cfg: dict) -> list:
    """아워박스 판매채널 [{code, name, type}] — 1시간 캐시."""
    def _f():
        d = _ob_post(cfg, "/api/oms/info/seller_list", {})
        return [{"code": c.get("channel_code"), "name": html.unescape(str(c.get("channel_name") or "")),
                 "type": c.get("channel_type")} for c in d.get("channel_list", [])]
    return _cached("ob_channels", 3600, _f)


def _ob_products(cfg: dict) -> list:
    """아워박스 상품 마스터(사용 중) [{code, company_code, name, norm, item_type}] — 30분 캐시."""
    def _f():
        first = _ob_post(cfg, "/api/oms/info/products", {"page": 1})
        rows = list(first.get("products") or [])
        for p in range(2, int(first.get("ctotal_page") or 1) + 1):
            rows += _ob_post(cfg, "/api/oms/info/products", {"page": p}).get("products") or []
        out = []
        for x in rows:
            if x.get("used") != "사용":
                continue
            name = html.unescape(str(x.get("product_name") or "")).strip()
            out.append({"code": x.get("sales_product_code"), "company_code": str(x.get("sales_product_company_code") or ""),
                        "name": name, "mnorm": _mnorm(name), "item_type": x.get("item_type")})
        return out
    return _cached("ob_products", 1800, _f)


def _bh_items(cfg: dict) -> list:
    """BoxHero 상품 [{id, name, sku, norm}] — 10분 캐시."""
    token = cfg.get("api_token") or ""
    if not token:
        return []
    def _f():
        return [{"id": i.get("id"), "name": str(i.get("name") or ""), "sku": str(i.get("sku") or "").strip(),
                 "norm": _mnorm(str(i.get("name") or ""))} for i in U.fetch_all_items_list(token)]
    return _cached("bh_items", 600, _f)


def _bh_partners(cfg: dict) -> list:
    token = cfg.get("api_token") or ""
    if not token:
        return []
    return _cached("bh_partners", 600, lambda: [{"id": p["id"], "name": p.get("name", "")} for p in U.fetch_partners(token)])


def _bh_locations(cfg: dict) -> list:
    token = cfg.get("api_token") or ""
    if not token:
        return []
    return _cached("bh_locations", 600, lambda: [{"id": l["id"], "name": l.get("name", "")} for l in U.fetch_locations(token)])


# ── 슬랙 메시지 파싱 ──────────────────────────────────────────────────────
def _clean(text: str) -> str:
    """슬랙 mrkdwn → 평문 (멘션/링크/볼드/이모지 제거, HTML 엔티티 해제)."""
    t = html.unescape(text or "")
    t = re.sub(r"<@[A-Z0-9]+>", "", t)
    t = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", t)
    t = re.sub(r"<(?:tel|mailto):[^|>]+\|([^>]+)>", r"\1", t)
    t = re.sub(r"<(https?:[^|>]+)\|([^>]+)>", r"\2", t)
    t = re.sub(r"<(https?:[^>]+)>", r"\1", t)
    t = re.sub(r"\*([^*\n]+)\*", r"\1", t)
    t = re.sub(r":[a-z0-9_+-]+:", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


def _sections(text: str) -> OrderedDict:
    """'1) 목적', '2) 일정' … 번호 섹션을 순서대로 {번호제목: 본문}. 번호 없는 머리글은 '_head'."""
    t = _clean(text)
    parts = re.split(r"(?m)^\s*(\d)\)\s*", t)
    out = OrderedDict()
    out["_head"] = parts[0].strip()
    for i in range(1, len(parts) - 1, 2):
        num, body = parts[i], parts[i + 1]
        lines = body.strip().split("\n")
        title = lines[0].strip()
        rest = "\n".join(l for l in lines[1:]).strip()
        out[f"{num}) {title}"] = rest
    return out


def _field(secs: OrderedDict, *keys) -> str:
    for k, v in secs.items():
        if any(x in k for x in keys):
            return v
    return ""


def _line_value(block: str, *labels) -> str:
    """블록에서 '• 라벨 : 값' 줄의 값."""
    for ln in (block or "").split("\n"):
        s = ln.strip().lstrip("-•●* ").strip()
        for lb in labels:
            if s.startswith(lb):
                v = s[len(lb):].strip().lstrip(":：").strip()
                return v
    return ""


def _parse_req_date(block: str) -> Optional[date]:
    """'출고 요청일: 2026-10-27 오후 17시까지' / '10/28(수)' → date."""
    s = block or ""
    m = re.search(r"(20\d{2})[./\-](\d{1,2})[./\-](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})[./](\d{1,2})", s)
    if m:
        today = date.today()
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
            if (d - today).days < -300:  # 연말에 내년 날짜 쓴 경우
                d = date(today.year + 1, d.month, d.day)
            return d
        except ValueError:
            pass
    return None


def _fmt_date(d: Optional[date]) -> str:
    if not d:
        return ""
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def _title_of(text: str) -> str:
    first = _clean(text).split("\n")[0].strip()
    first = re.sub(r"^\[[^\]]*\]\s*", "", first).strip()  # '[출고 요청]' 머리 제거
    first = re.sub(r"\s*(출고\s*요청|요청)?\s*(드립니다|의\s*건)?\s*$", "", first).strip()  # 꼬리 '출고 요청' 제거 (제목 템플릿이 붙임)
    first = first.strip("'\"“”‘’ ")
    return first[:60]


# ── 첨부 엑셀(주문내역) 파싱 ─────────────────────────────────────────────────
_COLS = {"order_no": ("주문번호",), "name": ("상품명", "제품명"), "qty": ("수량",), "receiver": ("수취인", "수령인", "받는"),
         "tel": ("연락처", "전화"), "zip": ("우편번호",), "addr": ("배송지", "주소"), "memo": ("주문메모", "메모", "배송메"),
         "courier": ("택배사",), "sender": ("발송인",)}


def _parse_order_sheet(data: bytes) -> list:
    """'아워박스 제품 출고양식(통합)' 주문내역 시트 → [{order_no, name, qty, receiver, tel, zip, addr, memo, courier, sender}]."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    rows_out = []
    for ws in wb.worksheets:
        header_idx, colmap = None, {}
        for r_i, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), 1):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if any("상품명" in c or "제품명" in c for c in cells) and any("수량" in c for c in cells):
                header_idx = r_i
                for c_i, c in enumerate(cells):
                    for key, labels in _COLS.items():
                        if key not in colmap and any(lb in c for lb in labels):
                            colmap[key] = c_i
                break
        if header_idx is None:
            continue
        for row in ws.iter_rows(min_row=header_idx + 1, values_only=True):
            cells = [c for c in row]
            def g(k):
                i = colmap.get(k)
                v = cells[i] if i is not None and i < len(cells) else None
                if v is None:
                    return ""
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                return str(v).strip()
            name, qty = g("name"), g("qty")
            if not name or not qty:
                continue
            try:
                q = int(float(qty))
            except ValueError:
                continue
            rows_out.append({"order_no": g("order_no"), "name": name, "qty": q, "receiver": g("receiver"),
                             "tel": g("tel"), "zip": g("zip").zfill(5) if g("zip").isdigit() else g("zip"),
                             "addr": g("addr"), "memo": g("memo"), "courier": g("courier"), "sender": g("sender")})
        if rows_out:
            break
    return rows_out


def _items_from_text(block: str) -> list:
    """첨부 없을 때 '품목' 섹션에서 '면역 / 2688EA' 같은 줄을 긁어 품목 추정."""
    out = []
    for ln in (block or "").split("\n"):
        s = ln.strip().lstrip("-•●* ").strip()
        if not s or s.startswith("총 ") or s.startswith("품목 요약"):
            continue
        m = re.search(r"^(.+?)\s*[/:：\-]\s*(\d[\d,]*)\s*(EA|ea|개|박스|BOX|box)?", s)
        if m:
            out.append({"order_no": "", "name": m.group(1).strip(), "qty": int(m.group(2).replace(",", "")),
                        "receiver": "", "tel": "", "zip": "", "addr": "", "memo": "", "courier": "", "sender": "",
                        "unit": m.group(3) or ""})
    return out


# ── 매칭 ────────────────────────────────────────────────────────────────────
def _norm_key(s: str) -> str:
    return re.sub(r"[\s\-_()\[\]/·,.&]+", "", html.unescape(str(s or ""))).lower()


def _mnorm(s: str) -> str:
    """매칭용 정규화: 괄호→공백, '맛' 접미 제거, 영문-숫자 붙은 토큰 분리, 소문자
    — '세라모사이드 피치맛' ≈ '세라모사이드(피치)', 'ERr731' ≈ 'ERR 731'."""
    t = re.sub(r"[()\[\]]", " ", html.unescape(str(s or "")))
    t = re.sub(r"(?<=[가-힣])맛\b", "", t)
    t = re.sub(r"([A-Za-z])(\d)", r"\1 \2", t)
    return U.normalize(t).lower()


def _alias_get(conn, name: str) -> dict:
    r = conn.execute("SELECT ob_code, ob_name, bh_sku FROM slack_dispatch_alias WHERE name=?", (name,)).fetchone()
    return {"ob_code": r[0] or "", "ob_name": r[1] or "", "bh_sku": r[2] or ""} if r else {}


def _fuzzy(q: str, choices: dict):
    """choices {mnorm: obj} 에서 best_match (질의도 같은 정규화)."""
    best, score = U.best_match(_mnorm(q), list(choices.keys()))
    return (choices[best] if best else None), int(score or 0)


def _match_ob_product(name: str, products: list) -> dict:
    """상품명 → 아워박스 상품. 바코드/업체코드 일치 → 정규화 동일 → 퍼지(임박 제외)."""
    q = (name or "").strip()
    if not q:
        return {"code": "", "name": "", "score": 0}
    for p in products:
        if p["company_code"] and p["company_code"] == q:
            return {"code": p["code"], "name": p["name"], "score": 100}
    mq, nk = _mnorm(q), _norm_key(q)
    exact = [p for p in products if p["mnorm"] == mq or _norm_key(p["name"]) == nk]
    if exact:
        exact.sort(key=lambda p: (p["item_type"] != "item", "임박" in p["name"]))
        return {"code": exact[0]["code"], "name": exact[0]["name"], "score": 100}
    pool = [p for p in products if p["item_type"] == "item" and "임박" not in p["name"]] or products
    if "임박" in q:
        pool = [p for p in products if "임박" in p["name"]] or pool
    choices = {}
    for p in pool:
        choices.setdefault(p["mnorm"], p)
    p, score = _fuzzy(q, choices)
    if p and score >= 60:
        return {"code": p["code"], "name": p["name"], "score": score}
    return {"code": "", "name": "", "score": score}


def _match_bh(name: str, ob_code: str, ob_name: str, items: list, conn) -> dict:
    """BH SKU 해석: product_mapping(아워박스 코드) → name_mapping → BH 상품명 퍼지."""
    cur = conn.cursor()
    if ob_code:
        cur.execute("SELECT boxhero_sku, boxhero_item_nm FROM product_mapping WHERE ourbox_prod_cd=?", (ob_code,))
        r = cur.fetchone()
        if r and r[0]:
            return {"sku": r[0], "name": r[1] or "", "score": 100, "via": "product_mapping"}
    for nm in (ob_name, name):
        if not nm:
            continue
        cur.execute("SELECT bh_sku, bh_name FROM name_mapping WHERE ob_name=?", (nm,))
        r = cur.fetchone()
        if r and r[0]:
            return {"sku": r[0], "name": r[1] or "", "score": 100, "via": "name_mapping"}
    by_sku = {i["sku"]: i for i in items if i["sku"]}
    if name in by_sku:
        return {"sku": name, "name": by_sku[name]["name"], "score": 100, "via": "sku"}
    choices = {}
    for i in items:
        if i["sku"] and i["norm"] and i["norm"] not in choices:
            choices[i["norm"]] = i
    i, score = _fuzzy(ob_name or name, choices)
    if i and score >= 60:
        return {"sku": i["sku"], "name": i["name"], "score": score, "via": "fuzzy"}
    return {"sku": "", "name": "", "score": score, "via": ""}


def _suggest_channel(text: str, s: dict, ob_channels: list, partners: list) -> dict:
    """요청문(목적/채널/제목)에서 아워박스 채널·BH 거래처 추천."""
    up = (text or "").upper()
    rule = next((r for r in s.get("dispatch_rules", []) if str(r.get("kw", "")).upper() in up), None)
    ob_code, bh_id, bh_name = "", None, ""
    if rule:
        ob_code = rule.get("ob") or ""
        want = _norm_key(rule.get("bh") or "")
        hit = next((p for p in partners if _norm_key(p["name"]) == want), None) or \
              next((p for p in partners if want and want in _norm_key(p["name"])), None)
        if hit:
            bh_id, bh_name = hit["id"], hit["name"]
    if not ob_code:
        # 채널명이 본문에 직접 들어있으면 그걸로
        for c in sorted(ob_channels, key=lambda c: -len(c["name"])):
            if len(c["name"]) >= 2 and c["name"].upper() in up:
                ob_code = c["code"]
                break
    if not bh_id and ob_code:
        ob_name = next((c["name"] for c in ob_channels if c["code"] == ob_code), "")
        want = _norm_key(ob_name)
        hit = next((p for p in partners if want and (_norm_key(p["name"]) == want or want in _norm_key(p["name"]))), None)
        if hit:
            bh_id, bh_name = hit["id"], hit["name"]
    return {"ob_channel_code": ob_code, "bh_partner_id": bh_id, "bh_partner_name": bh_name,
            "rule": (rule or {}).get("kw", "")}


def _suggest_tag(text: str, s: dict) -> str:
    up = (text or "").upper()
    for r in s.get("dispatch_tag_rules", []):
        if str(r.get("kw", "")).upper() in up:
            return r.get("tag") or s["dispatch_default_tag"]
    return s["dispatch_default_tag"]


def _mail_body(secs: OrderedDict, s: dict, rows: list) -> str:
    """메일 본문: 인사 + 슬랙 요청문 섹션(1)~6)) + 품목 요약 + 서명."""
    lines = [s["dispatch_mail_greeting"], ""]
    for k, v in secs.items():
        if k == "_head":
            continue
        lines.append(k)
        for ln in v.split("\n"):
            t = ln.strip().lstrip("-•●* ").strip()
            if t:
                lines.append(f"   - {t}")
        lines.append("")
    if rows:
        agg = OrderedDict()
        for r in rows:
            key = r.get("ob_name") or r["name"]
            agg[key] = agg.get(key, 0) + int(r["qty"])
        lines.append(f"※ 출고 품목 ({len(agg)}종 · {sum(agg.values()):,}개)")
        for n, q in agg.items():
            lines.append(f"   - {n} : {q:,}개")
        lines.append("")
    sig = (s.get("dispatch_mail_signature") or "").strip()
    if sig:
        lines += ["--", sig]
    return "\n".join(lines).rstrip() + "\n"


def _attach_dir(ts: str) -> str:
    d = os.path.join(ATTACH_DIR, re.sub(r"[^0-9.]", "", ts))
    os.makedirs(d, exist_ok=True)
    return d


def _attach_list(ts: str) -> list:
    d = _attach_dir(ts)
    return sorted(f for f in os.listdir(d) if not f.startswith("_"))


_INVOICE_SRC = "_invoice_items.json"


def _invoice_items_load(ts: str) -> list:
    pth = os.path.join(_attach_dir(ts), _INVOICE_SRC)
    if os.path.exists(pth):
        try:
            with open(pth, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _parse_channel_sheet(data: bytes) -> list:
    """올리브영 납품확인서/채널 발주서 → [{code, name, qty}]. 헤더에 '상품코드'+'상품명'이 있는 행을 찾고
    수량은 납품예정수량 > 납품수량 > 발주수량(EA) > 수량 순으로 고른다. 같은 코드는 수량 합산."""
    from openpyxl import load_workbook
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = OrderedDict()
    for ws in wb.worksheets:
        rows = ws.iter_rows(min_row=1, max_row=min(ws.max_row or 50, 2000), values_only=True)
        hdr, ci, ni, qi = None, None, None, None
        for r in rows:
            cells = [re.sub(r"\s+", "", str(c)) if c is not None else "" for c in r]
            if hdr is None:
                if any("상품코드" in c or "바코드" in c for c in cells) and any("상품명" in c or "제품명" in c for c in cells):
                    hdr = cells
                    ci = next(i for i, c in enumerate(cells) if "상품코드" in c or "바코드" in c)
                    ni = next(i for i, c in enumerate(cells) if "상품명" in c or "제품명" in c)
                    for key in ("납품예정수량", "납품수량", "발주수량", "출고수량", "수량"):
                        qi = next((i for i, c in enumerate(cells) if key in c), None)
                        if qi is not None:
                            break
                continue
            code = str(r[ci]).strip() if ci is not None and ci < len(r) and r[ci] is not None else ""
            name = str(r[ni]).strip() if ni is not None and ni < len(r) and r[ni] is not None else ""
            if not code or not name:
                continue
            if code.endswith(".0"):
                code = code[:-2]
            qty = 0
            if qi is not None and qi < len(r) and r[qi] not in (None, ""):
                try:
                    qty = int(float(r[qi]))
                except ValueError:
                    qty = 0
            it = out.setdefault(code, {"code": code, "name": name, "qty": 0})
            it["qty"] += qty
        if out:
            break
    return list(out.values())


def _channel_product_remember(items: list, source: str):
    with db._conn() as c:
        for it in items:
            key = U.normalize(re.sub(r"[()\[\]]", " ", it["name"])).lower()
            c.execute("INSERT INTO slack_dispatch_channel_product (name_key, code, name, source) VALUES (?,?,?,?) "
                      "ON CONFLICT(name_key) DO UPDATE SET code=excluded.code, name=excluded.name, source=excluded.source, "
                      "updated_at=datetime('now','localtime')", (key, it["code"], it["name"], source))
        c.commit()


def _channel_product_lookup(conn, *names) -> dict:
    """요청 상품명/아워박스명으로 기억된 채널 코드 찾기 (정규화 동일 → 퍼지 80점 이상)."""
    rows = conn.execute("SELECT name_key, code, name FROM slack_dispatch_channel_product").fetchall()
    if not rows:
        return {}
    keys = {r[0]: r for r in rows}
    for nm in names:
        if not nm:
            continue
        k = U.normalize(re.sub(r"[()\[\]]", " ", nm)).lower()
        if k in keys:
            return {"code": keys[k][1], "name": keys[k][2]}
    for nm in names:
        if not nm:
            continue
        best, score = U.best_match(re.sub(r"[()\[\]]", " ", nm).lower(), list(keys.keys()))
        if best and score >= 80:
            return {"code": keys[best][1], "name": keys[best][2]}
    return {}


def _log_get(ts: str) -> dict:
    with db._conn() as c:
        c.row_factory = None
        r = c.execute("SELECT ts, channel_id, title, ob_json, bh_tx_id, bh_json, mail_id, mail_json, oms_json, updated_at "
                      "FROM slack_dispatch_log WHERE ts=?", (ts,)).fetchone()
    if not r:
        return {}
    keys = ["ts", "channel_id", "title", "ob_json", "bh_tx_id", "bh_json", "mail_id", "mail_json", "oms_json", "updated_at"]
    d = dict(zip(keys, r))
    for k in ("ob_json", "bh_json", "mail_json", "oms_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        except Exception:
            d[k[:-5]] = None
    return d


def _log_set(ts: str, channel_id: str, title: str, **fields):
    with db._conn() as c:
        c.execute("INSERT OR IGNORE INTO slack_dispatch_log (ts, channel_id, title) VALUES (?,?,?)", (ts, channel_id, title))
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(json.dumps(v, ensure_ascii=False) if k.endswith("_json") else v)
        sets.append("updated_at=datetime('now','localtime')")
        c.execute(f"UPDATE slack_dispatch_log SET {', '.join(sets)} WHERE ts=?", (*vals, ts))
        c.commit()


# ── API ─────────────────────────────────────────────────────────────────────
@router.get("/products")
def products(q: str = ""):
    """UI 셀렉트용 아워박스 상품·BH 상품 목록 (검색어로 필터, 각 최대 300)."""
    cfg = _cfg()
    qk = _norm_key(q)
    ob = [{"code": p["code"], "name": p["name"], "item_type": p["item_type"]} for p in _ob_products(cfg)
          if not qk or qk in _norm_key(p["name"]) or qk in _norm_key(p["company_code"])][:300]
    bh = [{"sku": i["sku"], "name": i["name"]} for i in _bh_items(cfg)
          if i["sku"] and (not qk or qk in _norm_key(i["name"]) or qk in _norm_key(i["sku"]))][:300]
    return {"ob": ob, "bh": bh}


@router.get("/refs")
def refs():
    cfg = _cfg()
    out = {"settings": _settings(), "ob_channels": [], "bh_partners": [], "bh_locations": [],
           "bh_location_default": cfg.get("selected_location_id"),
           "gmail": {"connected": bool((cfg.get("gmail_token") or {}).get("refresh_token")),
                     "can_send": U.gmail_can_send(cfg.get("gmail_token") or {})},
           "errors": []}
    try:
        out["ob_channels"] = _ob_channels(cfg)
    except Exception as e:
        out["errors"].append(f"아워박스 채널: {str(e)[:120]}")
    try:
        out["bh_partners"] = _bh_partners(cfg)
        out["bh_locations"] = _bh_locations(cfg)
    except Exception as e:
        out["errors"].append(f"BoxHero: {str(e)[:120]}")
    return out


class ConfigReq(BaseModel):
    data: dict


@router.get("/config")
def get_config():
    return _settings()


@router.post("/config")
def set_config(body: ConfigReq):
    allowed = {k: v for k, v in body.data.items() if k in _DEFAULTS}
    U.save_config(allowed)
    return _settings()


class PrepareReq(BaseModel):
    channel_id: str
    ts: str


@router.post("/prepare")
def prepare(body: PrepareReq):
    """슬랙 메시지 → 검토용 초안. 쓰기 없음."""
    cfg = _cfg()
    token = cfg.get("slack_token") or ""
    if not token:
        raise HTTPException(400, "슬랙 토큰 없음")
    try:
        msg = U.fetch_slack_message(token, body.channel_id, body.ts)
    except Exception as e:
        raise HTTPException(400, f"슬랙 메시지 조회 실패: {str(e)[:150]}")
    if not msg:
        raise HTTPException(404, "메시지를 찾을 수 없습니다")
    text = msg.get("text") or ""
    secs = _sections(text)
    s = _settings()
    title = _title_of(text)
    purpose = _field(secs, "목적")
    sched = _field(secs, "일정")
    items_blk = _field(secs, "품목")
    contact = _field(secs, "담당자")
    ship = _field(secs, "운송")
    req_date = _parse_req_date(sched)

    # 첨부 엑셀 → 주문행
    files, rows, warnings = [], [], []
    for f in msg.get("files", []) or []:
        name = f.get("name") or ""
        ent = {"name": name, "url": f.get("url_private_download") or "", "size": f.get("size", 0), "parsed": 0}
        if name.lower().endswith((".xlsx", ".xlsm")) and ent["url"]:
            try:
                data = U.download_slack_file(ent["url"], token)
                parsed = _parse_order_sheet(data)
                ent["parsed"] = len(parsed)
                if parsed and not rows:
                    rows = parsed
                    ent["used"] = True
            except Exception as e:
                warnings.append(f"첨부 '{name}' 읽기 실패: {str(e)[:100]}")
        files.append(ent)
    source = "excel"
    if not rows:
        rows = _items_from_text(items_blk)
        source = "text" if rows else "none"
        if rows:
            warnings.append("첨부 엑셀에 주문내역이 없어 메시지 '품목' 섹션에서 추정했습니다 — 수량·수취인 확인 필요")
            recv = _line_value(contact, "수령인", "수취인", "담당자") or contact.split("\n")[0].strip()
            addr = _line_value(contact, "배송지 주소", "배송지", "주소")
            tel_m = re.search(r"0\d{1,2}-?\d{3,4}-?\d{4}", contact)
            for r in rows:
                r["receiver"] = re.sub(r"\s*\(.*$", "", recv)[:20]
                r["addr"] = addr
                r["tel"] = tel_m.group(0) if tel_m else ""
        else:
            warnings.append("주문내역 엑셀 첨부가 없고 품목도 읽어내지 못했습니다 — 품목을 직접 입력하세요")

    # 매칭
    ob_products, bh_items, ob_channels, partners = [], [], [], []
    try:
        ob_products = _ob_products(cfg)
    except Exception as e:
        warnings.append(f"아워박스 상품 조회 실패: {str(e)[:100]}")
    try:
        bh_items = _bh_items(cfg)
    except Exception as e:
        warnings.append(f"BoxHero 상품 조회 실패: {str(e)[:100]}")
    try:
        ob_channels = _ob_channels(cfg)
        partners = _bh_partners(cfg)
    except Exception as e:
        warnings.append(f"채널/거래처 조회 실패: {str(e)[:100]}")

    conn = db._conn()
    try:
        by_code = {p["code"]: p for p in ob_products}
        bh_by_sku = {i["sku"]: i for i in bh_items}
        for i, r in enumerate(rows):
            al = _alias_get(conn, r["name"])
            if al.get("ob_code") and al["ob_code"] in by_code:
                ob = {"code": al["ob_code"], "name": by_code[al["ob_code"]]["name"], "score": 100, "via": "학습"}
            else:
                ob = _match_ob_product(r["name"], ob_products) if ob_products else {"code": "", "name": "", "score": 0}
            if al.get("bh_sku") and al["bh_sku"] in bh_by_sku:
                bh = {"sku": al["bh_sku"], "name": bh_by_sku[al["bh_sku"]]["name"], "score": 100, "via": "학습"}
            else:
                bh = _match_bh(r["name"], ob["code"], ob["name"], bh_items, conn) if (bh_items or ob["code"]) else {"sku": "", "name": "", "score": 0, "via": ""}
            r.update({"idx": i, "ob_code": ob["code"], "ob_name": ob["name"], "ob_score": ob["score"], "ob_via": ob.get("via", ""),
                      "bh_sku": bh["sku"], "bh_name": bh["name"], "bh_score": bh["score"], "bh_via": bh.get("via", "")})
            if not r["order_no"]:
                r["order_no"] = f"{(req_date or date.today()).strftime('%y%m%d')}_{i + 1}"
    finally:
        conn.close()

    ctx = "\n".join([title, purpose, items_blk, ship])
    sug = _suggest_channel(ctx, s, ob_channels, partners)
    tag = _suggest_tag(ship + "\n" + purpose + "\n" + title, s)
    date_txt = _fmt_date(req_date) or _fmt_date(date.today())
    subject = s["dispatch_mail_subject"].format(tag=tag, date=date_txt, title=title)
    log = _log_get(body.ts)

    # 주문 묶음 (주문번호 단위 수취인/주소)
    orders = OrderedDict()
    for r in rows:
        o = orders.setdefault(r["order_no"], {"order_no": r["order_no"], "receiver": r["receiver"], "tel": r["tel"],
                                              "zip": r["zip"], "addr": r["addr"], "memo": r["memo"], "lines": 0, "qty": 0})
        o["lines"] += 1
        o["qty"] += int(r["qty"])
    miss_zip = [o["order_no"] for o in orders.values() if not o["zip"]]
    if miss_zip:
        warnings.append(f"우편번호 없는 주문 {len(miss_zip)}건 — 아워박스 발주 필수값이라 입력 필요 (예: {miss_zip[0]})")
    unmatched_ob = [r["name"] for r in rows if not r["ob_code"]]
    unmatched_bh = [r["name"] for r in rows if not r["bh_sku"]]
    if unmatched_ob:
        warnings.append(f"아워박스 상품 미매칭 {len(unmatched_ob)}건: {', '.join(unmatched_ob[:3])}")
    if unmatched_bh:
        warnings.append(f"BoxHero SKU 미매칭 {len(unmatched_bh)}건: {', '.join(unmatched_bh[:3])}")

    return {
        "ts": body.ts, "channel_id": body.channel_id, "title": title, "text": _clean(text),
        "sections": secs, "req_date": req_date.isoformat() if req_date else "", "date_txt": date_txt,
        "files": files, "rows": rows, "orders": list(orders.values()), "source": source,
        "suggest": {**sug, "tag": tag},
        "mail": {"to": _split_mails(s["dispatch_mail_to"]), "cc": _split_mails(s["dispatch_mail_cc"]),
                 "subject": subject, "body": _mail_body(secs, s, rows)},
        "bh_memo": s["dispatch_bh_memo"].format(partner=sug.get("bh_partner_name") or "", title=title,
                                                date_kr=f"{(req_date or date.today()).month}월 {(req_date or date.today()).day}일"),
        "attachments": _attach_list(body.ts),
        "invoice": {"enabled": False, "recipient": sug.get("bh_partner_name") or (rows[0].get("receiver") if rows else ""),
                    "trade_name": title, "issue_date": (req_date or date.today()).isoformat(), "payment_terms": "", "doc_number": "",
                    "items": _invoice_items_load(body.ts)},
        "log": log, "warnings": warnings,
    }


@router.post("/attach")
async def attach(ts: str = Form(...), file: UploadFile = File(...)):
    """메일 첨부 파일 업로드(거래명세서·부착문서 등). ts별 스테이징."""
    data = await file.read()
    name = os.path.basename(file.filename or "file")
    with open(os.path.join(_attach_dir(ts), name), "wb") as f:
        f.write(data)
    return {"ok": True, "attachments": _attach_list(ts)}


@router.delete("/attach")
def attach_delete(ts: str, name: str):
    p = os.path.join(_attach_dir(ts), os.path.basename(name))
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True, "attachments": _attach_list(ts)}


class RowIn(BaseModel):
    order_no: str
    name: str
    qty: int
    receiver: str = ""
    tel: str = ""
    zip: str = ""
    addr: str = ""
    memo: str = ""
    ob_code: str = ""
    ob_name: str = ""
    bh_sku: str = ""
    unit_price: int = 0   # 거래명세서 단가 (부가세 별도)


class InvoiceItemIn(BaseModel):
    code: str = ""
    name: str = ""
    qty: int = 0
    price: int = 0


class InvoiceIn(BaseModel):
    enabled: bool = False
    items: List[InvoiceItemIn] = []   # 채널 전표 품목 (납품확인서 업로드 결과). 비면 요청 품목 + 기억된 코드로 대체
    recipient: str = ""      # 수신 (거래처)
    trade_name: str = ""     # 거래 건명
    issue_date: str = ""     # 거래일자 YYYY-MM-DD
    payment_terms: str = ""  # 결제조건
    doc_number: str = ""     # 관리번호


class MailIn(BaseModel):
    to: List[str] = []
    cc: List[str] = []
    subject: str = ""
    body: str = ""
    attach_slack_files: bool = True


class RunReq(BaseModel):
    channel_id: str
    ts: str
    title: str = ""
    steps: dict = {"ourbox": True, "oms": True, "boxhero": True, "mail": True, "slack_done": True}
    rows: List[RowIn]
    ob_channel_code: str = ""
    order_prefix: str = ""
    bh_partner_id: Optional[int] = None
    bh_location_id: Optional[int] = None
    bh_memo: str = ""
    mail: MailIn = MailIn()
    invoice: InvoiceIn = InvoiceIn()
    force: bool = False   # True면 이미 완료된 단계도 다시 실행


def _invoice_items_for(req: "RunReq") -> list:
    """전표 품목: 업로드된 채널 전표 품목이 있으면 그대로, 없으면 요청 품목에 기억된 채널 코드/바코드를 붙인다."""
    if req.invoice.items:
        return [{"code": i.code, "name": i.name, "qty": int(i.qty), "price": int(i.price or 0)} for i in req.invoice.items if i.name or i.code]
    agg = OrderedDict()
    conn = db._conn()
    try:
        for r in req.rows:
            hit = _channel_product_lookup(conn, r.ob_name, r.name)
            code = hit.get("code") or (r.bh_sku if re.fullmatch(r"\d{8,14}", r.bh_sku or "") else "")
            name = hit.get("name") or r.ob_name or r.name
            key = code or name
            a = agg.setdefault(key, {"code": code, "name": name, "qty": 0, "price": int(r.unit_price or 0)})
            a["qty"] += int(r.qty)
            if r.unit_price:
                a["price"] = int(r.unit_price)
    finally:
        conn.close()
    return list(agg.values())


def build_invoice_xlsx(req: "RunReq") -> tuple:
    """거래명세서 엑셀 생성 — 사용자 양식(거래명세서_양식_v2: 수신 E8·건명 E9·날짜 E10·유효기간/결제조건 E11,
    16~22행 B=상품코드(바코드)·F=상품명·N=수량·P=단가, 금액/세액/합계는 양식 수식). 7행 초과 시 시트 복제.
    반환 (파일명, bytes)."""
    from openpyxl import load_workbook
    cfg = _cfg()
    tpl = cfg.get("dispatch_invoice_template", "") or os.path.join(_ROOT, "backend", "templates", "거래명세서_양식_v2.xlsx")
    if not os.path.exists(tpl):
        raise HTTPException(400, f"거래명세서 양식 파일 없음: {tpl}")
    inv = req.invoice
    items = _invoice_items_for(req)
    PAGE = 7
    chunks = [items[i:i + PAGE] for i in range(0, len(items), PAGE)] or [[]]
    wb = load_workbook(tpl)
    ws0 = wb.worksheets[0]
    extra = [wb.copy_worksheet(ws0) for _ in range(1, len(chunks))]
    issue = (inv.issue_date or date.today().isoformat())[:10]
    issue_dot = issue.replace("-", ".")

    def fill(ws, chunk, page):
        doc = inv.doc_number or issue.replace("-", "")
        ws["B2"] = f"관리번호: {doc if page == 1 else f'{doc}-{page - 1}'}"
        ws["E8"] = inv.recipient
        ws["E9"] = inv.trade_name or req.title
        ws["E10"] = issue_dot
        ws["E11"] = inv.payment_terms or "견적일로부터 1개월"
        for i in range(16, 16 + PAGE):
            for col in ("B", "F", "N", "P"):
                ws[f"{col}{i}"] = None
        for i, it in enumerate(chunk):
            row = 16 + i
            ws[f"B{row}"] = it["code"] or None
            ws[f"F{row}"] = it["name"]
            ws[f"N{row}"] = it["qty"]
            ws[f"P{row}"] = it["price"] if it.get("price") else None

    fill(ws0, chunks[0], 1)
    for i, (ch, ws) in enumerate(zip(chunks[1:], extra), 2):
        fill(ws, ch, i)
    buf = io.BytesIO()
    wb.save(buf)
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", inv.recipient or "거래처")
    return f"거래명세서_{safe}_{issue}.xlsx", buf.getvalue()


@router.post("/invoice-source")
async def invoice_source(ts: str = Form(...), file: UploadFile = File(...)):
    """올리브영 납품확인서/채널 발주서 업로드 → 전표 품목(상품코드·상품명·수량) 추출. ts별 저장 + 코드 기억."""
    data = await file.read()
    try:
        items = _parse_channel_sheet(data)
    except Exception as e:
        raise HTTPException(400, f"파일을 읽을 수 없습니다: {str(e)[:100]}")
    if not items:
        raise HTTPException(400, "상품코드/상품명 열을 찾지 못했습니다 (올리브영 납품확인서·발주서 형식인지 확인)")
    with open(os.path.join(_attach_dir(ts), _INVOICE_SRC), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    _channel_product_remember(items, os.path.basename(file.filename or ""))
    return {"ok": True, "items": items, "source": os.path.basename(file.filename or "")}


@router.delete("/invoice-source")
def invoice_source_delete(ts: str):
    pth = os.path.join(_attach_dir(ts), _INVOICE_SRC)
    if os.path.exists(pth):
        os.remove(pth)
    return {"ok": True}


@router.post("/invoice")
def invoice_preview(req: RunReq):
    """거래명세서 미리보기 다운로드 (메일 발송 전 확인용). 쓰기 없음."""
    from fastapi.responses import Response
    import urllib.parse
    name, data = build_invoice_xlsx(req)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})


def _run_ourbox(cfg: dict, req: RunReq, out: dict):
    if not req.ob_channel_code:
        raise HTTPException(400, "아워박스 채널을 선택하세요")
    bad = [r.name for r in req.rows if not r.ob_code]
    if bad:
        raise HTTPException(400, f"아워박스 상품 미매칭: {', '.join(bad[:3])}")
    groups = OrderedDict()
    for r in req.rows:
        key = r.order_no or "1"
        g = groups.setdefault(key, {"row": r, "items": []})
        g["items"].append(r)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    orders = []
    for key, g in groups.items():
        r = g["row"]
        if not r.zip or not r.addr or not r.receiver:
            raise HTTPException(400, f"주문 {key}: 수취인·우편번호·주소는 아워박스 발주 필수값입니다")
        code = f"{req.order_prefix}{key}"
        orders.append({
            "order_data": {
                "company_order_code": code, "buyer_name": r.receiver, "buyer_id": "", "buyer_tel": r.tel or "",
                "order_date": now, "receiver_name": r.receiver, "receiver_tel1": r.tel or "", "receiver_tel2": "",
                "zipcode": r.zip, "shipping_address1": r.addr, "shipping_address2": "",
                "channel_code": req.ob_channel_code, "shipping_message": r.memo or "", "tf_note": "",
                "memo1": (req.title or "")[:100],
            },
            "order_item_data": [
                {"order_product_code": f"{code}-{i + 1}", "sales_product_code": it.ob_code,
                 "sales_produc_name": it.ob_name or it.name, "quantity": int(it.qty), "product_id": "",
                 "product_price": 0, "shipping_kind": "3", "shipping_price": 0}
                for i, it in enumerate(g["items"])
            ],
        })
    res = _ob_post(cfg, "/api/oms/order/send", {"orders": orders}, timeout=120)
    processed = res.get("processed_orders") or []
    fails = [p for p in processed if str(p.get("result")).lower() != "true" or str(p.get("status")).lower() != "true"]
    result = {"ok": str(res.get("result")).lower() == "true" and not fails, "count": res.get("processed_order_count"),
              "order_codes": sorted({str(p.get("order_code")) for p in processed if p.get("order_code")}),
              "company_order_codes": [o["order_data"]["company_order_code"] for o in orders],
              "fails": fails[:20], "message": res.get("message") or "", "channel_code": req.ob_channel_code}
    out["ourbox"] = result
    state.add_log("success" if result["ok"] else "error",
                  f"슬랙출고 → 아워박스 발주등록 {len(orders)}건 ({'성공' if result['ok'] else '일부 실패'})",
                  f"채널={req.ob_channel_code} | {result['company_order_codes']} | {result['message']} {fails[:3]}",
                  source="slack-dispatch")
    if not result["ok"]:
        raise HTTPException(400, f"아워박스 발주등록 실패: {result['message']} {json.dumps(fails[:3], ensure_ascii=False)[:300]}")


def _run_oms(cfg: dict, req: RunReq, out: dict, log: dict):
    """아워박스 발주 뒤 OMS 단계 진행: 주문(10)→주문서처리(20)→출고준비(30)→WMS 출고등록. 저장 세션(Playwright) 사용."""
    ob = out.get("ourbox") or log.get("ob") or {}
    codes = [f"{req.order_prefix}{r.order_no}" for r in req.rows] if not ob.get("company_order_codes") else ob["company_order_codes"]
    codes = sorted(set(codes))
    channel_cd = ob.get("channel_code") or req.ob_channel_code
    if not codes or not channel_cd:
        raise HTTPException(400, "OMS 단계 진행: 발주등록된 주문번호/채널이 없습니다 (아워박스 단계를 먼저 실행)")
    oid, opw = ob_orders._creds()
    today = date.today().isoformat()
    start = (date.today() - timedelta(days=3)).isoformat()
    try:
        data = ob_orders._run_oms({"cmd": "push_codes", "oid": oid, "opw": opw, "codes": codes,
                                   "channel_cd": channel_cd, "start": start, "end": today}, timeout=600)
    except Exception as e:
        raise HTTPException(400, f"OMS 단계 진행 실패: {str(e)[:200]}")
    data["ok"] = bool(data.get("ok"))
    out["oms"] = data
    stages = {c: v.get("stage_nm") for c, v in (data.get("final") or {}).items()}
    state.add_log("success" if data["ok"] else "warning",
                  f"슬랙출고 → OMS 단계 진행 {len(codes)}건 ({'WMS 출고등록 완료' if data['ok'] else '미완료'})",
                  f"채널={channel_cd} | {stages} | 남음={data.get('remaining')} 미발견={data.get('missing')}", source="slack-dispatch")
    if not data["ok"]:
        raise HTTPException(400, f"OMS 단계 진행 미완료 — 현재 단계 {stages} / 남음 {data.get('remaining')} 미발견 {data.get('missing')}")


def _run_boxhero(cfg: dict, req: RunReq, out: dict):
    token, loc = cfg.get("api_token") or "", req.bh_location_id or cfg.get("selected_location_id")
    if not token or not loc:
        raise HTTPException(400, "BoxHero 토큰/출고 위치 설정 필요")
    bad = [r.name for r in req.rows if not r.bh_sku]
    if bad:
        raise HTTPException(400, f"BoxHero SKU 미매칭: {', '.join(bad[:3])}")
    sku_qty = defaultdict(int)
    for r in req.rows:
        sku_qty[r.bh_sku.strip()] += int(r.qty)
    sku_to_id = U.fetch_all_items(token)
    items, miss = [], []
    for sku, q in sku_qty.items():
        iid = sku_to_id.get(sku)
        (items.append({"item_id": iid, "quantity": -q}) if iid else miss.append(sku))
    if miss:
        raise HTTPException(400, f"BoxHero에 없는 SKU: {miss}")
    payload = {"type": "out", "to_location_id": int(loc), "items": items, "memo": req.bh_memo or ""}
    if req.bh_partner_id:
        payload["partner_id"] = int(req.bh_partner_id)
    tx = U.post_transaction(token, payload)
    tx_id = str(tx.get("id", ""))
    out["boxhero"] = {"ok": True, "tx_id": tx_id, "skus": len(items), "qty": sum(sku_qty.values()),
                      "partner_id": req.bh_partner_id}
    state.add_log("success", f"슬랙출고 → BoxHero 출고등록 {len(items)}종 {sum(sku_qty.values())}개",
                  f"tx={tx_id} | 거래처={req.bh_partner_id} | 메모={req.bh_memo}", payload=payload, source="slack-dispatch")


def _run_mail(cfg: dict, req: RunReq, out: dict):
    tok = cfg.get("gmail_token") or {}
    if not tok.get("refresh_token"):
        raise HTTPException(400, "Gmail 미연결")
    if not U.gmail_can_send(tok):
        raise HTTPException(400, "Gmail 보내기 권한 없음 — 설정에서 Gmail을 다시 연결하세요 (gmail.send 동의)")
    m = req.mail
    if not m.to:
        raise HTTPException(400, "메일 수신자 없음")
    atts = []
    if m.attach_slack_files:
        token = cfg.get("slack_token") or ""
        try:
            msg = U.fetch_slack_message(token, req.channel_id, req.ts)
            for f in msg.get("files", []) or []:
                url = f.get("url_private_download")
                if url:
                    atts.append((f.get("name") or "file", U.download_slack_file(url, token)))
        except Exception as e:
            raise HTTPException(400, f"슬랙 첨부 다운로드 실패: {str(e)[:100]}")
    for name in _attach_list(req.ts):
        with open(os.path.join(_attach_dir(req.ts), name), "rb") as f:
            atts.append((name, f.read()))
    if req.invoice.enabled:
        atts.append(build_invoice_xlsx(req))
    res = U.gmail_send_message(tok, m.to, m.cc, m.subject, m.body, atts)
    out["mail"] = {"ok": True, "id": res.get("id"), "to": m.to, "cc": m.cc, "subject": m.subject,
                   "attachments": [a[0] for a in atts]}
    state.add_log("success", f"슬랙출고 → 물류센터 메일 발송: {m.subject[:60]}",
                  f"to={m.to} cc={len(m.cc)} 첨부={[a[0] for a in atts]}", source="slack-dispatch")


def _run_slack_done(cfg: dict, req: RunReq, out: dict, log: dict):
    token = cfg.get("slack_token") or ""
    ob = out.get("ourbox") or log.get("ob") or {}
    bh = out.get("boxhero") or {}
    ml = out.get("mail") or {}
    parts = []
    if ob:
        parts.append(f"아워박스 발주등록 {len(ob.get('company_order_codes') or [])}건 (채널 {ob.get('channel_code')})")
    oms = out.get("oms") or log.get("oms") or {}
    if oms.get("ok"):
        parts.append("OMS WMS 출고등록 완료")
    if bh:
        parts.append(f"BoxHero 출고 tx {bh.get('tx_id')} ({bh.get('skus')}종 {bh.get('qty')}개)")
    elif log.get("bh_tx_id"):
        parts.append(f"BoxHero 출고 tx {log['bh_tx_id']}")
    if ml:
        parts.append(f"호법센터 메일 발송 ({ml.get('subject')})")
    elif log.get("mail_id"):
        parts.append("호법센터 메일 발송")
    text = "🤖 출고 전산처리 완료 — " + " · ".join(parts) if parts else "🤖 출고 전산처리 완료"
    try:
        # 토글이 아니라 '추가만' — 이미 ✅가 있으면 그대로 둔다 (toggle은 재실행 시 ✅를 지워버림)
        from slack_sdk import WebClient
        WebClient(token=token).reactions_add(channel=req.channel_id, timestamp=req.ts, name="white_check_mark")
    except Exception as e:
        if "already_reacted" not in str(e):
            out.setdefault("warnings", []).append(f"✅ 리액션 실패: {str(e)[:80]}")
    try:
        U.slack_post_message(token, req.channel_id, text, thread_ts=req.ts)
        out["slack_done"] = {"ok": True, "text": text}
    except Exception as e:
        out.setdefault("warnings", []).append(f"스레드 답글 실패: {str(e)[:80]}")
        out["slack_done"] = {"ok": False}


@router.post("/run")
def run(req: RunReq):
    """실제 실행. 단계 순서: 아워박스 → BoxHero → 메일 → 슬랙. 완료된 단계는 재실행 시 건너뜀(force=True면 재실행).
    한 단계가 실패하면 거기서 멈추고 앞 단계 결과는 기록에 남긴다."""
    cfg = _cfg()
    if not req.rows:
        raise HTTPException(400, "품목이 없습니다")
    with _lock:
        log = _log_get(req.ts)
        out = {"skipped": [], "warnings": []}
        _log_set(req.ts, req.channel_id, req.title)
        # 사람이 확정한 매칭을 학습 (다음 요청에서 같은 상품명은 바로 적용)
        try:
            with db._conn() as c:
                for r in req.rows:
                    if r.name and (r.ob_code or r.bh_sku):
                        c.execute("INSERT INTO slack_dispatch_alias (name, ob_code, ob_name, bh_sku) VALUES (?,?,?,?) "
                                  "ON CONFLICT(name) DO UPDATE SET ob_code=excluded.ob_code, ob_name=excluded.ob_name, "
                                  "bh_sku=excluded.bh_sku, updated_at=datetime('now','localtime')",
                                  (r.name.strip(), r.ob_code, r.ob_name, r.bh_sku))
                c.commit()
        except Exception as e:
            out["warnings"].append(f"매칭 학습 저장 실패: {str(e)[:80]}")

        def _step(name, done, fn, **save):
            if not req.steps.get(name):
                return
            if done and not req.force:
                out["skipped"].append(name)
                return
            fn(cfg, req, out)
            _log_set(req.ts, req.channel_id, req.title, **{k: v(out) for k, v in save.items()})

        try:
            _step("ourbox", bool((log.get("ob") or {}).get("ok")), _run_ourbox, ob_json=lambda o: o["ourbox"])
            _step("oms", bool((log.get("oms") or {}).get("ok")),
                  lambda c, r, o: _run_oms(c, r, o, _log_get(req.ts)), oms_json=lambda o: o["oms"])
            _step("boxhero", bool(log.get("bh_tx_id")), _run_boxhero,
                  bh_tx_id=lambda o: o["boxhero"]["tx_id"], bh_json=lambda o: o["boxhero"])
            _step("mail", bool(log.get("mail_id")), _run_mail,
                  mail_id=lambda o: o["mail"]["id"], mail_json=lambda o: o["mail"])
            if req.steps.get("slack_done"):
                _run_slack_done(cfg, req, out, _log_get(req.ts))
        except HTTPException as e:
            out["error"] = e.detail
            state.add_log("error", f"슬랙출고 자동처리 중단: {str(e.detail)[:100]}", source="slack-dispatch")
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            state.add_log("error", f"슬랙출고 자동처리 오류: {str(e)[:100]}", source="slack-dispatch")
        out["log"] = _log_get(req.ts)
        return out


@router.get("/log")
def log_list(limit: int = 50):
    with db._conn() as c:
        rows = c.execute("SELECT ts, channel_id, title, bh_tx_id, mail_id, updated_at FROM slack_dispatch_log "
                         "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) if hasattr(r, "keys") else dict(zip(["ts", "channel_id", "title", "bh_tx_id", "mail_id", "updated_at"], r))
            for r in rows]
