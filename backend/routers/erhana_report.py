"""이알하나 통합 보고 — 채널을 나누지 않고 한 화면에.

출고: 아워박스 MATE 출고 실적(out_perf_period)을 채널(channel)별로 — 스마트스토어(도착보장)·홈쇼핑·쿠팡·카페24·지마켓·면세점 …
      내부 이동(전산처리·세트구성)은 뺀다.
재고: 아워박스 재고(product_stock) 가용재고. 재고 일수 = 가용재고 ÷ 이달 일평균 출고.
발주: 발주 캘린더의 컬리 거래명세서 중 이알하나 행(컬리 보고에서는 빼는 품목) — 납기 미도래 발주 대기.
보고문: DJ&A 경영진보고와 같은 짧은 슬랙 양식. 제목·한줄정리·의견은 data/erhana_report_state.json.
"""
from __future__ import annotations

import os, sys, json, time, html, copy
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import utils_core as U
from routers.channel_report import (_n, _won, _pct_of, _prev_months, _md, _month_end, sku_windows, _channel_rows, BRIEF_EMOJI)
from routers.kurly_report import kurly_exclude, SPEC as KURLY_SPEC

router = APIRouter()

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_STATE = os.path.join(_DATA, "erhana_report_state.json")
_CACHE = os.path.join(_DATA, "erhana_ob_out_cache.json")
_TTL = 60 * 60          # 아워박스 출고 재조회 간격(초) — '새로고침'은 무시하고 다시 받는다

KW = ("이알하나", "이알히나", "ERr731", "ERR731", "ERr-731", "멜라토닌", "NMN", "메노포즈")
_SKIP_CH = ("전산처리", "세트구성")       # 내부 이동 — 출고 아님


def is_erhana(name: str, code: str = "") -> bool:
    t = f"{name} {code}"
    return any(k.lower() in t.lower() for k in KW) or str(code or "").strip().startswith("880613602")


def _product_label(nm: str) -> str:
    s = html.unescape(str(nm or "")).strip()
    for a, b in (("★[선물세트]★ 이알하나_", "선물세트 "), ("★[선물세트]★", "선물세트"), ("홈쇼핑(공통)_", "홈쇼핑 "), ("[이알하나] ", ""), ("이알하나_", "")):
        s = s.replace(a, b)
    return s.strip(" _")


def _channel_label(ch: str, mall: str = "") -> str:
    s = str(ch or mall or "").strip() or "기타"
    if s.startswith("홈쇼핑"):
        return "홈쇼핑"
    if s.startswith("쿠팡"):
        return "쿠팡"
    if "스마트스토어" in s:
        return "스마트스토어(도착보장)"
    if s.startswith("지마켓"):
        return "지마켓"
    if s.startswith("샘플") or s.startswith("행사"):
        return "샘플·행사"
    return s.split("_")[0] if "_" in s else s


# ── 저장 ─────────────────────────────────────────────────────────
class Notes(BaseModel):
    title: str = ""        # '' = 기본 '이알하나 출고·발주 현황'
    headline: str = ""     # 한줄정리 (줄마다 한 항목). '' = 자동
    opinion: str = ""      # 의견. '' = 자동
    updated_at: str = ""


def _load_notes() -> dict:
    try:
        if os.path.exists(_STATE):
            with open(_STATE, "r", encoding="utf-8") as f:
                return Notes(**json.load(f)).model_dump()
    except Exception:
        pass
    return Notes().model_dump()


