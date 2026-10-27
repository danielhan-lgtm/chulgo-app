"""재고 차이 추적(Stock Gap Tracker): 박스히어로(BH) ↔ 아워박스(OB) 재고 차이를
전 품목 일괄로 '무엇이 · 언제 · 왜' 벌어졌는지 분해한다.

기존 재고대사(reconcile)의 부품을 재사용한다:
  · get_stock            — 현재 재고 비교(그룹 병합, 차이 = BH − OB총재고)
  · _collect_bh_ob_raw   — 기간 BH·OB 원시 거래 수집(월 단위 영구 캐시)
  · flow_trace.trace_item — 단일 품목 이벤트 짝짓기 + 잔여 원인 분류

여기서 새로 하는 일:
  1) 현재 재고에서 거래를 거꾸로 걷어 일자별 차이(BH−OB)를 복원 → '언제' 벌어졌는지
  2) 모든 차이 품목에 trace_item을 한 번에 돌려 원인을 모으고, 품목별 한 줄 요약을 만든다 → '왜'
  3) 저장된 주간 리포트·일별 스냅샷의 실측 차이를 겹쳐 복원 결과를 검증한다
  4) 실행할 때마다 오늘의 양쪽 재고를 일별 스냅샷으로 남긴다(앞으로의 '언제' 근거)
"""
import sys, os, re, json, html
import threading as _threading
import time as _time
from datetime import datetime, timedelta, date as _date
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
from routers import reconcile as R
import flow_trace as FT

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

router = APIRouter()

_LAST_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "stock_gap_last.json")
_LOCK = _threading.Lock()
_LAST: dict = {"result": None, "full": {}}      # full: key → 상세(trace·timeline) — 메모리만


# ── 이름 정규화 (get_stock의 병합 키와 동일해야 거래가 같은 행에 붙는다) ──────
def _strip_date(nm: str) -> str:
    s = str(nm or "")
    s = re.sub(r'[\s\-_]+\d{2,4}[-.]\d{1,2}[-.]\d{1,2}\s*$', '', s)
    return s.strip()


def _snorm(nm: str) -> str:
    return U.normalize(_strip_date(nm))


def _nrm(s: str) -> str:
    return re.sub(r"[\s\-_·•\[\]()（）]", "", str(s or "")).lower()


def _window(months: int) -> tuple:
    to = _date.today()
    frm = (to - timedelta(days=int(months) * 30))
    return frm.isoformat(), to.isoformat()


