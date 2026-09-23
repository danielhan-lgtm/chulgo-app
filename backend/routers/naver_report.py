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
    _DATA_DIR, _load_json, _save_json, _nkey, _n, _pct1, _bar, _top, _prev_months,
    _bh_stock_by_sku, _ob_available_by_name,
)

router = APIRouter()

STATE_PATH = os.path.join(_DATA_DIR, "naver_report_state.json")
TX_PATH = os.path.join(_DATA_DIR, "naver_report_tx.json")
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


def _build_history(by_day: dict, month: str, n: int = 4, eff_to: str = "") -> list:
    """최근 n개월 월별 출고 수량 (주문일 기준, 취소 보정 입고 차감이 반영된 by_day 그대로).
    eff_to 가 있으면 그 날까지만 합산(이달은 자료 마지막 주문일·기준일 중 빠른 쪽). 자료가 한 날도 없는 달은 qty None.
    [{ym, label, qty}]"""
    out = []
    for ym in _prev_months(month, n):
        days = [d for d in by_day if d[:7] == ym and (not eff_to or d <= eff_to)]
        out.append({"ym": ym, "label": f"{int(ym[5:7])}월", "qty": (sum(by_day[d] for d in days) if days else None)})
    return out


def _build_summary(as_of: str, month: str, refresh: bool = False) -> dict:
    st = _state()
    aliases = st["aliases"]
    tx = _tx_all()
    kws = st["partner_kw"]

    out = [r for r in tx if r["kind"] == "출고"]
    ret = [r for r in tx if r["kind"] == "입고"]
    for r in tx:
        r["label"] = _label(r["name"], aliases)

    dates = sorted({r["date"] for r in out})
    data_from, data_to = (dates[0], dates[-1]) if dates else ("", "")
    # 자료 마지막 주문일과 기준일 중 빠른 쪽까지를 '경과일'로 본다 (자료가 어제까지면 어제까지)
    eff_to = min(as_of, data_to) if data_to else as_of
    prev_month = (datetime.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")

    by_day: dict = defaultdict(int)
    by_day_label: dict = defaultdict(lambda: defaultdict(int))
    for r in out:
        by_day[r["date"]] += r["qty"]
        by_day_label[r["date"]][r["label"]] += r["qty"]
    for r in ret:   # 취소 보정(입고)은 주문일에서 차감
        by_day[r["date"]] += r["qty"]
        by_day_label[r["date"]][r["label"]] += r["qty"]

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
    sku_of_label: dict = defaultdict(set); name_of_label: dict = defaultdict(set)
    for r in out:
        if r["sku"]:
            sku_of_label[r["label"]].add(r["sku"])
        name_of_label[r["label"]].add(r["name"])

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

    # 품목별
    labels = sorted({r["label"] for r in out}, key=lambda lb: -rng_sum(m_from, m_to, lb))
    rows = []
    for lb in labels:
        mq = rng_sum(m_from, m_to, lb); pq = rng_sum(p_from, p_to, lb)
        a14_from = (datetime.fromisoformat(eff_to).date() - timedelta(days=13)).isoformat() if data_to else ""
        q14 = rng_sum(a14_from, eff_to, lb) if data_to else 0
        avg14 = q14 / 14
        stock, basis = stock_of(lb)
        dos = (round(stock / avg14, 1) if (stock is not None and avg14 > 0) else None)
        spark = [rng_sum(w["from"], w["to"], lb) for w in weeks]
        rows.append({"label": lb, "names": sorted(name_of_label[lb]), "skus": sorted(sku_of_label[lb]),
                     "month_qty": mq, "prev_qty": pq,
                     "pct": _pct(mq / m_elapsed if mq else 0, pq / _month_days(prev_month) if pq else 0),
                     "avg14": round(avg14, 1), "w1": rng_sum(w1_from, w1_to, lb) if data_to else 0,
                     "stock": stock, "stock_basis": basis, "days_of_supply": dos, "spark": spark})
    rows = [r for r in rows if r["month_qty"] or r["prev_qty"] or r["w1"]]

    # 회차 (이번 달 거래ID 수) · 마지막 출고
    m_batches = len({r["id"] for r in out if m_from <= r["date"] <= m_to})
    last_day = data_to
    last_day_qty = by_day.get(last_day, 0) if last_day else 0
    last_day_top = _top([{"label": lb, "qty": q} for lb, q in sorted(by_day_label.get(last_day, {}).items(), key=lambda kv: -kv[1])], "qty", 2) if last_day else ""
    m_ret = -sum(r["qty"] for r in ret if m_from <= r["date"] <= m_to)

    summary = {
        "as_of": as_of, "as_of_md": f"{int(as_of[5:7])}/{int(as_of[8:10])}", "month": month, "month_label": f"{int(month[5:7])}월",
        "prev_month_label": f"{int(prev_month[5:7])}월",
        "partner_kw": kws, "partners": sorted({r["partner"] for r in tx}),
        "data_from": data_from, "data_to": data_to, "eff_to": eff_to, "tx_rows": len(tx),
        "stale_days": ((datetime.fromisoformat(as_of).date() - datetime.fromisoformat(data_to).date()).days if data_to else None),
        "month_qty": m_qty, "month_elapsed": m_elapsed, "month_avg": round(m_avg, 1), "month_batches": m_batches, "month_returns": m_ret,
        "prev_qty": p_qty, "prev_avg": round(p_avg, 1), "month_pct": _pct(m_avg, p_avg),
        "w1": w1, "w0": w0, "w1_avg": round(w1 / 7, 1), "w1_pct": _pct(w1, w0), "w1_from": w1_from, "w1_to": w1_to,
        "last_day": last_day, "last_day_md": (f"{int(last_day[5:7])}/{int(last_day[8:10])}" if last_day else ""),
        "last_day_qty": last_day_qty, "last_day_top": last_day_top,
        "weeks": weeks, "rows": rows,
        "stock_basis": ("ob" if ob_connected else ("bh" if bh else "")), "ob_connected": ob_connected, "bh_connected": bool(bh),
        "uploads": st["uploads"], "notes": st.get("notes", ""),
        "daily": [{"date": d, "qty": by_day[d]} for d in sorted(by_day) if d >= (datetime.fromisoformat(eff_to).date() - timedelta(days=59)).isoformat()] if data_to else [],
        "history": _build_history(by_day, month, eff_to=eff_to),
    }
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
        L.append(f"* {ml} 출고 {_n(s['month_qty'])}개 · 일평균 {_n(round(s['month_avg']))}개 {_pct_txt(s['month_pct'])}".rstrip()
                 + (f" — {pl} 일평균 {_n(round(s['prev_avg']))}개" if s["prev_avg"] else ""))
    else:
        L.append(f"* {ml} 출고 없음" + (f" ({pl} {_n(s['prev_qty'])}개)" if s["prev_qty"] else ""))
    if s["w1"]:
        rng = f"{int(s['w1_from'][5:7])}/{int(s['w1_from'][8:10])}~{int(s['w1_to'][5:7])}/{int(s['w1_to'][8:10])}"
        L.append(f"* 최근 7일({rng}) 일평균 {_n(round(s['w1_avg']))}개" + (f" · 전주 대비 {_pct_txt(s['w1_pct'])}" if s["w1_pct"] is not None else ""))
    if s["last_day"]:
        L.append(f"* 마지막 주문일 {s['last_day_md']} — {_n(s['last_day_qty'])}개" + (f" · {s['last_day_top']}" if s["last_day_top"] else ""))
    if s["month_returns"]:
        L.append(f"* {ml} 취소 보정 {_n(s['month_returns'])}개 (출고에서 차감)")
    if s.get("stale_days") and s["stale_days"] >= 2:
        L.append(f"* 자료는 {s['last_day_md']} 주문분까지 반영 (박스히어로 거래 내역 재업로드 시 갱신)")

    # 품목별 — 이번 달 상위 6종, 막대는 1위 대비
    rows = [r for r in s["rows"] if r["month_qty"] > 0][:6]
    if rows:
        top = rows[0]["month_qty"]
        L += ["", f"■ 품목별 {ml} 출고 (전월 일평균 대비)"]
        for r in rows:
            line = f"* {r['label']} — {_n(r['month_qty'])}개 {_pct_txt(r['pct'])}".rstrip() + f"  {_bar(r['month_qty'] / top)}"
            L.append(line)

    # 재고 소진 예상일 — 재고가 붙는 품목만, 최근 14일 판매 속도 기준
    # 이번 달 상위 품목 중 재고가 붙는 것만. 음수 재고(박스히어로 오차)는 '확인 필요'로, 90일 넘으면 숫자 대신 '90일 이상'
    srows = [r for r in s["rows"] if r["month_qty"] > 0 and r.get("stock") is not None and r["avg14"] > 0][:6]
    if srows:
        basis = "아워박스 가용재고" if s["stock_basis"] == "ob" else "박스히어로 재고"
        L += ["", f"■ 재고 소진 예상일 ({basis} ÷ 최근 14일 일평균 · 막대 1칸 = 5일)"]
        for r in sorted(srows, key=lambda r: (r["days_of_supply"] if r["stock"] >= 0 else -1)):
            d = r["days_of_supply"]
            if r["stock"] < 0 or d is None:
                L.append(f"* {r['label']} — 재고 {_n(r['stock'])}개 · 확인 필요")
                continue
            dtxt = "90일 이상" if d > 90 else f"{d:g}일분"
            L.append(f"* {r['label']} — 재고 {_n(r['stock'])}개 · {_n(round(r['avg14']))}개/일 → {dtxt}" + (" — 부족" if d < 7 else "") + f"  {_bar(d / 50)}")

    # 주별 추이 (최근 6주)
    wk = [w for w in s["weeks"] if w["qty"]][-6:]
    if len(wk) >= 2:
        top = max(w["qty"] for w in wk) or 1
        L += ["", "■ 주별 출고 (최근 6주)"]
        for w in wk:
            L.append(f"* {w['md']} — {_n(w['qty'])}개" + (" (진행 중)" if w["partial"] else "") + f"  {_bar(w['qty'] / top)}")

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
    for f in files:
        try:
            rows = _parse_transactions(await f.read(), st["partner_kw"])
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
    _save_json(STATE_PATH, st)
    return {"added": added_files, "errors": errors, "tx_rows": len(tx)}


@router.post("/upload/clear")
def clear_uploads():
    st = _state()
    st["uploads"] = []
    _save_json(STATE_PATH, st)
    _save_json(TX_PATH, [])
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
