from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import html as _html
import io, sys, os
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter()


def _find_col(cols, *keys):
    for c in cols:
        cl = str(c)
        if any(k in cl for k in keys):
            return c
    return None


@router.post("/parse")
async def parse_inventory(
    inv_file: UploadFile = File(...),
    sheet_name: Optional[str] = Form(None),
):
    """재고목록 엑셀 → 시트/컬럼 자동감지 + 정규화된 품목 리스트 반환.
    상태→폐기/기부 분류, 단가, 비용은 프론트에서 즉시 계산한다 (재업로드 불필요)."""
    raw = await inv_file.read()
    try:
        xf = pd.ExcelFile(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"파일 읽기 오류: {e}")

    sheets = xf.sheet_names
    # '상태' 컬럼이 있는 시트 자동 선택 (보통 '상세내역')
    target = sheet_name
    if not target:
        for sn in sheets:
            try:
                peek = xf.parse(sn, nrows=1)
            except Exception:
                continue
            if _find_col(peek.columns, "상태") is not None:
                target = sn
                break
        if not target:
            target = sheets[0]

    try:
        df = xf.parse(target)
    except Exception as e:
        raise HTTPException(400, f"시트 읽기 오류: {e}")

    cols = [str(c) for c in df.columns]
    df.columns = cols
    status_col = _find_col(cols, "상태")
    name_col = _find_col(cols, "제품명", "상품명", "품명")
    qty_col = _find_col(cols, "수량")
    brand_col = _find_col(cols, "브랜드")
    expiry_col = _find_col(cols, "소비기한", "유통기한")

    if not (status_col and name_col and qty_col):
        raise HTTPException(400, "상태·제품명·수량 컬럼을 찾을 수 없습니다. 재고목록 형식을 확인해주세요.")

    items = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if not name or name.lower() == "nan":
            continue
        try:
            qty = int(float(row.get(qty_col, 0)))
        except Exception:
            qty = 0
        items.append({
            "brand": (str(row.get(brand_col, "")).strip() if brand_col else ""),
            "name": name,
            "expiry": (str(row.get(expiry_col, "")).strip() if expiry_col else ""),
            "qty": qty,
            "status": str(row.get(status_col, "")).strip(),
        })

    statuses = sorted({it["status"] for it in items if it["status"]})
    brands = sorted({it["brand"] for it in items if it["brand"]})

    return {
        "sheets": sheets,
        "sheet": target,
        "columns": {
            "status": status_col, "name": name_col, "qty": qty_col,
            "brand": brand_col, "expiry": expiry_col,
        },
        "statuses": statuses,
        "brands": brands,
        "items": items,
    }


class ReportRow(BaseModel):
    brand: str = ""
    name: str
    unit_price: int = 0
    qty: int = 0
    amount: int = 0
    count: int = 1


class ExportBody(BaseModel):
    base_date: str = ""
    disposal_cost: int = 0
    disposal_rows: List[ReportRow] = []
    donate_rows: List[ReportRow] = []


@router.post("/export")
def export_report(body: ExportBody):
    """계산된 폐기·기부 내역 → 요약/폐기내역/기부내역 3시트 엑셀."""
    disp_qty = sum(r.qty for r in body.disposal_rows)
    disp_amt = sum(r.amount for r in body.disposal_rows)
    dona_qty = sum(r.qty for r in body.donate_rows)
    dona_amt = sum(r.amount for r in body.donate_rows)
    total_qty = disp_qty + dona_qty

    summary = pd.DataFrame({
        "항목": ["기준일", "폐기 수량", "폐기 손실(원)", "기부 수량", "기부 전환(원)",
                "폐기 처리 비용(원)", "총 처리 수량", "총 손실(폐기손실+처리비용)"],
        "값": [body.base_date or "-", disp_qty, disp_amt, dona_qty, dona_amt,
              body.disposal_cost, total_qty, disp_amt + body.disposal_cost],
    })

    def _rows_df(rows: List[ReportRow]):
        cols = ["브랜드", "제품명", "단가", "수량", "금액", "건수"]
        if not rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame([{
            "브랜드": r.brand, "제품명": r.name, "단가": r.unit_price,
            "수량": r.qty, "금액": r.amount, "건수": r.count,
        } for r in rows])[cols]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="요약", index=False)
        _rows_df(body.disposal_rows).to_excel(w, sheet_name="폐기내역", index=False)
        _rows_df(body.donate_rows).to_excel(w, sheet_name="기부내역", index=False)
    buf.seek(0)

    filename = "폐기리포트.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{requests.utils.quote(filename)}"},
    )


# ══════════════════════════════════════════════════════════════════
# OB API 기반 소비기한 · 기부 리포트
#   OurBox product_stock은 lot(소비기한)별 행이므로 잔여일을 바로 계산 가능.
#   등급: 잔여 < warn_days(60) → urgent(기부·폐기) / < caution_days(120) → caution(주의)
#         그 이상 → normal(정상) / 기한 없음 → none
# ══════════════════════════════════════════════════════════════════

GRADE_LABEL = {
    "urgent": "기부·폐기 대상",
    "caution": "주의",
    "normal": "정상",
    "none": "기한정보 없음",
}


def _classify(days: Optional[int], warn_days: int, caution_days: int) -> str:
    if days is None:
        return "none"
    if days < warn_days:
        return "urgent"
    if days < caution_days:
        return "caution"
    return "normal"