# ── 원시 거래 → 품목 키별 이벤트 ─────────────────────────────────────────────
def _build_events(token: str, from_date: str, to_date: str, loc_set: set, errors: list):
    """BH·OB 거래를 품목 키(get_stock의 row.key와 동일) 단위 이벤트로 변환.

    Returns (events, daily_out_by_prod, daily_in_by_prod, ob_source)
      events[key] = {"bh_in": [...], "bh_out": [...], "ob_in": [...], "ob_out": [...], "name": str}
      이벤트 = {"date", "qty"(>0), "memo", "channel", "type"}
    """
    cfg = U.load_config()
    (bh_in_raw, bh_out_raw, bh_move_raw, bh_adj_raw,
     ob_in_raw, ob_out_raw, ob_adj_raw, ob_source) = R._collect_bh_ob_raw(
        token, from_date, to_date, loc_set, errors, cfg)

    mg = R._build_mapping_groups() or {}
    bh_to_group      = mg.get("bh_to_group", {}) or {}
    bh_name_to_group = mg.get("bh_name_to_group", {}) or {}
    ob_code_to_group = mg.get("ob_code_to_group", {}) or {}
    ob_to_group      = mg.get("ob_to_group", {}) or {}
    group_name       = mg.get("group_name", {}) or {}
    group_label      = mg.get("group_label", {}) or {}

    def _glabel(g, fallback):
        return str(group_name.get(g) or group_label.get(g) or fallback)

    def _key_bh(name: str, sku: str) -> tuple:
        g = bh_to_group.get(sku) or bh_name_to_group.get(name)
        if g:
            lab = _glabel(g, name)
            return _snorm(lab), lab
        return _snorm(name), name

    def _key_ob(name: str, code: str) -> tuple:
        g = ob_code_to_group.get(code) or ob_to_group.get(name)
        if g:
            lab = _glabel(g, name)
            return _snorm(lab), lab
        return _snorm(name), name

    events: dict = defaultdict(lambda: {"bh_in": [], "bh_out": [], "ob_in": [], "ob_out": [], "name": ""})
    daily_out: dict = defaultdict(lambda: defaultdict(lambda: {"bh": 0, "ob": 0}))
    daily_in: dict = defaultdict(lambda: defaultdict(lambda: {"bh": 0, "ob": 0}))

    def _tx_date(tx) -> str:
        return str(tx.get("transaction_time") or tx.get("created_at") or "")[:10]

    for tx, ttype, sign in ([(t, "입고", 1) for t in bh_in_raw] +
                            [(t, "출고", -1) for t in bh_out_raw] +
                            [(t, "이동", -1) for t in bh_move_raw] +
                            [(t, "조정", 0) for t in bh_adj_raw]):
        d = _tx_date(tx)
        if not d or not (from_date <= d <= to_date):
            continue
        memo = str(tx.get("memo") or "")[:60]
        items = tx.get("items") or []
        if not items and tx.get("sku"):
            items = [{"sku": tx.get("sku"), "name": tx.get("name", ""),
                      "quantity": tx.get("quantity", tx.get("total_quantity", 0))}]
        for it in items:
            try:
                q = int(it.get("quantity", 0) or 0)
            except Exception:
                q = 0
            if not q:
                continue
            eff = q if ttype == "조정" else sign * abs(q)
            key, label = _key_bh(str(it.get("name") or "").strip(), str(it.get("sku") or "").strip())
            (daily_in if eff > 0 else daily_out)[label][d]["bh"] += abs(eff)
            ev = {"date": d, "qty": abs(eff), "memo": memo, "channel": "", "type": ttype}
            e = events[key]
            e["name"] = e["name"] or label
            (e["bh_in"] if eff > 0 else e["bh_out"]).append(ev)

    def _ob_date(rec, dks) -> str:
        h = rec.get("header", rec) if isinstance(rec, dict) else {}
        for dk in dks:
            v = str(h.get(dk) or rec.get(dk) or "")[:10]
            if v and v != "None":
                return v
        return ""

    def _ob_qty(rec, k) -> int:
        h = rec.get("header", rec) if isinstance(rec, dict) else {}
        v = h.get(k, rec.get(k))
        if v in (None, "", "None"):
            return 0
        try:
            return int(float(str(v).replace(",", "")))
        except Exception:
            return 0

    for rec in ob_in_raw:
        d = _ob_date(rec, ["input_dt", "input_complete_dt"])
        if not d or not (from_date <= d <= to_date):
            continue
        q = abs(_ob_qty(rec, "input_qty"))
        if not q:
            continue
        key, label = _key_ob(R._ob_rec_prod_nm(rec), R._ob_rec_prod_cd(rec))
        daily_in[label][d]["ob"] += q
        e = events[key]
        e["name"] = e["name"] or label
        e["ob_in"].append({"date": d, "qty": q, "memo": "", "channel": R._ob_rec_channel(rec), "type": "입고"})
    for rec in ob_out_raw:
        d = _ob_date(rec, ["out_dt", "out_complete_dt"])
        if not d or not (from_date <= d <= to_date):
            continue
        q = abs(_ob_qty(rec, "out_qty"))
        if not q:
            continue
        ch = R._ob_rec_channel(rec)
        key, label = _key_ob(R._ob_rec_prod_nm(rec), R._ob_rec_prod_cd(rec))
        daily_out[label][d]["ob"] += q
        e = events[key]
        e["name"] = e["name"] or label
        e["ob_out"].append({"date": d, "qty": q, "memo": "", "channel": ch,
                            "type": "세트" if ch in R.ASSEMBLY_CHANNELS else "출고"})
    for rec in ob_adj_raw:
        d = _ob_date(rec, ["adj_dt", "reg_dt", "reg_dtm"])
        if not d or not (from_date <= d <= to_date):
            continue
        q = _ob_qty(rec, "adj_qty")
        if not q:
            continue
        key, label = _key_ob(R._ob_rec_prod_nm(rec), R._ob_rec_prod_cd(rec))
        if not key:
            continue
        (daily_in if q > 0 else daily_out)[label][d]["ob"] += abs(q)
        e = events[key]
        e["name"] = e["name"] or label
        ev = {"date": d, "qty": abs(q), "memo": "", "channel": R._ob_rec_channel(rec), "type": "조정"}
        (e["ob_in"] if q > 0 else e["ob_out"]).append(ev)

    # OB 출고는 주문라인 단위라 (일자, 채널, 유형)으로 합산 — BH도 일 단위 배치라 매칭 의미 동일
    def _agg(evs: list) -> list:
        agg: dict = {}
        for ev in evs:
            k = (ev["date"], ev.get("channel", ""), ev.get("type", ""))
            if k in agg:
                agg[k]["qty"] += ev["qty"]; agg[k]["_n"] += 1
            else:
                agg[k] = dict(ev); agg[k]["_n"] = 1
        out = []
        for ev in agg.values():
            n = ev.pop("_n", 1)
            if n > 1:
                ev["memo"] = f"주문 {n}건 합산"
            out.append(ev)
        out.sort(key=lambda x: (x["date"], -x["qty"]))
        return out

    for e in events.values():
        e["ob_in"] = _agg(e["ob_in"])
        e["ob_out"] = _agg(e["ob_out"])
        e["bh_in"].sort(key=lambda x: (x["date"], -x["qty"]))
        e["bh_out"].sort(key=lambda x: (x["date"], -x["qty"]))

    return (dict(events),
            {k: dict(v) for k, v in daily_out.items()},
            {k: dict(v) for k, v in daily_in.items()},
            ob_source)


