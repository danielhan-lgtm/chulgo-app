"""네이버 스마트스토어(도착보장) 출고 보고 — 박스히어로 거래 내역(Transactions xlsx)을 올려 특정 거래처의 출고를 판매 현황으로 본다.

- 입력: 박스히어로 > 거래 내역 > 엑셀 내보내기 (시트 'BoxHero', 컬럼 거래ID/거래구분/거래처/거래일시/메모/SKU/제품명/수량 …)
- 거래처 필터: 기본 '스마트스토어' + 'DJ&A' (설정에서 변경). 도착보장 출고는 하루 한 회차씩 나가며
  메모의 '# 스마트 스토어 (dj&a) 9월 21일'이 주문일이라, 주문일 기준으로 일별 판매량을 집계한다.
- 출력: 이번 달·전월·최근 7일 출고량, 품목별·주별 추이, 자사 재고(아워박스 가용 → 박스히어로) 기준 재고 소진 예상일, 슬랙용 보고문.
"""
from __future__ import annotations

import io, os, re, time, calendar
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Optional

import pandas as pd
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

import utils_core as U
from routers.channel_report import (
    _DATA_DIR, _load_json, _save_json, _nkey, _n, _won, _pct1, _top, _prev_months,
    _bh_stock_by_sku, _ob_available_by_name,
)

router = APIRouter()

STATE_PATH = os.path.join(_DATA_DIR, "naver_report_state.json")
TX_PATH = os.path.join(_DATA_DIR, "naver_report_tx.json")
ORDERS_PATH = os.path.join(_DATA_DIR, "naver_report_orders.json")
PRODUCTS_PATH = os.path.join(_DATA_DIR, "naver_report_products.json")   # 스마트스토어 상품 목록(Product_*.csv) — 리스팅 상태·가격·재고   # 도착보장 주문 관리 목록(manageList xlsx) 일별 집계 — 사은품 분리용
DEFAULT_PARTNER_KW = ["스마트스토어", "DJ&A"]
EXEC_NAME = "네이버 스마트스토어(도착보장)"   # 경영 요약(4채널 묶음) 표기


# ─────────────────────────────────────────────────────────────────
# 저장소
# ─────────────────────────────────────────────────────────────────
def _state() -> dict:
    st = _load_json(STATE_PATH, {})
    st.setdefault("partner_kw", list(DEFAULT_PARTNER_KW))   # 거래처명에 모두 포함돼야 하는 키워드
    st.setdefault("aliases", {})                              # 제품명 → 짧은 라벨 (사용자 덮어쓰기)
    st.setdefault("uploads", [])                              # [{filename, uploaded_at, rows, from, to}]
    st.setdefault("notes", "")
    return st


def _tx_all() -> list:
    return _load_json(TX_PATH, [])


def _orders() -> dict:
    """{"days": {YYYY-MM-DD: {total, gift, returns, gift_items{name: qty}}}, "files": [...]}"""
    o = _load_json(ORDERS_PATH, {})
    o.setdefault("days", {})
    o.setdefault("files", [])
    return o


_ORDER_COLS = ["이슈", "주문일시", "주문수량", "상품명", "주문상태"]


def _is_order_list(df) -> bool:
    return all(c in df.columns for c in _ORDER_COLS)


def _num(v) -> float:
    """'1,234원' / '12,500' / NaN → float"""
    try:
        t = str(v).replace(",", "").replace("원", "").strip()
        return float(t) if t and t != "nan" else 0.0
    except ValueError:
        return 0.0


def _parse_order_list(df) -> dict:
    """도착보장 주문 관리 목록(manageList_*.xlsx) → 주문일별 집계.
    {total(정상 주문수량), gift(그중 사은품), returns(반품·교환·취소 수량), gift_items, items(상품별 수량),
     listings({쇼핑몰 상품코드: {qty, pay}} — 리스팅별, 상품 목록과 연결), orders(주문 건수), buyers(구매자 수),
     repeat(그중 자료 범위 안에서 이전에 산 적 있는 구매자 수), amt(주문금액), pay(결제 금액), disc(할인금액), return_orders}
    '이슈' 열에 '사은품' 이 있으면 사은품 행(주문금액 0). 주문상태 '정상' 만 출고로 본다.
    주문자 ID 는 재구매 수 계산에만 쓰고 저장하지 않는다."""
    days: dict = {}
    order_ids: dict = defaultdict(set); buyer_ids: dict = defaultdict(set); ret_orders: dict = defaultdict(set)
    first_seen: dict = {}
    for r in df.itertuples(index=False):
        rec = dict(zip(df.columns, r))
        ts = rec.get("주문일시")
        d = ts.strftime("%Y-%m-%d") if isinstance(ts, (datetime, pd.Timestamp)) else str(ts)[:10]
        if not d or d == "nan" or len(d) < 10:
            continue
        try:
            q = int(float(str(rec.get("주문수량") or 0).replace(",", "")))
        except (TypeError, ValueError):
            continue
        if q <= 0:
            continue
        e = days.setdefault(d, {"total": 0, "gift": 0, "returns": 0, "gift_items": {}, "items": {}, "listings": {},
                                "orders": 0, "buyers": 0, "repeat": 0, "amt": 0.0, "pay": 0.0, "disc": 0.0, "return_orders": 0})
        status = str(rec.get("주문상태") or "").strip()
        oid = str(rec.get("주문번호") or "").strip()
        if status != "정상":
            e["returns"] += q
            if oid and oid != "nan":
                ret_orders[d].add(oid)
            continue
        e["total"] += q
        nm = str(rec.get("상품명") or "").strip()
        e["items"][nm] = e["items"].get(nm, 0) + q          # 상품별 정상 주문수량 (사은품 포함) — 출고 집계의 원천
        pay = _num(rec.get("결제 금액")); e["amt"] += _num(rec.get("주문금액")); e["pay"] += pay; e["disc"] += _num(rec.get("할인금액"))
        code = rec.get("쇼핑몰 상품코드")
        code = "" if code is None or (isinstance(code, float) and code != code) else str(code).strip()
        if code.replace(".", "", 1).isdigit():
            code = str(int(float(code)))
        if code and code != "nan":
            le = e["listings"].setdefault(code, {"qty": 0, "pay": 0.0})
            le["qty"] += q; le["pay"] += pay
        if oid and oid != "nan":
            order_ids[d].add(oid)
        bid = str(rec.get("주문자ID") or "").strip()
        if bid and bid != "nan":
            buyer_ids[d].add(bid)
            if bid not in first_seen or d < first_seen[bid]:
                first_seen[bid] = d
        if "사은품" in str(rec.get("이슈") or ""):
            e["gift"] += q
            e["gift_items"][nm] = e["gift_items"].get(nm, 0) + q
    for d, e in days.items():
        e["orders"] = len(order_ids[d]); e["buyers"] = len(buyer_ids[d])
        e["repeat"] = sum(1 for bid in buyer_ids[d] if first_seen.get(bid, d) < d)
        e["return_orders"] = len(ret_orders[d])
        for k in ("amt", "pay", "disc"):
            e[k] = int(round(e[k]))
        for le in e["listings"].values():
            le["pay"] = int(round(le["pay"]))
    return days


