"""경영진 요약 보고서 저장소.

출고예측·재고분석 대시보드가 자동 생성한 요약 보고서를 사용자가 수기 수정하면
그 수정본을 페이지 키별로 보관한다. 수정본이 없으면 프론트가 항상 최신 데이터로
자동 생성해 보여주고, 수정본이 있으면 수정본을 우선 표시한다.
"""
import os, json
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "exec_reports.json")


def _load() -> dict:
    try:
        if os.path.exists(_PATH):
            with open(_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


class ReportBody(BaseModel):
    text: str


@router.get("/{page_key}")
def get_report(page_key: str):
    r = _load().get(page_key)
    return r or {"text": None, "updated_at": None}


@router.put("/{page_key}")
def save_report(page_key: str, body: ReportBody):
    d = _load()
    d[page_key] = {"text": body.text, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    _save(d)
    return d[page_key]


@router.delete("/{page_key}")
def reset_report(page_key: str):
    """수기 수정본 삭제 → 자동 생성본으로 복귀."""
    d = _load()
    if page_key in d:
        del d[page_key]
        _save(d)
    return {"ok": True}
