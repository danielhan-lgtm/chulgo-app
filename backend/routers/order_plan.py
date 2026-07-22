"""발주 캘린더 — 컬리·올리브영·쿠팡 등 여러 발주서를 취합해서
품목 × 출고일 매트릭스 대시보드로 보여주는 라우터."""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import io, sys, os, json, re, hashlib
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
import state

router = APIRouter()


# 컬럼 자동 감지용 키워드
DATE_KEYWORDS = [
    "입고예정일", "출고요청일", "출고예정일", "출고일", "납품요청일", "납품예정일", "납품일",
    "입고일", "도착일", "배송일", "출하일", "요청일", "희망일", "지정일", "발주일",
]
NAME_KEYWORDS = ["상품명", "품명", "제품명", "내품명", "물품명", "품목명"]
QTY_KEYWORDS = ["발주수량", "납품예정수량", "주문수량", "납품수량", "내품개수", "수량", "qty"]
# 확정 입고 수량 컬럼 — 발주서 안에 있는 실제 입고 확정 수량 (쿠팡 '확정수량', 올리브영 '납품수량' 등)
RECV_KEYWORDS = ["확정수량", "확정 수량", "입고확정수량", "입고수량", "검수수량", "실입고수량", "납품확인수량", "인수수량", "납품수량"]


# 번들 패턴 — "3ea", "3입", "3개입", "3팩", "3봉", "x3", "*3", "2개" 등
# 규격 토큰(30g, 100ml)과 충돌 X — 단위에 g/ml/kg/l은 포함하지 않음
# 한글 경계는 \b가 동작하지 않으므로 negative lookbehind/lookahead로 처리
_BUNDLE_UNITS = r"ea|pcs|개입|개|입|팩|봉|박스|병|set|세트"

_BUNDLE_PATTERNS = [
    # "3ea", "5개입", "3봉" 등 — 단위 키워드 동반
    re.compile(rf"(?<![A-Za-z0-9])(\d+)\s*(?:{_BUNDLE_UNITS})(?![A-Za-z0-9])", re.IGNORECASE),
    # "x3", "×2", "*5", "x 3봉" 등 (배수 표기)
    re.compile(r"(?<![A-Za-z0-9])[x×\*]\s*(\d+)(?![A-Za-z0-9])", re.IGNORECASE),
    # "(3개입)", "[5팩]"
    re.compile(rf"[\(\[](\d+)\s*(?:{_BUNDLE_UNITS})[\)\]]", re.IGNORECASE),
]


def _split_bundle(name: str) -> tuple:
    """상품명에서 번들 표기를 분리해서 (단품_상품명, 번들수량) 반환.
    번들 표기 없으면 (원본, 1).
    예) "DJ&A 머쉬룸 크리스프 30g 3ea" → ("DJ&A 머쉬룸 크리스프 30g", 3)
        "표고버섯 60g x2" → ("표고버섯 60g", 2)

    혼합 세트(서로 다른 규격이 섞인 상품, 예: "30g 4개 + 65g 2개")는
    분리하지 않는다 — 마커 수량을 곱하면 4×2=8배로 부풀려지는 오계산 방지.
    """
    if not name:
        return name, 1
    s = str(name)
    # 혼합 세트 감지: '+' 포함 또는 서로 다른 무게/용량 규격이 2개 이상
    specs = {m.lower().replace(" ", "")
             for m in re.findall(r"\d+(?:\.\d+)?\s*(?:g|ml|kg|l)(?![a-z])", s, re.IGNORECASE)}
    if "+" in s or len(specs) >= 2:
        return re.sub(r"\s+", " ", s).strip(), 1
    count = 1
    # 각 패턴을 매치 없을 때까지 반복 (한 이름 안에 "5개 2팩" 등 복수 마커 캐치)
    for pat in _BUNDLE_PATTERNS:
        while True:
            m = pat.search(s)
            if not m:
                break
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                # 숫자 파싱 실패면 무한루프 방지 위해 자르고 진행
                s = s[:m.start()] + " " + s[m.end():]
                continue
            if 2 <= n <= 50:
                count *= n
            s = s[:m.start()] + " " + s[m.end():]
    # 정리: 다중 공백, 빈 괄호
    s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s, count


def _safe_preview(df: pd.DataFrame, n: int = 5) -> list:
    """to_dict가 NaN/NaT/Timestamp를 JSON에 못 넣는 문제 방지."""
    out = []
    for _, row in df.head(n).iterrows():
        d = {}
        for col in df.columns:
            v = row[col]
            if pd.isna(v):
                d[str(col)] = ""
            elif hasattr(v, "isoformat"):
                d[str(col)] = v.isoformat()
            else:
                d[str(col)] = str(v)
        out.append(d)
    return out


def _detect_col(cols: list, keywords: list, fallback_idx: int = 0) -> str:
    for kw in keywords:
        for c in cols:
            if kw.lower() in str(c).lower():
                return c
    return cols[fallback_idx] if cols and fallback_idx < len(cols) else (cols[0] if cols else "")


def _detect_recv_col(cols: list, exclude: str = "") -> str:
    """확정 입고 수량 컬럼 자동 감지 — 없으면 빈 문자열. 발주수량 컬럼(exclude)은 제외."""
    for kw in RECV_KEYWORDS:
        for c in cols:
            if str(c) == exclude:
                continue
            if kw.lower() in str(c).lower():
                return str(c)
    return ""


def _guess_channel_from_filename(filename: str) -> str:
    name = filename.lower()
    if "컬리" in filename or "kurly" in name or "거래명세서" in filename:
        return "컬리"
    if "올리브영" in filename or "oliveyoung" in name or "납품확인서" in filename:
        return "올리브영"
    if "쿠팡" in filename or "coupang" in name or "발주서리스트" in filename:
        return "쿠팡"
    if "네이버" in filename or "naver" in name:
        return "네이버"
    if "11번가" in filename or "11st" in name:
        return "11번가"
    if "지마켓" in filename or "gmarket" in name:
        return "지마켓"
    return "기타"


def _guess_channel_from_columns(cols: list) -> Optional[str]:
    joined = "|".join(str(c) for c in cols)
    # 올리브영 특유 컬럼들
    oy_hits = sum(1 for kw in ("입고전표", "배송유형", "프리미엄여부", "LOT 번호") if kw in joined)
    if oy_hits >= 2:
        return "올리브영"
    return None


def _normalize_date(value) -> Optional[str]:
    """엑셀 셀(문자/숫자/datetime) → YYYY-MM-DD 문자열. 실패 시 None."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
    except Exception:
        pass
    # pandas Timestamp/datetime
    try:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
    except Exception:
        pass
    # 문자열에서 yyyy[-/.]mm[-/.]dd 패턴 추출
    s = str(value).strip()
    m = re.search(r"(\d{4})[\-/.년]\s*(\d{1,2})[\-/.월]\s*(\d{1,2})", s)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        return f"{y}-{mo}-{d}"
    m = re.search(r"(\d{2})[\-/.](\d{1,2})[\-/.](\d{1,2})", s)
    if m:
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        return f"20{y}-{mo}-{d}"
    return None


def _clean_pdf_text(s) -> str:
    """PDF 추출 시 단어 사이 \\x00을 공백으로, 줄바꿈 제거, 다중공백 정리."""
    if not s:
        return ""
    s = str(s).replace("\x00", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_kurly_pdf(pdf_bytes: bytes) -> bool:
    """컬리 PDF는 '발주코드'·'입고일'·'마스터코드'·'총수량'·'Kurly' 같은 라벨이 있음."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "".join((p.extract_text() or "") for p in pdf.pages)
        text = text.replace("\x00", " ")
        hits = sum(1 for kw in ("발주코드", "입고일", "마스터코드", "총수량", "Kurly", "컬리") if kw in text)
        return hits >= 2
    except Exception:
        return False


