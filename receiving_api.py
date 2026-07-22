"""FastAPI 서버 - 입고정산기 REST API (Node.js 서버와 동일한 엔드포인트)"""
import threading
import os
import json
from datetime import datetime
import requests as _requests

_server_thread = None
_app = None


def get_app():
    global _app
    if _app is not None:
        return _app

    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, FileResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import receiving_db as db
    import boxhero_incoming as bh
    import ourbox_scraper

    _app = FastAPI()
    _app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    _sync_status = {"status": "idle", "lastSyncTime": None, "lastSyncError": None}
    _sync_lock = threading.Lock()

    # ─── HTML ────────────────────────────────────────────────────────────────
    HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "receiving_ui.html")

    @_app.get("/", response_class=HTMLResponse)
    def root():
        with open(HTML_PATH, encoding="utf-8") as f:
            return f.read()

    # ─── 입고 목록 ────────────────────────────────────────────────────────────
    @_app.get("/api/receivings")
    def get_receivings():
        records = db.get_all()
        for r in records:
            r["items"] = db.get_items(r["put_sno"])
        return records

    @_app.get("/api/receivings/{put_sno}")
    def get_receiving(put_sno: str):
        records = db.get_all()
        rec = next((r for r in records if r["put_sno"] == put_sno), None)
        if not rec:
            raise HTTPException(404, "입고 기록 없음")
        rec["items"] = db.get_items(put_sno)
        return rec

    @_app.post("/api/receivings/{put_sno}/approve")
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
                from datetime import timedelta
                dt_kst = datetime.strptime(rec["put_compt_dtm"][:19], "%Y-%m-%d %H:%M:%S")
                tx_time = (dt_kst - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass

        try:
            result = bh.create_in_transaction(
                api_token, location_id, tx_items,
                memo=f"아워박스 입고번호: {put_sno} ({rec.get('put_depot_nm', '')})",
                tx_time=tx_time
            )
        except _requests.exceptions.HTTPError as e:
            try:
                detail = e.response.json()
            except Exception:
                detail = e.response.text if e.response else str(e)
            raise HTTPException(502, f"박스히어로 API 오류: {detail}")
        except Exception as e:
            raise HTTPException(500, f"승인 처리 중 오류: {str(e)}")
        db.update_status(put_sno, "approved", result["id"])
        unmapped = [i["sale_prod_nm"] for i in items if not i.get("boxhero_item_id")]
        return {
            "success": True,
            "boxhero_tx_id": result["id"],
            "mapped_count": len(mapped),
            "unmapped_count": len(unmapped),
            "unmapped_items": unmapped,
        }

    @_app.post("/api/receivings/{put_sno}/cancel")
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

    @_app.post("/api/receivings/{put_sno}/ignore")
    def ignore_receiving(put_sno: str):
        db.update_status(put_sno, "ignored")
        return {"success": True}

    # ─── 로그인 테스트 ──────────────────────────────────────────────────────────
    @_app.post("/api/test-login")
    def test_login():
        cfg = _load_cfg()
        try:
            ourbox_scraper._test_login(cfg.get("ourbox_id", ""), cfg.get("ourbox_pw", ""))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─── 동기화 ────────────────────────────────────────────────────────────────
    def _run_sync():
        if _sync_status["status"] == "syncing":
            return
        _sync_status["status"] = "syncing"
        _sync_status["lastSyncError"] = None
        try:
            cfg = _load_cfg()
            cnt = ourbox_scraper.sync_new_receivings(cfg.get("ourbox_id", ""), cfg.get("ourbox_pw", ""))
            _sync_status["lastSyncTime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _sync_status["status"] = "idle"
        except Exception as e:
            _sync_status["lastSyncError"] = str(e)
            _sync_status["status"] = "error"

    @_app.post("/api/sync")
    def manual_sync():
        if _sync_status["status"] == "syncing":
            return {"message": "이미 동기화 중입니다."}
        t = threading.Thread(target=_run_sync, daemon=True)
        t.start()
        return {"message": "동기화 시작됨"}

    @_app.get("/api/sync/status")
    def sync_status():
        return _sync_status

    # ─── 상품 매핑 ────────────────────────────────────────────────────────────
    @_app.get("/api/mappings")
    def get_mappings():
        return db.get_mappings()

    class MappingBody(BaseModel):
        ourbox_prod_cd: str
        ourbox_prod_nm: str = ""
        boxhero_item_id: int
        boxhero_item_nm: str = ""
        boxhero_sku: str = ""

    @_app.post("/api/mappings")
    def save_mapping(body: MappingBody):
        db.upsert_mapping(body.model_dump())
        return {"success": True}

    @_app.post("/api/mappings/auto")
    def auto_map():
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

    @_app.delete("/api/mappings/{prod_cd}")
    def delete_mapping(prod_cd: str):
        db.delete_mapping(prod_cd)
        return {"success": True}

    # ─── 아워박스 / 박스히어로 상품 목록 ─────────────────────────────────────
    @_app.get("/api/ourbox-products")
    def ourbox_products(page: int = 1):
        cfg = _load_cfg()
        prods = ourbox_scraper.fetch_all_ourbox_products(cfg.get("ourbox_id", ""), cfg.get("ourbox_pw", ""))
        return {"data": prods, "total": len(prods)}

    @_app.get("/api/boxhero-items")
    def boxhero_items():
        cfg = _load_cfg()
        return bh.fetch_all_items_list(cfg.get("api_token", ""))

    return _app


def _load_cfg() -> dict:
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def start_server(port: int = 3001):
    """백그라운드 스레드에서 FastAPI 서버 시작. 이미 실행 중이면 무시."""
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return

    def _run():
        import uvicorn
        uvicorn.run(get_app(), host="127.0.0.1", port=port, log_level="error")

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
