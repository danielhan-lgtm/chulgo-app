# -*- coding: utf-8 -*-
"""마켓컬리 직납 거래명세서(PDF) → 입고 라벨지 품목 파싱 (API 0원)

거래명세서의 품목표(순번/마스터코드/바코드/품명/총수량/유통기한/제조일자/수량/박스당입수…)를
읽어 라벨 생성용 품목 배열로 변환한다. PPT(라벨지) 생성은 프론트(pptxgenjs)에서 수행한다.
"""
import io
import re

import pdfplumber
from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter()


def _to_int(v) -> int:
    """'4,680'→4680, 4680.0/'4680.0'→4680, NaN/빈값→0 (마침표를 자릿수로 붙이는 오류 방지)."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        try:
            return int(round(v)) if v == v else 0  # NaN != NaN
        except Exception:
            return 0
    s = str(v).replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(round(float(s)))
    except Exception:
        pass
    try:
        return int(re.sub(r"[^\d\-]", "", s) or 0)
    except Exception:
        return 0


def _norm(s: str) -> str:
    # 일부 PDF 폰트는 글자 사이에 NULL(\x00)을 삽입 → 공백 처리 후 정리
    t = str(s or "").replace("\x00", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", t).strip()


def _date(s: str) -> str:
    """'(소) 2027-03-20' → '2027-03-20'"""
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(s or ""))
    return m.group(0) if m else _norm(s)


def _find_col_map(header: list) -> dict:
    """헤더 행에서 필요한 컬럼 인덱스를 찾는다."""
    idx = {}
    for i, h in enumerate(header):
        h = _norm(h)
        if "마스터" in h or "마스터코드" in h:
            idx.setdefault("code", i)
        elif h == "품명" or "품명" in h:
            idx.setdefault("name", i)
        elif "총수량" in h:
            idx.setdefault("total", i)
        elif "유통기한" in h or "소비기한" in h:
            idx.setdefault("expiry", i)
        elif "입수" in h:
            idx.setdefault("perBox", i)
        elif h == "수량":
            idx.setdefault("boxCount", i)
    return idx


def parse_kurly_statement(data: bytes) -> dict:
    order_code = ""
    order_codes: list[str] = []
    supplier = ""
    items: list[dict] = []

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        full_text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
        full_text = full_text.replace("\x00", "")

        mo = re.search(r"발주코드\s*[:：]\s*([A-Za-z0-9_\-]+)", full_text)
        if mo:
            order_code = mo.group(1).strip()
        ms = re.search(r"공급사\s+(.+?)\s*\(VD", full_text)
        if ms:
            supplier = _norm(ms.group(1))
        md = re.search(r"입고일\s*[:：]\s*([\d\-]+)", full_text)
        center = ""
        mc = re.search(r"\n([가-힣A-Za-z0-9]+)\s*\(\s*\d+층", full_text)  # '평택상온(5층)'
        if mc:
            center = _norm(mc.group(1))

        # 발주코드는 명세서(발주 건)마다 다르므로 페이지 단위로 추적해 품목에 매핑한다.
        # 표가 다음 페이지로 이어지면 발주코드가 없는 페이지도 있어 직전 코드를 유지한다.
        current_code = order_code
        for pg in pdf.pages:
            page_text = (pg.extract_text() or "").replace("\x00", "")
            mp = re.search(r"발주코드\s*[:：]\s*([A-Za-z0-9_\-]+)", page_text)
            if mp:
                current_code = mp.group(1).strip()
            if current_code and current_code not in order_codes:
                order_codes.append(current_code)
            for tbl in pg.extract_tables():
                if not tbl or len(tbl) < 2:
                    continue
                # 헤더 행 탐색(마스터코드 + 총수량 포함)
                header_i = None
                for ri, row in enumerate(tbl):
                    joined = " ".join(_norm(c) for c in row if c)
                    if "마스터코드" in joined and "총수량" in joined:
                        header_i = ri
                        break
                if header_i is None:
                    continue
                cmap = _find_col_map(tbl[header_i])
                if not all(k in cmap for k in ("code", "name", "total")):
                    continue
                for row in tbl[header_i + 1:]:
                    if not row:
                        continue
                    code = _norm(row[cmap["code"]]) if cmap["code"] < len(row) else ""
                    if not re.match(r"^M?\d", code):  # 마스터코드 형태가 아니면 skip
                        continue
                    name = _norm(row[cmap["name"]]) if cmap["name"] < len(row) else ""
                    total = _to_int(row[cmap["total"]]) if cmap["total"] < len(row) else 0
                    expiry = _date(row[cmap["expiry"]]) if "expiry" in cmap and cmap["expiry"] < len(row) else ""
                    per_box = _to_int(row[cmap["perBox"]]) if "perBox" in cmap and cmap["perBox"] < len(row) else 0
                    box_count = _to_int(row[cmap["boxCount"]]) if "boxCount" in cmap and cmap["boxCount"] < len(row) else 0
                    # 박스수가 없으면 총수량/입수로 보정
                    if box_count <= 0 and per_box > 0 and total > 0:
                        box_count = -(-total // per_box)
                    if not name and not code:
                        continue
                    items.append({
                        "name": name,
                        "code": code,
                        "total": total,
                        "expiry": expiry,
                        "perBox": per_box,
                        "boxCount": box_count,
                        "orderCode": current_code,
                    })

    return {
        "orderCode": order_code,
        "orderCodes": order_codes,
        "supplier": supplier or "(주)시나몬랩",
        "date": md.group(1) if md else "",
        "center": center,
        "items": items,
    }


@router.post("/parse")
async def parse_docs(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "마켓컬리 거래명세서 PDF를 업로드해주세요.")

    order_code = ""
    order_codes: list[str] = []
    supplier = ""
    items: list[dict] = []
    errors: list[str] = []

    for up in files:
        if not up.filename:
            continue
        data = await up.read()
        try:
            d = parse_kurly_statement(data)
            order_code = order_code or d["orderCode"]
            for oc in d.get("orderCodes") or []:
                if oc and oc not in order_codes:
                    order_codes.append(oc)
            supplier = supplier or d["supplier"]
            items.extend(d["items"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{up.filename}: {e}")

    if not items:
        raise HTTPException(
            400,
            "거래명세서에서 품목을 찾지 못했습니다. 마켓컬리 직납 거래명세서 PDF 인지 확인해주세요. "
            + ("(" + " / ".join(errors) + ")" if errors else ""),
        )

    return {
        "orderCode": order_code,
        "orderCodes": order_codes,
        "supplier": supplier or "(주)시나몬랩",
        "items": items,
        "parse_errors": errors,
    }
