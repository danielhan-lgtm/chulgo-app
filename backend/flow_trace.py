"""거래 흐름 정밀 대사(flow trace) — 단일 품목의 BH·OB 거래 이벤트를
일자별로 짝지어 재고 차이가 '어느 거래에서' 났는지 분해하는 순수 로직.

방법론 (메노포즈 6/12개입 수동 대사에서 검증된 방식):
  1. 기초 정합: 기간 시작 시점 차이 = 현재차이 − (BH순흐름 − OB순흐름)
  2. 이벤트 매칭: 같은 수량의 입/출고를 ±N일 허용오차로 짝짓기 (전표 시점차 흡수)
  3. 조합 매칭: 한쪽 1건 = 반대쪽 여러 건 합 (예: BH 651+154 = OB 805)
  4. 잔여 이벤트 = 진짜 차이 발생 지점 → 원인 자동 분류:
     · 교차기록: 같은 날 다른 품목이 정확히 반대 방향으로 어긋남 (품목 엇갈림 출고)
     · 선차감/가용외: 기간 말 BH만 차감 + OB 가용외 보유 (미리주문 선등록)
     · 기간경계: 조회 구간 경계 ±2일 → 시점차일 가능성
     · BH만/OB만 기록: 누락 의심

FastAPI 의존성 없음 — 단독 테스트 가능.
"""
from datetime import datetime, timedelta
from itertools import combinations
from typing import Optional

DATE_FMT = "%Y-%m-%d"


def _d(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(s)[:10], DATE_FMT)
    except Exception:
        return None


def _day_gap(a: str, b: str) -> int:
    da, db = _d(a), _d(b)
    if not da or not db:
        return 9999
    return abs((da - db).days)


# ── 이벤트 매칭 ─────────────────────────────────────────────────────────────
# 이벤트: {"date": "YYYY-MM-DD", "qty": int(>0), "memo": str, "channel": str, "type": str}

def match_events(bh_events: list, ob_events: list, tol_days: int = 3,
                 combo_max: int = 4) -> dict:
    """같은 방향(입고끼리/출고끼리) 이벤트를 수량 기준으로 짝짓기.

    1단계: 동일 수량 1:1 (날짜 가까운 순 greedy)
    2단계: 1:N 조합 (한쪽 1건 수량 = 반대쪽 ≤combo_max건 합, 날짜창 안)
    Returns: {"matched": [...], "bh_only": [...], "ob_only": [...]}
    """
    bh = sorted(bh_events, key=lambda e: (e["date"], -e["qty"]))
    ob = sorted(ob_events, key=lambda e: (e["date"], -e["qty"]))
    used_b = [False] * len(bh)
    used_o = [False] * len(ob)
    matched = []

    # ── 1단계: 동일 수량 1:1, 날짜차 오름차순 greedy ──
    pairs = []
    for i, b in enumerate(bh):
        for j, o in enumerate(ob):
            if b["qty"] == o["qty"]:
                g = _day_gap(b["date"], o["date"])
                if g <= tol_days:
                    pairs.append((g, i, j))
    pairs.sort()
    for g, i, j in pairs:
        if used_b[i] or used_o[j]:
            continue
        used_b[i] = True; used_o[j] = True
        matched.append({"bh": [bh[i]], "ob": [ob[j]], "qty": bh[i]["qty"],
                        "day_gap": g, "kind": "1:1"})

    # ── 2단계: 1:N 조합 매칭 ──
    def _combo_pass(single_side, single_used, multi_side, multi_used, label):
        for i, s in enumerate(single_side):
            if single_used[i]:
                continue
            sd = _d(s["date"])
            if not sd:
                continue
            cand = [j for j, m in enumerate(multi_side)
                    if not multi_used[j] and m["qty"] < s["qty"]
                    and _day_gap(s["date"], m["date"]) <= tol_days]
            if not cand:
                continue
            # 조합 폭발 방지: 후보가 많으면 날짜 가까운 순 상위 12개만 탐색
            # (C(12,4)=495 — 주문라인 단위 이벤트 수천 건이 들어와도 안전)
            if len(cand) > 12:
                cand.sort(key=lambda j: (_day_gap(s["date"], multi_side[j]["date"]),
                                         -multi_side[j]["qty"]))
                cand = cand[:12]
            found = None
            for r in range(2, min(combo_max, len(cand)) + 1):
                for combo in combinations(cand, r):
                    if sum(multi_side[j]["qty"] for j in combo) == s["qty"]:
                        found = combo
                        break
                if found:
                    break
            if found:
                single_used[i] = True
                for j in found:
                    multi_used[j] = True
                grp = [multi_side[j] for j in found]
                if label == "ob1":   # OB 1건 = BH N건
                    matched.append({"bh": grp, "ob": [s], "qty": s["qty"],
                                    "day_gap": max(_day_gap(s["date"], m["date"]) for m in grp),
                                    "kind": f"N:1({len(grp)})"})
                else:                 # BH 1건 = OB N건
                    matched.append({"bh": [s], "ob": grp, "qty": s["qty"],
                                    "day_gap": max(_day_gap(s["date"], m["date"]) for m in grp),
                                    "kind": f"1:N({len(grp)})"})

    _combo_pass(ob, used_o, bh, used_b, "ob1")   # OB 1건 = BH 여러 건 (예: OB 805 = BH 651+154)
    _combo_pass(bh, used_b, ob, used_o, "bh1")   # BH 1건 = OB 여러 건

    bh_only = [b for i, b in enumerate(bh) if not used_b[i]]
    ob_only = [o for j, o in enumerate(ob) if not used_o[j]]
    return {"matched": matched, "bh_only": bh_only, "ob_only": ob_only}


