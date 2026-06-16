"""채널별(위치별) 품목 입출고 현황 — 박스히어로 Location 기반"""
import sys, os, re
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional
import concurrent.futures

from fastapi import APIRouter, HTTPException, Query
import requests as _req

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U

router = APIRouter()

BH_BASE = "https://rest.boxhero-app.com"


def _bh_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _fetch_tx_detail(token: str, tx_id: int) -> list:
    """거래 상세 → items 리스트 반환."""
    try:
        r = _req.get(
            f"{BH_BASE}/v1/location-txs/{tx_id}",
            headers=_bh_headers(token),
            timeout=10,
        )
        if r.ok:
            return r.json().get("item", {}).get("items", [])
    except Exception:
        pass
    return []


def _extract_channel_tag(memo: str) -> Optional[str]:
    """메모에서 채널명 추출. '# 홈앤쇼핑 본방' → '홈앤쇼핑'."""
    if not memo:
        return None
    # # 채널명 형태 파싱
    m = re.match(r"#\s*([^\s#]+)", memo.strip())
    if m:
        return m.group(1)
    return None


def _fetch_location_txs(token: str, location_id: int, tx_type: str,
                         from_dt: datetime, to_dt: datetime) -> list:
    """위치별 거래 수집 (날짜 필터 포함)."""
    txs = []
    cursor = None
    while True:
        params = {"type": tx_type, "location_id": location_id, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = _req.get(f"{BH_BASE}/v1/location-txs",
                     headers=_bh_headers(token), params=params, timeout=15)
        if not r.ok:
            break
        data = r.json()
        items = data.get("items", [])
        stop = False
        for tx in items:
            tx_time = tx.get("transaction_time", "")[:10]
            try:
                tx_dt = datetime.fromisoformat(tx_time)
            except Exception:
                continue
            if tx_dt < from_dt:
                stop = True
                break
            if tx_dt <= to_dt:
                txs.append(tx)
        if stop or not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return txs


@router.get("/summary")
def channel_summary(
    token: str = Query(...),
    from_date: str = Query(...),
    to_date: str = Query(...),
    tx_type: str = Query("out"),        # in | out | both
    exclude_locations: str = Query(""), # 쉼표 구분 제외 위치명
):
    """위치(채널)별 품목 입출고 매트릭스 반환."""
    try:
        from_dt = datetime.fromisoformat(from_date)
        to_dt   = datetime.fromisoformat(to_date) + timedelta(days=1)
    except ValueError:
        raise HTTPException(400, "날짜 형식 오류 (YYYY-MM-DD)")

    exclude_set = {n.strip() for n in exclude_locations.split(",") if n.strip()}

    # 1. 위치 목록
    locs_r = _req.get(f"{BH_BASE}/v1/locations",
                      headers=_bh_headers(token), timeout=15)
    if not locs_r.ok:
        raise HTTPException(502, "박스히어로 위치 조회 실패")
    locations = [
        loc for loc in locs_r.json().get("items", [])
        if loc.get("name") not in exclude_set
    ]

    types = ["in", "out"] if tx_type == "both" else [tx_type]

    # 2. 위치별 × 거래유형별 거래 수집 (병렬)
    # {(loc_id, type): [tx, ...]}
    loc_txs: dict = {}

    def _fetch(loc_id, ttype):
        return (loc_id, ttype), _fetch_location_txs(token, loc_id, ttype, from_dt, to_dt)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(_fetch, loc["id"], tt): (loc["id"], tt)
            for loc in locations
            for tt in types
        }
        for fut in concurrent.futures.as_completed(futures):
            key, txs = fut.result()
            loc_txs[key] = txs

    # 3. 거래 상세(items) 병렬 조회
    all_tx_ids = {tx["id"]: tx for _, txs in loc_txs.items() for tx in txs}

    tx_items: dict = {}  # {tx_id: [item, ...]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futures2 = {ex.submit(_fetch_tx_detail, token, tx_id): tx_id
                    for tx_id in all_tx_ids}
        for fut in concurrent.futures.as_completed(futures2):
            tx_id = futures2[fut]
            tx_items[tx_id] = fut.result()

    # 4. 집계: {sku: {loc_name: {in: qty, out: qty}}}
    matrix: dict = defaultdict(lambda: defaultdict(lambda: {"in": 0, "out": 0, "memo_channel": set()}))
    sku_names: dict = {}

    for (loc_id, ttype), txs in loc_txs.items():
        loc_name = next((l["name"] for l in locations if l["id"] == loc_id), str(loc_id))
        for tx in txs:
            tx_id = tx["id"]
            memo = tx.get("memo", "") or ""
            channel_tag = _extract_channel_tag(memo)
            items = tx_items.get(tx_id, [])
            for item in items:
                sku = str(item.get("sku") or item.get("id", "")).strip()
                name = item.get("name", "")
                qty = abs(int(item.get("quantity", 0)))
                if qty == 0:
                    continue
                sku_names[sku] = name
                matrix[sku][loc_name][ttype] += qty
                if channel_tag:
                    matrix[sku][loc_name]["memo_channel"].add(channel_tag)

    # 5. 응답 포맷
    loc_names = [loc["name"] for loc in locations]
    rows = []
    for sku, loc_data in matrix.items():
        total_in = sum(v["in"] for v in loc_data.values())
        total_out = sum(v["out"] for v in loc_data.values())
        channel_details = {}
        for loc_name in loc_names:
            v = loc_data.get(loc_name, {"in": 0, "out": 0})
            channel_details[loc_name] = {
                "in_qty": v["in"],
                "out_qty": v["out"],
                "memo_tags": list(loc_data.get(loc_name, {}).get("memo_channel", set())),
            }
        rows.append({
            "sku": sku,
            "name": sku_names.get(sku, ""),
            "channels": channel_details,
            "total_in": total_in,
            "total_out": total_out,
        })

    # 출고 합계 기준 정렬
    sort_key = "total_out" if tx_type != "in" else "total_in"
    rows.sort(key=lambda x: -x[sort_key])

    return {
        "locations": loc_names,
        "rows": rows,
        "from_date": from_date,
        "to_date": to_date,
        "tx_type": tx_type,
        "total_skus": len(rows),
    }
