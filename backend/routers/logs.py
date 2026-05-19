from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import datetime, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import state

router = APIRouter()


class LogEntry(BaseModel):
    level: str
    message: str
    detail: Optional[str] = ""
    payload: Optional[dict] = None
    source: Optional[str] = ""


@router.get("/logs")
def get_logs():
    today = datetime.date.today().isoformat()
    cnt = state.tx_counter.get(today, {"total": 0, "success": 0, "error": 0})
    warn_count = sum(
        1 for l in state.logs
        if l["level"] == "warning" and l["ts"].startswith(datetime.date.today().strftime("%m-%d"))
    )
    return {"logs": state.logs, "counter": cnt, "warn_count": warn_count}


@router.post("/logs")
def add_log(entry: LogEntry):
    state.add_log(entry.level, entry.message, entry.detail, entry.payload, entry.source)
    return {"ok": True}


@router.delete("/logs")
def clear_logs():
    state.logs.clear()
    state.tx_counter.clear()
    return {"ok": True}