# ── 교차기록(품목 엇갈림) 탐지 ──────────────────────────────────────────────

def find_cross_partner(event: dict, direction: str, side: str,
                       daily_out_by_prod: dict, daily_in_by_prod: dict,
                       target_label: str, tol_days: int = 1) -> Optional[dict]:
    """잔여 이벤트와 정확히 반대로 어긋난 다른 품목을 찾는다.

    예: 7/8 BH가 6개입 102개 출고(OB 기록 없음) + 같은 날 OB가 12개입을 BH보다
    102개 더 출고 → 두 시스템이 같은 주문을 다른 품목으로 기록한 것.

    direction: "out"|"in", side: "bh_only"|"ob_only"
    daily_*_by_prod: {product_label: {date: {"bh": qty, "ob": qty}}}
    Returns: {"product", "date", "qty"} 또는 None
    """
    table = daily_out_by_prod if direction == "out" else daily_in_by_prod
    q = event["qty"]
    ed = _d(event["date"])
    if not ed:
        return None
    # side=bh_only(BH만 기록, diff=bh-ob=+q) → 반대 품목은 diff=-q 여야 함
    want = -q if side == "bh_only" else q
    best = None
    for prod, days in table.items():
        if prod == target_label:
            continue
        for ds, v in days.items():
            dd = _d(ds)
            if not dd or abs((dd - ed).days) > tol_days:
                continue
            d_diff = int(v.get("bh", 0)) - int(v.get("ob", 0))
            if d_diff == want:
                cand = {"product": prod, "date": ds, "qty": q,
                        "day_gap": abs((dd - ed).days)}
                if best is None or cand["day_gap"] < best["day_gap"]:
                    best = cand
    return best


# ── 잔여 이벤트 상쇄 (N:M 분할기록·부분차이 흡수) ──────────────────────────

