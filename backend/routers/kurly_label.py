# -*- coding: utf-8 -*-
"""마켓컬리 직납 거래명세서(PDF) → 입고 라벨지 품목 파싱 (API 0원)

거래명세서의 품목표(순번/마스터코드/바코드/품명/총수량/유통기한/제조일자/수량/박스당입수…)를
읽어 라벨 생성용 품목 배열로 변환한다. PPT(라벨지) 생성은 프론트(pptxgenjs)에서 수행한다.
"""
import difflib
import io
import json
import os
import re

import anyio.to_thread
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



# ─── OCR 오인식 보정 ────────────────────────────────────────────────
# 거래명세서가 이미지 PDF라 OCR로 읽히면 '시나몬랩→시나몬램', '표고버섯 크리스프→표고버'것 크리스 프'처럼
# 글자가 깨진다. 공급사명은 고정 교정하고, 상품명은 컬리 보고 단가표(상품코드→정식 상품명)로 치환한다.
_SUPPLIER_NAME = "(주)시나몬랩"
_PRICE_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kurly_report_state.json")


def _fix_supplier(s: str) -> str:
    t = _norm(s)
    if not t or "시나몬" in t.replace(" ", "") or difflib.SequenceMatcher(None, t, _SUPPLIER_NAME).ratio() >= 0.6:
        return _SUPPLIER_NAME
    return t


def _load_kurly_master() -> dict[str, str]:
    """컬리 단가표에서 {상품코드: 정식 상품명}. 파일이 없으면 빈 dict."""
    try:
        with open(_PRICE_STATE_PATH, encoding="utf-8") as f:
            items = (json.load(f).get("prices") or {}).get("items") or {}
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for v in items.values():
        if isinstance(v, dict) and v.get("code") and v.get("name"):
            out[str(v["code"]).strip()] = _norm(v["name"])
    return out


def _squash(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣&]", "", s or "")


def _fix_item_names(items: list[dict]) -> None:
    master = _load_kurly_master()
    if not master:
        return
    by_squash = {_squash(n): n for n in master.values()}
    for it in items:
        code = str(it.get("code") or "").strip()
        if code in master:
            it["name"] = master[code]
            continue
        # 코드도 오인식된 경우: 특수문자·공백 제거 후 정식 명칭과 유사도 매칭
        q = _squash(it.get("name") or "")
        if not q:
            continue
        best = difflib.get_close_matches(q, by_squash.keys(), n=1, cutoff=0.8)
        if best:
            it["name"] = by_squash[best[0]]


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


# ---------------------------------------------------------------------------
# 2026-08 신규 양식: 텍스트 레이어가 없는 이미지 PDF(스캔형).
# pdfplumber로는 아무것도 못 읽으므로 Windows 내장 OCR(winocr)로 폴백한다.
# 표는 OCR 단어의 bounding box 좌표로 컬럼/행을 복원한다.
# ---------------------------------------------------------------------------

_OCR_SCALE = 4  # 렌더링 배율(높을수록 OCR 정확도↑)


def _fix_gram_suffix(name: str) -> str:
    """OCR이 'g'를 '9'로 읽는 오류 보정: '크리스프 659' → '크리스프 65g'"""
    return re.sub(r"(?<=\d)9(?=\s*$)", "g", name)


def _ocr_pages(data: bytes) -> list[list[tuple[str, float, float]]]:
    """각 페이지를 이미지로 렌더링 후 OCR. 페이지별 (단어, 중심x, 중심y) 목록 반환."""
    import asyncio

    import pypdfium2 as pdfium
    import winocr

    pages: list[list[tuple[str, float, float]]] = []
    pdf = pdfium.PdfDocument(io.BytesIO(data))
    try:
        for page in pdf:
            img = page.render(scale=_OCR_SCALE).to_pil()
            result = asyncio.run(winocr.recognize_pil(img, "ko"))
            words: list[tuple[str, float, float]] = []
            for ln in result.lines:
                for w in ln.words:
                    r = w.bounding_rect
                    words.append((str(w.text).strip(), r.x + r.width / 2, r.y + r.height / 2))
            pages.append([w for w in words if w[0]])
    finally:
        pdf.close()
    return pages


