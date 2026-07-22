"""출고 추세 & 재고 소진 예측 — 박스히어로 출고(out) 거래 기반.

거래처별·품목별로 일/주/월 단위 출고 수량을 집계하고,
최근 추세(일평균)를 그대로 연장했을 때 향후 N개월간 예상 출고량과
현재고(기초재고) 기준 재고 소진 시점을 추정한다.
"""
import sys, os, io, json, re
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
import requests as _req

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U

router = APIRouter()

BH_BASE = "https://rest.boxhero-app.com"
_DAYS_PER_MONTH = 30.44  # 평균 한 달 일수 (월평균 환산용)
_EXCLUDE = "__EXCLUDE__"  # 거래처-팀 매핑에서 '의도적 제외' 표식

# ── 판매 목표치 저장소 (팀별 월간 목표 → 달성률 비교용) ─────────────────────────
_TARGETS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sales_targets.json")
_TARGET_MATCH_THRESHOLD = 80  # 목표 상품명 ↔ BH 품목명 fuzzy 매칭 임계값
_MONTH_RE = re.compile(r"(20\d{2})\D{0,2}(1[0-2]|0?[1-9])(?!\d)")  # 'YYYY-MM' 등에서 연-월 추출 (두자리 월 우선)


def _load_targets() -> dict:
    try:
        if os.path.exists(_TARGETS_PATH):
            with open(_TARGETS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_targets(data: dict) -> None:
    os.makedirs(os.path.dirname(_TARGETS_PATH), exist_ok=True)
    with open(_TARGETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_target_workbook(raw: bytes) -> dict:
    """판매예상량(목표) 엑셀 파싱.

    각 시트(=팀)별로 '브랜드 | 공통상품명 | YYYY-MM | YYYY-MM | …' 형태를 기대.
    헤더 행을 자동 탐지하고, 월 컬럼은 'YYYY-MM\\n🔒확정' 같은 라벨에서 연-월만 추출한다.
    여러 시트의 동일 상품명은 월별로 합산하고, 팀별 분해(by_team)도 함께 보존한다.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)

    products: dict = {}   # norm_name -> {name, brand, by_month, by_team}
    all_months: set = set()
    teams: list = []
    team_labels: dict = {}  # 'BD' -> '올리브영' (시트 제목 괄호에서 추출)

    for ws in wb.worksheets:
        team = ws.title
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        # 1. 헤더 행 탐지: '공통상품명' 또는 '상품명' 포함 + 월 컬럼 존재
        header_idx = None
        for i, row in enumerate(rows[:10]):
            cells = [str(c) if c is not None else "" for c in row]
            if any("상품명" in c for c in cells) and any(_MONTH_RE.search(c) for c in cells):
                header_idx = i
                break
        if header_idx is None:
            continue
        header = [str(c) if c is not None else "" for c in rows[header_idx]]

        # 팀 설명 라벨 추출: 헤더 위 제목행의 '...(올리브영)...' 괄호 내용
        for trow in rows[:header_idx]:
            title = " ".join(str(c) for c in trow if c)
            mlab = re.search(rf"{re.escape(team)}\s*\(([^)]+)\)", title) or re.search(r"\(([^)]{2,15})\)", title)
            if mlab:
                team_labels[team] = mlab.group(1).strip()
                break

        # 2. 컬럼 위치 파악
        brand_col = next((j for j, c in enumerate(header) if "브랜드" in c), None)
        name_col = next((j for j, c in enumerate(header) if "상품명" in c), None)
        if name_col is None:
            continue
        month_cols: dict = {}  # col_idx -> 'YYYY-MM'
        for j, c in enumerate(header):
            m = _MONTH_RE.search(c)
            if m:
                month_cols[j] = f"{m.group(1)}-{int(m.group(2)):02d}"

        if not month_cols:
            continue
        teams.append(team)

        # 3. 데이터 행 적재
        for row in rows[header_idx + 1:]:
            if name_col >= len(row):
                continue
            name = row[name_col]
            if not name or not str(name).strip():
                continue
            name = str(name).strip()
            brand = str(row[brand_col]).strip() if (brand_col is not None and brand_col < len(row) and row[brand_col]) else ""
            norm = U.normalize(name)
            if not norm:
                continue
            p = products.setdefault(norm, {"name": name, "brand": brand, "by_month": {}, "by_team": {}})
            if brand and not p["brand"]:
                p["brand"] = brand
            for j, month in month_cols.items():
                if j >= len(row):
                    continue
                v = row[j]
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    qty = int(round(v))
                    if qty == 0:
                        continue
                    all_months.add(month)
                    p["by_month"][month] = p["by_month"].get(month, 0) + qty
                    p["by_team"].setdefault(team, {})
                    p["by_team"][team][month] = p["by_team"][team].get(month, 0) + qty

    return {
        "products": list(products.values()),
        "months": sorted(all_months),
        "teams": teams,
        "team_labels": team_labels,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }


# ── 목표 수기 수정(오버라이드) 저장소 ─────────────────────────────────────────
# 키: "{team}|{공통상품명}|{YYYY-MM}" → 수정 목표 수량. 파싱된 목표 위에 덮어쓴다.
_OVERRIDE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "target_overrides.json")


def _load_target_overrides() -> dict:
    try:
        if os.path.exists(_OVERRIDE_PATH):
            with open(_OVERRIDE_PATH, "r", encoding="utf-8") as f:
                return {str(k): int(v) for k, v in json.load(f).items()}
    except Exception:
        pass
    return {}


def _save_target_overrides(d: dict) -> None:
    os.makedirs(os.path.dirname(_OVERRIDE_PATH), exist_ok=True)
    with open(_OVERRIDE_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


# ── 거래처 → 팀 수기 매핑 저장소 ───────────────────────────────────────────────
_PT_MAP_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "partner_team_map.json")


def _load_partner_team_map() -> dict:
    try:
        if os.path.exists(_PT_MAP_PATH):
            with open(_PT_MAP_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return {str(k): str(v) for k, v in d.items() if v}
    except Exception:
        pass
    return {}


def _save_partner_team_map(m: dict) -> None:
    os.makedirs(os.path.dirname(_PT_MAP_PATH), exist_ok=True)
    with open(_PT_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


def _load_set_expansion():
    """세트 BOM → 분해맵 2종 반환.
    set_by_sku: {set_sku: [(comp_sku, comp_name, qty_per_set), ...]}
    set_by_name: {normalize(set_name): [...동일...]}"""
    set_by_sku: dict = defaultdict(list)
    set_by_name: dict = defaultdict(list)
    try:
        import receiving_db as _rdb
        for b in (_rdb.get_set_boms() or []):
            entry = (
                str(b.get("component_sku") or "").strip(),
                str(b.get("component_name") or "").strip(),
                float(b.get("qty_per_set") or 1),
            )
            ss = str(b.get("set_sku") or "").strip()
            sn = b.get("set_name") or ""
            if ss:
                set_by_sku[ss].append(entry)
            if sn:
                set_by_name[U.normalize(sn)].append(entry)
    except Exception:
        pass
    return dict(set_by_sku), dict(set_by_name)


def _build_targets_block(targets_data: dict, unit_team_month: dict, unit_names: dict,
                         partner_team_map: dict, partner_out_total: dict,
                         set_expanded_qty: int = 0, stock_map: dict = None,
                         unit_partner_qty: dict = None, overrides: dict = None,
                         unit_partner_month: dict = None) -> dict:
    """팀×품목 목표 대비 실제 출고 비교 행 구성.

    - 목표 상품명(공통상품명) ↔ BH 단위(단품)명 fuzzy 매칭으로 단위 → 목표상품 배정
    - 실제 출고는 '해당 팀에 매핑된 거래처'의 출고만 합산 (세트는 사전에 단품 분해됨)
    - overrides: 수기 수정 목표 {"{team}|{name}|{month}": qty} 를 파싱 목표 위에 덮어씀
    """
    from rapidfuzz import fuzz as _fuzz

    overrides = overrides or {}
    products = targets_data.get("products", [])
    teams = targets_data.get("teams", [])
    # 오버라이드가 새 월을 추가할 수 있으므로 목표월 집합에 합집합
    _ov_months = {k.split("|")[-1] for k in overrides if len(k.split("|")) == 3}
    target_months = sorted(set(targets_data.get("months", [])) | _ov_months)

    # 목표 상품 정규화명 목록
    prod_norms = [(p, p.get("name", ""), U.normalize(p.get("name", ""))) for p in products]

    # 각 단위(단품) → 가장 잘 맞는 목표상품(norm) 배정
    unit_to_prod: dict = {}   # unit_key -> norm
    matched_names: dict = {}  # norm -> set(bh 단품명)
    for ukey, uname in unit_names.items():
        bn = U.normalize(uname)
        if not bn:
            continue
        best_norm, best_score = None, 0
        for _p, _raw, pn in prod_norms:
            if not pn:
                continue
            score = 100 if bn == pn else _fuzz.token_set_ratio(bn, pn)
            if score > best_score:
                best_score, best_norm = score, pn
        if best_norm is not None and best_score >= _TARGET_MATCH_THRESHOLD:
            unit_to_prod[ukey] = best_norm
            matched_names.setdefault(best_norm, set()).add(uname)

    # norm -> {team -> {month -> actual}}
    actual_by_prod_team: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for ukey, norm in unit_to_prod.items():
        for team, months in unit_team_month.get(ukey, {}).items():
            for month, qty in months.items():
                actual_by_prod_team[norm][team][month] += qty

    # norm -> 제품 기초재고 (매칭된 단위(sku)들의 현재고 합). 물리 재고는 팀 공유.
    stock_map = stock_map or {}
    prod_stock: dict = defaultdict(int)
    for ukey, norm in unit_to_prod.items():
        prod_stock[norm] += int(stock_map.get(ukey, 0) or 0)

    # norm -> {거래처: qty}  (제품별 거래처 점유율, 전체 출고 기준)
    unit_partner_qty = unit_partner_qty or {}
    prod_partner: dict = defaultdict(lambda: defaultdict(int))
    for ukey, norm in unit_to_prod.items():
        for partner, q in unit_partner_qty.get(ukey, {}).items():
            prod_partner[norm][partner] += q
    # 제품명 -> [{partner, team, qty}] (점유율 큰 순)
    partner_share: dict = {}
    for p in products:
        norm = U.normalize(p.get("name", ""))
        shares = prod_partner.get(norm)
        if not shares:
            continue
        partner_share[p.get("name", "")] = sorted(
            ({"partner": pt, "team": partner_team_map.get(pt, ""), "qty": q}
             for pt, q in shares.items()),
            key=lambda x: -x["qty"],
        )

    # 제품명 -> 월 -> [{partner, team, qty}]  (월별 거래처 점유율)
    unit_partner_month = unit_partner_month or {}
    prod_partner_month: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for ukey, norm in unit_to_prod.items():
        for month, pts in unit_partner_month.get(ukey, {}).items():
            for pt, q in pts.items():
                prod_partner_month[norm][month][pt] += q
    partner_share_by_month: dict = {}
    for p in products:
        norm = U.normalize(p.get("name", ""))
        pm = prod_partner_month.get(norm)
        if not pm:
            continue
        partner_share_by_month[p.get("name", "")] = {
            month: sorted(
                ({"partner": pt, "team": partner_team_map.get(pt, ""), "qty": q} for pt, q in pts.items()),
                key=lambda x: -x["qty"],
            )
            for month, pts in pm.items()
        }

    # 오버라이드 인덱싱: (name) -> {team -> {month -> qty}}
    ov_by_name: dict = defaultdict(lambda: defaultdict(dict))
    for okey, oval in overrides.items():
        parts = okey.split("|")
        if len(parts) < 3:
            continue
        o_team, o_month, o_name = parts[0], parts[-1], "|".join(parts[1:-1])
        ov_by_name[o_name][o_team][o_month] = oval

    rows = []
    totals_by_team: dict = defaultdict(lambda: {"target": 0, "actual": 0})
    for p in products:
        name = p.get("name", "")
        norm = U.normalize(name)
        parsed_teams = set(p.get("by_team", {}).keys())
        ov_teams = set(ov_by_name.get(name, {}).keys())
        for team in sorted(parsed_teams | ov_teams):
            tgt = dict(p.get("by_team", {}).get(team, {}))  # {month: qty}
            ov = ov_by_name.get(name, {}).get(team, {})
            if ov:
                tgt.update(ov)  # 수기 수정 목표 덮어쓰기 (값 0이면 0으로)
            act = actual_by_prod_team.get(norm, {}).get(team, {})
            rows.append({
                "team": team,
                "brand": p.get("brand", ""),
                "name": p.get("name", ""),
                "matched": bool(matched_names.get(norm)),
                "matched_names": sorted(matched_names.get(norm, [])),
                "stock": int(prod_stock.get(norm, 0)),
                "target_by_month": tgt,
                "actual_by_month": {m: act.get(m, 0) for m in act},
            })
            for m, q in tgt.items():
                totals_by_team[team]["target"] += q
            for m, q in act.items():
                totals_by_team[team]["actual"] += q

    rows.sort(key=lambda r: (r["team"], -sum(r["target_by_month"].values())))

    # 출고는 있으나 팀 미매핑인 거래처 (사용자에게 매핑 보완 안내)
    unmapped = sorted(
        [{"partner": pt, "total_out": tot} for pt, tot in partner_out_total.items()
         if pt not in partner_team_map and tot > 0],
        key=lambda x: -x["total_out"],
    )

    return {
        "enabled": True,
        "teams": teams,
        "team_labels": targets_data.get("team_labels", {}),
        "months": target_months,
        "uploaded_at": targets_data.get("uploaded_at", ""),
        "rows": rows,
        "totals_by_team": {t: dict(v) for t, v in totals_by_team.items()},
        "unmapped_partners": unmapped,
        "mapped_partner_count": len(partner_team_map),
        "set_expanded_qty": set_expanded_qty,
        "partner_share": partner_share,
        "partner_share_by_month": partner_share_by_month,
        "overrides": overrides,  # 수기 수정된 셀 표시용
    }


def _bucket_key(date_str: str, period: str) -> str:
    """거래일(YYYY-MM-DD)을 집계 단위 버킷 키로 변환."""
    if period == "day":
        return date_str
    if period == "month":
        return date_str[:7]  # YYYY-MM
    # week: 해당 주 월요일(ISO) 날짜
    try:
        d = datetime.fromisoformat(date_str)
    except Exception:
        return date_str
    monday = d - timedelta(days=d.weekday())
    return monday.strftime("%Y-%m-%d")


def _gen_buckets(from_dt: datetime, to_dt: datetime, period: str) -> list:
    """from~to 구간을 채우는 연속된 버킷 키 목록 (출고 0인 구간도 컬럼 유지)."""
    keys: list = []
    seen: set = set()
    if period == "month":
        cur = datetime(from_dt.year, from_dt.month, 1)
        while cur <= to_dt:
            k = cur.strftime("%Y-%m")
            if k not in seen:
                keys.append(k); seen.add(k)
            # 다음 달
            cur = datetime(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    elif period == "week":
        cur = from_dt - timedelta(days=from_dt.weekday())
        while cur <= to_dt:
            k = cur.strftime("%Y-%m-%d")
            if k not in seen:
                keys.append(k); seen.add(k)
            cur += timedelta(days=7)
    else:  # day
        cur = from_dt
        while cur <= to_dt:
            k = cur.strftime("%Y-%m-%d")
            keys.append(k); seen.add(k)
            cur += timedelta(days=1)
    return keys


def _fetch_stock_map(token: str, loc_ids: set) -> dict:
    """SKU → 현재고 수량. loc_ids 지정 시 해당 위치 합산, 없으면 전체."""
    stock: dict = defaultdict(int)
    cursor = None
    seen_cursor: set = set()
    pages = 0
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            r = _req.get(f"{BH_BASE}/v1/items",
                         headers={"Authorization": f"Bearer {token}"},
                         params=params, timeout=20)
        except Exception:
            break
        if not r.ok:
            break
        d = r.json()
        for it in d.get("items", []):
            sku = str(it.get("sku") or it.get("id") or "").strip()
            if not sku:
                continue
            if loc_ids:
                qty = sum(int(q.get("quantity") or 0)
                          for q in (it.get("quantities") or [])
                          if int(q.get("location_id") or 0) in loc_ids)
            else:
                qty = int(it.get("quantity") or 0)
            stock[sku] += qty
        pages += 1
        if not d.get("has_more"):
            break
        nc = d.get("cursor")
        if not nc or nc in seen_cursor or pages >= 100:
            break
        seen_cursor.add(nc); cursor = nc
    return stock


@router.get("/forecast")
def outbound_forecast(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    period: str = Query("month"),         # day | week | month (집계 단위)
    forecast_months: int = Query(3),      # 향후 예측 개월수
    location_ids: str = Query(""),        # 콤마구분 BH 위치 필터 (빈값=전체)
    expand_sets: bool = Query(False),     # 품목별 집계 시 세트를 구성 단품으로 분해
):
    """거래처별·품목별 출고 추세 + 재고 소진 예측."""
    if period not in ("day", "week", "month"):
        period = "month"
    forecast_months = max(1, min(int(forecast_months or 3), 24))

    try:
        from_dt = datetime.fromisoformat(from_date)
        to_dt = datetime.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(400, "날짜 형식 오류 (YYYY-MM-DD)")
    if to_dt < from_dt:
        raise HTTPException(400, "종료일이 시작일보다 빠릅니다")

    loc_ids = {int(x) for x in location_ids.split(",") if x.strip().isdigit()}
    loc_id_single = next(iter(loc_ids)) if len(loc_ids) == 1 else None

    # 1. 출고(out) 거래 수집 (헤더) → items 병렬 보강 (영구 캐시)
    try:
        txs = U.fetch_transactions(token, "out", from_date, to_date, location_id=loc_id_single)
    except Exception as e:
        raise HTTPException(502, f"박스히어로 출고 조회 실패: {e}")

    from routers.reconcile import _enrich_bh_items, _get_bh_tx_loc_id
    _enrich_bh_items(token, txs, "location")

    days_span = (to_dt - from_dt).days + 1
    buckets = _gen_buckets(from_dt, to_dt, period)
    bucket_set = set(buckets)

    # 목표 + 거래처→팀 매핑 로드 (목표 대비 달성률 계산용)
    targets_data = _load_targets()
    partner_team_map = _load_partner_team_map()
    has_targets = bool(targets_data.get("products"))

    # 세트 BOM 로드 → 세트 출고를 구성 단품으로 분해 (목표 단품 기준 집계용)
    set_by_sku, set_by_name = _load_set_expansion()

    def _expand_units(sku: str, name: str, qty: int):
        """출고 1품목을 (단위키, 단위명, 단위수량) 리스트로 변환.
        등록된 세트면 구성 단품들로 분해, 아니면 자기 자신."""
        comps = set_by_sku.get(sku) or set_by_name.get(U.normalize(name))
        if comps:
            return [((cs or cn), cn, int(round(qty * qp))) for cs, cn, qp in comps]
        return [(sku, name, qty)]

    # 2. 집계: 품목별 / 거래처별 ( + 목표 비교용 팀별 월간 집계, 세트는 단품 분해)
    item_agg: dict = defaultdict(lambda: {"name": "", "by_bucket": defaultdict(int), "total": 0})
    partner_agg: dict = defaultdict(lambda: {"by_bucket": defaultdict(int), "total": 0, "skus": set()})
    # 단위(단품)키 -> team -> 'YYYY-MM' -> qty (목표 대비용, 세트 분해 후, 항상 월 단위)
    unit_team_month: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    unit_names: dict = {}  # 단위키 -> 단품명 (목표 매칭용, 팀매핑 무관 전체 출고 기준)
    unit_partner_qty: dict = defaultdict(lambda: defaultdict(int))  # 단위키 -> 거래처 -> qty (전체 기간 점유율)
    unit_partner_month: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # 단위키 -> 'YYYY-MM' -> 거래처 -> qty (월별 점유율)
    partner_out_total: dict = defaultdict(int)  # 미매핑 거래처 안내용
    set_expanded_qty = 0  # 분해된 세트 수량 누계 (안내용)

    for tx in txs:
        # 다수 위치 필터: 단일 위치는 API에서 이미 필터됨, 2개 이상이면 여기서 후필터
        if loc_ids and not loc_id_single:
            lid = _get_bh_tx_loc_id(token, tx["id"], "location")
            if lid is not None and int(lid) not in loc_ids:
                continue
        date_str = (tx.get("transaction_time") or tx.get("created_at") or "")[:10]
        if not date_str:
            continue
        bk = _bucket_key(date_str, period)
        month_key = date_str[:7]
        partner_obj = tx.get("partner") or {}
        partner = partner_obj.get("name", "") if isinstance(partner_obj, dict) else ""
        partner = partner or "(거래처 미지정)"
        team_raw = partner_team_map.get(partner, "")
        team = "" if team_raw == _EXCLUDE else team_raw  # 제외 표식은 실적 집계 안 함
        for item in (tx.get("items") or []):
            sku = str(item.get("sku") or item.get("id") or "").strip()
            if not sku:
                continue
            qty = abs(int(item.get("quantity", 0) or 0))
            if qty == 0:
                continue
            name = item.get("name", "")
            # 품목별 집계 (토글 시 세트 → 구성 단품으로 분해)
            units = _expand_units(sku, name, qty) if expand_sets else [(sku, name, qty)]
            for ukey, uname, uqty in units:
                ia = item_agg[ukey]
                if uname:
                    ia["name"] = uname
                ia["by_bucket"][bk] += uqty
                ia["total"] += uqty
            pa = partner_agg[partner]
            pa["by_bucket"][bk] += qty
            pa["total"] += qty
            pa["skus"].add(sku)
            partner_out_total[partner] += qty
            if has_targets:
                is_set = bool(set_by_sku.get(sku) or set_by_name.get(U.normalize(name)))
                if is_set and team:
                    set_expanded_qty += qty
                for ukey, uname, uqty in _expand_units(sku, name, qty):
                    if uname:
                        unit_names[ukey] = uname          # 매칭용 (전체 출고)
                    unit_partner_qty[ukey][partner] += uqty  # 거래처 점유율 (전체 기간)
                    unit_partner_month[ukey][month_key][partner] += uqty  # 거래처 점유율 (월별)
                    if team:
                        unit_team_month[ukey][team][month_key] += uqty  # 달성률용 (매핑 팀만)

    # 2-b. 전년 동월(YoY) 실적 수집 → unit_team_month에 해당 월 키로 추가 (actual_by_month로 노출)
    #      조회 범위 밖이라 별도 1개월 수집. 품목별/거래처별 집계(item_agg 등)는 건드리지 않음.
    yoy_month = ""
    if has_targets:
        now = datetime.now()
        yy = now.year - 1
        yoy_month = f"{yy}-{now.month:02d}"
        yoy_from = f"{yy}-{now.month:02d}-01"
        _nm = datetime(yy + (now.month // 12), (now.month % 12) + 1, 1)
        yoy_to = (_nm - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            yoy_txs = U.fetch_transactions(token, "out", yoy_from, yoy_to, location_id=loc_id_single)
            _enrich_bh_items(token, yoy_txs, "location")
            for tx in yoy_txs:
                if loc_ids and not loc_id_single:
                    lid = _get_bh_tx_loc_id(token, tx["id"], "location")
                    if lid is not None and int(lid) not in loc_ids:
                        continue
                pobj = tx.get("partner") or {}
                pnm = (pobj.get("name", "") if isinstance(pobj, dict) else "") or "(거래처 미지정)"
                traw = partner_team_map.get(pnm, "")
                tm = "" if traw == _EXCLUDE else traw
                if not tm:
                    continue  # 매핑 팀만 (달성/실적 기준과 동일)
                for item in (tx.get("items") or []):
                    isku = str(item.get("sku") or item.get("id") or "").strip()
                    iqty = abs(int(item.get("quantity", 0) or 0))
                    if not isku or iqty == 0:
                        continue
                    for ukey, _un, uqty in _expand_units(isku, item.get("name", ""), iqty):
                        unit_team_month[ukey][tm][yoy_month] += uqty
        except Exception:
            pass

    # 3. 현재고(기초재고) 조회
    stock_map = _fetch_stock_map(token, loc_ids)

    # 4. 품목별 예측 계산
    def _build_row(by_bucket: dict, total: int) -> dict:
        daily = total / days_span if days_span else 0.0
        monthly = daily * _DAYS_PER_MONTH
        forecast = round(daily * _DAYS_PER_MONTH * forecast_months)
        return {
            "by_bucket": {k: by_bucket.get(k, 0) for k in by_bucket if k in bucket_set},
            "total_out": total,
            "daily_avg": round(daily, 2),
            "monthly_avg": round(monthly, 1),
            "forecast_total": forecast,
        }

    items_out = []
    for sku, agg in item_agg.items():
        row = _build_row(agg["by_bucket"], agg["total"])
        stock = stock_map.get(sku, 0)
        monthly = row["monthly_avg"]
        deplete_months = round(stock / monthly, 1) if monthly > 0 else None
        deplete_date = None
        if monthly > 0 and stock > 0:
            try:
                deplete_date = (datetime.now() + timedelta(days=stock / (monthly / _DAYS_PER_MONTH))).strftime("%Y-%m-%d")
            except Exception:
                deplete_date = None
        items_out.append({
            "sku": sku,
            "name": agg["name"],
            "stock": stock,
            **row,
            "deplete_months": deplete_months,
            "deplete_date": deplete_date,
            "remaining_after": stock - row["forecast_total"],
        })
    items_out.sort(key=lambda x: -x["total_out"])

    partners_out = []
    for partner, agg in partner_agg.items():
        row = _build_row(agg["by_bucket"], agg["total"])
        partners_out.append({
            "partner": partner,
            "sku_count": len(agg["skus"]),
            **row,
        })
    partners_out.sort(key=lambda x: -x["total_out"])

    grand_total = sum(r["total_out"] for r in items_out)
    grand_daily = grand_total / days_span if days_span else 0.0
    deplete_soon = sum(
        1 for r in items_out
        if r["deplete_months"] is not None and r["deplete_months"] <= forecast_months
    )

    # 5. 목표 대비 달성률 (팀×품목×월, 세트는 단품 분해 후 집계)
    targets_block = _build_targets_block(
        targets_data, unit_team_month, unit_names, partner_team_map, partner_out_total,
        set_expanded_qty, stock_map, unit_partner_qty, _load_target_overrides(),
        unit_partner_month,
    ) if has_targets else {"enabled": False}

    return {
        "from_date": from_date,
        "to_date": to_date,
        "period": period,
        "forecast_months": forecast_months,
        "days_span": days_span,
        "expand_sets": expand_sets,
        "buckets": buckets,
        "items": items_out,
        "partners": partners_out,
        "targets": targets_block,
        "summary": {
            "grand_total": grand_total,
            "daily_avg": round(grand_daily, 1),
            "monthly_avg": round(grand_daily * _DAYS_PER_MONTH, 1),
            "forecast_total": round(grand_daily * _DAYS_PER_MONTH * forecast_months),
            "item_count": len(items_out),
            "partner_count": len(partners_out),
            "deplete_soon": deplete_soon,
            "tx_count": len(txs),
        },
    }


# ── 판매 목표치 업로드/조회/삭제 ───────────────────────────────────────────────
@router.post("/targets/upload")
async def upload_targets(file: UploadFile = File(...)):
    """판매예상량(목표) 엑셀 업로드 → 파싱·저장. 팀(시트)별 월간 목표 보존."""
    raw = await file.read()
    try:
        data = _parse_target_workbook(raw)
    except Exception as e:
        raise HTTPException(400, f"목표 파일 파싱 실패: {e}")
    if not data.get("products"):
        raise HTTPException(400, "목표 데이터를 찾지 못했습니다. '공통상품명'과 'YYYY-MM' 월 컬럼이 있는지 확인하세요.")
    data["filename"] = file.filename
    _save_targets(data)
    return {
        "ok": True,
        "filename": file.filename,
        "product_count": len(data["products"]),
        "teams": data["teams"],
        "team_labels": data.get("team_labels", {}),
        "months": data["months"],
        "uploaded_at": data["uploaded_at"],
    }


@router.get("/targets")
def get_targets():
    """저장된 목표치 요약 반환."""
    data = _load_targets()
    if not data.get("products"):
        return {"loaded": False}
    return {
        "loaded": True,
        "filename": data.get("filename", ""),
        "product_count": len(data["products"]),
        "teams": data.get("teams", []),
        "team_labels": data.get("team_labels", {}),
        "months": data.get("months", []),
        "uploaded_at": data.get("uploaded_at", ""),
        "products": data["products"],
    }


@router.delete("/targets")
def delete_targets():
    try:
        if os.path.exists(_TARGETS_PATH):
            os.remove(_TARGETS_PATH)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


# ── 거래처 → 팀 매핑 ──────────────────────────────────────────────────────────
@router.get("/partner-team-map")
def get_partner_team_map(token: str = Query("")):
    """현재 거래처→팀 매핑 + 팀 목록(목표 기준) + 거래처 후보 목록 반환."""
    mapping = _load_partner_team_map()
    targets = _load_targets()
    teams = targets.get("teams", [])
    partners = []
    if token:
        try:
            partners = [p.get("name", "") for p in U.fetch_partners(token) if p.get("name")]
        except Exception:
            partners = []
    return {
        "mappings": mapping,
        "teams": teams,
        "team_labels": targets.get("team_labels", {}),
        "partners": sorted(set(partners)),
    }


@router.post("/partner-team-map")
def set_partner_team_map(body: dict):
    """거래처→팀 매핑 갱신. body: {mappings: {partner: team}} 전체 덮어쓰기,
    또는 {partner, team} 단건 설정 (team 빈값이면 매핑 해제)."""
    m = _load_partner_team_map()
    if "mappings" in body and isinstance(body["mappings"], dict):
        m = {str(k): str(v) for k, v in body["mappings"].items() if v}
    else:
        partner = str(body.get("partner", "")).strip()
        team = str(body.get("team", "")).strip()
        if not partner:
            raise HTTPException(400, "partner 필요")
        if team:
            m[partner] = team
        else:
            m.pop(partner, None)
    _save_partner_team_map(m)
    return {"ok": True, "mappings": m, "count": len(m)}


# ── 목표 수기 수정(오버라이드) ────────────────────────────────────────────────
@router.get("/target-override")
def get_target_overrides():
    return {"overrides": _load_target_overrides()}


@router.post("/target-override")
def set_target_override(body: dict):
    """목표 수기 수정. body: {team, name, month, qty}.
    qty가 null/빈값이면 해당 셀 수정 해제(원본 목표로 복귀)."""
    team = str(body.get("team", "")).strip()
    name = str(body.get("name", "")).strip()
    month = str(body.get("month", "")).strip()
    if not (team and name and month):
        raise HTTPException(400, "team, name, month 필요")
    ov = _load_target_overrides()
    key = f"{team}|{name}|{month}"
    qty = body.get("qty", None)
    if qty is None or qty == "":
        ov.pop(key, None)
    else:
        try:
            ov[key] = int(round(float(qty)))
        except Exception:
            raise HTTPException(400, "qty는 숫자여야 합니다")
    _save_target_overrides(ov)
    return {"ok": True, "overrides": ov}