def _interval_cancel(bh_left: list, ob_left: list, tol_days: int) -> tuple:
    """구간 순상쇄: 날짜순 누적 순차(BH−OB)가 0으로 돌아오는 구간을 통째로 상쇄.
    (예: BH 65+800+7 = OB 465+407 — 3일에 걸친 N:M 분할 기록)
    구간 길이가 tol_days+2일을 넘으면 상쇄하지 않음(우연 상쇄 방지).
    Returns: (남은 bh, 남은 ob, 상쇄된 구간 리스트)"""
    ev = ([("bh", e) for e in bh_left] + [("ob", e) for e in ob_left])
    ev.sort(key=lambda x: (x[1]["date"], x[0]))
    cancelled = []
    keep_b, keep_o = [], []
    seg, running = [], 0
    for side, e in ev:
        running += e["qty"] if side == "bh" else -e["qty"]
        seg.append((side, e))
        if running == 0:
            span = _day_gap(seg[0][1]["date"], seg[-1][1]["date"])
            has_both = any(s == "bh" for s, _ in seg) and any(s == "ob" for s, _ in seg)
            if has_both and span <= tol_days + 2:
                cancelled.append({
                    "kind": "구간상쇄(분할기록)",
                    "qty": sum(x[1]["qty"] for x in seg if x[0] == "bh"),
                    "from": seg[0][1]["date"], "to": seg[-1][1]["date"],
                    "bh": [x[1] for x in seg if x[0] == "bh"],
                    "ob": [x[1] for x in seg if x[0] == "ob"],
                })
            else:
                for s, x in seg:
                    (keep_b if s == "bh" else keep_o).append(x)
            seg = []
    for s, x in seg:   # 0으로 못 돌아온 꼬리
        (keep_b if s == "bh" else keep_o).append(x)
    return keep_b, keep_o, cancelled


def _daynet_cancel(bh_left: list, ob_left: list) -> tuple:
    """동일일 부분상쇄: 같은 날 양쪽에 잔여가 있으면 겹치는 만큼 상쇄하고
    순차(net)만 합성 이벤트로 남김. (예: 7/8 BH 372 vs OB 474 → OB만 102 잔존)"""
    from collections import defaultdict
    by_date: dict = defaultdict(lambda: {"bh": [], "ob": []})
    for e in bh_left:
        by_date[e["date"]]["bh"].append(e)
    for e in ob_left:
        by_date[e["date"]]["ob"].append(e)
    keep_b, keep_o, cancelled = [], [], []
    for d, g in sorted(by_date.items()):
        sb = sum(e["qty"] for e in g["bh"])
        so = sum(e["qty"] for e in g["ob"])
        if not g["bh"] or not g["ob"]:
            keep_b += g["bh"]; keep_o += g["ob"]
            continue
        net = sb - so
        cancelled.append({"kind": "동일일상쇄(부분)", "qty": min(sb, so), "from": d, "to": d,
                          "bh": g["bh"], "ob": g["ob"]})
        if net != 0:
            memos = " / ".join(filter(None, {e.get("memo", "") for e in (g["bh"] if net > 0 else g["ob"])}))
            synth = {"date": d, "qty": abs(net),
                     "memo": (memos or "")[:80],
                     "channel": " / ".join(filter(None, {e.get("channel", "") for e in g["ob"]}))[:40],
                     "type": "합계차", "synthetic": True,
                     "detail": f"동일일 잔여 BH {sb:,} vs OB {so:,} 부분상쇄 후 순차 {net:+,}"}
            (keep_b if net > 0 else keep_o).append(synth)
    return keep_b, keep_o, cancelled


# ── 선차감→발송 장기 시점차 짝짓기 ─────────────────────────────────────────

