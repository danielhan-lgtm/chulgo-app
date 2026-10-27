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



# ── 경영진보고(짧은 양식) 사용자 입력 — 제목·한줄정리·채널별 의견. 비우면 자동 문구가 들어간다 ──
_BRIEF_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "exec_brief_notes.json")


class BriefNotes(BaseModel):
    title: str = ""                 # '' = 기본 'DJ&A(수입제품) 발주·납품 현황'
    headline: str = ""              # 한줄정리 (줄마다 한 항목, '•' 없이 써도 됨). '' = 자동
    opinions: dict = {}             # {coupang|oliveyoung|kurly|naver: 의견}. '' = 자동
    updated_at: str = ""


def load_brief_notes() -> dict:
    try:
        if os.path.exists(_BRIEF_PATH):
            with open(_BRIEF_PATH, "r", encoding="utf-8") as f:
                return BriefNotes(**json.load(f)).model_dump()
    except Exception:
        pass
    return BriefNotes().model_dump()


def save_brief_notes(body: BriefNotes) -> dict:
    d = body.model_dump()
    d["opinions"] = {k: str(v or "") for k, v in (d.get("opinions") or {}).items()}
    d["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(_BRIEF_PATH), exist_ok=True)
    with open(_BRIEF_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d