def _save_notes(n: Notes) -> dict:
    d = n.model_dump(); d["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(_DATA, exist_ok=True)
    with open(_STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


# ── 아워박스 ──────────────────────────────────────────────────────
def _client():
    import ourbox_api as api_mod
    c = api_mod.make_client(U.load_config())
    if not c:
        raise RuntimeError("아워박스 API 미설정 (설정에서 access/secret key 확인)")
    return c


def _ob_outbounds(frm: str, to: str, refresh: bool = False) -> tuple[list, dict]:
    """이알하나 출고 행 [{date, name, label, qty, channel, ch}] — 파일 캐시(기간이 덮이고 TTL 안이면 재사용)."""
    meta = {"from": frm, "to": to, "fetched_at": "", "cached": False, "error": ""}
    try:
        if os.path.exists(_CACHE) and not refresh:
            with open(_CACHE, "r", encoding="utf-8") as f:
                c = json.load(f)
            if c.get("from") <= frm and c.get("to") >= to and time.time() - float(c.get("ts") or 0) < _TTL:
                meta.update({"fetched_at": c.get("fetched_at", ""), "cached": True})
                return [r for r in c["rows"] if frm <= r["date"] <= to], meta
    except Exception:
        pass
    raw = _client().fetch_outbounds(frm, to)
    rows = []
    for r in raw:
        nm = html.unescape(str(r.get("product_name") or ""))
        if not is_erhana(nm, str(r.get("product_code") or "")):
            continue
        ch = str(r.get("channel") or r.get("mall_name") or "")
        if any(k in ch for k in _SKIP_CH):
            continue
        dt = str(r.get("out_dt") or r.get("out_complete_dt") or "")[:10]
        if not dt:
            continue
        rows.append({"date": dt, "name": nm, "label": _product_label(nm), "qty": int(float(r.get("out_qty") or 0)),
                     "channel": ch, "ch": _channel_label(ch, str(r.get("mall_name") or ""))})
    meta["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(_CACHE, "w", encoding="utf-8") as f:
            json.dump({"from": frm, "to": to, "ts": time.time(), "fetched_at": meta["fetched_at"], "rows": rows}, f, ensure_ascii=False)
    except Exception:
        pass
    return rows, meta


def _ob_stock() -> tuple[dict, str]:
    """{label: {available, total}} 이알하나만"""
    try:
        raw = _client().fetch_stock()
    except Exception as e:
        return {}, str(e)[:120]
    m: dict = {}
    for r in raw:
        nm = html.unescape(str(r.get("product_name") or ""))
        if not is_erhana(nm, str(r.get("sales_product_company_code") or "")):
            continue
        e = m.setdefault(_product_label(nm), {"available": 0, "total": 0})
        e["available"] += int(r.get("available_stock") or 0)
        e["total"] += int(r.get("total_stock") or 0)
    return m, ""


# ── 컬리 발주 (이알하나 행만) ──────────────────────────────────────
def _kurly_rows() -> list:
    sp = copy.copy(KURLY_SPEC)
    sp.key = "kurly_erhana"
    sp.exclude_item = lambda n, s: not kurly_exclude(n, s)   # 컬리 보고와 반대로 이알하나만 남긴다
    sp.unit_basis = False
    rows, _ = _channel_rows(sp)
    return rows


# ── 결제 금액: 아워박스 MATE 발주 조회(/api/oms/order/orders · 발주등록일 기준) ───────────────
# 주문 행에는 상품명이 없어 상품 정보 조회(/api/oms/info/products)의 OMS 상품코드 → 상품명으로 이알하나를 판별한다.
# 금액(pymt_price)은 스마트스토어(도착보장)·카페24(자사몰)·쿠팡 주문수집·카카오 등 소비자 주문 채널에만 들어 있고
# 홈쇼핑·컬리·면세점·로켓그로스 납품(B2B)은 0 이라 '결제 채널'만 금액을 보여준다.
_PAY_CACHE = os.path.join(_DATA, "erhana_ob_pay_cache.json")
_PAY_CH = ("스마트스토어(도착보장)", "카페24", "쿠팡 주문수집", "카카오 톡스토어", "지마켓")   # 금액이 있는 소비자 주문 채널
_PAY_LABEL = {"쿠팡 주문수집": "쿠팡(판매자배송)"}


def _pay_channel(ch: str) -> str:
    s = html.unescape(str(ch or "")).strip()
    if "스마트스토어" in s:
        return "스마트스토어(도착보장)"
    if s.startswith("카페24"):
        return "카페24"
    return _PAY_LABEL.get(s, s)


def _ob_product_map(client, cache: dict) -> dict:
    """OMS 상품코드 → 상품명 (하루 캐시)"""
    pm = cache.get("products") or {}
    if pm.get("map") and time.time() - float(pm.get("ts") or 0) < 24 * 3600:
        return pm["map"]
    m: dict = {}
    page = 1
    while True:
        r = client._post("/api/oms/info/products", {"page": page})
        for x in r.get("products") or []:
            m[str(x.get("sales_product_code") or "")] = html.unescape(str(x.get("product_name") or ""))
        if page >= int(r.get("ctotal_page") or r.get("total_page") or 1) or page > 200:
            break
        page += 1
    cache["products"] = {"ts": time.time(), "map": m}
    return m


def _ob_pay_rows(frm: str, to: str, refresh: bool = False) -> tuple[list, str]:
    """이알하나 주문의 결제 금액 — 날짜별 캐시. [{date, ch, label, pay, qty}] (날짜·채널·품목으로 합친 행), error"""
    try:
        with open(_PAY_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    days: dict = cache.setdefault("days", {})
    today = date.today().isoformat()
    want = []
    d = datetime.fromisoformat(frm).date()
    end = datetime.fromisoformat(to).date()
    while d <= end:
        ds = d.isoformat()
        recent = (date.today() - d).days <= 2          # 최근 이틀은 주문이 더 들어오므로 다시 받는다
        if refresh or recent or ds not in days:
            want.append(ds)
        d += timedelta(days=1)
    err = ""
    if want:
        try:
            client = _client()
            pmap = _ob_product_map(client, cache)
            for ds in want:
                agg: dict = defaultdict(lambda: {"pay": 0.0, "qty": 0})
                page = 1
                while True:
                    r = client._post("/api/oms/order/orders", {"order_dt": ds, "page": page})
                    for o in r.get("order_datas") or []:
                        nm = pmap.get(str(o.get("product_code") or ""), "")
                        if not is_erhana(nm):
                            continue
                        ch = _pay_channel(o.get("channel"))
                        if ch not in _PAY_CH and ch not in _PAY_LABEL.values():
                            continue
                        e = agg[(ch, _product_label(nm))]
                        e["pay"] += float(o.get("pymt_price") or 0)
                        e["qty"] += sum(int(x.get("release_qty") or 0) for x in (o.get("release_orders") or []))
                    if page >= int(r.get("total_page") or 1) or page > 100:
                        break
                    page += 1
                days[ds] = [{"ch": k[0], "label": k[1], "pay": int(round(v["pay"])), "qty": v["qty"]} for k, v in agg.items()]
            cache["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            os.makedirs(_DATA, exist_ok=True)
            with open(_PAY_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception as e:
            err = str(e)[:160]
    rows = []
    for ds, lst in days.items():
        if frm <= ds <= to:
            for x in lst:
                rows.append({"date": ds, **x})
    return rows, err


def _pay_windows(rows: list, win: dict) -> dict:
    """{ch: {mtd, prev, w1, w0, pct, w1_pct}} + {"_total": …}"""
    out: dict = defaultdict(lambda: {"mtd": 0, "prev": 0, "w1": 0, "w0": 0})
    for r in rows:
        for k, (a, b) in (("mtd", win["mtd"]), ("prev", win["mtd_prev"]), ("w1", win["w1"]), ("w0", win["w0"])):
            if a <= r["date"] <= b:
                out[r["ch"]][k] += int(r["pay"] or 0)
                out["_total"][k] += int(r["pay"] or 0)
    for e in out.values():
        e["pct"] = _pct_of(e["mtd"], e["prev"]); e["w1_pct"] = _pct_of(e["w1"], e["w0"])
    return dict(out)


# ── 로켓그로스 판매(쿠팡 Open API) — 결제일 기준 수량·매출. 허용 IP 미등록이면 오류만 남긴다 ──
_RG_CACHE = os.path.join(_DATA, "erhana_rg_cache.json")


def _rg_sales(frm: str, to: str, refresh: bool = False) -> dict:
    """{mtd:{qty, amount}, prev:{…}, items:[{name, qty, amount}], error, fetched_at}"""
    try:
        with open(_RG_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if not refresh and cache.get("from") == frm and cache.get("to") == to and time.time() - float(cache.get("ts") or 0) < _TTL:
        return cache.get("data") or {"error": "", "rows": []}
    import coupang_api as cp
    try:
        rows = cp.fetch_rg_orders(U.load_config(), frm, to)
        data = {"error": "", "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "rows": [r for r in rows if is_erhana(r.get("productName", ""))]}
        with open(_RG_CACHE, "w", encoding="utf-8") as f:
            json.dump({"from": frm, "to": to, "ts": time.time(), "data": data}, f, ensure_ascii=False)
        return data
    except Exception as e:
        return {"error": str(e)[:160], "rows": (cache.get("data") or {}).get("rows") or [], "fetched_at": (cache.get("data") or {}).get("fetched_at", "")}


def _rg_label(name: str) -> str:
    """쿠팡 상품명 → 짧은 라벨: '이알하나 ERr731 중년 여성 영양제 80mg 2개 28정' → 'ERr731 2개입'"""
    import re as _re
    s = str(name or "")
    key = next((k for k in ("ERr731", "LF300", "메노포즈", "NMN", "멜라토닌") if k in s), None)
    pack = _re.search(r"(\d+)\s*(개입|개|박스|병)", s)
    if key:
        return key + (f" {pack.group(1)}개입" if pack else "")
    return s[:18]


def _rg_windows(rg: dict, win: dict) -> dict:
    out = {"mtd": {"qty": 0, "amount": 0}, "prev": {"qty": 0, "amount": 0}, "items": {}, "error": rg.get("error", ""), "fetched_at": rg.get("fetched_at", "")}
    (a, b), (pa, pb) = win["mtd"], win["mtd_prev"]
    for r in rg.get("rows") or []:
        d = r.get("paid_at", "")
        amt = int(r.get("qty", 0)) * int(r.get("unit_price", 0))
        if a <= d <= b:
            out["mtd"]["qty"] += int(r["qty"]); out["mtd"]["amount"] += amt
            it = out["items"].setdefault(_rg_label(r["productName"]), {"qty": 0, "amount": 0}); it["qty"] += int(r["qty"]); it["amount"] += amt
        elif pa <= d <= pb:
            out["prev"]["qty"] += int(r["qty"]); out["prev"]["amount"] += amt
    out["pct"] = _pct_of(out["mtd"]["amount"], out["prev"]["amount"])
    out["qty_pct"] = _pct_of(out["mtd"]["qty"], out["prev"]["qty"])
    out["items"] = sorted([{"label": k, **v} for k, v in out["items"].items()], key=lambda x: -x["amount"])[:3]
    return out


# ── 집계 ─────────────────────────────────────────────────────────
def _rng(rows: list, a: str, b: str, key=None) -> dict:
    out: dict = defaultdict(int)
    for r in rows:
        if a <= r["date"] <= b:
            out[key(r) if key else "_"] += int(r["qty"] or 0)
    return out


def _pct_s(p) -> str:
    return "" if p is None else f"{'+' if p > 0 else ''}{p}%"


def build_summary(as_of: str, refresh: bool = False) -> dict:
    today = date.today().isoformat()
    as_of = min(as_of[:10], today)
    month = as_of[:7]
    prev = _prev_months(month, 2)[0]
    ml, pl = f"{int(month[5:7])}월", f"{int(prev[5:7])}월"
    win = sku_windows(as_of, month)
    (m_a, m_b), (p_a, p_b) = win["mtd"], win["mtd_prev"]
    (w1_a, w1_b), (w0_a, w0_b) = win["w1"], win["w0"]
    day = int(m_b[8:10])
    period = f"{ml} 1~{day}일" if day > 1 else f"{ml} 1일"

    ob_error = ""
    try:
        rows, meta = _ob_outbounds(p_a, as_of, refresh=refresh)
    except Exception as e:
        rows, meta, ob_error = [], {"from": p_a, "to": as_of, "fetched_at": "", "cached": False, "error": str(e)[:160]}, str(e)[:160]
    stock, stock_err = _ob_stock()
    pay_rows, pay_err = _ob_pay_rows(p_a, as_of, refresh=refresh)
    pay = _pay_windows(pay_rows, win)
    rg = _rg_windows(_rg_sales(p_a, as_of, refresh=refresh), win)

    # 채널별
    by_ch_m, by_ch_p = _rng(rows, m_a, m_b, lambda r: r["ch"]), _rng(rows, p_a, p_b, lambda r: r["ch"])
    by_ch_w1, by_ch_w0 = _rng(rows, w1_a, w1_b, lambda r: r["ch"]), _rng(rows, w0_a, w0_b, lambda r: r["ch"])
    channels = []
    for ch in sorted(set(by_ch_m) | set(by_ch_p), key=lambda c: -(by_ch_m.get(c, 0), by_ch_p.get(c, 0))[0] or -by_ch_p.get(c, 0)):
        c, p = by_ch_m.get(ch, 0), by_ch_p.get(ch, 0)
        pc = pay.get(ch) or {}
        channels.append({"name": ch, "mtd": c, "prev": p, "pct": _pct_of(c, p), "w1": by_ch_w1.get(ch, 0), "w0": by_ch_w0.get(ch, 0),
                         "pay_mtd": pc.get("mtd"), "pay_prev": pc.get("prev"), "pay_pct": pc.get("pct")})
    channels.sort(key=lambda x: -(x["mtd"] or x["prev"]))
    t_m, t_p = sum(by_ch_m.values()), sum(by_ch_p.values())
    t_w1, t_w0 = sum(by_ch_w1.values()), sum(by_ch_w0.values())
    tp_ = pay.get("_total") or {}
    total = {"mtd": t_m, "prev": t_p, "pct": _pct_of(t_m, t_p), "w1": t_w1, "w0": t_w0, "w1_pct": _pct_of(t_w1, t_w0),
             "daily": round(t_m / day, 1) if day else 0.0,
             "pay_mtd": tp_.get("mtd", 0), "pay_prev": tp_.get("prev", 0), "pay_pct": tp_.get("pct"), "pay_w1": tp_.get("w1", 0), "pay_w0": tp_.get("w0", 0), "pay_w1_pct": tp_.get("w1_pct")}
    # 결제 채널인데 출고 집계에 이름이 없으면(예: 카페24 는 출고 채널명이 다를 때) 결제만 있는 행으로 추가
    for ch, pc in pay.items():
        if ch != "_total" and ch not in by_ch_m and ch not in by_ch_p and (pc["mtd"] or pc["prev"]):
            channels.append({"name": ch, "mtd": 0, "prev": 0, "pct": None, "w1": 0, "w0": 0, "pay_mtd": pc["mtd"], "pay_prev": pc["prev"], "pay_pct": pc["pct"]})

    # 품목별 (출고 + 가용재고 + 일수)
    by_pr_m, by_pr_p = _rng(rows, m_a, m_b, lambda r: r["label"]), _rng(rows, p_a, p_b, lambda r: r["label"])
    products = []
    for lb in sorted(set(by_pr_m) | set(by_pr_p) | set(stock), key=lambda x: -(by_pr_m.get(x, 0) or by_pr_p.get(x, 0))):
        c, p = by_pr_m.get(lb, 0), by_pr_p.get(lb, 0)
        st = stock.get(lb) or {}
        avg = c / day if day else 0
        products.append({"label": lb, "mtd": c, "prev": p, "pct": _pct_of(c, p), "available": st.get("available"), "total_stock": st.get("total"),
                         "days": (round(st["available"] / avg) if st and avg > 0 else None)})
    products.sort(key=lambda x: (-(x["mtd"] or 0), -(x["prev"] or 0)))

    # 컬리 발주
    kurly: dict = {"mtd": 0, "prev": 0, "pct": None, "pending": [], "error": ""}
    try:
        krows = _kurly_rows()
        km = _rng([{"date": r["date"][:10], "qty": r.get("order_qty", r["qty"])} for r in krows], m_a, m_b)
        kp = _rng([{"date": r["date"][:10], "qty": r.get("order_qty", r["qty"])} for r in krows], p_a, p_b)
        kurly["mtd"], kurly["prev"] = km.get("_", 0), kp.get("_", 0)
        kurly["pct"] = _pct_of(kurly["mtd"], kurly["prev"])
        pend: dict = defaultdict(lambda: {"qty": 0, "items": defaultdict(int)})
        for r in krows:
            d = r["date"][:10]
            if d > as_of and (r.get("order_qty", r["qty"]) or 0) > 0:
                pend[d]["qty"] += int(r.get("order_qty", r["qty"]) or 0)
                pend[d]["items"][_product_label(r["name"])] += int(r.get("order_qty", r["qty"]) or 0)
        kurly["pending"] = [{"date": d, "md": _md(d), "qty": v["qty"], "items": [{"label": k, "qty": q} for k, q in sorted(v["items"].items(), key=lambda kv: -kv[1])]}
                            for d, v in sorted(pend.items())]
    except Exception as e:
        kurly["error"] = str(e)[:160]

    notes = _load_notes()
    s = {"as_of": as_of, "as_of_md": _md(as_of), "month": month, "month_label": ml, "prev_month": prev, "prev_month_label": pl, "period": period,
         "windows": {"mtd": [m_a, m_b], "mtd_prev": [p_a, p_b], "w1": [w1_a, w1_b], "w0": [w0_a, w0_b]},
         "channels": channels, "total": total, "products": products, "kurly": kurly,
         "stock_error": stock_err, "ob": meta, "ob_error": ob_error, "pay_error": pay_err, "rg": rg, "notes": notes}
    s["text"] = _render(s)
    return s


def _render(s: dict) -> str:
    notes = s.get("notes") or {}
    t, pl, ml, period = s["total"], s["prev_month_label"], s["month_label"], s["period"]
    title = (notes.get("title") or "이알하나 출고·발주 현황").strip()
    pct = t["pct"]
    direction = "flat" if pct is None else ("up" if pct >= 10 else ("down" if pct <= -10 else "flat"))
    word = {"up": "출고 증가", "down": "출고 감소", "flat": "출고 보합"}[direction]
    L = [f"안녕하세요, {s['as_of_md']} 기준 {title} 보고드립니다.", "", "한줄정리"]
    headline = (notes.get("headline") or "").strip()
    if headline:
        L += [ln.strip() if ln.lstrip().startswith("•") else "• " + ln.strip() for ln in headline.splitlines() if ln.strip()]
    else:
        L.append(f"• 이알하나 전체 — {word} {BRIEF_EMOJI[direction]} ({period} {_n(t['mtd'])}개" + (f" · {pl} 같은 기간 대비 {_pct_s(pct)}" if pct is not None else "") + ")")
        floor = max(100, int(t["mtd"] * 0.03))   # 전체의 3% 미만인 소량 채널은 변동이 커도 언급하지 않는다
        chs = [c for c in s["channels"] if c["pct"] is not None and abs(c["pct"]) >= 30 and max(c["mtd"], c["prev"]) >= floor][:3]
        if chs:
            L.append("• 변동 큰 채널 — " + " · ".join(f"{c['name']} {_pct_s(c['pct'])}" for c in chs))
    op = (notes.get("opinion") or "").strip()
    if op:
        L.append("• 의견 : " + op)
    if s.get("ob_error"):
        L += ["", f"• 아워박스 출고 조회 실패 ({s['ob_error'][:80]})"]
    # 채널별 출고
    L += ["", f"[채널별 출고] {period} · {pl} 같은 기간 대비"]
    shown = sorted([c for c in s["channels"] if c["mtd"] or c["prev"] or c.get("pay_mtd")],
                   key=lambda c: -(max(c["mtd"], c["prev"]) + (c.get("pay_mtd") or 0) / 10000))[:10]   # 결제만 있는 채널(쿠팡 판매자배송)도 금액 크기로 끼워 넣는다
    for c in shown:
        if c["mtd"] or c["prev"]:
            tail = f" ({_pct_s(c['pct'])})" if c["pct"] is not None else (" (신규)" if not c["prev"] else f" ({pl} {_n(c['prev'])}개 → 0)")
            line = f"• {c['name']} {_n(c['mtd'])}개{tail}"
        else:
            line = f"• {c['name']}"
        if c.get("pay_mtd"):
            line += f" · 결제 {_won(c['pay_mtd'])}" + (f" ({_pct_s(c['pay_pct'])})" if c.get("pay_pct") is not None else "")
        L.append(line)
    if t["w1"]:
        L.append(f"• 최근 7일 {_n(t['w1'])}개" + (f" (직전 7일 대비 {_pct_s(t['w1_pct'])})" if t["w1_pct"] is not None else "")
                 + (f" · 결제 {_won(t['pay_w1'])}" + (f" ({_pct_s(t['pay_w1_pct'])})" if t.get("pay_w1_pct") is not None else "") if t.get("pay_w1") else ""))
    if t.get("pay_mtd"):
        L.append(f"• 결제 합계(스마트스토어·자사몰 등) {_won(t['pay_mtd'])}" + (f" ({pl} 같은 기간 대비 {_pct_s(t['pay_pct'])})" if t.get("pay_pct") is not None else ""))
    if s.get("pay_error"):
        L.append(f"• 결제 금액 조회 실패 ({s['pay_error'][:80]})")
    rg = s.get("rg") or {}
    if rg.get("mtd", {}).get("qty") or rg.get("prev", {}).get("qty"):
        L += ["", f"[로켓그로스 판매] {period} · 결제일 기준"]
        L.append(f"• {_n(rg['mtd']['qty'])}개 · 매출 {_won(rg['mtd']['amount'])}" + (f" ({pl} 같은 기간 대비 {_pct_s(rg['pct'])})" if rg.get("pct") is not None else ""))
        if rg.get("items"):
            L.append("• " + " · ".join(f"{i['label']} {_n(i['qty'])}개 {_won(i['amount'])}" for i in rg["items"]))
    elif rg.get("error"):
        why = "쿠팡 오픈API 허용 IP 미등록 (403)" if "403" in rg["error"] else rg["error"][:70]
        L += ["", f"• 로켓그로스 판매 — 쿠팡 API 조회 실패 ({why})"]
    # 주요 품목
    tops = [p for p in s["products"] if p["mtd"] > 0][:4]
    gone = [p for p in s["products"] if p["mtd"] == 0 and p["prev"] >= max(1, (tops[0]["mtd"] if tops else 0) * 0.2)][:2]
    if tops or gone:
        parts = [f"{p['label']} {_n(p['mtd'])}개" + (f" ({_pct_s(p['pct'])})" if p["pct"] is not None else " (신규)") for p in tops]
        if gone:
            parts.append("·".join(p["label"] for p in gone) + f" {ml} 출고 없음")
        L += ["", "[주요 품목]", "• " + " · ".join(parts)]
    # 재고
    st = [p for p in s["products"] if p.get("available") is not None and (p["mtd"] > 0 or (p["available"] or 0) > 0)]
    st.sort(key=lambda p: (p["days"] if p["days"] is not None else 10**6))
    if st:
        L += ["", "[재고] 아워박스 가용재고 · 이달 일평균 출고 기준"]
        L.append("• " + " · ".join(f"{p['label']} {_n(p['available'])}개" + (f" ({p['days']}일분{' · 부족' if p['days'] <= 7 else ''})" if p["days"] is not None else "") for p in st[:6]))
    elif s.get("stock_error"):
        L += ["", f"• 아워박스 재고 조회 실패 ({s['stock_error'][:80]})"]
    # 컬리 발주
    k = s["kurly"]
    L += ["", "[컬리 발주]"]
    if k.get("error"):
        L.append(f"• 발주 캘린더 조회 실패 ({k['error'][:80]})")
    else:
        line = f"• {period} 납기 발주 {_n(k['mtd'])}개" + (f" ({pl} 같은 기간 대비 {_pct_s(k['pct'])})" if k["pct"] is not None else (f" ({pl} 같은 기간 {_n(k['prev'])}개)" if k["prev"] else ""))
        if k["pending"]:
            line += " · " + " / ".join(f"{g['md']} 납기 {_n(g['qty'])}개 대기" for g in k["pending"][:3])
        else:
            line += " · 납기 대기 없음"
        L.append(line)
    L += ["", "감사합니다."]
    return "\n".join(L)


# ── 라우터 ───────────────────────────────────────────────────────
@router.get("/summary")
def get_summary(as_of: str = "", refresh: int = 0):
    as_of = (as_of or date.today().isoformat())[:10]
    try:
        datetime.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(400, "as_of 형식 오류 (YYYY-MM-DD)")
    return build_summary(as_of, refresh=bool(refresh))


@router.get("/notes")
def get_notes():
    return _load_notes()


@router.put("/notes")
def put_notes(body: Notes):
    return _save_notes(body)