@router.get("/expiry-report")
def expiry_report(
    warn_days: int = Query(60, ge=1, description="이 잔여일 미만 → 기부·폐기 대상"),
    caution_days: int = Query(120, ge=2, description="이 잔여일 미만 → 주의"),
):
    """OurBox API 현재고(lot별 소비기한) → 잔여일 등급 분류 리포트."""
    import utils_core as U
    import ourbox_api as api_mod
    from datetime import date

    if caution_days <= warn_days:
        raise HTTPException(400, "caution_days는 warn_days보다 커야 합니다.")

    cfg = U.load_config()
    client = api_mod.make_client(cfg)
    if not client:
        raise HTTPException(400, "OurBox API 미설정 — 설정에서 access/secret key를 확인해주세요.")
    try:
        raw = client.fetch_stock()
    except PermissionError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        raise HTTPException(502, f"OurBox 재고 조회 실패: {e}")

    today = date.today()
    rows = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        total = int(r.get("total_stock") or 0)
        if total <= 0:
            continue  # 재고 없는 lot은 리포트 대상 아님
        exp = str(r.get("expiration_date") or "").strip()
        days = None
        if exp:
            try:
                days = (date.fromisoformat(exp[:10]) - today).days
            except ValueError:
                days = None
        rows.append({
            "code": str(r.get("sales_product_code") or ""),
            "name": _html.unescape(str(r.get("product_name") or "").strip()),
            "expiry": exp,
            "days_left": days,
            "total": total,
            "available": int(r.get("available_stock") or 0),
            "unavailable": int(r.get("unavailable_stock") or 0),
            "grade": _classify(days, warn_days, caution_days),
        })

    # 임박한 것부터 (기한 없음은 맨 뒤)
    rows.sort(key=lambda x: (x["days_left"] is None, x["days_left"] or 0, x["name"]))

    summary = {g: {"items": 0, "total": 0, "available": 0} for g in GRADE_LABEL}
    expired_items = expired_qty = 0
    for r in rows:
        s = summary[r["grade"]]
        s["items"] += 1
        s["total"] += r["total"]
        s["available"] += r["available"]
        if r["days_left"] is not None and r["days_left"] < 0:
            expired_items += 1
            expired_qty += r["total"]

    return {
        "base_date": today.isoformat(),
        "warn_days": warn_days,
        "caution_days": caution_days,
        "summary": summary,
        "expired": {"items": expired_items, "total": expired_qty},
        "rows": rows,
    }


class ExpiryRow(BaseModel):
    code: str = ""
    name: str
    expiry: str = ""
    days_left: Optional[int] = None
    total: int = 0
    available: int = 0
    unavailable: int = 0
    grade: str = "none"


class ExpiryExportBody(BaseModel):
    base_date: str = ""
    warn_days: int = 60
    caution_days: int = 120
    rows: List[ExpiryRow] = []


@router.post("/expiry-export")
def expiry_export(body: ExpiryExportBody):
    """소비기한 리포트 → 요약 + 등급별 시트 엑셀 (기부·폐기 시트가 핵심)."""
    def _days_label(d: Optional[int]) -> str:
        if d is None:
            return "-"
        return f"만료 {-d}일 경과" if d < 0 else f"D-{d}"

    def _rows_df(rows: List[ExpiryRow]):
        cols = ["상품코드", "제품명", "소비기한", "잔여일", "전체재고", "가용재고", "가용외", "구분"]
        if not rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame([{
            "상품코드": r.code, "제품명": r.name, "소비기한": r.expiry,
            "잔여일": _days_label(r.days_left), "전체재고": r.total,
            "가용재고": r.available, "가용외": r.unavailable,
            "구분": GRADE_LABEL.get(r.grade, r.grade),
        } for r in rows])[cols]

    by_grade: dict = {g: [] for g in GRADE_LABEL}
    for r in body.rows:
        by_grade.get(r.grade, by_grade["none"]).append(r)

    expired = [r for r in by_grade["urgent"] if r.days_left is not None and r.days_left < 0]
    summary = pd.DataFrame({
        "항목": [
            "기준일",
            f"기부·폐기 대상 (잔여 {body.warn_days}일 미만) 품목수",
            "기부·폐기 대상 수량(전체)",
            "기부·폐기 대상 수량(가용)",
            "  └ 이미 만료 품목수",
            "  └ 이미 만료 수량(전체)",
            f"주의 (잔여 {body.warn_days}~{body.caution_days}일) 품목수",
            "주의 수량(전체)",
            f"정상 (잔여 {body.caution_days}일 이상) 품목수",
            "정상 수량(전체)",
            "기한정보 없음 품목수",
        ],
        "값": [
            body.base_date or "-",
            len(by_grade["urgent"]),
            sum(r.total for r in by_grade["urgent"]),
            sum(r.available for r in by_grade["urgent"]),
            len(expired),
            sum(r.total for r in expired),
            len(by_grade["caution"]),
            sum(r.total for r in by_grade["caution"]),
            len(by_grade["normal"]),
            sum(r.total for r in by_grade["normal"]),
            len(by_grade["none"]),
        ],
    })

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="요약", index=False)
        _rows_df(by_grade["urgent"]).to_excel(w, sheet_name="기부·폐기 대상", index=False)
        _rows_df(by_grade["caution"]).to_excel(w, sheet_name="주의", index=False)
        _rows_df(by_grade["normal"]).to_excel(w, sheet_name="정상", index=False)
        if by_grade["none"]:
            _rows_df(by_grade["none"]).to_excel(w, sheet_name="기한정보없음", index=False)
    buf.seek(0)

    filename = f"기부리포트_{body.base_date or ''}.xlsx".replace("_.", ".")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{requests.utils.quote(filename)}"},
    )
