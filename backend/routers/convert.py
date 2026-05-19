from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Optional
import io, sys, os
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils_core as U
import state

router = APIRouter()


def _get_master_df() -> pd.DataFrame:
    if state.master_bytes:
        return U.load_master_from_bytes(state.master_bytes)
    if U.DEFAULT_MASTER:
        return U.load_master_from_path(U.DEFAULT_MASTER)
    raise HTTPException(400, "마스터 파일이 없습니다. /api/master/upload로 업로드해주세요.")


@router.post("/general")
async def convert_general(
    order_file: UploadFile = File(...),
    master_file: Optional[UploadFile] = File(None),
    name_col: str = Form(...),
    qty_col: str = Form(...),
    threshold: int = Form(70),
):
    order_bytes = await order_file.read()
    try:
        order_df = pd.read_excel(io.BytesIO(order_bytes))
    except Exception as e:
        raise HTTPException(400, f"출고 파일 읽기 오류: {e}")

    if master_file:
        master_bytes = await master_file.read()
        master_df = U.load_master_from_bytes(master_bytes)
    else:
        master_df = _get_master_df()

    master_lookup = U.build_master_lookup(master_df)
    norm_names = list(master_lookup.keys())

    try:
        order_df[qty_col] = pd.to_numeric(order_df[qty_col], errors="coerce").fillna(0)
        grouped = (
            order_df.groupby(name_col)[qty_col]
            .sum()
            .reset_index()
            .rename(columns={name_col: "상품명", qty_col: "수량"})
        )
    except Exception as e:
        raise HTTPException(400, f"컬럼 처리 오류: {e}")

    results = []
    unmatched = []
    for _, row in grouped.iterrows():
        raw_name = str(row["상품명"])
        qty = int(row["수량"])
        best_key, score = U.best_match(raw_name, norm_names)
        if best_key and score >= threshold:
            sku, price, matched_name = master_lookup[best_key]
            results.append({
                "original_name": raw_name,
                "quantity": qty,
                "sku": sku,
                "price": price,
                "matched_name": matched_name,
                "score": score,
            })
        else:
            best_name = master_lookup.get(best_key, ("", 0, ""))[2] if best_key else ""
            unmatched.append({
                "original_name": raw_name,
                "quantity": qty,
                "score": score,
                "best_candidate": best_name,
            })

    # Build master name list for dropdown
    master_names = [v[2] for v in master_lookup.values()]
    master_by_name = {v[2]: {"sku": v[0], "price": v[1]} for v in master_lookup.values()}

    return {
        "results": results,
        "unmatched": unmatched,
        "master_names": master_names,
        "master_by_name": master_by_name,
        "columns": list(order_df.columns),
    }


@router.post("/general/columns")
async def get_general_columns(
    order_file: UploadFile = File(...),
    sheet_name: Optional[str] = Form(None),
):
    order_bytes = await order_file.read()
    try:
        if sheet_name:
            df = pd.read_excel(io.BytesIO(order_bytes), sheet_name=sheet_name)
        else:
            df = pd.read_excel(io.BytesIO(order_bytes))
    except Exception as e:
        raise HTTPException(400, f"파일 읽기 오류: {e}")

    cols = list(df.columns)
    name_col = next((c for c in cols if "상품명" in str(c) or "품명" in str(c) or "제품명" in str(c)), cols[0] if cols else "")
    qty_col = next((c for c in cols if "수량" in str(c) or "qty" in str(c).lower() or "내품개수" in str(c)), cols[1] if len(cols) > 1 else cols[0] if cols else "")

    # Get sheet names
    xf = pd.ExcelFile(io.BytesIO(order_bytes))
    sheets = xf.sheet_names

    preview = df.head(5).fillna("").to_dict(orient="records")
    return {"columns": cols, "sheets": sheets, "name_col": name_col, "qty_col": qty_col, "preview": preview}


@router.post("/naver")
async def convert_naver(
    order_file: UploadFile = File(...),
    master_file: Optional[UploadFile] = File(None),
    threshold: int = Form(65),
):
    order_bytes = await order_file.read()

    if master_file:
        master_bytes = await master_file.read()
        master_df = U.load_master_from_bytes(master_bytes)
    else:
        master_df = _get_master_df()

    master_lookup = U.build_master_lookup(master_df)

    try:
        naver_df, name_to_sku = U.load_naver(order_bytes)
    except Exception as e:
        raise HTTPException(400, f"네이버 파일 읽기 오류: {e}. '양식' 시트와 'BoxHero' 시트가 필요합니다.")

    results = []
    unmatched = []
    for _, row in naver_df.iterrows():
        sku, method = U.resolve_naver_sku(row["SKU_원본"], row["상품명"], name_to_sku, master_lookup)
        try:
            qty = int(float(row["수량"])) if pd.notna(row["수량"]) else 0
        except Exception:
            qty = 0
        price = next((v[1] for v in master_lookup.values() if v[0] == sku), 0)
        matched_name = next((v[2] for v in master_lookup.values() if v[0] == sku), "(건너뜀)")
        entry = {
            "original_name": str(row["상품명"]),
            "quantity": qty,
            "sku": sku,
            "price": price,
            "matched_name": matched_name,
            "method": method,
        }
        if sku.startswith("UNKNOWN"):
            unmatched.append(entry)
        else:
            results.append(entry)

    master_names = [v[2] for v in master_lookup.values()]
    master_by_name = {v[2]: {"sku": v[0], "price": v[1]} for v in master_lookup.values()}

    # Preview summary
    try:
        import io as _io
        preview_df = (
            naver_df.groupby("상품명")["수량"]
            .apply(lambda x: x.apply(lambda v: int(float(v)) if pd.notna(v) else 0).sum())
            .reset_index()
            .sort_values("수량", ascending=False)
        )
        preview = preview_df.head(10).to_dict(orient="records")
    except Exception:
        preview = []

    return {
        "results": results,
        "unmatched": unmatched,
        "master_names": master_names,
        "master_by_name": master_by_name,
        "boxhero_mapping_count": len(name_to_sku),
        "preview": preview,
    }
