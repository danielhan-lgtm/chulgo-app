from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
from typing import Optional, Any
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import state
import utils_core as U

router = APIRouter()


class ConfigUpdate(BaseModel):
    data: dict


@router.get("/config")
def get_config():
    cfg = U.load_config()
    # Mask sensitive fields for display
    safe = {k: v for k, v in cfg.items()}
    return {
        "config": safe,
        "has_master_default": bool(U.DEFAULT_MASTER),
        "master_loaded": state.master_bytes is not None,
    }


@router.post("/config")
def update_config(body: ConfigUpdate):
    U.save_config(body.data)
    return {"ok": True}


@router.post("/master/upload")
async def upload_master(file: UploadFile = File(...)):
    data = await file.read()
    state.master_bytes = data
    import pandas as pd, io
    df = pd.read_excel(io.BytesIO(data))
    return {"ok": True, "rows": len(df), "filename": file.filename}


@router.get("/master/status")
def master_status():
    if state.master_bytes:
        return {"loaded": True, "source": "uploaded"}
    if U.DEFAULT_MASTER:
        return {"loaded": True, "source": "default", "path": U.DEFAULT_MASTER}
    return {"loaded": False}