def _ocr_find_header(words: list[tuple[str, float, float]]) -> tuple[float, dict] | None:
    """헤더 단어들(마스터코드/품명/총수량…)의 x중심으로 컬럼 위치를 찾는다."""
    anchor = next((w for w in words if "마스터" in w[0]), None)
    if not anchor:
        return None
    hy = anchor[2]
    cols: dict[str, float] = {"code": anchor[1]}
    for t, cx, cy in words:
        if abs(cy - hy) > 90:  # 헤더는 2줄(유통기한/소비기한 등)까지 허용
            continue
        if "품명" in t:
            cols.setdefault("name", cx)
        elif "총수량" in t:
            cols.setdefault("total", cx)
        elif "유통" in t or "소비" in t:
            cols.setdefault("expiry", cx)
        elif "제조" in t:
            cols.setdefault("made", cx)
        elif t == "수량":
            cols.setdefault("boxCount", cx)
        elif "입수" in t or "박스" in t:
            cols.setdefault("perBox", cx)
        elif "규격" in t:
            cols.setdefault("spec", cx)
        elif "바코드" in t:
            cols.setdefault("barcode", cx)
        elif "비고" in t:
            cols.setdefault("note", cx)
    # OCR이 헤더 일부를 놓치는 경우 위치를 합성한다
    if "total" not in cols:
        # '총수량' 글자를 통째로 놓친 경우(2026-10 평택 명세서) — 품명과 유통기한 사이 본문의 정수 토큰 x중심 중앙값으로 복원
        lo = cols.get("name", cols.get("barcode", cols["code"]))
        hi = cols.get("expiry") or cols.get("made") or cols.get("boxCount")
        if hi:
            xs = sorted(cx for t, cx, cy in words
                        if cy > hy + 60 and re.match(r"^\d{1,5}$", t) and lo + 60 < cx < hi - 60)
            if xs:
                cols["total"] = xs[len(xs) // 2]
    if "total" not in cols:
        return None
    if "name" not in cols and "barcode" in cols:
        cols["name"] = (cols["barcode"] + cols["total"]) / 2
    if "perBox" not in cols and "boxCount" in cols and "spec" in cols:
        xs = sorted(cx for t, cx, cy in words
                    if cy > hy + 60 and re.match(r"^\d{1,4}$", t) and cols["boxCount"] + 40 < cx < cols["spec"] - 40)
        cols["perBox"] = xs[len(xs) // 2] if xs else (cols["boxCount"] + cols["spec"]) / 2
    if "name" not in cols:
        return None
    return hy, cols


def _ocr_parse_items(words: list[tuple[str, float, float]], order_code: str) -> list[dict]:
    header = _ocr_find_header(words)
    if not header:
        return []
    hy, cols = header

    # 컬럼 경계: 인접 컬럼 x중심의 중간값
    order = sorted(cols.items(), key=lambda kv: kv[1])
    bounds: dict[str, tuple[float, float]] = {}
    for i, (key, cx) in enumerate(order):
        left = (order[i - 1][1] + cx) / 2 if i > 0 else 0
        right = (cx + order[i + 1][1]) / 2 if i + 1 < len(order) else 10 ** 9
        bounds[key] = (left, right)

    # 품명은 여러 줄로 길게 퍼지므로 총수량/바코드 쪽 경계를 넓혀준다
    pad = 18 * _OCR_SCALE
    if "name" in bounds and "total" in cols:
        lo, hi = bounds["name"]
        bounds["name"] = (lo, cols["total"] - pad)
        tlo, thi = bounds["total"]
        bounds["total"] = (cols["total"] - pad, thi)
    if "name" in bounds and "barcode" in cols:
        lo, hi = bounds["name"]
        bounds["name"] = (cols["barcode"] + pad, hi)
        blo, bhi = bounds["barcode"]
        bounds["barcode"] = (blo, cols["barcode"] + pad)

    def col_of(cx: float) -> str | None:
        for key, (lo, hi) in bounds.items():
            if lo <= cx < hi:
                return key
        return None

    body = [w for w in words if w[2] > hy + 60]
    anchors = sorted((w for w in body if re.match(r"^M\d{8,}$", w[0])), key=lambda w: w[2])
    if not anchors:
        return []

    items: list[dict] = []
    for i, (code, _, ay) in enumerate(anchors):
        top = (anchors[i - 1][2] + ay) / 2 if i > 0 else hy + 60
        bot = (ay + anchors[i + 1][2]) / 2 if i + 1 < len(anchors) else ay + 250
        row = sorted((w for w in body if top <= w[2] < bot), key=lambda w: (w[2], w[1]))
        cells: dict[str, list[tuple[str, float, float]]] = {}
        for t, cx, cy in row:
            k = col_of(cx)
            if k:
                cells.setdefault(k, []).append((t, cx, cy))

        def _cell_text(key: str) -> str:
            # 같은 시각적 줄인데 y가 몇 px씩 어긋나므로 줄 단위로 묶은 뒤 x순 정렬
            ws = sorted(cells.get(key, []), key=lambda w: w[2])
            lines: list[list[tuple[str, float, float]]] = []
            for w in ws:
                if lines and w[2] - lines[-1][-1][2] <= 8 * _OCR_SCALE:
                    lines[-1].append(w)
                else:
                    lines.append([w])
            return " ".join(t for line in lines for t, _, _ in sorted(line, key=lambda w: w[1]))

        name = _fix_gram_suffix(_norm(_cell_text("name")))
        total = _to_int(_cell_text("total") or "0")
        expiry = _date(_cell_text("expiry"))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", expiry):
            m = re.search(r"\d{4}-\d{2}-\d{2}", " ".join(t for t, _, _ in row))
            expiry = m.group(0) if m else ""
        per_box = _to_int(_cell_text("perBox") or "0")
        box_count = _to_int(_cell_text("boxCount") or "0")
        if box_count <= 0 and per_box > 0 and total > 0:
            box_count = -(-total // per_box)
        if not name and total <= 0:
            continue
        items.append({
            "name": name,
            "code": code,
            "total": total,
            "expiry": expiry,
            "perBox": per_box,
            "boxCount": box_count,
            "orderCode": order_code,
        })
    return items


# OCR이 코드 글자를 자주 바꿔 읽는 문자 → 원래 글자 (예: 'K260930-A]53ERA' / 'ÅJ53ERÅ')
_OCR_CODE_FIX = str.maketrans({
    "Å": "A", "À": "A", "Á": "A", "Ä": "A",
    "]": "J", "[": "J", "}": "J", "{": "J",
    "|": "I", "!": "I",
})


def _ocr_order_code(full: str) -> str:
    """OCR 텍스트에서 거래명세서코드(K260930_AJ53ERA 형식)를 복원한다.

    '거래 명세서코드: K260930-A]53ERA' 처럼 오인식이 섞여도 접미부 토큰을 문자 치환 후
    영숫자만 남겨 조합한다. 라벨 뒤에서 못 찾으면 본문 어디든 'K{6자리} 토큰' 패턴을 찾는다.
    """
    def _clean(tok: str) -> str:
        tok = tok.translate(_OCR_CODE_FIX)
        return re.sub(r"[^A-Z0-9]", "", tok.upper())

    pats = (
        r"거래\s*명세서\s*코드\s*[:：\.]?\s*([A-Z]\d{6})[\s_\-\.]*(\S+)",
        r"(?<![A-Z0-9])([A-Z]\d{6})[\s_\-\.]+(\S+)",
    )
    for pat in pats:
        for m in re.finditer(pat, full):
            suffix = _clean(m.group(2))
            if 4 <= len(suffix) <= 10:
                return f"{m.group(1)}_{suffix}"
    return ""


def _ocr_parse_kurly(data: bytes, filename: str = "") -> dict:
    pages = _ocr_pages(data)
    full = "\n".join(" ".join(t for t, _, _ in pg) for pg in pages)

    # 거래명세서코드(구 발주코드): OCR 글자 오인식이 잦아 파일명을 우선 사용
    order_code = ""
    mf = re.search(r"([A-Z]\d{6}[_\-][A-Z0-9]{4,})", filename or "")
    if mf:
        order_code = mf.group(1).replace("-", "_")
    else:
        order_code = _ocr_order_code(full)

    supplier = ""
    ms = re.search(r"([\(\)가-힣A-Za-z0-9]+)\s*\(\s*VD\d*", full)
    if ms:
        supplier = _norm(ms.group(1))
    # 입고일: (1) '입고일 2026-09-21' (2) 입고대행 양식 — '최종입고지 입고일 … 2026-09-21 ~ 2026-09-23' 처럼 사이에 단어가 끼거나
    # 입차일('입사일'로 오인식)만 있는 경우 (3) 거래명세서코드 K{yymmdd} = 입고일 규칙 폴백
    date = ""
    for pat in (r"입고일\s*[:：]?\s*(\d{4}-\d{2}-\d{2})",
                r"입고\S{0,4}일\s*[:：]?\s*(?:\S+\s+){0,4}?(\d{4}-\d{2}-\d{2})",
                r"입[차사]\S{0,2}일\s*[:：]?\s*(\d{4}-\d{2}-\d{2})"):
        m_ = re.search(pat, full)
        if m_:
            date = m_.group(1)
            break
    if not date:
        mk = re.match(r"[A-Z](\d{2})(\d{2})(\d{2})", order_code or "")
        if mk:
            date = f"20{mk.group(1)}-{mk.group(2)}-{mk.group(3)}"
    mc = re.search(r"([가-힣]+)\s*\(\s*\d+\s*[층증]", full)

    items: list[dict] = []
    for pg in pages:
        items.extend(_ocr_parse_items(pg, order_code))

    return {
        "orderCode": order_code,
        "orderCodes": [order_code] if order_code else [],
        "supplier": supplier or "(주)시나몬랩",
        "date": date,
        "center": _norm(mc.group(1)) if mc else "",
        "items": items,
    }


def parse_kurly_statement(data: bytes, filename: str = "") -> dict:
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

    # 2026-08 신규 양식(이미지 PDF)은 텍스트 추출이 안 되므로 OCR로 폴백
    if not items:
        return _ocr_parse_kurly(data, filename)

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
            # OCR 폴백은 내부에서 asyncio.run을 쓰므로 이벤트 루프 밖(스레드)에서 실행
            d = await anyio.to_thread.run_sync(parse_kurly_statement, data, up.filename)
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

    _fix_item_names(items)
    return {
        "orderCode": order_code,
        "orderCodes": order_codes,
        "supplier": _fix_supplier(supplier),
        "items": items,
        "parse_errors": errors,
    }