# ── 스마트스토어 상품 목록 (판매자센터 > 상품관리 > 상품 목록 다운로드, Product_*.csv)
_PRODUCT_COLS = ["상품번호(스마트스토어)", "상품명", "판매상태", "재고수량", "판매가", "할인가"]
_BUNDLE_RE = re.compile(r"\+|세트|묶음|[xX×]\s*\d|\(\d+\s*개|증정|팩")   # 리스팅명으로 묶음·세트 판정


def _is_product_list(df) -> bool:
    return all(c in df.columns for c in _PRODUCT_COLS)


def _parse_product_list(df) -> dict:
    """→ {상품번호: {name, status(판매중/품절/판매중지/판매종료), display, stock(리스팅 재고수량), price, sale_price, updated, fulfil}}"""
    out = {}
    for r in df.itertuples(index=False):
        rec = dict(zip(df.columns, r))
        code = str(rec.get("상품번호(스마트스토어)") or "").strip()
        if not code or code == "nan":
            continue
        code = code.split(".")[0]
        out[code] = {"name": str(rec.get("상품명") or "").strip(), "status": str(rec.get("판매상태") or "").strip(),
                     "display": str(rec.get("전시상태") or "").strip(), "stock": int(_num(rec.get("재고수량"))),
                     "price": int(_num(rec.get("판매가"))), "sale_price": int(_num(rec.get("할인가"))),
                     "updated": str(rec.get("최종수정일") or "")[:19], "fulfil": str(rec.get("풀필먼트사") or "").strip()}
    return out


def _products() -> dict:
    return _load_json(PRODUCTS_PATH, {"items": {}, "filename": "", "uploaded_at": ""})


# ─────────────────────────────────────────────────────────────────
# 파싱
# ─────────────────────────────────────────────────────────────────
_REQ_COLS = ["거래ID", "거래구분", "거래처", "거래일시", "메모", "SKU", "제품명", "수량"]
_MEMO_DATE_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _order_date(memo: str, tx_dt: str) -> str:
    """메모 '# 스마트 스토어 (dj&a) 9월 21일' → 주문일. 없으면 거래일시 날짜.
    범위('3월 27일 ~ 29일')는 첫 날짜. 연도는 거래일시 기준, 12월 주문이 1월에 처리된 경우만 전년으로."""
    m = _MEMO_DATE_RE.search(memo or "")
    d0 = tx_dt[:10]
    if not m:
        return d0
    mo, dd = int(m.group(1)), int(m.group(2))
    y = int(d0[:4])
    if mo > int(d0[5:7]) + 6:
        y -= 1
    try:
        return date(y, mo, dd).isoformat()
    except ValueError:
        return d0


def _match_partner(name: str, kws: list) -> bool:
    n = _nkey(name)
    return bool(n) and all(_nkey(k) in n for k in kws if k.strip())


def _parse_transactions(data: bytes, kws: list) -> list:
    """BoxHero 거래 내역 xlsx → 거래처 필터를 통과한 출고/입고 행 목록."""
    df = pd.read_excel(io.BytesIO(data), sheet_name=0)
    missing = [c for c in _REQ_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"박스히어로 거래 내역 형식이 아닙니다 (없는 컬럼: {', '.join(missing)})")
    rows = []
    for r in df.itertuples(index=False):
        rec = dict(zip(df.columns, r))
        if not _match_partner(str(rec.get("거래처") or ""), kws):
            continue
        kind = str(rec.get("거래구분") or "").strip()
        if kind not in ("출고", "입고"):
            continue
        try:
            qty = int(float(rec.get("수량") or 0))
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        ts = rec.get("거래일시")
        tx_dt = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, (datetime, pd.Timestamp)) else str(ts)[:19]
        memo = "" if pd.isna(rec.get("메모")) else str(rec.get("메모"))
        sku = "" if pd.isna(rec.get("SKU")) else str(rec.get("SKU")).strip()
        name = "" if pd.isna(rec.get("제품명")) else str(rec.get("제품명")).strip()
        rows.append({
            "id": str(rec.get("거래ID")), "kind": kind, "partner": str(rec.get("거래처")), "tx_dt": tx_dt,
            "date": _order_date(memo, tx_dt), "memo": memo, "sku": sku, "name": name,
            # 출고는 음수로 찍혀 있음 → 판매량은 양수로, 입고(취소 보정)는 음수로 본다
            "qty": (abs(qty) if kind == "출고" else -abs(qty)),
        })
    return rows


def _merge(existing: list, new: list) -> tuple[list, int]:
    """(거래ID, SKU, 제품명, 수량) 키로 중복 제거 — 누적 내보내기를 다시 올려도 두 번 세지 않는다."""
    key = lambda r: (r["id"], r["sku"], r["name"], r["qty"], r["tx_dt"])
    seen = {key(r) for r in existing}
    added = 0
    for r in new:
        if key(r) not in seen:
            existing.append(r); seen.add(key(r)); added += 1
    existing.sort(key=lambda r: (r["date"], r["tx_dt"]))
    return existing, added


# ─────────────────────────────────────────────────────────────────
# 라벨 (제품명 → 짧은 이름)
# ─────────────────────────────────────────────────────────────────
_STRIP = [
    (re.compile(r"^\(\s*임박\s*\)\s*"), ""),
    (re.compile(r"^[A-Z]{2,4}\d{3,5}\s*(\[[^\]]*\])?\s*-?\s*"), ""),   # DJA4025- / DJA4087[GWP]
    (re.compile(r"디제이앤에이\s*|DJ&A\s*"), ""),
    (re.compile(r"시타케\s*머쉬룸"), "머쉬룸"),
    (re.compile(r"표고버섯\s*"), ""),
    (re.compile(r"베지\s+크리스프"), "베지크리스프"),
    (re.compile(r"\(리테일\)"), " 리테일"),
    (re.compile(r"\(스몰\)"), " 스몰"),
    (re.compile(r"\s*\[(\d+g)\]"), r" \1"),
    (re.compile(r"\s{2,}"), " "),
]


def _label(name: str, aliases: dict) -> str:
    if name in aliases and aliases[name].strip():
        return aliases[name].strip()
    s = name
    for rx, rep in _STRIP:
        s = rx.sub(rep, s)
    return s.strip() or name