def _parse_kurly(pdf_bytes: bytes) -> tuple:
    """컬리 거래명세서 PDF → ([{name, qty, barcode}], delivery_date | None)."""
    import pdfplumber
    items = []
    delivery_date = None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        full_text = full_text.replace("\x00", " ")
        m = re.search(r"입고일\s*[:：]?\s*(\d{4})[\-/.]\s*(\d{1,2})[\-/.]\s*(\d{1,2})", full_text)
        if m:
            delivery_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

        for page in pdf.pages:
            for tbl in (page.extract_tables() or []):
                if not tbl or len(tbl) < 2:
                    continue
                header = [_clean_pdf_text(c) for c in tbl[0]]
                name_idx = next((i for i, c in enumerate(header) if "품명" in c), None)
                qty_idx = next((i for i, c in enumerate(header) if "총수량" in c), None)
                if qty_idx is None:
                    qty_idx = next((i for i, c in enumerate(header) if c.strip() == "수량"), None)
                barcode_idx = next((i for i, c in enumerate(header) if "바코드" in c), None)
                if name_idx is None or qty_idx is None:
                    continue
                for row in tbl[1:]:
                    if not row or len(row) <= max(name_idx, qty_idx):
                        continue
                    name = _clean_pdf_text(row[name_idx])
                    qty_raw = _clean_pdf_text(row[qty_idx])
                    try:
                        qty = int(float(qty_raw))
                    except (TypeError, ValueError):
                        continue
                    if not name or qty <= 0:
                        continue
                    barcode = _clean_pdf_text(row[barcode_idx]) if barcode_idx is not None and barcode_idx < len(row) else ""
                    items.append({"name": name, "qty": qty, "barcode": barcode})
    return items, delivery_date


def _is_coupang_format(df_raw: pd.DataFrame) -> bool:
    """쿠팡 발주서는 시트 상단에 '발주서 No.'·'거래처정보'·'입고예정일시' 같은 라벨이 있음."""
    sample = df_raw.head(20).fillna("").astype(str).values.flatten()
    joined = "|".join(sample)
    hits = sum(1 for kw in ("발주서 No", "거래처정보", "입고예정일", "발주정보", "상품정보") if kw in joined)
    return hits >= 2


def _find_keyword(df_raw: pd.DataFrame, keyword: str, max_row: int = 30) -> Optional[tuple]:
    rows = min(max_row, len(df_raw))
    for i in range(rows):
        for j in range(df_raw.shape[1]):
            v = df_raw.iat[i, j]
            if pd.notna(v) and keyword in str(v):
                return (i, j)
    return None


def _parse_coupang(df_raw: pd.DataFrame) -> tuple:
    """쿠팡 발주서 → ([{name, qty}], delivery_date_str | None)."""
    # 1) 입고예정일시 추출 — '입고예정일시' 라벨 셀 주변에서 날짜 패턴 셀 검색
    delivery_date = None
    pos = _find_keyword(df_raw, "입고예정일")
    if pos:
        i0, j0 = pos
        # 같은 행 우측 + 아래 2행 × ±5열 범위 스캔
        for di in range(0, 3):
            for dj in range(-5, 12):
                ni, nj = i0 + di, j0 + dj
                if (di, dj) == (0, 0):
                    continue
                if 0 <= ni < len(df_raw) and 0 <= nj < df_raw.shape[1]:
                    d = _normalize_date(df_raw.iat[ni, nj])
                    if d:
                        delivery_date = d
                        break
            if delivery_date:
                break

    # 2) 상품 표 헤더 탐색 ('상품명' + '발주수량' 둘 다 포함된 행)
    header_row = None
    for i in range(len(df_raw)):
        row = df_raw.iloc[i].fillna("").astype(str).tolist()
        joined = "|".join(row)
        if "상품명" in joined and "발주수량" in joined:
            header_row = i
            break

    items = []
    if header_row is not None:
        header = df_raw.iloc[header_row].fillna("").astype(str).tolist()
        name_idx = next((k for k, c in enumerate(header) if "상품명" in c), None)
        qty_idx = next((k for k, c in enumerate(header) if "발주수량" in c), None)
        # 확정 입고 수량 — 실제 쿠팡 발주서 헤더는 '입고수량'
        # ('업체납품가능수량'과 혼동 주의, 구양식 대비 '확정수량'도 fallback)
        recv_idx = next((k for k, c in enumerate(header)
                         if "입고수량" in c and "가능" not in c), None)
        if recv_idx is None:
            recv_idx = next((k for k, c in enumerate(header)
                             if "확정" in c and "수량" in c and "발주" not in c), None)
        # 금액 — '발주금액'/'입고금액' (매입가 기준 행 합계, 콤마 문자열 가능)
        amt_idx = next((k for k, c in enumerate(header) if "발주금액" in c), None)
        recv_amt_idx = next((k for k, c in enumerate(header) if "입고금액" in c), None)

        def _amt_at(row, idx):
            if idx is None or idx >= len(row):
                return None
            v = row.iat[idx]
            if pd.isna(v):
                return None
            try:
                return int(float(str(v).replace(",", "").strip()))
            except (TypeError, ValueError):
                return None

        if name_idx is not None and qty_idx is not None:
            for i in range(header_row + 1, len(df_raw)):
                row = df_raw.iloc[i]
                # No. 컬럼(0)이 정수면 새 품목 행
                try:
                    int(float(row.iat[0]))
                except (TypeError, ValueError):
                    continue
                name_v = row.iat[name_idx] if name_idx < len(row) else None
                qty_v = row.iat[qty_idx] if qty_idx < len(row) else None
                if pd.isna(name_v) or pd.isna(qty_v):
                    continue
                try:
                    q = int(float(qty_v))
                except (TypeError, ValueError):
                    continue
                if q <= 0:
                    continue
                recv = None
                if recv_idx is not None and recv_idx < len(row):
                    rv = row.iat[recv_idx]
                    if pd.notna(rv):
                        try:
                            recv = int(float(rv))
                        except (TypeError, ValueError):
                            recv = None
                items.append({"name": str(name_v).strip(), "qty": q, "recv": recv,
                              "amt": _amt_at(row, amt_idx), "recv_amt": _amt_at(row, recv_amt_idx)})
    return items, delivery_date


def _get_master_df() -> Optional[pd.DataFrame]:
    if state.master_bytes:
        try:
            return U.load_master_from_bytes(state.master_bytes)
        except Exception:
            pass  # 마스터가 깨져 있어도 집계는 미매칭으로 계속 (500 방지)
    if getattr(U, "DEFAULT_MASTER", ""):
        try:
            return U.load_master_from_path(U.DEFAULT_MASTER)
        except Exception:
            return None
    return None


# ─────────────────────────────────────────────────────────────────
# 수동 매핑 저장소 — backend/data/order_plan_user_mappings.json
# 키는 normalize() 통과한 원본명. 값은 {sku, master_name, note, raw}
# sku == "" 이면 "강제 미매칭" (퍼지 매칭이 다른 SKU로 잡아채는 걸 막음)
# ─────────────────────────────────────────────────────────────────

_USER_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "order_plan_user_mappings.json",
)


def _load_user_mappings() -> dict:
    try:
        with open(_USER_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_user_mappings(data: dict) -> None:
    os.makedirs(os.path.dirname(_USER_MAP_PATH), exist_ok=True)
    tmp = _USER_MAP_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _USER_MAP_PATH)


class UserMappingIn(BaseModel):
    raw_name: str
    sku: str = ""  # 빈 문자열 = 강제 미매칭(unmatch)
    master_name: str = ""
    note: str = ""


class UserMappingDelete(BaseModel):
    raw_name: str


@router.get("/user-mappings")
def list_user_mappings():
    data = _load_user_mappings()
    items = []
    for k, v in data.items():
        items.append({
            "raw_name": v.get("raw", k),
            "key": k,
            "sku": v.get("sku", ""),
            "master_name": v.get("master_name", ""),
            "note": v.get("note", ""),
        })
    items.sort(key=lambda x: x["raw_name"])
    return {"items": items, "total": len(items)}


@router.post("/user-mappings")
def upsert_user_mapping(payload: UserMappingIn):
    if not payload.raw_name.strip():
        raise HTTPException(400, "raw_name 비어있음")
    data = _load_user_mappings()
    key = U.normalize(payload.raw_name)
    data[key] = {
        "raw": payload.raw_name.strip(),
        "sku": payload.sku.strip(),
        "master_name": payload.master_name.strip(),
        "note": payload.note.strip(),
    }
    _save_user_mappings(data)
    return {"ok": True, "key": key, "total": len(data)}


@router.post("/user-mappings/delete")
def delete_user_mapping(payload: UserMappingDelete):
    if not payload.raw_name.strip():
        raise HTTPException(400, "raw_name 비어있음")
    data = _load_user_mappings()
    key = U.normalize(payload.raw_name)
    if key in data:
        del data[key]
        _save_user_mappings(data)
        return {"ok": True, "removed": True, "total": len(data)}
    return {"ok": True, "removed": False, "total": len(data)}


@router.post("/user-mappings/clear")
def clear_user_mappings():
    _save_user_mappings({})
    return {"ok": True, "total": 0}


