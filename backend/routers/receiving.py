"""입고정산기 라우터 - 아워박스 입고 데이터를 박스히어로로 정산"""
import sys
import os
import threading
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# 프로젝트 루트(receiving_db, boxhero_incoming, ourbox_scraper가 있는 곳)를 sys.path에 추가
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import receiving_db as db
import boxhero_incoming as bh

router = APIRouter()

_sync_status = {"status": "idle", "lastSyncTime": None, "lastSyncError": None}
_sync_lock = threading.Lock()


def _load_cfg() -> dict:
    cfg_path = os.path.join(_ROOT, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ─── 입고 목록 ─────────────────────────────────────────────────────────────

@router.get("/receivings")
def get_receivings():
    records = db.get_all()
    for r in records:
        r["items"] = db.get_items(r["put_sno"])
    return records


@router.get("/receivings/{put_sno}")
def get_receiving(put_sno: str):
    records = db.get_all()
    rec = next((r for r in records if r["put_sno"] == put_sno), None)
    if not rec:
        raise HTTPException(404, "입고 기록 없음")
    rec["items"] = db.get_items(put_sno)
    return rec


@router.post("/receivings/{put_sno}/approve")
def approve_receiving(put_sno: str):
    cfg = _load_cfg()
    api_token = cfg.get("api_token", "")
    location_id = cfg.get("selected_location_id")
    if not api_token or not location_id:
        raise HTTPException(400, "BoxHero API 토큰 또는 위치가 설정되지 않았습니다.")

    records = db.get_all()
    rec = next((r for r in records if r["put_sno"] == put_sno), None)
    if not rec:
        raise HTTPException(404, "입고 기록 없음")
    if rec["status"] == "approved":
        raise HTTPException(400, "이미 승인됨")

    items = db.get_items(put_sno)
    mapped = [i for i in items if i.get("boxhero_item_id")]
    if not mapped:
        raise HTTPException(400, "박스히어로에 매핑된 상품이 없습니다. 상품 매핑을 먼저 설정해주세요.")

    item_map: dict = {}
    for i in mapped:
        iid = str(i["boxhero_item_id"])
        item_map[iid] = item_map.get(iid, 0) + int(i["put_qty"] or 0)
    tx_items = [{"item_id": int(k), "quantity": v} for k, v in item_map.items()]

    tx_time = None
    if rec.get("put_compt_dtm"):
        try:
            tx_time = datetime.strptime(rec["put_compt_dtm"][:19], "%Y-%m-%d %H:%M:%S").isoformat()
        except Exception:
            pass

    result = bh.create_in_transaction(
        api_token, location_id, tx_items,
        memo=f"아워박스 입고번호: {put_sno} ({rec.get('put_depot_nm', '')})",
        tx_time=tx_time,
    )
    db.update_status(put_sno, "approved", result["id"])
    unmapped = [i["sale_prod_nm"] for i in items if not i.get("boxhero_item_id")]
    return {
        "success": True,
        "boxhero_tx_id": result["id"],
        "mapped_count": len(mapped),
        "unmapped_count": len(unmapped),
        "unmapped_items": unmapped,
    }


@router.post("/receivings/{put_sno}/cancel")
def cancel_receiving(put_sno: str):
    cfg = _load_cfg()
    api_token = cfg.get("api_token", "")
    records = db.get_all()
    rec = next((r for r in records if r["put_sno"] == put_sno), None)
    if not rec:
        raise HTTPException(404, "입고 기록 없음")
    if rec["status"] != "approved":
        raise HTTPException(400, "승인된 건만 취소할 수 있습니다.")
    if not rec.get("boxhero_tx_id"):
        raise HTTPException(400, "박스히어로 트랜잭션 ID 없음")
    bh.delete_transaction(api_token, int(rec["boxhero_tx_id"]))
    db.update_status(put_sno, "pending", None)
    return {"success": True}


@router.post("/receivings/{put_sno}/ignore")
def ignore_receiving(put_sno: str):
    db.update_status(put_sno, "ignored")
    return {"success": True}


# ─── 동기화 ─────────────────────────────────────────────────────────────────

def _run_sync():
    global _sync_status
    if _sync_status["status"] == "syncing":
        return
    _sync_status["status"] = "syncing"
    _sync_status["lastSyncError"] = None
    try:
        import ourbox_scraper
        cfg = _load_cfg()
        ourbox_scraper.sync_new_receivings(cfg.get("ourbox_id", ""), cfg.get("ourbox_pw", ""))
        _sync_status["lastSyncTime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _sync_status["status"] = "idle"
    except Exception as e:
        _sync_status["lastSyncError"] = str(e)
        _sync_status["status"] = "error"


@router.post("/sync")
def manual_sync():
    if _sync_status["status"] == "syncing":
        return {"message": "이미 동기화 중입니다."}
    t = threading.Thread(target=_run_sync, daemon=True)
    t.start()
    return {"message": "동기화 시작됨"}


@router.get("/sync/status")
def sync_status_endpoint():
    return _sync_status


# ─── 상품 매핑 ───────────────────────────────────────────────────────────────

@router.get("/mappings")
def get_mappings():
    return db.get_mappings()


class MappingBody(BaseModel):
    ourbox_prod_cd: str
    ourbox_prod_nm: str = ""
    boxhero_item_id: int
    boxhero_item_nm: str = ""
    boxhero_sku: str = ""


@router.post("/mappings")
def save_mapping(body: MappingBody):
    db.upsert_mapping(body.model_dump())
    return {"success": True}


@router.post("/mappings/auto")
def auto_map():
    try:
        import ourbox_scraper
        cfg = _load_cfg()
        ourbox_prods = ourbox_scraper.fetch_all_ourbox_products(cfg.get("ourbox_id", ""), cfg.get("ourbox_pw", ""))
        bh_items = bh.fetch_all_items_list(cfg.get("api_token", ""))
        bh_by_sku = {i["sku"].strip(): i for i in bh_items if i.get("sku")}

        added, skipped = 0, 0
        for p in ourbox_prods:
            candidates = [
                (p.get("item_pkg_unit_barcode") or "").strip(),
                (p.get("item_pkg_unit_inspect_barcode") or "").strip(),
                (p.get("item_cd") or "").strip(),
            ]
            found = next((bh_by_sku[c] for c in candidates if c and c in bh_by_sku), None)
            if found:
                db.upsert_mapping({
                    "ourbox_prod_cd": p["prod_cd"],
                    "ourbox_prod_nm": p.get("sale_prod_nm", ""),
                    "boxhero_item_id": found["id"],
                    "boxhero_item_nm": found["name"],
                    "boxhero_sku": found.get("sku", ""),
                })
                added += 1
            else:
                skipped += 1

        return {"success": True, "added": added, "skipped": skipped, "total": len(ourbox_prods)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/mappings/{prod_cd}")
def delete_mapping(prod_cd: str):
    db.delete_mapping(prod_cd)
    return {"success": True}


# ─── 아워박스 / 박스히어로 상품 목록 ─────────────────────────────────────────

@router.get("/ourbox-products")
def ourbox_products():
    try:
        import ourbox_scraper
        cfg = _load_cfg()
        prods = ourbox_scraper.fetch_all_ourbox_products(cfg.get("ourbox_id", ""), cfg.get("ourbox_pw", ""))
        return {"data": prods, "total": len(prods)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/boxhero-items")
def boxhero_items():
    cfg = _load_cfg()
    token = cfg.get("api_token", "")
    if not token:
        raise HTTPException(400, "BoxHero API 토큰이 설정되지 않았습니다.")
    return bh.fetch_all_items_list(token)