# ─────────────────────────────────────────────────────────────────
# 집계
# ─────────────────────────────────────────────────────────────────
def _month_days(ym: str) -> int:
    return calendar.monthrange(int(ym[:4]), int(ym[5:7]))[1]


def _pct(cur: float, prev: float) -> Optional[int]:
    if not prev:
        return None
    return round((cur - prev) / prev * 100)


def _build_history(by_day: dict, month: str, n: int = 4, eff_to: str = "", order_days: Optional[dict] = None) -> list:
    """최근 n개월 월별 출고 수량 (주문일 기준, 취소 보정 입고 차감이 반영된 by_day 그대로).
    eff_to 가 있으면 그 날까지만 합산(이달은 자료 마지막 주문일·기준일 중 빠른 쪽). 자료가 한 날도 없는 달은 qty None.
    order_days(주문 관리 목록 일별 집계)가 있으면 그 달의 사은품 수량 gift 를 붙인다 — 주문 목록 자료가 없는 달은 None.
    사은품은 박스히어로 출고 총량(qty)에 이미 포함된 물량이라 따로 뺀 값이 아니다.
    [{ym, label, qty, gift, gift_total}]"""
    out = []
    order_days = order_days or {}
    for ym in _prev_months(month, n):
        days = [d for d in by_day if d[:7] == ym and (not eff_to or d <= eff_to)]
        odays = sorted(d for d in order_days if d[:7] == ym and (not eff_to or d <= eff_to))
        # 주문 목록이 그 달을 거의 다 덮을 때만 사은품 수치를 쓴다 (6/30 하루만 있는 파일로 '6월 사은품 8개' 가 나가지 않게):
        #   지난 달은 주문일이 20일 이상, 이달은 1~3일 사이에 시작하는 자료여야 함
        covered = bool(odays) and ((ym != month and len(odays) >= 20) or (ym == month and odays[0] <= ym + "-03"))
        out.append({"ym": ym, "label": f"{int(ym[5:7])}월", "qty": (sum(by_day[d] for d in days) if days else None),
                    "gift": (sum(order_days[d]["gift"] for d in odays) if covered else None),
                    "gift_total": (sum(order_days[d]["total"] for d in odays) if covered else None),   # 주문 목록 기준 전체(대조용)
                    "pay": (sum(order_days[d].get("pay", 0) for d in odays) if covered else None),      # 결제 금액(VAT 포함, 취소·반품 제외)
                    "order_days": len(odays), "gift_partial": bool(odays) and not covered})
    return out