@router.post("/columns")
async def detect_columns(order_file: UploadFile = File(...)):
    """발주서 파일 1개에서 양식 자동 인식 → 컬리 PDF/쿠팡 엑셀이면 전용 파싱 결과,
    일반 표면 컬럼·자동 감지된 컬럼 매핑 반환."""
    data = await order_file.read()
    filename = order_file.filename or ""
    channel = _guess_channel_from_filename(filename)

    # PDF (컬리 거래명세서) 자동 인식
    is_pdf = filename.lower().endswith(".pdf") or data[:4] == b"%PDF"
    if is_pdf:
        if _is_kurly_pdf(data):
            items, delivery_date = _parse_kurly(data)
            return {
                "format": "kurly",
                "columns": [],
                "date_col": delivery_date or "",
                "name_col": "(컬리 자동인식)",
                "qty_col": "(컬리 자동인식)",
                "channel": "컬리",
                "preview": [{"품명": it["name"], "총수량": it["qty"], "바코드": it.get("barcode", ""), "입고일": delivery_date or ""} for it in items[:5]],
                "rows": len(items),
                "detected_date": delivery_date,
                "item_count": len(items),
            }
        raise HTTPException(400, "지원하지 않는 PDF 양식입니다. 현재 컬리 거래명세서만 지원합니다.")

    try:
        df_raw = pd.read_excel(io.BytesIO(data), header=None)
    except Exception as e:
        raise HTTPException(400, f"파일 읽기 오류: {e}")

    # 쿠팡 양식 자동 인식
    if _is_coupang_format(df_raw):
        items, delivery_date = _parse_coupang(df_raw)
        recv_detected = any(it.get("recv") is not None for it in items)
        return {
            "format": "coupang",
            "columns": [],
            "date_col": delivery_date or "",
            "name_col": "(쿠팡 자동인식)",
            "qty_col": "(쿠팡 자동인식)",
            "recv_col": "(쿠팡 자동인식)" if recv_detected else "",
            "recv_detected": recv_detected,
            "channel": "쿠팡",
            "preview": [{"상품명": it["name"], "발주수량": it["qty"], "확정수량": ("" if it.get("recv") is None else it["recv"]), "입고예정일": delivery_date or ""} for it in items[:5]],
            "rows": len(items),
            "detected_date": delivery_date,
            "item_count": len(items),
        }

    # 일반 표 양식
    try:
        df = pd.read_excel(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(400, f"파일 읽기 오류: {e}")
    cols = [str(c) for c in df.columns]
    date_col = _detect_col(cols, DATE_KEYWORDS, fallback_idx=0)
    name_col = _detect_col(cols, NAME_KEYWORDS, fallback_idx=1 if len(cols) > 1 else 0)
    qty_col = _detect_col(cols, QTY_KEYWORDS, fallback_idx=2 if len(cols) > 2 else 0)
    recv_col = _detect_recv_col(cols, exclude=qty_col)
    if channel == "기타":
        guess = _guess_channel_from_columns(cols)
        if guess:
            channel = guess
    preview = _safe_preview(df, 5)
    return {
        "format": "generic",
        "columns": cols,
        "date_col": date_col,
        "name_col": name_col,
        "qty_col": qty_col,
        "recv_col": recv_col,
        "channel": channel,
        "preview": preview,
        "rows": int(len(df)),
    }


@router.post("/aggregate")
async def aggregate_plan(
    files: List[UploadFile] = File(...),
    mappings: str = Form(...),  # JSON: [{filename, channel, date_col, name_col, qty_col}]
    threshold: int = Form(70),
    use_master: int = Form(1),
    split_bundles: int = Form(1),
):
    """여러 발주서를 취합해서 품목 × 날짜 매트릭스로 반환.

    mappings 예:
    [
      {"filename":"kurly.xlsx","channel":"컬리","date_col":"납품일","name_col":"상품명","qty_col":"수량"},
      ...
    ]
    """
    try:
        mapping_list = json.loads(mappings)
    except Exception as e:
        raise HTTPException(400, f"mappings JSON 파싱 실패: {e}")
    mapping_by_name = {m.get("filename"): m for m in mapping_list}

    # 마스터 매핑 준비 (있으면 사용, 없으면 원본 품목명 그대로 키로 사용)
    master_lookup = {}
    sku_index: dict = {}   # SKU(=종종 바코드와 같음) → (sku, price, name)
    if use_master:
        mdf = _get_master_df()
        if mdf is not None:
            try:
                master_lookup = U.build_master_lookup(mdf)
                for sku, price, name in master_lookup.values():
                    if sku:
                        sku_index[sku.strip()] = (sku, price, name)
            except Exception:
                master_lookup = {}
    norm_names = list(master_lookup.keys())
    user_maps = _load_user_mappings()

    # 집계 컨테이너 — key = (sku, display_name)
    # value = {"by_date": {date: qty}, "by_channel": {ch: qty}, "matched": bool, "sources": [filename]}
    agg: dict = {}
    all_dates: set = set()
    all_channels: set = set()
    errors: list = []
    per_file_stats: list = []
    bundle_splits: list = []   # [{raw_name, count, qty_each, qty_total}]

    def _accumulate(name: str, qty: int, date_str: str, channel: str, fname: str, barcode: str = ""):
        original_raw = name
        # 0) 번들 분리 — 단품 이름으로 변환하고 수량을 번들수만큼 증폭
        bundle_count = 1
        match_name = name
        if split_bundles:
            cleaned, n = _split_bundle(name)
            if n > 1:
                bundle_count = n
                match_name = cleaned
                bundle_splits.append({
                    "raw_name": original_raw,
                    "cleaned": cleaned,
                    "count": n,
                    "qty_each": qty,
                    "qty_total": qty * n,
                    "source": fname,
                })
        effective_qty = qty * bundle_count

        sku = ""
        display = match_name
        matched = False
        score = 0
        source_tag = ""

        # 1) 수동 매핑이 최우선 (원본명 + 단품명 양쪽 확인)
        for lookup_name in (original_raw, match_name):
            key_norm = U.normalize(lookup_name)
            um = user_maps.get(key_norm)
            if um is not None:
                forced_sku = (um.get("sku") or "").strip()
                if forced_sku:
                    # 마스터에 있으면 마스터 이름 사용, 없으면 user-supplied master_name
                    master_match = next(
                        ((s, p, n) for (s, p, n) in master_lookup.values() if s == forced_sku),
                        None,
                    )
                    if master_match:
                        sku, _p, master_name = master_match
                        display = master_name
                    else:
                        sku = forced_sku
                        display = um.get("master_name") or forced_sku
                    matched = True
                    score = 100
                    source_tag = "user"
                else:
                    # 빈 SKU = 강제 미매칭 (퍼지가 잘못 잡는 걸 차단)
                    sku = ""
                    display = match_name
                    matched = False
                    score = 0
                    source_tag = "user-unmatch"
                break

        # 2) 바코드 == 마스터 SKU 직접 매칭 (정확)
        if not source_tag and barcode and barcode in sku_index:
            sku, _price, master_name = sku_index[barcode]
            display = master_name
            matched = True
            score = 100
            source_tag = "barcode"

        # 3) 이름 퍼지 매칭 fallback (단품 이름 기준)
        if not source_tag and norm_names:
            best, sc = U.best_match(match_name, norm_names)
            if best and sc >= threshold:
                sku, _price, master_name = master_lookup[best]
                display = master_name
                matched = True
                score = sc
                source_tag = "fuzzy"

        key = (sku, display) if sku else ("", display)
        if key not in agg:
            agg[key] = {
                "sku": sku, "name": display, "matched": matched,
                "by_date": {}, "by_channel": {}, "sources": set(),
                "raw_names": set(), "match_score": score if matched else 0,
                "match_source": source_tag,
                "had_bundle": False,
                # raw_name별 수량 분해 — 프론트에서 raw 단위 드래그 이동 시 사용
                "raw_breakdown": {},
            }
        entry = agg[key]
        entry["by_date"][date_str] = entry["by_date"].get(date_str, 0) + effective_qty
        entry["by_channel"][channel] = entry["by_channel"].get(channel, 0) + effective_qty
        entry["sources"].add(fname)
        entry["raw_names"].add(original_raw)
        if bundle_count > 1:
            entry["had_bundle"] = True
        rb = entry["raw_breakdown"].setdefault(original_raw, {
            "by_date": {}, "by_channel": {}, "total": 0,
            "had_bundle": False, "bundle_count": 1,
        })
        rb["by_date"][date_str] = rb["by_date"].get(date_str, 0) + effective_qty
        rb["by_channel"][channel] = rb["by_channel"].get(channel, 0) + effective_qty
        rb["total"] += effective_qty
        if bundle_count > 1:
            rb["had_bundle"] = True
            rb["bundle_count"] = bundle_count
        all_dates.add(date_str)

    for upload in files:
        fname = upload.filename or "unknown.xlsx"
        m = mapping_by_name.get(fname)
        if not m:
            errors.append(f"{fname}: 매핑 정보 없음")
            continue
        channel = m.get("channel") or _guess_channel_from_filename(fname)
        fmt = m.get("format") or "generic"

        data = await upload.read()
        file_rows = 0
        file_qty = 0
        file_skipped = 0
        all_channels.add(channel)

        if fmt == "coupang":
            try:
                df_raw = pd.read_excel(io.BytesIO(data), header=None)
            except Exception as e:
                errors.append(f"{fname}: 읽기 오류 {e}")
                continue
            items, delivery_date = _parse_coupang(df_raw)
            if not delivery_date:
                delivery_date = _normalize_date(m.get("date_col")) or m.get("date_col")
            if not delivery_date:
                errors.append(f"{fname}: 입고예정일 없음 — 매핑에서 날짜 지정 필요")
                continue
            for it in items:
                _accumulate(it["name"], int(it["qty"]), delivery_date, channel, fname)
                file_rows += 1
                file_qty += int(it["qty"])
        elif fmt == "kurly":
            try:
                items, delivery_date = _parse_kurly(data)
            except Exception as e:
                errors.append(f"{fname}: PDF 읽기 오류 {e}")
                continue
            if not delivery_date:
                delivery_date = _normalize_date(m.get("date_col")) or m.get("date_col")
            if not delivery_date:
                errors.append(f"{fname}: 입고일 없음 — 매핑에서 날짜 지정 필요")
                continue
            for it in items:
                _accumulate(it["name"], int(it["qty"]), delivery_date, channel, fname, barcode=it.get("barcode", ""))
                file_rows += 1
                file_qty += int(it["qty"])
        else:
            date_col = m.get("date_col")
            name_col = m.get("name_col")
            qty_col = m.get("qty_col")
            if not (date_col and name_col and qty_col):
                errors.append(f"{fname}: 컬럼 매핑 누락")
                continue
            try:
                df = pd.read_excel(io.BytesIO(data))
            except Exception as e:
                errors.append(f"{fname}: 읽기 오류 {e}")
                continue
            missing = [c for c in [date_col, name_col, qty_col] if c not in df.columns]
            if missing:
                errors.append(f"{fname}: 컬럼 없음 {missing}")
                continue

            for _, row in df.iterrows():
                raw_name = row.get(name_col)
                if pd.isna(raw_name) or str(raw_name).strip() == "":
                    continue
                raw_qty = row.get(qty_col)
                try:
                    qty = int(float(raw_qty)) if pd.notna(raw_qty) else 0
                except Exception:
                    qty = 0
                if qty <= 0:
                    continue
                date_str = _normalize_date(row.get(date_col))
                if not date_str:
                    file_skipped += 1
                    continue
                _accumulate(str(raw_name).strip(), qty, date_str, channel, fname)
                file_rows += 1
                file_qty += qty

        per_file_stats.append({
            "filename": fname,
            "channel": channel,
            "rows": file_rows,
            "qty": file_qty,
            "skipped_no_date": file_skipped,
        })

    # 정렬된 날짜 목록
    sorted_dates = sorted(all_dates)
    sorted_channels = sorted(all_channels)

    # 응답 가공
    items_out = []
    for entry in agg.values():
        total = sum(entry["by_date"].values())
        items_out.append({
            "sku": entry["sku"],
            "name": entry["name"],
            "matched": entry["matched"],
            "match_score": entry["match_score"],
            "match_source": entry.get("match_source", ""),
            "had_bundle": entry.get("had_bundle", False),
            "by_date": entry["by_date"],
            "by_channel": entry["by_channel"],
            "total": total,
            "sources": sorted(list(entry["sources"])),
            "raw_names": sorted(list(entry["raw_names"])),
            "raw_breakdown": entry.get("raw_breakdown", {}),
        })
    # 매칭 안된 항목을 아래로, 그 다음 총수량 내림차순
    items_out.sort(key=lambda x: (0 if x["matched"] else 1, -x["total"]))

    total_by_date = {d: 0 for d in sorted_dates}
    total_by_channel = {c: 0 for c in sorted_channels}
    for it in items_out:
        for d, q in it["by_date"].items():
            total_by_date[d] = total_by_date.get(d, 0) + q
        for c, q in it["by_channel"].items():
            total_by_channel[c] = total_by_channel.get(c, 0) + q

    return {
        "dates": sorted_dates,
        "channels": sorted_channels,
        "items": items_out,
        "total_by_date": total_by_date,
        "total_by_channel": total_by_channel,
        "grand_total": sum(total_by_date.values()),
        "item_count": len(items_out),
        "matched_count": sum(1 for x in items_out if x["matched"]),
        "unmatched_count": sum(1 for x in items_out if not x["matched"]),
        "per_file": per_file_stats,
        "master_used": bool(norm_names),
        "errors": errors,
        "bundle_splits": bundle_splits,
        "user_mapping_count": len(user_maps),
    }


# ─────────────────────────────────────────────────────────────────
# 누적 발주 저장소 — 파일을 넣을수록 누적/업데이트. 파일별 파싱 행을 보관하고
# 조회 시 전체를 재집계 → 매핑 변경도 일관 반영, 파일 단위 추가/삭제 가능.
# (기존 /aggregate 는 1회성 미리보기용으로 그대로 둠 — 위 로직과 동일 결과)
# ─────────────────────────────────────────────────────────────────

_PLAN_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "order_plan_store.json",
)

