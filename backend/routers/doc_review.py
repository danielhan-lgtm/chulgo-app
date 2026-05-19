from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Optional
import base64
import io
import os
import pandas as pd

router = APIRouter()

REVIEW_PROMPT = """당신은 물류 서류 검토 전문가입니다. 제공된 서류들을 교차 검토하여 아래 형식으로 분석 결과를 작성해주세요.

검토 항목:
1. **기본 입고 정보 확인** - 입고예정일, 납품처, 밀크런 번호, 발주번호(PO) 서류 간 일치 여부
2. **SKU별 수량 및 박스 입수 검증** - 품목명, 확정수량(EA), 입수기준, 박스수량(BOX)을 표 형태로 정리하고 검토결과 명시
3. **기타 물류 정보** - 팔레트 수량, 유통기한 등
4. **서류 간 상이한 점** - 수량, 날짜, 코드 등 불일치 항목 (없으면 "이상 없음" 명시)
5. **최종 검토 결론** - 입고 진행 가능 여부

각 항목 옆에 (통과 ✅ / 주의 ⚠️ / 오류 ❌) 상태를 표시해주세요.
불일치가 발견된 경우 어느 서류와 어느 서류가 다른지 구체적으로 명시해주세요."""


def _extract_pptx_text(data: bytes) -> str:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if t:
                        lines.append(t)
        if lines:
            parts.append(f"[슬라이드 {i}]\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _extract_excel_text(data: bytes, filename: str) -> str:
    try:
        xl = pd.ExcelFile(io.BytesIO(data))
        parts = []
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if not df.empty:
                parts.append(f"[시트: {sheet}]\n{df.to_string(index=False)}")
        return "\n\n".join(parts) if parts else "(내용 없음)"
    except Exception as e:
        return f"(엑셀 파싱 오류: {e})"


@router.post("/coupang")
async def review_coupang(
    거래명세서: Optional[UploadFile] = File(None),
    부착리스트: Optional[UploadFile] = File(None),
    적재리스트: Optional[UploadFile] = File(None),
    밀크런등록내역: Optional[UploadFile] = File(None),
    출고내역: Optional[UploadFile] = File(None),
):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    content = []
    uploaded = []

    # PDF 파일: Claude가 직접 읽음
    for label, upload in [("거래명세서", 거래명세서), ("부착리스트", 부착리스트)]:
        if upload and upload.filename:
            data = await upload.read()
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(data).decode(),
                },
                "title": label,
            })
            uploaded.append(label)

    # PPT/PPTX: 텍스트 추출
    if 적재리스트 and 적재리스트.filename:
        data = await 적재리스트.read()
        text = _extract_pptx_text(data)
        content.append({"type": "text", "text": f"[적재리스트 내용]\n{text}"})
        uploaded.append("적재리스트")

    # 이미지: Claude가 직접 읽음
    if 밀크런등록내역 and 밀크런등록내역.filename:
        data = await 밀크런등록내역.read()
        ext = (밀크런등록내역.filename.rsplit(".", 1)[-1]).lower()
        mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
        mime = mime_map.get(ext, "image/jpeg")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.standard_b64encode(data).decode(),
            },
        })
        content.append({"type": "text", "text": "위 이미지는 밀크런 등록내역(현황) 화면입니다."})
        uploaded.append("밀크런등록내역")

    # 엑셀: 텍스트 추출
    if 출고내역 and 출고내역.filename:
        data = await 출고내역.read()
        text = _extract_excel_text(data, 출고내역.filename)
        content.append({"type": "text", "text": f"[출고내역 (엑셀) 내용]\n{text}"})
        uploaded.append("출고내역")

    if not uploaded:
        raise HTTPException(400, "최소 하나의 파일을 업로드해주세요.")

    content.append({"type": "text", "text": REVIEW_PROMPT})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )

    return {
        "result": response.content[0].text,
        "uploaded_files": uploaded,
    }


@router.post("/kurly")
async def review_kurly(
    files: list[UploadFile] = File(...),
):
    raise HTTPException(501, "마켓컬리 검토는 준비 중입니다.")