def _build_summary(as_of: str, month: str, refresh: bool = False) -> dict:
    st = _state()
    aliases = st["aliases"]
    tx = _tx_all()
    kws = st["partner_kw"]

    # 출고 원천: 도착보장 주문 관리 목록(주문일별 상품별 정상 주문수량)이 있는 날은 그것을, 없는 날만 박스히어로 거래 내역을 쓴다.
    # 주문 목록은 담당자가 올리는 유일한 자료라 이것만으로 판단되어야 한다 — 거래 내역은 과거(주문 목록 이전) 보완용.
    order_days = {d: v for d, v in _orders().get("days", {}).items() if v.get("items")}
    out = [r for r in tx if r["kind"] == "출고" and r["date"] not in order_days]
    ret = [r for r in tx if r["kind"] == "입고" and r["date"] not in order_days]
    for r in tx:
        r["label"] = _label(r["name"], aliases)
    sku_by_name = {r["name"]: r["sku"] for r in tx if r.get("sku")}   # 주문 목록엔 바코드가 없어 거래 내역의 제품명→SKU 로 보완

    dates = sorted({r["date"] for r in out} | set(order_days))
    data_from, data_to = (dates[0], dates[-1]) if dates else ("", "")
    # 자료 마지막 주문일과 기준일 중 빠른 쪽까지를 '경과일'로 본다 (자료가 어제까지면 어제까지).
    # 주문 목록을 내보낸 당일의 주문은 오전 일부만 담기므로 그날은 미완료로 보고 하루 앞까지만 집계 창에 넣는다
    eff_to = min(as_of, data_to) if data_to else as_of
    partial_last = False
    ofiles = _orders().get("files") or []
    if data_to and ofiles and data_to in order_days:
        exported = max((f.get("uploaded_at") or "")[:10] for f in ofiles)
        if exported == data_to and data_to > (min(order_days) if order_days else data_to):
            partial_last = True
            eff_to = min(eff_to, (datetime.fromisoformat(data_to).date() - timedelta(days=1)).isoformat())
    prev_month = (datetime.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")

    by_day: dict = defaultdict(int)
    by_day_label: dict = defaultdict(lambda: defaultdict(int))
    for r in out:
        by_day[r["date"]] += r["qty"]
        by_day_label[r["date"]][r["label"]] += r["qty"]
    for r in ret:   # 취소 보정(입고)은 주문일에서 차감
        by_day[r["date"]] += r["qty"]
        by_day_label[r["date"]][r["label"]] += r["qty"]
    name_of_label: dict = defaultdict(set)
    for d, v in order_days.items():   # 주문 목록: 상품별 정상 주문수량 (반품·취소·교환은 애초에 제외됨)
        for nm, q in v["items"].items():
            lb = _label(nm, aliases)
            by_day[d] += q
            by_day_label[d][lb] += q
            name_of_label[lb].add(nm)

    def rng_sum(d1: str, d2: str, lb: Optional[str] = None) -> int:
        if lb is None:
            return sum(q for d, q in by_day.items() if d1 <= d <= d2)
        return sum(m.get(lb, 0) for d, m in by_day_label.items() if d1 <= d <= d2)

    # 이번 달 / 전월
    m_from, m_to = month + "-01", (eff_to if eff_to[:7] == month else month + f"-{_month_days(month):02d}")
    m_qty = rng_sum(m_from, m_to)
    m_elapsed = max(1, int(m_to[8:10])) if m_to[:7] == month and m_qty else _month_days(month)
    p_from, p_to = prev_month + "-01", prev_month + f"-{_month_days(prev_month):02d}"
    p_qty = rng_sum(p_from, p_to)
    m_avg = m_qty / m_elapsed if m_qty else 0
    p_avg = p_qty / _month_days(prev_month) if p_qty else 0

    # 최근 7일 vs 그 전 7일 (자료 마지막 주문일 기준)
    if data_to:
        e = datetime.fromisoformat(eff_to).date()
        w1_from, w1_to = (e - timedelta(days=6)).isoformat(), e.isoformat()
        w0_from, w0_to = (e - timedelta(days=13)).isoformat(), (e - timedelta(days=7)).isoformat()
    else:
        w1_from = w1_to = w0_from = w0_to = ""
    w1 = rng_sum(w1_from, w1_to) if data_to else 0
    w0 = rng_sum(w0_from, w0_to) if data_to else 0
    # 14일 창 (쿠팡 보고와 같은 기준): 최근 14일 vs 직전 14일
    if data_to:
        e = datetime.fromisoformat(eff_to).date()
        a14_from, a14_to = (e - timedelta(days=13)).isoformat(), e.isoformat()
        p14_from, p14_to = (e - timedelta(days=27)).isoformat(), (e - timedelta(days=14)).isoformat()
    else:
        a14_from = a14_to = p14_from = p14_to = ""
    q14_all = rng_sum(a14_from, a14_to) if data_to else 0
    p14_all = rng_sum(p14_from, p14_to) if data_to else 0
    w7_pay = sum(v.get("pay", 0) for d, v in order_days.items() if w1_from <= d <= w1_to) if data_to else 0
    w0_pay = sum(v.get("pay", 0) for d, v in order_days.items() if w0_from <= d <= w0_to) if data_to else 0

    # 주별 (최근 8주, 월~일)
    weeks = []
    if data_to:
        e = datetime.fromisoformat(eff_to).date()
        wk_end = e + timedelta(days=(6 - e.weekday()))
        for i in range(8):
            we = wk_end - timedelta(days=7 * i); ws = we - timedelta(days=6)
            q = rng_sum(ws.isoformat(), we.isoformat())
            weeks.append({"from": ws.isoformat(), "to": we.isoformat(), "md": f"{ws.month}/{ws.day}~{we.month}/{we.day}",
                          "qty": q, "partial": we > e})
        weeks.reverse()

    # 재고 (아워박스 가용 → 박스히어로 수량) — 제품명·SKU 로 매칭
    cfg = U.load_config()
    bh = _bh_stock_by_sku(cfg.get("api_token") or "", force=refresh)
    ob = _ob_available_by_name(force=refresh)
    ob_connected = bool(ob)
    sku_of_label: dict = defaultdict(set)
    for r in out:
        if r["sku"]:
            sku_of_label[r["label"]].add(r["sku"])
        name_of_label[r["label"]].add(r["name"])
    for lb, nms in name_of_label.items():
        for nm in nms:
            if sku_by_name.get(nm):
                sku_of_label[lb].add(sku_by_name[nm])

    def stock_of(lb: str) -> tuple[Optional[int], str]:
        if ob_connected:
            tot = None
            for nm in name_of_label[lb]:
                e = ob.get(_nkey(nm))
                if e is not None:
                    tot = (tot or 0) + int(e.get("available") or 0)
            if tot is not None:
                return tot, "ob"
        if bh:
            tot = None
            for sk in sku_of_label[lb]:
                e = bh.get(sk)
                if e is not None:
                    tot = (tot or 0) + int(e.get("qty") or 0)
            if tot is not None:
                return tot, "bh"
        return None, ""

    # 소비자 반응 근거 — 주문 관리 목록(신형 집계 키가 있는 날만) 창별 합계
    odays = {d: v for d, v in order_days.items() if "orders" in v}
    prods = _products(); pitems = prods.get("items") or {}

    def owin(d1: str, d2: str) -> dict:
        ds = [d for d in odays if d1 <= d <= d2]
        w = {k: sum(odays[d].get(k, 0) for d in ds) for k in ("total", "gift", "returns", "orders", "buyers", "repeat", "amt", "pay", "disc", "return_orders")}
        lq: dict = defaultdict(int)
        for d in ds:
            for c, le in odays[d].get("listings", {}).items():
                lq[c] += le["qty"]
        w["listings"] = dict(lq); w["days"] = len(ds)
        o = w["orders"]
        w["pay_per_order"] = round(w["pay"] / o) if o else None
        w["qty_per_order"] = round(w["total"] / o, 1) if o else None
        w["disc_rate"] = round(w["disc"] / w["amt"] * 100, 1) if w["amt"] else None
        w["repeat_rate"] = round(w["repeat"] / w["buyers"] * 100, 1) if w["buyers"] else None
        w["return_rate"] = round(w["returns"] / (w["total"] + w["returns"]) * 100, 1) if (w["total"] + w["returns"]) else None
        w["gift_share"] = round(w["gift"] / w["total"] * 100, 1) if w["total"] else None
        bt = sum(lq.values())
        w["bundle_share"] = (round(sum(q for c, q in lq.items() if c in pitems and _BUNDLE_RE.search(pitems[c]["name"])) / bt * 100, 1)
                             if (bt and pitems) else None)
        return w

    if data_to:
        e28 = datetime.fromisoformat(eff_to).date()
        w28 = owin((e28 - timedelta(days=27)).isoformat(), eff_to)
        stockout = sorted([{"code": c, "name": pitems[c]["name"], "status": pitems[c]["status"], "qty": q}
                           for c, q in w28["listings"].items() if c in pitems and pitems[c]["status"] in ("품절", "판매중지", "판매종료")],
                          key=lambda x: -x["qty"])
    else:
        w28 = None; stockout = []
    st_cnt: dict = defaultdict(int)
    for v in pitems.values():
        st_cnt[v["status"]] += 1
    behavior = {
        "has_detail": bool(odays), "has_products": bool(pitems),
        "cur": owin(a14_from, a14_to) if data_to else None, "prev": owin(p14_from, p14_to) if data_to else None,
        "month": owin(m_from, m_to), "prev_month": owin(p_from, p_to),
        "stockout": stockout[:5], "stockout_count": len(stockout), "stockout_qty": sum(x["qty"] for x in stockout),
        "stockout_share": (round(sum(x["qty"] for x in stockout) / sum(w28["listings"].values()) * 100) if (w28 and sum(w28["listings"].values())) else None),
        "listing_status": dict(st_cnt), "products_at": prods.get("uploaded_at", ""), "products_file": prods.get("filename", ""),
    }

    # 품목별
    labels = sorted({r["label"] for r in out} | set(name_of_label), key=lambda lb: -rng_sum(m_from, m_to, lb))
    rows = []
    for lb in labels:
        mq = rng_sum(m_from, m_to, lb); pq = rng_sum(p_from, p_to, lb)
        q14 = rng_sum(a14_from, a14_to, lb) if data_to else 0
        avg14 = q14 / 14
        p14 = rng_sum(p14_from, p14_to, lb) if data_to else 0
        avg14_prev = p14 / 14
        stock, basis = stock_of(lb)
        dos = (round(stock / avg14, 1) if (stock is not None and avg14 > 0) else None)
        spark = [rng_sum(w["from"], w["to"], lb) for w in weeks]
        rows.append({"label": lb, "names": sorted(name_of_label[lb]), "skus": sorted(sku_of_label[lb]),
                     "month_qty": mq, "prev_qty": pq,
                     "pct": _pct(mq / m_elapsed if mq else 0, pq / _month_days(prev_month) if pq else 0),
                     "avg14": round(avg14, 1), "avg14_prev": round(avg14_prev, 1), "pct14": _pct(avg14, avg14_prev),
                     "w1": rng_sum(w1_from, w1_to, lb) if data_to else 0,
                     "stock": stock, "stock_basis": basis, "days_of_supply": dos, "spark": spark})
    rows = [r for r in rows if r["month_qty"] or r["prev_qty"] or r["w1"]]

    # 회차 (이번 달 거래ID 수) · 마지막 출고
    m_batches = len({r["id"] for r in out if m_from <= r["date"] <= m_to})
    last_day = data_to
    last_day_qty = by_day.get(last_day, 0) if last_day else 0
    last_day_top = _top([{"label": lb, "qty": q} for lb, q in sorted(by_day_label.get(last_day, {}).items(), key=lambda kv: -kv[1])], "qty", 2) if last_day else ""
    m_ret = -sum(r["qty"] for r in ret if m_from <= r["date"] <= m_to)         + sum(v["returns"] for d, v in order_days.items() if m_from <= d <= m_to)   # 주문 목록의 반품·취소·교환 (출고에 미포함)

    summary = {
        "as_of": as_of, "as_of_md": f"{int(as_of[5:7])}/{int(as_of[8:10])}", "month": month, "month_label": f"{int(month[5:7])}월",
        "prev_month_label": f"{int(prev_month[5:7])}월", "prev_month": prev_month,
        "prev2_month": (datetime.fromisoformat(prev_month + "-01") - timedelta(days=1)).strftime("%Y-%m"),
        "partner_kw": kws, "partners": sorted({r["partner"] for r in tx}),
        "data_from": data_from, "data_to": data_to, "eff_to": eff_to, "tx_rows": len(tx),
        "stale_days": ((datetime.fromisoformat(as_of).date() - datetime.fromisoformat(data_to).date()).days if data_to else None),
        "month_qty": m_qty, "month_elapsed": m_elapsed, "month_avg": round(m_avg, 1), "month_batches": m_batches, "month_returns": m_ret,
        "prev_qty": p_qty, "prev_avg": round(p_avg, 1), "month_pct": _pct(m_avg, p_avg),
        "w1": w1, "w0": w0, "w1_avg": round(w1 / 7, 1), "w1_pct": _pct(w1, w0), "w1_from": w1_from, "w1_to": w1_to,
        "a14_from": a14_from, "a14_to": a14_to, "q14": q14_all, "p14": p14_all, "partial_last": partial_last,
        "w1_pay": w7_pay, "w0_pay": w0_pay,
        "avg14": round(q14_all / 14, 1), "avg14_prev": round(p14_all / 14, 1), "pct14": _pct(q14_all, p14_all),
        "last_day": last_day, "last_day_md": (f"{int(last_day[5:7])}/{int(last_day[8:10])}" if last_day else ""),
        "last_day_qty": last_day_qty, "last_day_top": last_day_top,
        "weeks": weeks, "rows": rows, "behavior": behavior,
        "month_pay": behavior["month"]["pay"], "prev_month_pay": behavior["prev_month"]["pay"],
        "q14_pay": (behavior["cur"] or {}).get("pay", 0), "p14_pay": (behavior["prev"] or {}).get("pay", 0),
        "stock_basis": ("ob" if ob_connected else ("bh" if bh else "")), "ob_connected": ob_connected, "bh_connected": bool(bh),
        "uploads": st["uploads"], "notes": st.get("notes", ""),
        "daily": [{"date": d, "qty": by_day[d]} for d in sorted(by_day) if d >= (datetime.fromisoformat(eff_to).date() - timedelta(days=59)).isoformat()] if data_to else [],
        "history": _build_history(by_day, month, eff_to=eff_to, order_days=_orders().get("days")),
    }
    summary["gift_month"] = next((h["gift"] for h in summary["history"] if h["ym"] == month), None)
    summary["order_days"] = len(order_days)
    summary["order_from"], summary["order_to"] = (min(order_days), max(order_days)) if order_days else ("", "")
    summary["has_orders"] = bool(_orders().get("days"))
    summary["order_files"] = _orders().get("files", [])
    summary["text"] = _render_text(summary)
    summary["text_exec"] = _render_text_exec(summary)
    return summary


# ─────────────────────────────────────────────────────────────────
# 보고문 (슬랙 본문용 — 담당자가 직접 쓰던 양식)
# ─────────────────────────────────────────────────────────────────
def _pct_txt(tp) -> str:
    if tp is None:
        return ""
    return f"({'+' if tp > 0 else ''}{tp:g}%)"


def _render_text(s: dict) -> str:
    ml = s["month_label"]; pl = s["prev_month_label"]
    L = [f"[네이버] 안녕하세요, {s['as_of_md']} 기준 네이버 스마트스토어(도착보장) 출고 현황 공유드립니다.", "", "■ 요약"]
    if not s["data_to"]:
        L.append("* 박스히어로 거래 내역이 아직 업로드되지 않았습니다")
        return "\n".join(L)
    if s["month_qty"]:
        L.append(f"* {ml} 출고 {_n(s['month_qty'])}개" + (f" · 결제 {_won(s['month_pay'])}" if s.get("month_pay") else "") + f" · 일평균 {_n(round(s['month_avg']))}개"
                 + (f" ({pl} 일평균 {_n(round(s['prev_avg']))}개 대비 {_pct_txt(s['month_pct'])[1:-1]})" if s["prev_avg"] and s["month_pct"] is not None else ""))
    else:
        L.append(f"* {ml} 출고 없음")
    # 지난달 마감 — 출고 수량 · 전월 대비 · 사은품 (월별 추이 history 기준)
    hist = {h["ym"]: h for h in (s.get("history") or [])}
    ph = hist.get(s["prev_month"]); pph = hist.get(s["prev2_month"])
    if ph and ph.get("qty"):
        pct = f" · 전월 대비 {_pct_txt(_pct(ph['qty'], pph['qty']))[1:-1]}" if (pph and pph.get("qty")) else ""
        gift = f" · 사은품 {_n(ph['gift'])}개 포함" if ph.get("gift") else ""
        pay = ""
        if ph.get("pay"):
            pp = (pph or {}).get("pay")
            pay = f" · 결제 {_won(ph['pay'])}" + (f" ({_pct_txt(_pct(ph['pay'], pp))[1:-1]})" if pp else "")
        L.append(f"* {ph['label']} 마감 — 출고 {_n(ph['qty'])}개{pct}{pay}{gift}")
    if s.get("q14"):
        rng = f"{int(s['a14_from'][5:7])}/{int(s['a14_from'][8:10])}~{int(s['a14_to'][5:7])}/{int(s['a14_to'][8:10])}"
        L.append(f"* 최근 14일({rng}) 일평균 {_n(round(s['avg14']))}개" + (f" · 직전 14일 대비 {_pct_txt(s['pct14'])}" if s["pct14"] is not None else "")
                 + (f" · 결제 {_won(s['q14_pay'])}" + (f" ({_pct_txt(_pct(s['q14_pay'], s['p14_pay']))[1:-1]})" if s.get("p14_pay") else "") if s.get("q14_pay") else ""))
    if s.get("w1"):
        rng7 = f"{int(s['w1_from'][5:7])}/{int(s['w1_from'][8:10])}~{int(s['w1_to'][5:7])}/{int(s['w1_to'][8:10])}"
        L.append(f"* 최근 7일({rng7}) 출고 {_n(s['w1'])}개" + (f" · 직전 7일 대비 {_pct_txt(s['w1_pct'])}" if s["w1_pct"] is not None else "")
                 + (f" · 결제 {_won(s['w1_pay'])}" + (f" ({_pct_txt(_pct(s['w1_pay'], s['w0_pay']))[1:-1]})" if s.get("w0_pay") else "") if s.get("w1_pay") else ""))
    if s["last_day"]:
        L.append(f"* 마지막 주문일 {s['last_day_md']} — {_n(s['last_day_qty'])}개" + (f" · {s['last_day_top']}" if s["last_day_top"] else "")
                 + (" (내보낸 당일 · 미완료라 평균에서 제외)" if s.get("partial_last") else ""))
    if s.get("gift_month"):
        L.append(f"* {ml} 사은품 출고 {_n(s['gift_month'])}개 (위 출고에 포함 · 주문 관리 목록 기준)")
    if s["month_returns"]:
        L.append(f"* {ml} 반품·취소·교환 {_n(s['month_returns'])}개 (출고에서 제외)")
    if s.get("stale_days") and s["stale_days"] >= 2:
        L.append(f"* 자료는 {s['last_day_md']} 주문분까지 반영 (도착보장 주문 관리 목록 재업로드 시 갱신)")

    # 품목별 — 최근 14일 일평균 상위 6종, 쿠팡·올영과 같은 한 줄: 일 N개 (직전 14일 대비) · 재고 N개 (며칠분)
    rows = sorted([r for r in s["rows"] if r["avg14"] > 0], key=lambda r: -r["avg14"])[:6]
    if rows:
        basis = "아워박스 가용재고" if s["stock_basis"] == "ob" else "박스히어로 재고"
        rng = f"{int(s['a14_from'][5:7])}/{int(s['a14_from'][8:10])}~{int(s['a14_to'][5:7])}/{int(s['a14_to'][8:10])}"
        L += ["", f"■ 네이버 판매·재고 ({rng} 14일 평균 · 직전 14일 대비 · {basis})"]
        for r in rows:
            line = f"* {r['label']} — 일 {_n(round(r['avg14']))}개 {_pct_txt(r['pct14'])}".rstrip()
            st = r.get("stock"); d = r.get("days_of_supply")
            if st is not None:
                if st < 0 or d is None:
                    line += f" · 재고 {_n(st)}개 (확인 필요)"
                else:
                    line += f" · 재고 {_n(st)}개 (" + ("90일 이상" if d > 90 else f"{d:g}일분") + (" · 부족" if d < 7 else "") + ")"
            L.append(line)

    # 소비자 반응 근거 — 최근 14일 vs 직전 14일 (주문 관리 목록의 주문 건수·구매자·결제·할인·반품 + 상품 목록의 리스팅 상태)
    bh = s.get("behavior") or {}
    cur, prev = bh.get("cur"), bh.get("prev")
    if bh.get("has_detail") and cur and cur.get("orders"):
        rng = f"{int(s['a14_from'][5:7])}/{int(s['a14_from'][8:10])}~{int(s['a14_to'][5:7])}/{int(s['a14_to'][8:10])}"
        L += ["", f"■ 소비자 반응 근거 ({rng} vs 직전 14일 · 도착보장 주문 관리 목록)"]
        pv = prev or {}

        def vs(cur_v, prev_v, fmt=lambda v: f"{v:g}%"):
            return (f" (직전 {fmt(prev_v)})" if prev_v is not None else "")

        L.append(f"* 주문 {_n(cur['orders'])}건 {_pct_txt(_pct(cur['orders'], pv.get('orders')))}".rstrip()
                 + f" · 구매자 {_n(cur['buyers'])}명"
                 + (f" · 재구매 {cur['repeat_rate']:g}%" + vs(cur['repeat_rate'], pv.get('repeat_rate')) if cur.get("repeat_rate") is not None else ""))
        L.append(f"* 객단가 {_n(cur['pay_per_order'])}원" + vs(cur['pay_per_order'], pv.get('pay_per_order'), lambda v: f"{_n(v)}원")
                 + f" · 건당 {cur['qty_per_order']:g}개" + vs(cur['qty_per_order'], pv.get('qty_per_order'), lambda v: f"{v:g}개")
                 + (f" · 할인율 {cur['disc_rate']:g}%" + vs(cur['disc_rate'], pv.get('disc_rate')) if cur.get("disc_rate") is not None else ""))
        rline = f"* 반품·취소·교환 {_n(cur['return_orders'])}건 · {_n(cur['returns'])}개"
        if cur.get("return_rate") is not None:
            rline += f" ({cur['return_rate']:g}%" + (f", 직전 {pv['return_rate']:g}%" if pv.get("return_rate") is not None else "") + ")"
        if cur.get("gift_share"):
            rline += f" · 사은품 비중 {cur['gift_share']:g}%" + vs(cur['gift_share'], pv.get('gift_share'))
        L.append(rline)
        if cur.get("bundle_share") is not None:
            L.append(f"* 묶음·세트 비중 {cur['bundle_share']:g}% (수량 기준)" + vs(cur['bundle_share'], pv.get('bundle_share')))
        if bh.get("has_products"):
            ls = bh.get("listing_status") or {}
            so = bh.get("stockout") or []
            line = f"* 리스팅 판매중 {ls.get('판매중', 0)} · 품절 {ls.get('품절', 0)} · 판매중지 {ls.get('판매중지', 0)}"
            if so:
                short = lambda nm: re.sub(r"디제이앤에이\s*|영양 건강 간식 야채칩\s*|아보카도오일 팝콘\s*", "", nm).strip()[:24]
                share = f", 28일 판매의 {bh['stockout_share']}%" if bh.get("stockout_share") else ""
                line += f" — 최근 28일 판매 있던 품절·중지 {bh['stockout_count']}개 ({_n(bh['stockout_qty'])}개{share}): " + " · ".join(f"{short(x['name'])} {_n(x['qty'])}" for x in so[:3])
            L.append(line)
        # 판정 — 숫자에서 바로 읽히는 것만
        hints = []
        op = _pct(cur["orders"], pv.get("orders")); pp = _pct(cur.get("pay_per_order") or 0, pv.get("pay_per_order") or 0)
        if op is not None and op <= -15 and pp is not None and abs(pp) <= 10:
            hints.append("주문 건수 감소가 주도, 객단가는 유지 → 유입·전환 약화")
        elif op is not None and op >= 15 and pp is not None and abs(pp) <= 10:
            hints.append("주문 건수 증가가 주도, 객단가 유지 → 유입 확대")
        if cur.get("disc_rate") is not None and pv.get("disc_rate") is not None and cur["disc_rate"] - pv["disc_rate"] <= -3:
            hints.append(f"할인율 축소({pv['disc_rate']:g}% → {cur['disc_rate']:g}%)와 겹침")
        if cur.get("disc_rate") is not None and pv.get("disc_rate") is not None and cur["disc_rate"] - pv["disc_rate"] >= 2 and op is not None and op <= -15:
            hints.append(f"할인율은 커졌는데({pv['disc_rate']:g}% → {cur['disc_rate']:g}%) 주문은 줄어 가격보다 유입 문제")
        if cur.get("repeat_rate") is not None and pv.get("repeat_rate") is not None and cur["repeat_rate"] - pv["repeat_rate"] >= 3:
            hints.append(f"재구매 비중 상승({pv['repeat_rate']:g}% → {cur['repeat_rate']:g}%) → 신규 구매자 유입 감소")
        if bh.get("stockout_qty"):
            hints.append(f"품절·판매중지 리스팅이 최근 28일 판매의 {bh.get('stockout_share') or 0}%({_n(bh['stockout_qty'])}개) → 행사 종료·품절에 따른 기회손실")
        if hints:
            L.append("* 판정: " + " / ".join(hints))

    # 주별 추이 (최근 6주)
    wk = [w for w in s["weeks"] if w["qty"]][-6:]
    if len(wk) >= 2:
        L += ["", "■ 주별 출고 (최근 6주)"]
        for w in wk:
            L.append(f"* {w['md']} — {_n(w['qty'])}개" + (" (진행 중)" if w["partial"] else ""))

    notes = [ln.strip().lstrip("*-•■ ").strip() for ln in (s.get("notes") or "").splitlines() if ln.strip()]
    if notes:
        L += ["", "■ 특이사항"] + [f"* {ln}" for ln in notes]
    L += ["", "참고로만 공유드립니다. 감사합니다."]
    return "\n".join(L)


def _render_text_exec(s: dict, idx: Optional[int] = None) -> str:
    """경영 요약(채널 묶음) 한 블록 —
        4. 네이버 스마트스토어(도착보장) — 출고 6월 X → 7월 Y → 8월 Z개 → 9월(W / 진행중)

        9월 출고 W개 · 일평균 N개 (전월 일평균 대비 +x%) · 9/20 주문분까지
    발주·잔량 개념이 없으므로 미납 줄은 없다. 값이 None 인 달은 건너뛰고, 지난 달 마지막 항목 뒤에만 '개'."""
    ml = s["month_label"]; pl = s["prev_month_label"]; month = s["month"]
    hist = s.get("history") or []
    cur = int(s.get("month_qty") or 0)
    in_progress = (month == (s.get("as_of") or "")[:7])

    past = [h for h in hist if h["ym"] != month and h.get("qty") is not None]
    parts = [f"{h['label']} {_n(h['qty'])}" for h in past]
    if parts:
        parts[-1] += "개"
    cur_txt = f"{ml}({_n(cur)}"
    if in_progress:
        cur_txt += " / 진행중"
        prev_q = int(s.get("prev_qty") or 0)
        # 아직 전월보다 적지만 일평균 × 월 일수로 전월 합계를 넘을 것으로 보이면
        if prev_q and cur < prev_q and (s.get("month_avg") or 0) * _month_days(month) >= prev_q:
            cur_txt += f", {pl} 실적 초과예상"
    cur_txt += ")"
    head = f"{EXEC_NAME} — 출고 " + " → ".join(parts + [cur_txt]) if parts else f"{EXEC_NAME} — {ml} 출고 {cur_txt}"
    L = [f"{idx}. {head}" if idx else head, ""]

    if cur:
        line = f"{ml} 출고 {_n(cur)}개 · 일평균 {_n(round(s.get('month_avg') or 0))}개"
        tp = s.get("month_pct")
        if tp is not None and s.get("prev_avg"):
            line += f" (전월 일평균 대비 {'+' if tp > 0 else ''}{tp:g}%)"
        if (s.get("stale_days") or 0) >= 2 and s.get("last_day_md"):
            line += f" · {s['last_day_md']} 주문분까지"
        L.append(line)
    else:
        L.append(f"{ml} 출고 없음" + ("" if s.get("data_to") else " (박스히어로 거래 내역 미업로드)"))
    # 사은품 월별 별도 표기 — 주문 관리 목록(manageList) 이 올라온 달만. 위 출고 수량에 포함된 물량이다.
    gifts = [(h["label"], h["gift"]) for h in hist if h.get("gift") is not None]
    if gifts:
        gtxt = " → ".join(f"{lb} {_n(g)}" for lb, g in gifts) + "개"
        L.append(f"사은품 출고 {gtxt} (위 출고에 포함 · 별도 표기)")
    return "\n".join(L)


def build_exec_block(as_of: str, month: str, idx: Optional[int] = None) -> dict:
    """channel_report.build_exec_text 의 extra_blocks 용 — {key, name, text, history, error}. 실패해도 예외를 내지 않는다."""
    month = (month or as_of[:7])[:7]
    try:
        s = _build_summary(as_of, month)
        return {"key": "naver", "name": EXEC_NAME, "text": _render_text_exec(s, idx=idx), "history": s.get("history"), "error": ""}
    except Exception as e:
        return {"key": "naver", "name": EXEC_NAME, "text": f"{idx}. {EXEC_NAME} — 집계 실패 ({str(e)[:80]})" if idx else f"{EXEC_NAME} — 집계 실패 ({str(e)[:80]})",
                "history": None, "error": str(e)[:200]}


build_exec_block.exec_key = "naver"     # build_exec_text 가 예외 블록을 만들 때 쓰는 식별자
build_exec_block.exec_name = EXEC_NAME


# ─────────────────────────────────────────────────────────────────
# 라우터
# ─────────────────────────────────────────────────────────────────
class SettingsIn(BaseModel):
    partner_kw: Optional[list[str]] = None
    notes: Optional[str] = None


class AliasIn(BaseModel):
    aliases: dict


@router.get("/summary")
def get_summary(as_of: str = "", month: str = "", refresh: int = 0):
    as_of = (as_of or date.today().isoformat())[:10]
    try:
        datetime.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(400, "as_of 형식 오류 (YYYY-MM-DD)")
    month = (month or as_of[:7])[:7]
    return _build_summary(as_of, month, refresh=bool(refresh))


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...)):
    """박스히어로 거래 내역 xlsx 업로드. 거래처 키워드로 걸러 저장(중복 제거)."""
    st = _state()
    tx = _tx_all()
    added_files, errors = [], []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    orders = _orders()
    for f in files:
        data = await f.read()
        # 1) 도착보장 주문 관리 목록(manageList_*.xlsx) — 사은품 분리용. 같은 날짜 범위는 새 파일로 교체(재업로드 중복 방지)
        low = (f.filename or "").lower()
        try:
            if low.endswith(".csv"):
                try:
                    df0 = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig")
                except UnicodeDecodeError:
                    df0 = pd.read_csv(io.BytesIO(data), encoding="cp949")
            else:
                df0 = pd.read_excel(io.BytesIO(data), sheet_name=0)
        except Exception as e:
            errors.append(f"{f.filename}: 읽기 오류 {str(e)[:80]}")
            continue
        # 0) 스마트스토어 상품 목록 (Product_*.csv) — 리스팅 상태·가격·재고. 통째로 교체
        if _is_product_list(df0):
            items = _parse_product_list(df0)
            if not items:
                errors.append(f"{f.filename}: 상품 행이 없습니다")
                continue
            _save_json(PRODUCTS_PATH, {"items": items, "filename": f.filename, "uploaded_at": now})
            st["uploads"].append({"filename": f.filename, "uploaded_at": now, "rows": len(items), "added": len(items), "type": "products"})
            cnt = defaultdict(int)
            for v in items.values():
                cnt[v["status"]] += 1
            added_files.append({"filename": f.filename, "rows": len(items), "added": len(items), "type": "products", "status": dict(cnt)})
            continue
        if _is_order_list(df0):
            days = _parse_order_list(df0)
            if not days:
                errors.append(f"{f.filename}: 주문 행이 없습니다")
                continue
            ds = sorted(days)
            for d in [d for d in orders["days"] if ds[0] <= d <= ds[-1]]:
                del orders["days"][d]
            orders["days"].update(days)
            gift_sum = sum(v["gift"] for v in days.values()); tot = sum(v["total"] for v in days.values())
            orders["files"] = (orders["files"] + [{"filename": f.filename, "uploaded_at": now, "from": ds[0], "to": ds[-1],
                                                    "total": tot, "gift": gift_sum}])[-20:]
            st["uploads"].append({"filename": f.filename, "uploaded_at": now, "rows": int(len(df0)), "added": gift_sum,
                                  "from": ds[0], "to": ds[-1], "type": "orders"})
            added_files.append({"filename": f.filename, "rows": int(len(df0)), "added": gift_sum, "from": ds[0], "to": ds[-1],
                                "type": "orders", "total": tot, "gift": gift_sum})
            continue
        # 2) 박스히어로 거래 내역
        try:
            rows = _parse_transactions(data, st["partner_kw"])
        except Exception as e:
            errors.append(f"{f.filename}: {str(e)[:120]}")
            continue
        if not rows:
            errors.append(f"{f.filename}: '{' + '.join(st['partner_kw'])}' 거래처 행이 없습니다")
            continue
        tx, added = _merge(tx, rows)
        ds = sorted(r["date"] for r in rows)
        st["uploads"].append({"filename": f.filename, "uploaded_at": now, "rows": len(rows), "added": added, "from": ds[0], "to": ds[-1]})
        added_files.append({"filename": f.filename, "rows": len(rows), "added": added, "from": ds[0], "to": ds[-1]})
    st["uploads"] = st["uploads"][-20:]
    _save_json(TX_PATH, tx)
    _save_json(ORDERS_PATH, orders)
    _save_json(STATE_PATH, st)
    return {"added": added_files, "errors": errors, "tx_rows": len(tx), "order_days": len(orders["days"])}