# 파서 버전 — 파싱 결과에 영향 주는 변경 시 올린다.
# 저장된 파일의 버전이 낮으면 중복 체크를 건너뛰고 재파싱(갱신)한다.
# v2: 확정 입고 수량(recv) 컬럼 파싱 추가
# v3: 쿠팡 발주서 확정 입고 컬럼명 수정 ('입고수량')
# v4: 쿠팡 행에 발주번호(po) 포함 — 동일 내용·다른 발주 오판 방지
# v5: 발주금액·입고금액 파싱 추가
# v6: 일반 양식 단가 기반 금액 계산 (올리브영 원단가×납품수량 등)
# v7: 원가금액을 발주금액으로 오인하던 것 수정 (원가금액 = 납품 기준 → 입고금액)
_PARSER_VER = 7


def _load_plan_store() -> dict:
    try:
        with open(_PLAN_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                return data
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        # 파일이 있는데 깨진 경우 — 덮어쓰기로 데이터가 사라지지 않게 손상본 보존
        try:
            from datetime import datetime as _dt
            os.replace(_PLAN_STORE_PATH,
                       _PLAN_STORE_PATH + ".corrupt-" + _dt.now().strftime("%Y%m%d%H%M%S"))
        except OSError:
            pass
    return {"files": {}}


def _save_plan_store(data: dict) -> None:
    """임시 파일에 쓴 뒤 교체 (원자적) — 저장 도중 프로세스가 죽어도 기존 파일 보존."""
    os.makedirs(os.path.dirname(_PLAN_STORE_PATH), exist_ok=True)
    tmp = _PLAN_STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PLAN_STORE_PATH)


