"""재고 분석 — 박스히어로·아워박스 재고수량 + 회전율 + 부진재고.

BH 현재고(위치 합산)와 OB product_stock(총/가용/가용외)을 상품 매핑 그룹으로 병합하고,
기간 내 BH 출고(out) 거래로 품목별·거래처별 출고량/월평균/회전율을 계산한다.

  · 회전율(월) = 월평균 출고 ÷ 기간 평균재고 (재고가 한 달에 몇 번 도는지)
      평균재고 = (기초재고 + 현재고) ÷ 2
      기초재고 = 현재고 + 기간출고 − 기간입고 (BH 입고(in) 거래로 역산한 추정치.
                 이동/조정 거래는 미반영, 음수면 0 처리)
      현재고는 입출고 타이밍에 따라 계속 변해서 그대로 분모로 쓰면
      입고 직후엔 회전율이 낮게(가짜 저회전), 재고 바닥일 땐 높게 왜곡된다.
      입고 거래 조회 실패 시에만 현재고 기준으로 폴백 (avg_basis="current").
  · 소진 개월 = 현재고 ÷ 월평균 출고 — "지금 재고가 언제 떨어지나"는
      미래 전망이므로 평균이 아닌 현재고 기준이 맞다.
  · 부진재고 = 재고>0 인데 최근 N일(기본 60) 출고 없음 (출고 이력 기준)
  · 저회전   = 재고>0 이고 회전율 < 기준(기본 0.5회/월)

거래처별 회전율은 품목 '평균재고'를 거래처 출고 점유율로 배분한 '배분재고' 기준.

expand_sets=True(세트→단품 토글)면 세트 출고·입고를 세트 구성(BOM, 출고 예측과 공유)
기준으로 구성 단품 수량으로 환산해 집계한다 — 세트로만 팔리는 단품의 부진 오분류 방지.
"""
import sys, os, html, json
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
import requests as _req

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
from utils_core import HTTP as _HTTP

router = APIRouter()

BH_BASE = "https://rest.boxhero-app.com"
_DAYS_PER_MONTH = 30.44

# ── 계산 제외 거래처 저장소 (수기 지정, 재고분석 출고 집계에서 제외) ───────────
_EXCL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "inventory_excluded_partners.json")


def _load_excluded_partners() -> list:
    try:
        if os.path.exists(_EXCL_PATH):
            with open(_EXCL_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return [str(x) for x in d if str(x).strip()]
    except Exception:
        pass
    return []


def _save_excluded_partners(partners: list) -> None:
    os.makedirs(os.path.dirname(_EXCL_PATH), exist_ok=True)
    with open(_EXCL_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(set(str(x).strip() for x in partners if str(x).strip())), f, ensure_ascii=False, indent=2)


@router.get("/excluded-partners")
def get_excluded_partners():
    return {"partners": _load_excluded_partners()}


@router.post("/excluded-partners")
def set_excluded_partners(body: dict):
    """계산 제외 거래처 전체 덮어쓰기. body: {partners: [이름, ...]}"""
    partners = body.get("partners")
    if not isinstance(partners, list):
        raise HTTPException(400, "partners 리스트 필요")
    _save_excluded_partners(partners)
    return {"ok": True, "partners": _load_excluded_partners()}


# ── 마지막 분석 결과 스냅샷 (앱을 껐다 켜도 마지막 분석 유지) ─────────────────
_LAST_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "inventory_last_analysis.json")