@router.post("/upload/clear")
def clear_uploads():
    st = _state()
    st["uploads"] = []
    _save_json(STATE_PATH, st)
    _save_json(TX_PATH, [])
    _save_json(ORDERS_PATH, {"days": {}, "files": []})
    _save_json(PRODUCTS_PATH, {"items": {}, "filename": "", "uploaded_at": ""})
    return {"ok": True}


@router.get("/settings")
def get_settings():
    st = _state()
    return {"partner_kw": st["partner_kw"], "notes": st.get("notes", ""), "aliases": st["aliases"]}


@router.put("/settings")
def put_settings(body: SettingsIn):
    st = _state()
    if body.partner_kw is not None:
        kws = [k.strip() for k in body.partner_kw if k and k.strip()]
        if not kws:
            raise HTTPException(400, "거래처 키워드는 1개 이상 필요합니다")
        st["partner_kw"] = kws
    if body.notes is not None:
        st["notes"] = body.notes
    _save_json(STATE_PATH, st)
    return {"partner_kw": st["partner_kw"], "notes": st.get("notes", "")}


@router.get("/sku-daily")
def get_sku_daily():
    """SKU 출고 추이 화면용 — 네이버 도착보장 출고를 SKU(바코드)별·일자별로 집계.
    출고는 +, 취소 보정(입고)은 -. 라벨은 별칭이 있으면 별칭, 없으면 자동 축약명."""
    st = _state()
    aliases = st["aliases"]
    items: dict = {}
    for r in _tx_all():
        if r.get("kind") not in ("출고", "입고"):
            continue
        sku = str(r.get("sku") or "").strip() or str(r.get("name") or "").strip()
        if not sku:
            continue
        e = items.setdefault(sku, {"sku": sku, "name": _label(str(r.get("name") or ""), aliases), "raw_name": str(r.get("name") or ""), "by_date": {}})
        d = str(r.get("date") or "")[:10]
        q = int(r.get("qty") or 0)
        if r["kind"] == "입고":
            q = -abs(q)
        e["by_date"][d] = e["by_date"].get(d, 0) + q
    out = []
    for e in items.values():
        e["by_date"] = {d: q for d, q in e["by_date"].items() if q > 0}
        e["total"] = sum(e["by_date"].values())
        if e["total"] > 0:
            out.append(e)
    out.sort(key=lambda x: -x["total"])
    return {"channel": "네이버", "items": out}


@router.get("/aliases")
def get_aliases():
    st = _state()
    names = sorted({r["name"] for r in _tx_all()})
    return {"defaults": {nm: _label(nm, {}) for nm in names}, "overrides": st["aliases"]}


@router.put("/aliases")
def put_aliases(body: AliasIn):
    st = _state()
    st["aliases"] = {k: str(v).strip() for k, v in (body.aliases or {}).items() if str(v).strip()}
    _save_json(STATE_PATH, st)
    return {"ok": True}