def _parse_rows_from_bytes(data: bytes, fname: str, m: dict) -> tuple:
    """파일 1개(bytes) → 정규화 행 [{name, qty, date, barcode}] + 통계 + 에러.
    채널/포맷은 호출부에서 파일 메타로 보관. (파싱 로직은 /aggregate 와 동일)"""
    channel = m.get("channel") or _guess_channel_from_filename(fname)
    fmt = m.get("format") or "generic"
    rows: list = []
    skipped = 0
    if fmt == "coupang":
        try:
            df_raw = pd.read_excel(io.BytesIO(data), header=None)
        except Exception as e:
            return [], channel, fmt, 0, [f"{fname}: 읽기 오류 {e}"]
        items, delivery_date = _parse_coupang(df_raw)
        if not delivery_date:
            delivery_date = _normalize_date(m.get("date_col")) or m.get("date_col")
        if not delivery_date:
            return [], channel, fmt, 0, [f"{fname}: 입고예정일 없음 — 매핑에서 날짜 지정 필요"]
        # 발주번호 — 품목·수량·날짜가 같은 별개 발주서(물류센터별 분할 등)를
        # 중복으로 오판하지 않도록 행에 포함해 rows_hash를 구분한다.
        po_no = ""
        pos = _find_keyword(df_raw, "발주서 No")
        if pos:
            i0, j0 = pos
            for di in range(0, 2):
                for dj in range(0, 9):
                    ni, nj = i0 + di, j0 + dj
                    if ni < len(df_raw) and nj < df_raw.shape[1]:
                        v = df_raw.iat[ni, nj]
                        m2 = re.search(r"\d{6,}", str(v)) if pd.notna(v) else None
                        if m2:
                            po_no = m2.group(0)
                            break
                if po_no:
                    break
        if not po_no:
            m2 = re.search(r"(\d{6,})", fname)
            po_no = m2.group(1) if m2 else ""
        for it in items:
            rows.append({"name": str(it["name"]).strip(), "qty": int(it["qty"]), "date": delivery_date,
                         "barcode": "", "recv": it.get("recv"), "po": po_no,
                         "amt": it.get("amt"), "recv_amt": it.get("recv_amt")})
    elif fmt == "kurly":
        try:
            items, delivery_date = _parse_kurly(data)
        except Exception as e:
            return [], channel, fmt, 0, [f"{fname}: PDF 읽기 오류 {e}"]
        if not delivery_date:
            delivery_date = _normalize_date(m.get("date_col")) or m.get("date_col")
        if not delivery_date:
            return [], channel, fmt, 0, [f"{fname}: 입고일 없음 — 매핑에서 날짜 지정 필요"]
        for it in items:
            rows.append({"name": str(it["name"]).strip(), "qty": int(it["qty"]), "date": delivery_date,
                         "barcode": it.get("barcode", ""), "recv": None})
    else:
        date_col = m.get("date_col"); name_col = m.get("name_col"); qty_col = m.get("qty_col")
        recv_col = m.get("recv_col") or ""   # 확정 입고 수량 컬럼 (선택)
        if not (date_col and name_col and qty_col):
            return [], channel, fmt, 0, [f"{fname}: 컬럼 매핑 누락"]
        try:
            df = pd.read_excel(io.BytesIO(data))
        except Exception as e:
            return [], channel, fmt, 0, [f"{fname}: 읽기 오류 {e}"]
        missing = [c for c in [date_col, name_col, qty_col] if c not in df.columns]
        if missing:
            return [], channel, fmt, 0, [f"{fname}: 컬럼 없음 {missing}"]
        if recv_col and recv_col not in df.columns:
            recv_col = ""
        # 금액 컬럼 자동 감지 — 발주 기준이 명확한 컬럼만 발주금액으로 사용.
        # (올리브영 '원가금액'은 납품수량 기준 금액이라 발주금액으로 쓰면 안 됨 → 단가×수량으로 계산)
        amt_col = next((c for c in df.columns
                        if any(k in str(c) for k in ("발주금액", "주문금액"))), None)
        recv_amt_col = next((c for c in df.columns
                             if any(k in str(c) for k in ("입고금액", "확정금액", "납품금액", "원가금액"))), None)
        # 단가 컬럼 — 금액 컬럼이 없는 쪽은 단가 × 수량으로 계산 (올리브영 '원단가' 등)
        price_col = next((c for c in df.columns if "단가" in str(c) and "금액" not in str(c)), None)

        def _amt_of(row, col):
            if col is None:
                return None
            v = row.get(col)
            if pd.isna(v):
                return None
            try:
                return int(float(str(v).replace(",", "").strip()))
            except (TypeError, ValueError):
                return None

        def _price_of(row):
            if price_col is None:
                return None
            v = row.get(price_col)
            if pd.isna(v):
                return None
            try:
                return float(str(v).replace(",", "").strip())
            except (TypeError, ValueError):
                return None

        for _, row in df.iterrows():
            raw_name = row.get(name_col)
            if pd.isna(raw_name) or str(raw_name).strip() == "":
                continue
            raw_qty = row.get(qty_col)
            try:
                qty = int(float(raw_qty)) if pd.notna(raw_qty) else 0
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            date_str = _normalize_date(row.get(date_col))
            if not date_str:
                skipped += 1
                continue
            recv = None
            if recv_col:
                rv = row.get(recv_col)
                if pd.notna(rv):
                    try:
                        recv = int(float(rv))
                    except Exception:
                        recv = None
            amt = _amt_of(row, amt_col)
            recv_amt = _amt_of(row, recv_amt_col)
            price = _price_of(row)
            if amt is None and price is not None:
                amt = int(round(price * qty))
            if recv_amt is None and price is not None and recv is not None:
                recv_amt = int(round(price * recv))
            rows.append({"name": str(raw_name).strip(), "qty": qty, "date": date_str,
                         "barcode": "", "recv": recv, "amt": amt, "recv_amt": recv_amt})
    return rows, channel, fmt, skipped, []