# ── OB 가용외 스냅샷(일별 변화) 일괄 로드 ─────────────────────────────────────
def _load_unav_by_code(from_date: str) -> dict:
    """ob_stock_snapshots를 한 번에 읽어 code → [(captured_at, unavailable)] (시간순)."""
    import receiving_db as _db
    out: dict = defaultdict(list)
    try:
        with _db._conn() as conn:
            rows = conn.execute(
                "SELECT captured_at, product_code, unavailable FROM ob_stock_snapshots "
                "WHERE captured_at >= ? ORDER BY captured_at ASC", (from_date,)).fetchall()
        for r in rows:
            out[str(r["product_code"])].append((str(r["captured_at"]), int(r["unavailable"] or 0)))
    except Exception:
        pass
    return out


def _unav_events_for(codes: list, unav_by_code: dict) -> list:
    """품목(코드 묶음)의 가용외 일별 변화량 [{date, delta}]."""
    series: dict = defaultdict(int)
    for c in codes:
        for t, u in unav_by_code.get(str(c), []):
            series[t] += u
    if not series:
        return []
    prev = None
    daily: dict = defaultdict(int)
    for t in sorted(series):
        if prev is not None:
            dv = series[t] - prev
            if dv:
                daily[t[:10]] += dv
        prev = series[t]
    return [{"date": d, "delta": v} for d, v in sorted(daily.items()) if v]


# ── 실측 이력(주간 리포트 + 일별 스냅샷) ──────────────────────────────────────
def _load_observed(location_ids: str, since: str) -> dict:
    """정규화 이름 → [{date, diff, bh, ob, src}] (시간순). 양쪽 재고가 모두 있는 행만."""
    import receiving_db as _db
    obs: dict = defaultdict(dict)   # nkey → date → point (같은 날은 일별 스냅샷 우선)
    try:
        for rep in _db.list_stock_reports(300):
            rd = str(rep.get("report_date") or "")[:10]
            if rd < since:
                continue
            if str(rep.get("location_ids") or "") != str(location_ids or ""):
                continue
            full = _db.get_stock_report(rep["id"])
            for row in full.get("rows", []):
                bh, ob = row.get("bh_stock"), row.get("ob_stock_total")
                if bh is None or ob is None:
                    continue
                nk = _nrm(row.get("name"))
                if rd not in obs[nk]:
                    obs[nk][rd] = {"date": rd, "diff": int(bh) - int(ob), "bh": int(bh), "ob": int(ob), "src": "weekly"}
    except Exception:
        pass
    try:
        for row in _db.get_stock_gap_history(location_ids, since):
            nk = _nrm(row.get("name"))
            obs[nk][row["date"]] = {"date": row["date"], "diff": row["diff"], "bh": row["bh_stock"],
                                    "ob": row["ob_total"], "src": "daily"}
    except Exception:
        pass
    return {k: [v[d] for d in sorted(v)] for k, v in obs.items()}


