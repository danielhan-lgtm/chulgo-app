from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import base64, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
import state

router = APIRouter()


class ConnectRequest(BaseModel):
    token: str


class ToggleReactionRequest(BaseModel):
    token: str
    channel_id: str
    ts: str
    emoji: str


class JoinChannelRequest(BaseModel):
    token: str
    channel_id: str


@router.post("/connect")
def slack_connect(body: ConnectRequest):
    try:
        channels = U.fetch_slack_channels(body.token)
        U.save_config({"slack_token": body.token})
        return {"ok": True, "channels": channels}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/messages")
def get_messages(token: str, channel_id: str):
    try:
        orders, debug = U.fetch_slack_orders(token, channel_id)
        return {"orders": orders, "debug": debug}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/file")
def download_file(url: str, token: str):
    try:
        data = U.download_slack_file(url, token)
        return {"data": base64.b64encode(data).decode(), "size": len(data)}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/reaction")
def toggle_reaction(body: ToggleReactionRequest):
    try:
        result = U.slack_toggle_reaction(body.token, body.channel_id, body.ts, body.emoji)
        return {"result": result}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/join")
def join_channel(body: JoinChannelRequest):
    try:
        U.slack_join_channel(body.token, body.channel_id)
        return {"ok": True}
    except Exception as e:
        err_str = str(e)
        if "method_not_supported_for_channel_type" in err_str:
            raise HTTPException(400, "private_channel")
        raise HTTPException(400, err_str)