def _aggregate_rows(all_rows: list, threshold: int = 70, use_master: int = 1, split_bundles: int = 1) -> dict:
    """정규화 행들[{name,qty,date,channel,fname,barcode}] → 품목×날짜 매트릭스 결과.
    (집계/매칭 로직은 /aggregate 와 동일 — 누적 저장 경로에서 재사용)"""
    master_lookup = {}
    sku_index: dict = {}
    if use_master:
        mdf = _get_master_df()
        if mdf is not None:
            try:
                master_lookup = U.build_master_lookup(mdf)
                for sku, price, name in master_lookup.values():
                    if sku:
                        sku_index[sku.strip()] = (sku, price, name)
            except Exception:
                master_lookup = {}
    norm_names = list(master_lookup.keys())
    user_maps = _load_user_mappings()

    agg: dict = {}
    all_dates: set = set()
    all_channels: set = set()
    bundle_splits: list = []

    def _accumulate(name, qty, date_str, channel, fname, barcode="", recv=None, amt=None, recv_amt=None):
        original_raw = name
        bundle_count = 1
        match_name = name
        if split_bundles:
            cleaned, n = _split_bundle(name)
            if n > 1:
                bundle_count = n
                match_name = cleaned
                bundle_splits.append({"raw_name": original_raw, "cleaned": cleaned, "count": n,
                                      "qty_each": qty, "qty_total": qty * n, "source": fname})
        effective_qty = qty * bundle_count
        # 확정 입고 수량 — 발주서 파일 안의 확정수량 컬럼. 번들 분리 시 동일 배율 적용.
        effective_recv = None if recv is None else int(recv) * bundle_count
        sku = ""; display = match_name; matched = False; score = 0; source_tag = ""
        for lookup_name in (original_raw, match_name):
            key_norm = U.normalize(lookup_name)
            um = user_maps.get(key_norm)
            if um is not None:
                forced_sku = (um.get("sku") or "").strip()
                if forced_sku:
                    master_match = next(((s, p, n) for (s, p, n) in master_lookup.values() if s == forced_sku), None)
                    if master_match:
                        sku, _p, master_name = master_match; display = master_name
                    else:
                        sku = forced_sku; display = um.get("master_name") or forced_sku
                    matched = True; score = 100; source_tag = "user"
                else:
                    sku = ""; display = match_name; matched = False; score = 0; source_tag = "user-unmatch"
                break
        if not source_tag and barcode and barcode in sku_index:
            sku, _price, master_name = sku_index[barcode]
            display = master_name; matched = True; score = 100; source_tag = "barcode"
        if not source_tag and norm_names:
            best, sc = U.best_match(match_name, norm_names)
            if best and sc >= threshold:
                sku, _price, master_name = master_lookup[best]
                display = master_name; matched = True; score = sc; source_tag = "fuzzy"
        key = (sku, display) if sku else ("", display)
        if key not in agg:
            agg[key] = {"sku": sku, "name": display, "matched": matched, "by_date": {}, "by_channel": {},
                        "sources": set(), "raw_names": set(), "match_score": score if matched else 0,
                        "match_source": source_tag, "had_bundle": False, "raw_breakdown": {},
                        "recv_by_date": {}, "recv_total": 0, "has_recv": False,
                        "amt_by_date": {}, "amt_total": 0,
                        "recv_amt_by_date": {}, "recv_amt_total": 0, "has_amt": False}
        entry = agg[key]
        entry["by_date"][date_str] = entry["by_date"].get(date_str, 0) + effective_qty
        entry["by_channel"][channel] = entry["by_channel"].get(channel, 0) + effective_qty
        entry["sources"].add(fname)
        entry["raw_names"].add(original_raw)
        if effective_recv is not None:
            entry["recv_by_date"][date_str] = entry["recv_by_date"].get(date_str, 0) + effective_recv
            entry["recv_total"] += effective_recv
            entry["has_recv"] = True
        # 금액은 행 합계 그대로 (번들 분리해도 금액은 불변)
        if amt is not None:
            entry["amt_by_date"][date_str] = entry["amt_by_date"].get(date_str, 0) + int(amt)
            entry["amt_total"] += int(amt)
            entry["has_amt"] = True
        if recv_amt is not None:
            entry["recv_amt_by_date"][date_str] = entry["recv_amt_by_date"].get(date_str, 0) + int(recv_amt)
            entry["recv_amt_total"] += int(recv_amt)
        if bundle_count > 1:
            entry["had_bundle"] = True
        rb = entry["raw_breakdown"].setdefault(original_raw, {"by_date": {}, "by_channel": {}, "total": 0,
                                                              "had_bundle": False, "bundle_count": 1,
                                                              "recv_by_date": {}, "has_recv": False,
                                                              "amt_by_date": {}, "recv_amt_by_date": {}})
        rb["by_date"][date_str] = rb["by_date"].get(date_str, 0) + effective_qty
        rb["by_channel"][channel] = rb["by_channel"].get(channel, 0) + effective_qty
        rb["total"] += effective_qty
        if effective_recv is not None:
            rb["recv_by_date"][date_str] = rb["recv_by_date"].get(date_str, 0) + effective_recv
            rb["has_recv"] = True
        if amt is not None:
            rb["amt_by_date"][date_str] = rb["amt_by_date"].get(date_str, 0) + int(amt)
        if recv_amt is not None:
            rb["recv_amt_by_date"][date_str] = rb["recv_amt_by_date"].get(date_str, 0) + int(recv_amt)
        if bundle_count > 1:
            rb["had_bundle"] = True; rb["bundle_count"] = bundle_count
        all_dates.add(date_str)

    for r in all_rows:
        ch = r.get("channel") or "채널미상"
        all_channels.add(ch)
        _accumulate(str(r.get("name") or ""), int(r.get("qty") or 0), r.get("date") or "",
                    ch, r.get("fname", ""), r.get("barcode", ""), recv=r.get("recv"),
                    amt=r.get("amt"), recv_amt=r.get("recv_amt"))

    sorted_dates = sorted(d for d in all_dates if d)
    sorted_channels = sorted(all_channels)
    items_out = []
    for entry in agg.values():
        total = sum(entry["by_date"].values())
        items_out.append({
            "sku": entry["sku"], "name": entry["name"], "matched": entry["matched"],
            "match_score": entry["match_score"], "match_source": entry.get("match_source", ""),
            "had_bundle": entry.get("had_bundle", False),
            "by_date": entry["by_date"], "by_channel": entry["by_channel"], "total": total,
            "sources": sorted(list(entry["sources"])), "raw_names": sorted(list(entry["raw_names"])),
            "raw_breakdown": entry.get("raw_breakdown", {}),
            "recv_by_date": entry.get("recv_by_date", {}),
            "recv_total": entry.get("recv_total", 0),
            "has_recv": entry.get("has_recv", False),
            "amt_by_date": entry.get("amt_by_date", {}),
            "amt_total": entry.get("amt_total", 0),
            "recv_amt_by_date": entry.get("recv_amt_by_date", {}),
            "recv_amt_total": entry.get("recv_amt_total", 0),
            "has_amt": entry.get("has_amt", False),
        })
    items_out.sort(key=lambda x: (0 if x["matched"] else 1, -x["total"]))
    total_by_date = {d: 0 for d in sorted_dates}
    total_by_channel = {c: 0 for c in sorted_channels}
    for it in items_out:
        for d, q in it["by_date"].items():
            total_by_date[d] = total_by_date.get(d, 0) + q
        for c, q in it["by_channel"].items():
            total_by_channel[c] = total_by_channel.get(c, 0) + q
    return {
        "dates": sorted_dates, "channels": sorted_channels, "items": items_out,
        "total_by_date": total_by_date, "total_by_channel": total_by_channel,
        "grand_total": sum(total_by_date.values()), "item_count": len(items_out),
        "matched_count": sum(1 for x in items_out if x["matched"]),
        "unmatched_count": sum(1 for x in items_out if not x["matched"]),
        "master_used": bool(norm_names), "bundle_splits": bundle_splits,
        "user_mapping_count": len(user_maps),
    }


def _store_rows() -> list:
    """저장된 모든 파일의 행을 (channel/fname 포함) 평탄화."""
    store = _load_plan_store()
    out = []
    for fname, f in store.get("files", {}).items():
        ch = f.get("channel") or "채널미상"
        for r in f.get("rows", []):
            out.append({"name": r.get("name"), "qty": r.get("qty"), "date": r.get("date"),
                        "barcode": r.get("barcode", ""), "recv": r.get("recv"),
                        "amt": r.get("amt"), "recv_amt": r.get("recv_amt"),
                        "channel": ch, "fname": fname})
    return out


def _build_plan_result(threshold=70, use_master=1, split_bundles=1) -> dict:
    """저장된 전체 누적 발주를 재집계한 결과 + 파일목록."""
    store = _load_plan_store()
    result = _aggregate_rows(_store_rows(), threshold, use_master, split_bundles)
    result["per_file"] = [
        {"filename": fn, "channel": f.get("channel", ""), "rows": f.get("rows_count", len(f.get("rows", []))),
         "qty": f.get("qty", 0), "skipped_no_date": f.get("skipped", 0), "ingested_at": f.get("ingested_at", ""),
         "format": f.get("format", "generic")}
        for fn, f in store.get("files", {}).items()
    ]
    result["per_file"].sort(key=lambda x: x.get("ingested_at", ""))
    result["errors"] = []
    result["file_count"] = len(store.get("files", {}))
    return result