# ── 품목별 분해 ───────────────────────────────────────────────────────────────
def _analyze_item(row: dict, ev: dict, from_date: str, to_date: str, tol_days: int,
                  daily_out: dict, daily_in: dict, unav_events: list) -> dict:
    name = row.get("name") or row.get("key")
    bh_stock = row.get("bh_stock")
    ob_total = row.get("ob_stock_total")
    ob_unav = int(row.get("ob_unusable") or 0)
    diff_now = row.get("diff")

    bh_in, bh_out = ev.get("bh_in", []), ev.get("bh_out", [])
    ob_in, ob_out = ev.get("ob_in", []), ev.get("ob_out", [])

    trace = FT.trace_item(
        bh_in, bh_out, ob_in, ob_out,
        diff_now=diff_now, ob_unav=ob_unav,
        from_date=from_date, to_date=to_date, tol_days=tol_days,
        daily_out_by_prod=daily_out, daily_in_by_prod=daily_in,
        target_label=name, unav_events=unav_events,
    )
    ob_avail = (ob_total - ob_unav) if ob_total is not None else None
    trace["avail_basis"] = {
        "diff_avail": (bh_stock - ob_avail) if (bh_stock is not None and ob_avail is not None) else None,
        "ob_unav": ob_unav, "unav_events": unav_events, "snapshots": None,
    }
    trace.update({"name": name, "from": from_date, "to": to_date,
                  "bh_stock": bh_stock, "ob_total": ob_total, "ob_unav": ob_unav,
                  "ob_source": "", "errors": []})

    # ── 일자별 차이 복원(현재 재고에서 거꾸로) ──
    net: dict = defaultdict(lambda: {"bh": 0, "ob": 0})
    by_day: dict = defaultdict(lambda: {"bh": [], "ob": []})
    for e in bh_in:
        net[e["date"]]["bh"] += e["qty"]; by_day[e["date"]]["bh"].append({**e, "dir": "+"})
    for e in bh_out:
        net[e["date"]]["bh"] -= e["qty"]; by_day[e["date"]]["bh"].append({**e, "dir": "-"})
    for e in ob_in:
        net[e["date"]]["ob"] += e["qty"]; by_day[e["date"]]["ob"].append({**e, "dir": "+"})
    for e in ob_out:
        net[e["date"]]["ob"] -= e["qty"]; by_day[e["date"]]["ob"].append({**e, "dir": "-"})

    causes = trace.get("causes") or []
    cause_by_day: dict = defaultdict(list)
    for c in causes:
        if c.get("impact") and c.get("type") != "기간이전차이":
            cause_by_day[str(c.get("date") or "")[:10]].append(c)

    gap = diff_now if diff_now is not None else 0
    bh_cur = bh_stock if bh_stock is not None else 0
    ob_cur = ob_total if ob_total is not None else 0
    pts: list = []
    for d in sorted(net, reverse=True):
        bn, on = net[d]["bh"], net[d]["ob"]
        dg = bn - on
        pts.append({"date": d, "gap": gap, "bh": bh_cur, "ob": ob_cur,
                    "d_gap": dg, "bh_net": bn, "ob_net": on})
        gap -= dg; bh_cur -= bn; ob_cur -= on
    pts.reverse()
    opening = gap   # == trace["opening_gap"]

    changes: list = []
    for p in pts:
        if p["d_gap"] == 0:
            continue
        d = p["date"]
        cs = cause_by_day.get(d, [])
        csum = sum(int(c.get("impact") or 0) for c in cs)
        if cs and csum == p["d_gap"]:
            verdict, vkind = "원인 확정", "cause"
        elif cs:
            verdict, vkind = "일부 원인 확정", "partial"
        else:
            verdict, vkind = "시점차(이후 상쇄)", "timing"
        def _sum_side(lst):
            return [{"type": x.get("type"), "dir": x.get("dir"), "qty": x.get("qty"),
                     "memo": x.get("memo") or "", "channel": x.get("channel") or ""} for x in lst[:12]]
        changes.append({
            "date": d, "d_gap": p["d_gap"], "gap_after": p["gap"],
            "bh_net": p["bh_net"], "ob_net": p["ob_net"],
            "bh_events": _sum_side(by_day[d]["bh"]), "ob_events": _sum_side(by_day[d]["ob"]),
            "causes": [{"type": c.get("type"), "impact": c.get("impact"), "desc": c.get("desc")} for c in cs],
            "verdict": verdict, "kind": vkind,
        })

    real_days = sorted({c["date"] for c in changes if c["kind"] != "timing"})
    opening_linked = any(c.get("type") == "기간이전 선반영(상쇄)" for c in causes)
    opening_cause = next((c for c in causes if c.get("type") == "기간이전차이"), None)
    if opening_cause:
        first_gap = f"{from_date} 이전"
    elif real_days:
        first_gap = real_days[0]
    else:
        first_gap = ""
    last_change = real_days[-1] if real_days else ""

    # ── 원인 유형 집계 + 한 줄 요약 ──
    ctypes: dict = defaultdict(lambda: {"count": 0, "impact": 0})
    for c in causes:
        t = c.get("type") or "기타"
        ctypes[t]["count"] += 1
        ctypes[t]["impact"] += int(c.get("impact") or 0)
    cause_types = [{"type": t, **v} for t, v in sorted(ctypes.items(), key=lambda x: -abs(x[1]["impact"]))]
    top = [c for c in causes if c.get("impact")][:5]

    fmt = lambda n: f"{int(n):+,}"
    parts = []
    if diff_now is None:
        headline = "한쪽 시스템에만 재고가 있어 비교 불가"
    elif diff_now == 0 and not top:
        headline = "일치 — 차이 없음"
    else:
        parts.append(f"현재 차이 {fmt(diff_now)} (BH {int(bh_stock or 0):,} vs OB {int(ob_total or 0):,}).")
        if opening_cause:
            parts.append(f"{from_date} 이전에 이미 {fmt(opening)} 차이가 있었고,")
        elif opening_linked:
            parts.append("조회 시작 전 차이는 기간 초 한쪽 선반영 전표와 상쇄됐고,")
        if top:
            seg = []
            for c in top[:2]:
                who = c.get("type") or ""
                seg.append(f"{c.get('date')} {who} {fmt(c.get('impact') or 0)}")
            parts.append("주요 원인은 " + ", ".join(seg) + ("" if len(top) <= 2 else f" 외 {len(top)-2}건") + ".")
        elif opening_cause:
            parts.append("기간 중 새로 벌어진 차이는 없습니다.")
        resid = trace.get("residual")
        if resid not in (None, 0):
            parts.append(f"(설명되지 않은 잔차 {fmt(resid)})")
        headline = " ".join(parts)

    light = {
        "key": row.get("key"), "name": name,
        "bh_skus": row.get("bh_skus") or [], "ob_codes": row.get("ob_codes") or [],
        "bh_stock": bh_stock, "ob_total": ob_total, "ob_avail": ob_avail, "ob_unav": ob_unav,
        "diff": diff_now, "only_side": row.get("only_side"), "manual": row.get("manual"),
        "opening_gap": trace.get("opening_gap"), "flow_diff": trace.get("flow_diff"),
        "explained": trace.get("explained"), "residual": trace.get("residual"),
        "first_gap_date": first_gap, "last_change_date": last_change,
        "change_days": len([c for c in changes if c["kind"] != "timing"]),
        "timing_days": len([c for c in changes if c["kind"] == "timing"]),
        "cause_types": cause_types,
        "top_causes": [{"type": c.get("type"), "impact": c.get("impact"), "date": c.get("date"),
                        "qty": c.get("qty"), "desc": c.get("desc"), "memo": c.get("memo"),
                        "channel": c.get("channel")} for c in top],
        "headline": headline,
        "bh_in": trace["totals"]["bh_in"], "bh_out": trace["totals"]["bh_out"],
        "ob_in": trace["totals"]["ob_in"], "ob_out": trace["totals"]["ob_out"],
        "n_changes": len(changes),
    }
    full = {**light, "trace": trace, "changes": changes,
            "series": [{"date": p["date"], "gap": p["gap"], "bh": p["bh"], "ob": p["ob"]} for p in pts if p["d_gap"] != 0]}
    return light, full