def _save_last_analysis(result: dict) -> None:
    """분석 성공 시 결과 전체를 스냅샷으로 저장. 실패해도 분석 응답에는 영향 없음."""
    try:
        os.makedirs(os.path.dirname(_LAST_PATH), exist_ok=True)
        with open(_LAST_PATH, "w", encoding="utf-8") as f:
            json.dump({"saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "result": result},
                      f, ensure_ascii=False)
    except Exception:
        pass


@router.get("/analysis/last")
def get_last_analysis():
    """마지막 분석 결과 스냅샷 (없으면 result=None)."""
    try:
        if os.path.exists(_LAST_PATH):
            with open(_LAST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"saved_at": None, "result": None}


def _fetch_bh_items(token: str, loc_ids: set) -> list:
    """BH 품목 목록 → [{sku, name, qty}] (loc_ids 지정 시 해당 위치 합산)."""
    out: list = []
    cursor = None
    seen_cursor: set = set()
    pages = 0
    while True:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = _HTTP.get(f"{BH_BASE}/v1/items",
                     headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=20)
        if not r.ok:
            break
        d = r.json()
        for it in d.get("items", []):
            sku = str(it.get("sku") or it.get("id") or "").strip()
            name = str(it.get("name") or "").strip()
            if not (sku or name):
                continue
            if loc_ids:
                qty = sum(int(q.get("quantity") or 0)
                          for q in (it.get("quantities") or [])
                          if int(q.get("location_id") or 0) in loc_ids)
            else:
                qty = int(it.get("quantity") or 0)
            out.append({"sku": sku, "name": name, "qty": qty})
        pages += 1
        if not d.get("has_more"):
            break
        nc = d.get("cursor")
        if not nc or nc in seen_cursor or pages >= 100:
            break
        seen_cursor.add(nc)
        cursor = nc
    return out


@router.get("/analysis")
def inventory_analysis(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    location_ids: str = Query(""),        # 콤마구분 BH 위치 필터 (빈값=전체)
    slow_days: int = Query(60),           # 부진재고 기준: 최근 N일 출고 없음
    low_turnover: float = Query(0.5),     # 저회전 기준: 월 회전율 미만
    expand_sets: bool = Query(False),     # 세트 출고·입고를 구성 단품으로 분해해 집계
):
    """품목별·거래처별 재고수량 / 회전율 / 부진재고 분석."""
    try:
        from_dt = datetime.fromisoformat(from_date)
        to_dt = datetime.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(400, "날짜 형식 오류 (YYYY-MM-DD)")
    if to_dt < from_dt:
        raise HTTPException(400, "종료일이 시작일보다 빠릅니다")
    slow_days = max(1, int(slow_days or 60))
    low_turnover = max(0.0, float(low_turnover or 0.5))

    loc_ids = {int(x) for x in location_ids.split(",") if x.strip().isdigit()}
    loc_id_single = next(iter(loc_ids)) if len(loc_ids) == 1 else None
    days_span = (to_dt - from_dt).days + 1
    errors: list = []

    # BH 상품명의 유통기한 suffix 제거 정규화 (reconcile와 동일 규칙)
    import re as _re
    def _strip_date(nm: str) -> str:
        s = str(nm or "")
        return _re.sub(r'[\s\-_]+\d{2,4}[-.]\d{1,2}[-.]\d{1,2}\s*$', '', s).strip()

    def _snorm(nm: str) -> str:
        return U.normalize(_strip_date(nm))

    # ── 1. BH 현재고 ─────────────────────────────────────────────
    try:
        bh_items = _fetch_bh_items(token, loc_ids)
    except Exception as e:
        raise HTTPException(502, f"박스히어로 재고 조회 실패: {e}")

    # ── 2. OB 현재고 (미설정/실패 시 스킵) ───────────────────────
    ob_enabled = False
    ob_agg: dict = {}  # norm -> {total, available, unavail, name, codes}
    try:
        import ourbox_api as api_mod
        client = api_mod.make_client(U.load_config())
        if client:
            raw = client.fetch_stock()
            ob_enabled = True
            agg: dict = defaultdict(lambda: {"total": 0, "available": 0, "unavail": 0, "name": "", "codes": set(), "lots": []})
            for r in raw:
                nm = html.unescape(str(r.get("product_name") or "").strip())
                k = _snorm(nm)
                if not k:
                    continue
                a = agg[k]
                total = int(r.get("total_stock") or 0)
                avail = int(r.get("available_stock") or 0)
                unav = int(r.get("unavailable_stock") or 0)
                a["total"] += total
                a["available"] += avail
                a["unavail"] += unav
                a["name"] = nm
                code = str(r.get("sales_product_code") or "")
                if code:
                    a["codes"].add(code)
                # 로트(소비기한)별 현재고 — 같은 상품이 유통기한별로 여러 행 등장
                if total or avail or unav:
                    a["lots"].append({
                        "code": code,
                        "expiration_date": str(r.get("expiration_date") or "").strip()[:10],
                        "total": total,
                        "available": avail,
                        "unavail": unav,
                    })
            ob_agg = dict(agg)
    except PermissionError:
        errors.append("아워박스 API 인증 실패 (IP 화이트리스트 확인)")
    except Exception as e:
        errors.append(f"아워박스 재고 조회 실패: {e}")

    # ── 3. 매핑 그룹 (BH SKU/이름 ↔ OB 코드/이름) ─────────────────
    bh_to_group: dict = {}
    bh_name_to_group: dict = {}
    ob_to_group: dict = {}
    ob_code_to_group: dict = {}
    group_name: dict = {}
    group_label: dict = {}
    try:
        from routers.reconcile import _build_mapping_groups
        _g = _build_mapping_groups()
        bh_to_group = _g.get("bh_to_group", {}) or {}
        bh_name_to_group = _g.get("bh_name_to_group", {}) or {}
        ob_to_group = _g.get("ob_to_group", {}) or {}
        ob_code_to_group = _g.get("ob_code_to_group", {}) or {}
        group_name = _g.get("group_name", {}) or {}
        group_label = _g.get("group_label", {}) or {}
    except Exception:
        pass

    def _glabel(g, fallback: str) -> str:
        return str(group_name.get(g) or group_label.get(g) or fallback)

    def _key_bh(orig_nm: str, sku: str) -> str:
        """BH 품목 → 병합 키(그룹 대표명 norm 또는 상품명 norm)."""
        g = bh_to_group.get(sku) or bh_name_to_group.get(orig_nm)
        return _snorm(_glabel(g, orig_nm)) if g else _snorm(orig_nm)

    def _key_ob(orig_nm: str, norm_nm: str, codes: set) -> str:
        for c in codes:
            g = ob_code_to_group.get(c)
            if g:
                return _snorm(_glabel(g, orig_nm))
        g = ob_to_group.get(orig_nm)
        return _snorm(_glabel(g, orig_nm)) if g else norm_nm

    # ── 4. 병합 행 구성 (키: 매핑 그룹 대표명 norm) ───────────────
    rows: dict = defaultdict(lambda: {
        "name": "", "bh_skus": set(), "ob_codes": set(),
        "bh_stock": 0, "ob_total": None, "ob_available": None, "ob_unusable": None,
        "out_qty": 0, "last_out": "", "partners": defaultdict(int), "lots": [],
    })
    sku_to_key: dict = {}
    for it in bh_items:
        k = _key_bh(it["name"], it["sku"])
        if not k:
            continue
        m = rows[k]
        m["bh_stock"] += it["qty"]
        if not m["name"]:
            m["name"] = _strip_date(it["name"]) or it["name"]
        if it["sku"]:
            m["bh_skus"].add(it["sku"])
            sku_to_key[it["sku"]] = k
    for norm, ob in ob_agg.items():
        k = _key_ob(ob["name"], norm, ob["codes"])
        if not k:
            continue
        m = rows[k]
        m["ob_total"] = (m["ob_total"] or 0) + ob["total"]
        m["ob_available"] = (m["ob_available"] or 0) + ob["available"]
        m["ob_unusable"] = (m["ob_unusable"] or 0) + ob["unavail"]
        if not m["name"]:
            m["name"] = ob["name"]
        m["ob_codes"] |= ob["codes"]
        m["lots"].extend(ob["lots"])

    # ── 4-b. 세트 → 단품 분해맵 (expand_sets 토글 시 출고·입고를 구성 단품으로 환산) ──
    set_by_sku: dict = {}
    set_by_name: dict = {}
    if expand_sets:
        try:
            from routers.outbound import _load_set_expansion
            set_by_sku, set_by_name = _load_set_expansion()
        except Exception:
            errors.append("세트 구성(BOM) 로드 실패 — 세트 분해 없이 집계했습니다")

    def _expand_units(sku: str, name: str, qty: int):
        """출고/입고 1품목을 (단위SKU, 단위명, 단위수량) 리스트로. 등록된 세트면 구성 단품으로 분해."""
        comps = (set_by_sku.get(sku) or set_by_name.get(_snorm(name))
                 or set_by_name.get(U.normalize(name)))
        if comps:
            return [(cs, cn, int(round(qty * qp))) for cs, cn, qp in comps]
        return [(sku, name, qty)]

    def _is_set(sku: str, name: str) -> bool:
        return bool(set_by_sku.get(sku) or set_by_name.get(_snorm(name))
                    or set_by_name.get(U.normalize(name)))

    set_expanded_out = 0  # 단품으로 분해된 세트 출고 수량 (안내용)

    # ── 5. 기간 출고(out) 거래 수집 → 품목·거래처 집계 ────────────
    try:
        txs = U.fetch_transactions(token, "out", from_date, to_date, location_id=loc_id_single)
    except Exception as e:
        raise HTTPException(502, f"박스히어로 출고 조회 실패: {e}")

    from routers.reconcile import _enrich_bh_items, _get_bh_tx_loc_id
    _enrich_bh_items(token, txs, "location")

    excluded_partners = set(_load_excluded_partners())  # 수기 제외 거래처 → 출고 집계에서 완전 제외
    excluded_out_qty = 0  # 제외된 출고량 (안내용)

    partner_agg: dict = defaultdict(lambda: {"out_qty": 0, "keys": defaultdict(int)})
    for tx in txs:
        # 위치 필터: 거래목록 API가 location_id 파라미터를 무시하므로 항상 상세(캐시)로 후필터
        if loc_ids:
            lid = _get_bh_tx_loc_id(token, tx["id"], "location")
            if lid is not None and int(lid) not in loc_ids:
                continue
        date_str = (tx.get("transaction_time") or tx.get("created_at") or "")[:10]
        pobj = tx.get("partner") or {}
        partner = (pobj.get("name", "") if isinstance(pobj, dict) else "") or "(거래처 미지정)"
        if partner in excluded_partners:
            excluded_out_qty += sum(abs(int(i.get("quantity", 0) or 0)) for i in (tx.get("items") or []))
            continue
        for item in (tx.get("items") or []):
            sku = str(item.get("sku") or item.get("id") or "").strip()
            qty = abs(int(item.get("quantity", 0) or 0))
            if qty == 0:
                continue
            if expand_sets and _is_set(sku, item.get("name", "")):
                set_expanded_out += qty
            # 세트→단품 분해 토글 시 구성 단품 단위로 집계 (미등록 상품은 그대로)
            for usku, uname, uqty in _expand_units(sku, item.get("name", ""), qty):
                if uqty == 0:
                    continue
                k = sku_to_key.get(usku) or _key_bh(uname, usku)
                if not k:
                    continue
                m = rows[k]
                if not m["name"]:
                    m["name"] = _strip_date(uname) or uname
                if usku:
                    m["bh_skus"].add(usku)
                    sku_to_key.setdefault(usku, k)
                m["out_qty"] += uqty
                if date_str and date_str > m["last_out"]:
                    m["last_out"] = date_str
                m["partners"][partner] += uqty
                pa = partner_agg[partner]
                pa["out_qty"] += uqty
                pa["keys"][k] += uqty

    # ── 5-b. 기간 입고(in) 거래 수집 → 평균재고 역산 ──────────────
    #   기초재고 = 현재고 + 기간출고 − 기간입고 (이동/조정 미반영 추정, 음수는 0)
    #   평균재고 = (기초재고 + 현재고) ÷ 2 → 회전율 분모
    #   현재고를 그대로 쓰면 입고 직후/재고 바닥 등 조회 시점에 따라 회전율이 왜곡됨.
    in_agg: dict = defaultdict(int)
    avg_basis = "average"
    try:
        in_txs = U.fetch_transactions(token, "in", from_date, to_date, location_id=loc_id_single)
        _enrich_bh_items(token, in_txs, "location")
        for tx in in_txs:
            if loc_ids:
                lid = _get_bh_tx_loc_id(token, tx["id"], "location")
                if lid is not None and int(lid) not in loc_ids:
                    continue
            for item in (tx.get("items") or []):
                sku = str(item.get("sku") or item.get("id") or "").strip()
                qty = abs(int(item.get("quantity", 0) or 0))
                if qty == 0:
                    continue
                # 출고와 동일 기준으로 세트→단품 분해 (평균재고 역산 일관성)
                for usku, uname, uqty in _expand_units(sku, item.get("name", ""), qty):
                    if uqty == 0:
                        continue
                    k = sku_to_key.get(usku) or _key_bh(uname, usku)
                    if k in rows:   # 분석 대상(재고 보유 또는 기간 출고) 품목만
                        in_agg[k] += uqty
    except Exception as e:
        avg_basis = "current"
        errors.append(f"입고 거래 조회 실패 — 회전율을 평균재고 대신 현재고 기준으로 계산했습니다: {e}")

    # ── 6. 품목 행 산출 (회전율/소진/부진 분류) ───────────────────
    today = datetime.now()
    items_out: list = []
    key_avg_stock: dict = {}
    slow_count = low_count = 0
    for k, m in rows.items():
        stock = m["bh_stock"]
        daily = m["out_qty"] / days_span if days_span else 0.0
        monthly = daily * _DAYS_PER_MONTH
        # 회전율 분모: 평균재고 (입고 조회 실패 시 현재고 폴백)
        if avg_basis == "average":
            in_q = in_agg.get(k, 0)
            begin_stock = max(0, stock + m["out_qty"] - in_q)
            avg_stock = (begin_stock + stock) / 2
        else:
            in_q = None
            begin_stock = None
            avg_stock = float(stock)
        key_avg_stock[k] = avg_stock
        turnover = round(monthly / avg_stock, 2) if avg_stock > 0 else None
        # 소진 개월은 '지금 재고가 언제 떨어지나'이므로 현재고 기준 유지
        deplete_months = round(stock / monthly, 1) if monthly > 0 and stock > 0 else None
        if m["last_out"]:
            try:
                days_since = (today - datetime.fromisoformat(m["last_out"])).days
            except Exception:
                days_since = None
        else:
            days_since = None  # 기간 내 출고 없음

        if stock <= 0 and (m["ob_total"] or 0) <= 0:
            status = "no_stock"       # 재고 없음
        elif m["out_qty"] == 0 or (days_since is not None and days_since >= slow_days):
            status = "slow"           # 부진 (최근 N일 출고 없음)
            slow_count += 1
        elif turnover is not None and turnover < low_turnover:
            status = "low"            # 저회전
            low_count += 1
        else:
            status = "ok"

        partners = sorted(
            ({"partner": p, "qty": q,
              "share": round(q * 100 / m["out_qty"], 1) if m["out_qty"] else 0}
             for p, q in m["partners"].items()),
            key=lambda x: -x["qty"],
        )
        items_out.append({
            "key": k,
            "name": m["name"] or k,
            "bh_skus": sorted(m["bh_skus"]),
            "ob_codes": sorted(m["ob_codes"]),
            "bh_stock": stock,
            "ob_total": m["ob_total"],
            "ob_available": m["ob_available"],
            "ob_unusable": m["ob_unusable"],
            "in_qty": in_q,
            "begin_stock": begin_stock,
            "avg_stock": round(avg_stock, 1),
            "out_qty": m["out_qty"],
            "daily_avg": round(daily, 2),
            "monthly_avg": round(monthly, 1),
            "turnover": turnover,
            "deplete_months": deplete_months,
            "last_out": m["last_out"] or None,
            "days_since_out": days_since,
            "status": status,
            "partners": partners,
            "lots": sorted(m["lots"], key=lambda x: x["expiration_date"] or "9999-99-99"),
        })
    items_out.sort(key=lambda x: -x["out_qty"])

    # ── 7. 거래처 행 산출 (배분재고 기준 회전율) ──────────────────
    key_stock = {k: m["bh_stock"] for k, m in rows.items()}
    key_out = {k: m["out_qty"] for k, m in rows.items()}
    key_name = {k: (m["name"] or k) for k, m in rows.items()}
    grand_out = sum(key_out.values())
    partners_out: list = []
    for partner, pa in partner_agg.items():
        daily = pa["out_qty"] / days_span if days_span else 0.0
        monthly = daily * _DAYS_PER_MONTH
        # 배분재고: 품목 '평균재고' × 그 품목에서 이 거래처가 차지한 출고 점유율
        alloc_stock = 0.0
        for k, q in pa["keys"].items():
            tot = key_out.get(k, 0)
            if tot > 0:
                alloc_stock += key_avg_stock.get(k, 0) * (q / tot)
        turnover = round(monthly / alloc_stock, 2) if alloc_stock > 0 else None
        top_items = sorted(
            ({"key": k, "name": key_name.get(k, k), "qty": q,
              "stock": key_stock.get(k, 0)}
             for k, q in pa["keys"].items()),
            key=lambda x: -x["qty"],
        )
        partners_out.append({
            "partner": partner,
            "out_qty": pa["out_qty"],
            "share": round(pa["out_qty"] * 100 / grand_out, 1) if grand_out else 0,
            "daily_avg": round(daily, 2),
            "monthly_avg": round(monthly, 1),
            "item_count": len(pa["keys"]),
            "alloc_stock": round(alloc_stock),
            "turnover": turnover,
            "items": top_items,
        })
    partners_out.sort(key=lambda x: -x["out_qty"])

    total_bh_stock = sum(key_stock.values())
    total_avg_stock = sum(key_avg_stock.values())
    total_monthly = (grand_out / days_span * _DAYS_PER_MONTH) if days_span else 0.0
    result = {
        "from_date": from_date,
        "to_date": to_date,
        "days_span": days_span,
        "slow_days": slow_days,
        "low_turnover": low_turnover,
        "avg_basis": avg_basis,
        "expand_sets": expand_sets,
        "set_expanded_out": set_expanded_out,
        "ob_enabled": ob_enabled,
        "errors": errors,
        "excluded_partners": sorted(excluded_partners),
        "excluded_out_qty": excluded_out_qty,
        "items": items_out,
        "partners": partners_out,
        "summary": {
            "item_count": len(items_out),
            "total_bh_stock": total_bh_stock,
            "total_avg_stock": round(total_avg_stock),
            "total_ob_total": sum((m["ob_total"] or 0) for m in rows.values()),
            "total_ob_available": sum((m["ob_available"] or 0) for m in rows.values()),
            "total_out": grand_out,
            "monthly_avg": round(total_monthly, 1),
            "overall_turnover": round(total_monthly / total_avg_stock, 2) if total_avg_stock > 0 else None,
            "slow_count": slow_count,
            "low_count": low_count,
            "partner_count": len(partners_out),
            "tx_count": len(txs),
        },
    }
    _save_last_analysis(result)
    return result