@router.post("/ingest")
async def ingest_plan(
    files: List[UploadFile] = File(...),
    mappings: str = Form(...),
    threshold: int = Form(70),
    use_master: int = Form(1),
    split_bundles: int = Form(1),
):
    """발주서를 누적 저장소에 추가/갱신하고 전체 누적 결과를 반환.

    중복 체크: 이미 누적된 파일과 내용(바이트 해시) 또는 파싱 결과(행 해시)가 같으면
    건너뛰고 duplicates에 보고 — 같은 발주서를 다시 넣어도 이중 집계되지 않음.
    같은 파일명 + 다른 내용은 덮어쓰기(재업로드=갱신).
    """
    from datetime import datetime as _dt
    try:
        mapping_list = json.loads(mappings)
    except Exception as e:
        raise HTTPException(400, f"mappings JSON 파싱 실패: {e}")
    mapping_by_name = {m.get("filename"): m for m in mapping_list}

    store = _load_plan_store()
    errors: list = []
    added: list = []
    duplicates: list = []
    replaced: list = []
    now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    for upload in files:
        fname = upload.filename or "unknown.xlsx"
        m = mapping_by_name.get(fname)
        if not m:
            errors.append(f"{fname}: 매핑 정보 없음")
            continue
        data = await upload.read()

        # 1차 중복 체크 — 파일 바이트가 동일하면 파일명이 달라도 같은 발주서
        # (구버전 파서로 저장된 항목은 중복이어도 재파싱해서 갱신)
        content_hash = hashlib.sha256(data).hexdigest()
        dup_of = next((efn for efn, ef in store["files"].items()
                       if ef.get("content_hash") == content_hash
                       and ef.get("parser_ver", 1) >= _PARSER_VER), None)
        if dup_of is not None:
            duplicates.append({"filename": fname, "existing": dup_of, "reason": "동일 파일 내용"})
            continue

        rows, channel, fmt, skipped, errs = _parse_rows_from_bytes(data, fname, m)
        errors.extend(errs)
        if not rows:
            continue

        # 2차 중복 체크 — 바이트는 달라도(재다운로드 등) 파싱된 발주 내용이 동일하면 중복
        rows_hash = hashlib.sha256(json.dumps(
            sorted(rows, key=lambda r: (r["date"], r["name"], r["qty"])),
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        dup_of = next((efn for efn, ef in store["files"].items()
                       if ef.get("rows_hash") == rows_hash
                       and ef.get("parser_ver", 1) >= _PARSER_VER), None)
        if dup_of is not None:
            duplicates.append({"filename": fname, "existing": dup_of, "reason": "동일 발주 내용"})
            continue

        # 같은 발주번호(PO) 파일 갱신 — 재다운로드로 파일명이 'xxx (1).xlsx'처럼 바뀌어도
        # 발주번호 집합이 같으면 같은 발주서 → 기존 항목을 대체 (이중 집계 방지)
        po_set = {r.get("po") for r in rows if r.get("po")}
        if po_set:
            old_fn = next((efn for efn, ef in store["files"].items()
                           if efn != fname
                           and po_set == {r.get("po") for r in ef.get("rows", []) if r.get("po")}),
                          None)
            if old_fn is not None:
                del store["files"][old_fn]
                replaced.append({"filename": fname, "replaced": old_fn})

        store["files"][fname] = {
            "channel": channel, "format": fmt, "rows": rows, "rows_count": len(rows),
            "qty": sum(int(r["qty"]) for r in rows), "skipped": skipped, "ingested_at": now,
            "content_hash": content_hash, "rows_hash": rows_hash, "parser_ver": _PARSER_VER,
        }
        added.append(fname)
    _save_plan_store(store)
    result = _build_plan_result(threshold, use_master, split_bundles)
    result["errors"] = errors
    result["added"] = added
    result["duplicates"] = duplicates
    result["replaced"] = replaced
    return result


@router.get("/plan")
def get_plan(threshold: int = 70, use_master: int = 1, split_bundles: int = 1):
    """현재 누적된 발주 전체를 재집계해 반환 (페이지 진입 시 로드)."""
    return _build_plan_result(threshold, use_master, split_bundles)


@router.get("/plan/files")
def list_plan_files():
    """누적 저장된 파일 목록 (삭제/관리용)."""
    store = _load_plan_store()
    files = [
        {"filename": fn, "channel": f.get("channel", ""), "format": f.get("format", "generic"),
         "rows": f.get("rows_count", len(f.get("rows", []))), "qty": f.get("qty", 0),
         "ingested_at": f.get("ingested_at", "")}
        for fn, f in store.get("files", {}).items()
    ]
    files.sort(key=lambda x: x.get("ingested_at", ""))
    return {"files": files, "total": len(files)}


class PlanRemoveIn(BaseModel):
    filename: str


@router.post("/plan/remove")
def remove_plan_file(payload: PlanRemoveIn, threshold: int = 70, use_master: int = 1, split_bundles: int = 1):
    """누적 저장소에서 파일 1개 제거 후 갱신된 전체 결과 반환."""
    store = _load_plan_store()
    if payload.filename in store.get("files", {}):
        del store["files"][payload.filename]
        _save_plan_store(store)
    return _build_plan_result(threshold, use_master, split_bundles)


@router.post("/plan/clear")
def clear_plan():
    """누적 발주 전체 초기화."""
    _save_plan_store({"files": {}})
    return {"ok": True}


class PlanClearDateIn(BaseModel):
    date: str  # YYYY-MM-DD


@router.post("/plan/clear-date")
def clear_plan_date(payload: PlanClearDateIn, threshold: int = 70, use_master: int = 1, split_bundles: int = 1):
    """특정 출고일(YYYY-MM-DD)에 해당하는 발주 행을 모든 파일에서 제거 후 갱신 결과 반환.
    파일의 모든 행이 사라지면 그 파일도 저장소에서 제거한다."""
    target = (payload.date or "").strip()
    if not target:
        raise HTTPException(400, "date 필요 (YYYY-MM-DD)")
    store = _load_plan_store()
    removed_qty = 0
    removed_rows = 0
    emptied_files: list = []
    for fname, f in list(store.get("files", {}).items()):
        rows = f.get("rows", [])
        kept = [r for r in rows if (r.get("date") or "") != target]
        gone = len(rows) - len(kept)
        if gone <= 0:
            continue
        removed_rows += gone
        removed_qty += sum(int(r.get("qty") or 0) for r in rows if (r.get("date") or "") == target)
        if kept:
            f["rows"] = kept
            f["rows_count"] = len(kept)
            f["qty"] = sum(int(r.get("qty") or 0) for r in kept)
        else:
            del store["files"][fname]
            emptied_files.append(fname)
    _save_plan_store(store)
    result = _build_plan_result(threshold, use_master, split_bundles)
    result["cleared_date"] = target
    result["removed_rows"] = removed_rows
    result["removed_qty"] = removed_qty
    result["emptied_files"] = emptied_files
    return result


# ─────────────────────────────────────────────────────────────────
# 쿠팡 공급사 OpenAPI 자동 동기화 — 발주서를 직접 조회해 누적 저장소에 적재
# ─────────────────────────────────────────────────────────────────

@router.get("/coupang/test")
def coupang_test(from_date: str, to_date: str):
    """쿠팡 발주서 조회 연결 테스트 — 파싱된 행 수 + 원본 응답 일부 반환(파서 검증용)."""
    import coupang_api as cp
    cfg = U.load_config()
    try:
        rows, raw = cp.fetch_purchase_orders(cfg, from_date, to_date)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "row_count": len(rows), "sample_rows": rows[:10], "raw_sample": raw}


class CoupangSyncIn(BaseModel):
    from_date: str
    to_date: str


@router.post("/coupang/sync")
def coupang_sync(payload: CoupangSyncIn, threshold: int = 70, use_master: int = 1, split_bundles: int = 1):
    """쿠팡 발주서를 기간 조회해 누적 저장소에 적재(파일 업로드와 동일 경로). 갱신 결과 반환.
    같은 기간 재동기화 시 해당 가상 파일을 덮어쓴다."""
    from datetime import datetime as _dt
    import coupang_api as cp
    cfg = U.load_config()
    try:
        rows, _raw = cp.fetch_purchase_orders(cfg, payload.from_date, payload.to_date)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not rows:
        return {**_build_plan_result(threshold, use_master, split_bundles), "added": [], "synced_rows": 0}
    # 가상 파일 1개로 저장 (기간 키) → 파일 관리/삭제와 동일하게 다룸
    vfname = f"[쿠팡API] {payload.from_date}~{payload.to_date}"
    store = _load_plan_store()
    store["files"][vfname] = {
        "channel": "쿠팡", "format": "coupang-api",
        "rows": [{"name": r["name"], "qty": int(r["qty"]), "date": r["date"], "barcode": r.get("barcode", "")} for r in rows],
        "rows_count": len(rows), "qty": sum(int(r["qty"]) for r in rows),
        "skipped": 0, "ingested_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_plan_store(store)
    result = _build_plan_result(threshold, use_master, split_bundles)
    result["added"] = [vfname]
    result["synced_rows"] = len(rows)
    return result


@router.get("/dashboard")
def plan_dashboard(threshold: int = 70, use_master: int = 1, split_bundles: int = 1):
    """누적 발주 통계 — 일별 / 월별 / 거래처별 / 거래처×월 / 상위 품목."""
    res = _build_plan_result(threshold, use_master, split_bundles)
    items = res["items"]
    daily = res["total_by_date"]                  # {YYYY-MM-DD: qty}
    by_channel = res["total_by_channel"]          # {channel: qty}
    # 월별
    monthly: dict = {}
    for d, q in daily.items():
        mo = d[:7]  # YYYY-MM
        monthly[mo] = monthly.get(mo, 0) + q
    # 채널×월은 저장된 원본 행에서 직접 집계 (정확)
    cm: dict = {}
    for r in _store_rows():
        mo = (r.get("date") or "")[:7]
        ch = r.get("channel") or "채널미상"
        if not mo:
            continue
        cm.setdefault(ch, {})
        cm[ch][mo] = cm[ch].get(mo, 0) + int(r.get("qty") or 0)
    # 상위 품목 (총수량)
    top_items = [{"sku": it["sku"], "name": it["name"], "total": it["total"], "matched": it["matched"]}
                 for it in sorted(items, key=lambda x: -x["total"])[:20]]
    months = sorted(monthly.keys())
    channels = sorted(by_channel.keys())
    return {
        "daily": [{"date": d, "qty": daily[d]} for d in sorted(daily.keys())],
        "monthly": [{"month": mo, "qty": monthly[mo]} for mo in months],
        "by_channel": [{"channel": c, "qty": by_channel[c]} for c in sorted(by_channel, key=lambda c: -by_channel[c])],
        "channel_month": {"channels": channels, "months": months,
                          "matrix": {c: {mo: cm.get(c, {}).get(mo, 0) for mo in months} for c in channels}},
        "top_items": top_items,
        "grand_total": res["grand_total"],
        "item_count": res["item_count"],
        "file_count": res.get("file_count", 0),
        "date_range": {"from": (min(daily) if daily else ""), "to": (max(daily) if daily else "")},
    }


# ─────────────────────────────────────────────────────────────────
# 발주 대비 확정 입고 비교 — 발주서 파일 안의 확정수량 컬럼 기준
# (쿠팡 '확정수량' 자동 인식, 일반 양식은 매핑에서 확정수량 컬럼 지정)
# ─────────────────────────────────────────────────────────────────

@router.get("/receiving-compare")
def receiving_compare(
    threshold: int = 70,
    use_master: int = 1,
    split_bundles: int = 1,
    from_date: str = "",
    to_date: str = "",
    channel: str = "",
):
    """누적 발주량 vs 발주서 안의 확정 입고 수량 비교.

    입고 수량은 업로드한 발주서 파일에서 파싱한 확정수량(recv)이다.
    확정수량 정보가 전혀 없는 품목(컬럼 미지정·컬리 PDF 등)은 status='nodata'.
    기간 미지정 시 발주 데이터의 전체 날짜 범위.
    channel 지정 시 요약·일별·품목별을 해당 거래처만으로 집계.
    by_channel에는 항상 전체 거래처별 발주/입고 분해를 담는다.
    """
    all_rows = _store_rows()
    sel_rows = ([r for r in all_rows if (r.get("channel") or "채널미상") == channel]
                if channel else all_rows)
    plan = _aggregate_rows(sel_rows, threshold, use_master, split_bundles)
    # 기간은 거래처 필터와 무관하게 전체 데이터 기준 (거래처별 비교 시 동일 기간 유지)
    all_dates = sorted({r.get("date") for r in all_rows if r.get("date")})
    rng_from = from_date.strip() or (all_dates[0] if all_dates else "")
    rng_to = to_date.strip() or (all_dates[-1] if all_dates else "")

    def _in_range(d: str) -> bool:
        if not d:
            return False
        if rng_from and d < rng_from:
            return False
        if rng_to and d > rng_to:
            return False
        return True

    items_out = []
    daily_ordered: dict = {}
    daily_ordered_wd: dict = {}   # 확정수량 정보 있는 품목만의 발주량 (일별 입고율 분모)
    daily_received: dict = {}
    daily_ordered_amt: dict = {}
    daily_received_amt: dict = {}
    for it in plan["items"]:
        ordered_bd = {d: q for d, q in it["by_date"].items() if q > 0 and _in_range(d)}
        recv_bd = {d: q for d, q in (it.get("recv_by_date") or {}).items() if _in_range(d)}
        ordered = sum(ordered_bd.values())
        received = sum(recv_bd.values())
        if ordered <= 0 and received <= 0:
            continue
        has_recv = bool(it.get("has_recv"))
        for d, q in ordered_bd.items():
            daily_ordered[d] = daily_ordered.get(d, 0) + q
            if has_recv:
                daily_ordered_wd[d] = daily_ordered_wd.get(d, 0) + q
        for d, q in recv_bd.items():
            daily_received[d] = daily_received.get(d, 0) + q
        # 금액 (파싱된 경우만)
        ordered_amt_bd = {d: a for d, a in (it.get("amt_by_date") or {}).items() if _in_range(d)}
        received_amt_bd = {d: a for d, a in (it.get("recv_amt_by_date") or {}).items() if _in_range(d)}
        ordered_amt = sum(ordered_amt_bd.values())
        received_amt = sum(received_amt_bd.values())
        for d, a in ordered_amt_bd.items():
            daily_ordered_amt[d] = daily_ordered_amt.get(d, 0) + a
        for d, a in received_amt_bd.items():
            daily_received_amt[d] = daily_received_amt.get(d, 0) + a
        if not has_recv:
            status = "nodata"
        elif received <= 0:
            status = "none"
        elif received < ordered:
            status = "partial"
        elif received == ordered:
            status = "full"
        else:
            status = "over"
        # raw 품명 단위 분해 — 프론트에서 매트릭스 세부이동(rawMoves)을 동일하게 적용하기 위함
        rb_out = {}
        for raw, b in (it.get("raw_breakdown") or {}).items():
            o_bd = {d: q for d, q in (b.get("by_date") or {}).items() if q > 0 and _in_range(d)}
            r_bd = {d: q for d, q in (b.get("recv_by_date") or {}).items() if _in_range(d)}
            oa_bd = {d: a for d, a in (b.get("amt_by_date") or {}).items() if _in_range(d)}
            ra_bd = {d: a for d, a in (b.get("recv_amt_by_date") or {}).items() if _in_range(d)}
            if not (o_bd or r_bd or oa_bd or ra_bd):
                continue
            rb_out[raw] = {"ordered_by_date": o_bd, "received_by_date": r_bd,
                           "ordered_amt_by_date": oa_bd, "received_amt_by_date": ra_bd,
                           "has_recv": bool(b.get("has_recv"))}
        items_out.append({
            "sku": it["sku"], "name": it["name"],
            "ordered": ordered, "received": received,
            "diff": received - ordered,
            "rate": round(received / ordered * 100, 1) if ordered else 0,
            "status": status,
            "ordered_amt": ordered_amt,
            "received_amt": received_amt,
            "ordered_by_date": ordered_bd,
            "received_by_date": recv_bd,
            "ordered_amt_by_date": ordered_amt_bd,
            "received_amt_by_date": received_amt_bd,
            "raw_breakdown": rb_out,
        })
    # 확정정보 없는 품목은 아래로, 나머지는 부족분 큰 순
    items_out.sort(key=lambda x: (1 if x["status"] == "nodata" else 0, x["diff"], -x["ordered"]))

    all_days = sorted(set(daily_ordered) | set(daily_received))
    daily = [{"date": d, "ordered": daily_ordered.get(d, 0),
              "ordered_with_data": daily_ordered_wd.get(d, 0),
              "received": daily_received.get(d, 0),
              "ordered_amt": daily_ordered_amt.get(d, 0),
              "received_amt": daily_received_amt.get(d, 0)}
             for d in all_days]

    # ── 거래처별 발주 vs 입고 분해 (필터와 무관하게 전체 거래처, 동일 기간) ──
    channels = sorted({r.get("channel") or "채널미상" for r in all_rows})
    by_channel = []
    for ch in channels:
        p = _aggregate_rows([r for r in all_rows if (r.get("channel") or "채널미상") == ch],
                            threshold, use_master, split_bundles)
        o_tot = r_tot = o_wd = oa = ra = 0
        for it2 in p["items"]:
            o = sum(q for d, q in it2["by_date"].items() if _in_range(d))
            rcv = sum(q for d, q in (it2.get("recv_by_date") or {}).items() if _in_range(d))
            if o <= 0 and rcv <= 0:
                continue
            o_tot += o
            r_tot += rcv
            if it2.get("has_recv"):
                o_wd += o
            oa += sum(a for d, a in (it2.get("amt_by_date") or {}).items() if _in_range(d))
            ra += sum(a for d, a in (it2.get("recv_amt_by_date") or {}).items() if _in_range(d))
        by_channel.append({"channel": ch, "ordered": o_tot, "received": r_tot,
                           "rate": round(r_tot / o_wd * 100, 1) if o_wd else 0,
                           "ordered_amt": oa, "received_amt": ra})
    by_channel.sort(key=lambda x: -x["ordered"])

    with_data = [x for x in items_out if x["status"] != "nodata"]
    ordered_with_data = sum(x["ordered"] for x in with_data)
    received_total = sum(x["received"] for x in with_data)
    return {
        "range": {"from": rng_from, "to": rng_to},
        "channel": channel,
        "channels": channels,
        "by_channel": by_channel,
        "summary": {
            "ordered_total": sum(x["ordered"] for x in items_out),
            "ordered_with_data": ordered_with_data,
            "received_total": received_total,
            # 입고율 = 확정수량 정보가 있는 품목의 발주량 대비
            "rate": round(received_total / ordered_with_data * 100, 1) if ordered_with_data else 0,
            "item_count": len(items_out),
            "full_count": sum(1 for x in with_data if x["status"] in ("full", "over")),
            "partial_count": sum(1 for x in with_data if x["status"] == "partial"),
            "none_count": sum(1 for x in with_data if x["status"] == "none"),
            "over_count": sum(1 for x in with_data if x["status"] == "over"),
            "nodata_count": len(items_out) - len(with_data),
            "ordered_amt_total": sum(x["ordered_amt"] for x in items_out),
            "received_amt_total": sum(x["received_amt"] for x in items_out),
        },
        "items": items_out,
        "daily": daily,
    }