# ── 엔드포인트 ────────────────────────────────────────────────────────────────
@router.get("/analyze")
def analyze(
    token: str = Query(""),
    location_ids: str = Query("228640"),
    months: int = Query(3),
    tol_days: int = Query(3),
):
    """전 품목 재고 차이 분해. 결과는 메모리·파일에 보관해 /last, /item 으로 다시 읽는다."""
    cfg = U.load_config()
    token = token or str(cfg.get("api_token") or "")
    if not token:
        raise HTTPException(400, "박스히어로 API 토큰이 없습니다 (설정에서 연결)")
    months = max(1, min(int(months), 12))
    from_date, to_date = _window(months)
    loc_set = {int(x) for x in str(location_ids or "").split(",") if x.strip().isdigit()}
    errors: list = []
    t0 = _time.time()

    stock = R.get_stock(token=token, location_ids=location_ids or "", use_mapping=True)
    rows = stock.get("rows") or []
    if not rows:
        raise HTTPException(502, "현재 재고를 가져오지 못했습니다 (BH 토큰 또는 OB API 확인)")

    # 오늘 양쪽 재고를 일별 스냅샷으로 남긴다 (앞으로의 '언제' 근거)
    try:
        import receiving_db as _db
        _db.save_stock_gap_daily(_date.today().isoformat(), location_ids or "", rows)
    except Exception as e:
        errors.append(f"[정보] 일별 스냅샷 저장 실패: {str(e)[:60]}")

    events, daily_out, daily_in, ob_source = _build_events(token, from_date, to_date, loc_set, errors)
    if ob_source in ("failed", "none"):
        # OB 거래가 없으면 모든 BH 거래가 'BH만 기록'으로 잡혀 결과가 전부 오염된다 → 결과를 내지 않는다
        raise HTTPException(502, "아워박스 거래 수집 실패 — 결과를 만들지 않았습니다. " + " / ".join(errors)[:300])
    unav_by_code = _load_unav_by_code(from_date)
    observed = _load_observed(location_ids or "", from_date)

    items: list = []
    full: dict = {}
    for row in rows:
        key = row.get("key")
        ev = events.get(key) or {}
        unav_ev = _unav_events_for(row.get("ob_codes") or [], unav_by_code)
        try:
            light, det = _analyze_item(row, ev, from_date, to_date, tol_days, daily_out, daily_in, unav_ev)
        except Exception as e:
            light = {"key": key, "name": row.get("name"), "diff": row.get("diff"),
                     "bh_stock": row.get("bh_stock"), "ob_total": row.get("ob_stock_total"),
                     "ob_unav": row.get("ob_unusable"), "only_side": row.get("only_side"),
                     "headline": f"분해 실패: {str(e)[:60]}", "cause_types": [], "top_causes": [],
                     "opening_gap": None, "flow_diff": None, "explained": None, "residual": None,
                     "first_gap_date": "", "last_change_date": "", "n_changes": 0,
                     "change_days": 0, "timing_days": 0, "bh_skus": row.get("bh_skus") or [],
                     "ob_codes": row.get("ob_codes") or []}
            det = {**light, "trace": None, "changes": [], "series": []}
        obs = observed.get(_nrm(row.get("name")), [])
        light["observed"] = obs
        det["observed"] = obs
        items.append(light)
        full[key] = det

    diff_items = [i for i in items if i.get("diff") not in (None, 0)]
    ok_items = [i for i in items if i.get("diff") == 0]
    only_bh = [i for i in items if i.get("only_side") == "bh"]
    only_ob = [i for i in items if i.get("only_side") == "ob"]
    # 발생 시점 특정 = 조회 시작 전 차이가 0이고(전부 기간 안에서 생김) 등식 잔차도 0인 품목
    opening_items = [i for i in diff_items if i.get("opening_gap") not in (None, 0)]
    explained_items = [i for i in diff_items if i.get("opening_gap") == 0 and i.get("residual") == 0]
    unresolved_items = [i for i in diff_items if i.get("residual") not in (None, 0)]

    ctot: dict = defaultdict(lambda: {"count": 0, "impact": 0, "items": 0})
    for i in diff_items:
        for ct in i.get("cause_types") or []:
            ctot[ct["type"]]["count"] += ct["count"]
            ctot[ct["type"]]["impact"] += ct["impact"]
            ctot[ct["type"]]["items"] += 1
    cause_summary = [{"type": t, **v} for t, v in sorted(ctot.items(), key=lambda x: -abs(x[1]["impact"]))]

    items.sort(key=lambda i: -(abs(i["diff"]) if i.get("diff") is not None else -1))
    result = {
        "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": from_date, "to": to_date, "months": months, "tol_days": tol_days,
        "location_ids": location_ids, "ob_source": ob_source,
        "elapsed_sec": round(_time.time() - t0, 1),
        "summary": {
            "total": len(items), "ok": len(ok_items), "diff": len(diff_items),
            "bh_more": len([i for i in diff_items if i["diff"] > 0]),
            "ob_more": len([i for i in diff_items if i["diff"] < 0]),
            "abs_gap": sum(abs(i["diff"]) for i in diff_items),
            "only_bh": len(only_bh), "only_ob": len(only_ob),
            "explained": len(explained_items),
            "opening": len(opening_items),
            "unresolved": len(unresolved_items),
            "cause_summary": cause_summary,
        },
        "errors": errors,
        "items": items,
    }
    with _LOCK:
        _LAST["result"] = result
        _LAST["full"] = full
    try:
        os.makedirs(os.path.dirname(_LAST_PATH), exist_ok=True)
        with open(_LAST_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass
    return result


@router.get("/last")
def last():
    """마지막 분석 결과(가벼운 목록). 백엔드 재시작 후엔 파일에서 복원."""
    with _LOCK:
        if _LAST["result"]:
            return _LAST["result"]
    try:
        with open(_LAST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": [], "summary": None}


@router.get("/item")
def item(key: str = Query(...)):
    """한 품목의 상세(정밀 대사 trace + 일자별 변동 + 복원 시계열). /analyze 이후에만 유효."""
    with _LOCK:
        det = _LAST["full"].get(key)
    if not det:
        raise HTTPException(404, "분석 결과가 없습니다. '분석 실행'을 먼저 눌러 주세요 (백엔드 재시작 후엔 다시 실행)")
    return det


@router.get("/history")
def history(location_ids: str = Query("228640"), days: int = Query(180), name: str = Query("")):
    """실측 일별/주간 차이 이력. name이 있으면 그 품목만."""
    since = (_date.today() - timedelta(days=int(days))).isoformat()
    obs = _load_observed(location_ids or "", since)
    if name:
        return {"name": name, "points": obs.get(_nrm(name), [])}
    return {"since": since, "items": {k: v for k, v in obs.items()}}


@router.post("/capture")
def capture(token: str = Query(""), location_ids: str = Query("228640")):
    """오늘의 양쪽 재고를 일별 스냅샷으로 저장(분석 없이)."""
    return capture_daily(token, location_ids)


def capture_daily(token: str = "", location_ids: str = "228640") -> dict:
    import receiving_db as _db
    cfg = U.load_config()
    token = token or str(cfg.get("api_token") or "")
    if not token:
        return {"saved": False, "error": "BH 토큰 없음"}
    stock = R.get_stock(token=token, location_ids=location_ids or "", use_mapping=True)
    rows = stock.get("rows") or []
    both = [r for r in rows if r.get("bh_stock") is not None and r.get("ob_stock_total") is not None]
    if not both:
        return {"saved": False, "error": "양쪽 재고가 모두 있는 행이 없음 (BH/OB 조회 실패?)"}
    n = _db.save_stock_gap_daily(_date.today().isoformat(), location_ids or "", rows)
    return {"saved": True, "date": _date.today().isoformat(), "count": n}


# ── 일별 자동 스냅샷 스케줄러 (백엔드 가동 중, 하루 1회) ─────────────────────
def start_daily_capture(location_ids: str = "228640", hour: int = 7):
    def _loop():
        import receiving_db as _db
        _time.sleep(90)   # 기동 직후 OB 스냅샷 스케줄러와 겹치지 않게
        while True:
            try:
                today = _date.today().isoformat()
                if datetime.now().hour >= hour and not _db.has_stock_gap_daily(today, location_ids):
                    capture_daily("", location_ids)
            except Exception:
                pass
            _time.sleep(30 * 60)
    _threading.Thread(target=_loop, daemon=True, name="stock-gap-daily").start()