def _longgap_pair(bh_left: list, ob_left: list, unav_events: list,
                  tol_days: int, max_gap: int = 45) -> tuple:
    """BH가 먼저 출고 차감(선등록)하고 OB가 며칠~몇 주 뒤 실제 발송하는 패턴.
    같은 수량의 BH 선행 출고 ↔ OB 후행 출고를 tol_days 초과 ~ max_gap일 안에서
    짝지어 상쇄. BH 차감 시점 근처에 가용외(+할당) 스냅샷이 있으면 '확인' 등급.
    Returns: (남은 bh, 남은 ob, 짝 리스트)"""
    pairs = []
    for i, b in enumerate(bh_left):
        for j, o in enumerate(ob_left):
            if b["qty"] != o["qty"]:
                continue
            db, do = _d(b["date"]), _d(o["date"])
            if not db or not do:
                continue
            gap = (do - db).days          # OB 발송이 BH 차감보다 뒤여야 함
            if tol_days < gap <= max_gap:
                pairs.append((gap, i, j))
    pairs.sort()
    used_b, used_o = set(), set()
    out_pairs = []
    for gap, i, j in pairs:
        if i in used_b or j in used_o:
            continue
        used_b.add(i); used_o.add(j)
        b = bh_left[i]
        snap_ok = any(int(u.get("delta", 0)) > 0 and _day_gap(u["date"], b["date"]) <= tol_days + 1
                      for u in (unav_events or []))
        out_pairs.append({"bh": b, "ob": ob_left[j], "gap": gap, "snap_ok": snap_ok})
    keep_b = [b for i, b in enumerate(bh_left) if i not in used_b]
    keep_o = [o for j, o in enumerate(ob_left) if j not in used_o]
    return keep_b, keep_o, out_pairs


# ── 종합 분해 ───────────────────────────────────────────────────────────────

