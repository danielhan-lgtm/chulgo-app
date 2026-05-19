from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
import state

router = APIRouter()


class ConnectRequest(BaseModel):
    token: str


class SendRequest(BaseModel):
    token: str
    location_id: int
    items: List[dict]  # [{sku: str, quantity: int}]
    memo: Optional[str] = ""
    partner_id: Optional[int] = None


@router.post("/connect")
def boxhero_connect(body: ConnectRequest):
    try:
        locs = U.fetch_locations(body.token)
        U.save_config({"api_token": body.token})
        return {"ok": True, "locations": locs}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/locations")
def get_locations(token: str):
    try:
        return {"locations": U.fetch_locations(token)}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/partners")
def get_partners(token: str):
    try:
        return {"partners": U.fetch_partners(token)}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/send")
def send_to_boxhero(body: SendRequest):
    try:
        sku_to_id = U.fetch_all_items(body.token)
        items_payload = []
        missing = []
        for item in body.items:
            sku = str(item["sku"]).strip()
            qty = int(item["quantity"])
            item_id = sku_to_id.get(sku)
            if item_id:
                items_payload.append({"item_id": item_id, "quantity": -qty})
            else:
                missing.append(sku)

        if not items_payload:
            raise HTTPException(400, f"전송할 유효한 항목 없음. 미등록 SKU: {missing}")

        payload = {
            "type": "out",
            "to_location_id": body.location_id,
            "items": items_payload,
            "memo": body.memo or "",
        }
        if body.partner_id:
            payload["partner_id"] = body.partner_id

        result = U.post_transaction(body.token, payload)
        tx_id = result.get("id", "")
        state.add_log(
            "success",
            f"출고 전송 완료 ({len(items_payload)}개 SKU, {sum(i['quantity'] for i in body.items)}개)",
            f"트랜잭션 ID: {tx_id} | 메모: {body.memo}",
            payload=payload,
            source="boxhero",
        )
        return {"ok": True, "tx_id": tx_id, "missing_skus": missing, "item_count": len(items_payload)}
    except HTTPException:
        raise
    except Exception as e:
        state.add_log("error", f"출고 전송 실패: {str(e)[:100]}", source="boxhero")
        raise HTTPException(400, str(e))
