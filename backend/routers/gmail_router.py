from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional
import base64, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
import state

router = APIRouter()

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://localhost:8081"
REDIRECT_URI = f"{BACKEND_URL}/api/gmail/callback"


class AuthUrlRequest(BaseModel):
    client_id: str
    client_secret: str


class ExchangeRequest(BaseModel):
    client_id: str
    client_secret: str
    redirect_url: str  # full redirect URL pasted by user


class DisconnectRequest(BaseModel):
    pass


@router.post("/auth-url")
def get_auth_url(body: AuthUrlRequest):
    try:
        flow = U.gmail_build_flow(body.client_id, body.client_secret, REDIRECT_URI)
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        state.gmail_flow = flow
        U.save_config({"gmail_client_id": body.client_id, "gmail_client_secret": body.client_secret})
        return {"auth_url": auth_url}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/callback")
def gmail_callback(code: str = None, error: str = None):
    if error or not code:
        return RedirectResponse(f"{FRONTEND_URL}?gmail_error=1")
    try:
        if not state.gmail_flow:
            return RedirectResponse(f"{FRONTEND_URL}?gmail_error=flow_expired")
        state.gmail_flow.fetch_token(code=code)
        creds = state.gmail_flow.credentials
        token_info = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or U.GMAIL_SCOPES),
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
        U.save_config({"gmail_token": token_info})
        state.gmail_flow = None
        return RedirectResponse(f"{FRONTEND_URL}?gmail_connected=1")
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}?gmail_error={str(e)[:50]}")


@router.get("/status")
def gmail_status():
    cfg = U.load_config()
    token_info = cfg.get("gmail_token")
    if not token_info or not token_info.get("refresh_token"):
        return {"connected": False}
    client_id = token_info.get("client_id", "")
    return {"connected": True, "client_id": client_id[:20] + "..." if len(client_id) > 20 else client_id}


@router.post("/disconnect")
def gmail_disconnect():
    U.save_config({"gmail_token": None})
    return {"ok": True}


@router.get("/messages")
def get_messages():
    cfg = U.load_config()
    token_info = cfg.get("gmail_token")
    if not token_info:
        raise HTTPException(400, "Gmail이 연결되지 않았습니다.")
    try:
        orders, debug = U.fetch_gmail_orders(token_info)
        return {"orders": orders, "debug": debug, "senders": U.GMAIL_SENDERS}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/attachment")
def get_attachment(message_id: str, attachment_id: str):
    cfg = U.load_config()
    token_info = cfg.get("gmail_token")
    if not token_info:
        raise HTTPException(400, "Gmail이 연결되지 않았습니다.")
    try:
        data = U.download_gmail_attachment(token_info, message_id, attachment_id)
        return {"data": base64.b64encode(data).decode(), "size": len(data)}
    except Exception as e:
        raise HTTPException(400, str(e))