def trace_item(bh_in: list, bh_out: list, ob_in: list, ob_out: list,
               diff_now: Optional[int] = None, ob_unav: int = 0,
               from_date: str = "", to_date: str = "",
               tol_days: int = 3,
               daily_out_by_prod: Optional[dict] = None,
               daily_in_by_prod: Optional[dict] = None,
               target_label: str = "",
               unav_events: Optional[list] = None) -> dict:
    """단일 품목 종합 대사. 반환: 요약 + 원인 리스트 + 잔여 이벤트.

    diff = BH재고 − OB총재고 기준 부호:
      BH만 출고 → −q (BH가 더 적어짐)  /  OB만 출고 → +q
      BH만 입고 → +q                  /  OB만 입고 → −q

    unav_events: OB 가용외 일별 변화 [{date, delta}] (스냅샷 기반, 선택).
      선차감 판정을 '추정'에서 스냅샷 '확인'으로 승격하는 데 사용.
    """
    m_in = match_events(bh_in, ob_in, tol_days)
    m_out = match_events(bh_out, ob_out, tol_days)

    # 2차: 구간 순상쇄(N:M 분할 기록) → 3차: 동일일 부분상쇄(합계 차이만 잔존)
    cancelled_all = []
    for m in (m_in, m_out):
        b2, o2, c1 = _interval_cancel(m["bh_only"], m["ob_only"], tol_days)
        b3, o3, c2 = _daynet_cancel(b2, o2)
        m["bh_only"], m["ob_only"] = b3, o3
        cancelled_all += c1 + c2

    t = {
        "bh_in": sum(e["qty"] for e in bh_in), "ob_in": sum(e["qty"] for e in ob_in),
        "bh_out": sum(e["qty"] for e in bh_out), "ob_out": sum(e["qty"] for e in ob_out),
    }
    bh_net = t["bh_in"] - t["bh_out"]
    ob_net = t["ob_in"] - t["ob_out"]
    flow_diff = bh_net - ob_net                     # 기간 중 벌어진 차이
    opening_gap = (diff_now - flow_diff) if diff_now is not None else None

    causes = []
    opening_linked = False
    if opening_gap not in (None, 0):
        # 기초차이 자동 연결: 기간 초 반대 부호의 한쪽 기록이 기초차이와 정확히 같으면
        # "기간 이전 선반영"(한쪽이 먼저 전표 처리)으로 묶어 상쇄 표시
        #   opening>0 (BH가 많았음) ↔ OB만 입고 = -q  /  opening<0 ↔ BH만 입고 = +q 등
        def _try_link(pool: list, side: str, direction: str) -> bool:
            impact_sign = (1 if direction == "in" else -1) * (1 if side == "bh" else -1)
            for i, e in enumerate(pool):
                if e["qty"] == abs(opening_gap) and impact_sign * e["qty"] == -opening_gap \
                        and from_date and _day_gap(e["date"], from_date) <= tol_days + 2:
                    pool.pop(i)
                    who = "BH" if side == "bh" else "OB"
                    causes.append({
                        "type": "기간이전 선반영(상쇄)", "impact": 0, "qty": e["qty"], "date": e["date"],
                        "memo": e.get("memo", ""), "channel": e.get("channel", ""), "ev_type": e.get("type", ""),
                        "desc": f"조회 시작 전 차이 {opening_gap:+,} = {e['date']} {who}만 "
                                f"{'입고' if direction == 'in' else '출고'} {e['qty']:,}와 정확히 상쇄 — "
                                f"한쪽이 기간 이전에 먼저 전표 처리한 시점차로 추정. 차이에 영향 없음",
                    })
                    return True
            return False
        opening_linked = (_try_link(m_in["ob_only"], "ob", "in") or
                          _try_link(m_in["bh_only"], "bh", "in") or
                          _try_link(m_out["ob_only"], "ob", "out") or
                          _try_link(m_out["bh_only"], "bh", "out"))
        if not opening_linked:
            causes.append({
                "type": "기간이전차이", "impact": opening_gap, "date": from_date, "qty": abs(opening_gap),
                "desc": f"조회 시작({from_date}) 이전에 이미 있던 차이 {opening_gap:+,}. "
                        f"기간을 늘려 다시 추적하면 발생 시점을 좁힐 수 있습니다.",
            })

    # 선차감→발송 장기 시점차: BH 선행 출고 ↔ OB 후행 출고(같은 수량, tol 초과 ~45일)를
    # 짝지어 상쇄 — 그대로 두면 'BH만 기록 −q'와 'OB만 기록 +q' 두 원인으로 흩어져 혼란스러움
    m_out["bh_only"], m_out["ob_only"], longgap_pairs = _longgap_pair(
        m_out["bh_only"], m_out["ob_only"], unav_events or [], tol_days)
    for p in longgap_pairs:
        causes.append({
            "type": "선차감→발송(상쇄)", "impact": 0, "qty": p["bh"]["qty"],
            "date": p["bh"]["date"],
            "memo": p["bh"].get("memo", ""), "channel": p["ob"].get("channel", ""),
            "ev_type": "출고",
            "desc": f"{p['bh']['date']} BH 선차감 출고 {p['bh']['qty']:,} → {p['ob']['date']} "
                    f"OB 실제 발송 ({p['gap']}일 시점차)"
                    f"{' — 가용외 할당 스냅샷으로 확인' if p['snap_ok'] else ' — 수량 일치 기반 추정'}. "
                    f"상쇄되어 차이에 영향 없음",
        })

    def _near_boundary(ds: str) -> bool:
        return (from_date and _day_gap(ds, from_date) <= 2) or \
               (to_date and _day_gap(ds, to_date) <= 2)

    unav_linked = 0   # 가용외로 설명된 누적 수량
    # 스냅샷 기반 가용외 증가 풀 — BH만 출고를 실제 할당 발생과 대조해 '확인' 판정
    unav_pool = [{"date": u["date"], "left": int(u.get("delta", 0))}
                 for u in (unav_events or []) if int(u.get("delta", 0)) > 0]

    def _classify(ev: dict, direction: str, side: str):
        nonlocal unav_linked
        q = ev["qty"]
        impact = (q if direction == "in" else -q) if side == "bh_only" else \
                 (-q if direction == "in" else q)
        base = {"impact": impact, "qty": q, "date": ev["date"],
                "memo": ev.get("memo", ""), "channel": ev.get("channel", ""),
                "ev_type": ev.get("type", "")}
        # 1) 교차기록: 다른 품목이 같은 날 정반대로 어긋남
        if daily_out_by_prod is not None:
            p = find_cross_partner(ev, direction, side, daily_out_by_prod,
                                   daily_in_by_prod or {}, target_label)
            if p:
                who = "BH" if side == "bh_only" else "OB"
                return {**base, "type": "교차기록의심", "partner": p["product"],
                        "desc": f"{ev['date']} {who}만 {'입고' if direction=='in' else '출고'} {q:,} "
                                f"({ev.get('memo') or ev.get('channel') or ''}) — 같은 날 "
                                f"'{p['product']}'이(가) 정확히 반대로 {q:,} 어긋남. "
                                f"같은 주문을 서로 다른 품목으로 기록했을 가능성 → 실제 출고 품목 확인 후 한쪽 수정"}
        # 2a) 선차감(가용외확인): BH만 출고한 시점 근처에 OB 가용외가 실제로 그만큼 증가(스냅샷)
        if side == "bh_only" and direction == "out":
            for u in unav_pool:
                if u["left"] >= q and _day_gap(ev["date"], u["date"]) <= tol_days + 1:
                    u["left"] -= q
                    return {**base, "type": "선차감(가용외확인)",
                            "desc": f"{ev['date']} BH만 출고 {q:,} ({ev.get('memo','')}) — 같은 시기 "
                                    f"OB 가용외가 {u['date']}에 그만큼 증가(스냅샷 확인). 주문이 OB에 "
                                    f"할당(가용외) 상태로 보류 중 → 실제 발송되면 자동 정합"}
        # 2b) 선차감/가용외 (휴리스틱): 기간 말 BH만 출고 + OB 가용외 잔량
        if side == "bh_only" and direction == "out" and to_date \
                and _day_gap(ev["date"], to_date) <= 7 and unav_linked + q <= max(ob_unav, 0):
            unav_linked += q
            return {**base, "type": "선차감(가용외추정)",
                    "desc": f"{ev['date']} BH만 출고 {q:,} ({ev.get('memo','')}) — OB엔 출고 없음. "
                            f"OB 가용외 {ob_unav:,}개 보유 중 → 미리주문 선차감분이 OB에 할당(가용외) 상태일 "
                            f"가능성. 실제 발송되면 자동 정합"}
        # 3) 기간 경계 시점차 가능성
        if _near_boundary(ev["date"]):
            who = "BH" if side == "bh_only" else "OB"
            return {**base, "type": "기간경계(시점차가능)",
                    "desc": f"{ev['date']} {who}만 {'입고' if direction=='in' else '출고'} {q:,} — "
                            f"조회 경계 근처라 반대쪽 전표가 기간 밖(1~2일 차)일 수 있음. 기간을 넓혀 재확인"}
        # 4) 순수 한쪽 기록
        who = "BH" if side == "bh_only" else "OB"
        return {**base, "type": f"{who}만 기록",
                "desc": f"{ev['date']} {who}에만 {'입고' if direction=='in' else '출고'} {q:,} "
                        f"({ev.get('memo') or ev.get('channel') or ''}) — 반대쪽 누락 또는 별도 처리(직배송·세트변경·조정) 확인"}

    for ev in m_in["bh_only"]:
        causes.append(_classify(ev, "in", "bh_only"))
    for ev in m_in["ob_only"]:
        causes.append(_classify(ev, "in", "ob_only"))
    for ev in m_out["bh_only"]:
        causes.append(_classify(ev, "out", "bh_only"))
    for ev in m_out["ob_only"]:
        causes.append(_classify(ev, "out", "ob_only"))

    explained = sum(c["impact"] for c in causes)
    residual = (diff_now - explained) if diff_now is not None else None

    causes.sort(key=lambda c: -abs(c["impact"]))
    return {
        "totals": t,
        "flow_diff": flow_diff,
        "opening_gap": opening_gap,
        "diff_now": diff_now,
        "explained": explained,
        "residual": residual,          # 0이어야 정상 (등식 검증)
        "causes": causes,
        "matched_in": len(m_in["matched"]), "matched_out": len(m_out["matched"]),
        "matched_pairs": {
            "in": m_in["matched"], "out": m_out["matched"],
        },
        "cancelled": cancelled_all,   # 구간상쇄·동일일상쇄 내역 (감사용)
        "unmatched": {
            "bh_in": m_in["bh_only"], "ob_in": m_in["ob_only"],
            "bh_out": m_out["bh_only"], "ob_out": m_out["ob_only"],
        },
    }
