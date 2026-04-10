import streamlit as st
import pandas as pd
import io
from utils import *

has_g = bool(st.session_state.get("results") or st.session_state.get("unmatched"))
has_staged_g = bool(st.session_state.get("general_staged_df") is not None)
st.markdown(f"""
<div class="flow-bar">
    <div class="flow-step">
        <div class="flow-num">1</div>
        <div class="flow-label">📁 출고 파일</div>
    </div>
    <div class="flow-arrow">›</div>
    <div class="flow-step">
        <div class="flow-num">2</div>
        <div class="flow-label">⚙️ 매칭 옵션</div>
    </div>
    <div class="flow-arrow">›</div>
    <div class="flow-step">
        <div class="flow-num {'done' if has_g else ''}">3</div>
        <div class="flow-label">🔄 변환</div>
    </div>
    <div class="flow-arrow">›</div>
    <div class="flow-step">
        <div class="flow-num {'done' if has_staged_g else 'pending'}">4</div>
        <div class="flow-label">📋 스테이징</div>
    </div>
    <div class="flow-arrow">›</div>
    <div class="flow-step">
        <div class="flow-num {'done' if has_staged_g else 'pending'}">5</div>
        <div class="flow-label">⬇️ 전송</div>
    </div>
</div>
""", unsafe_allow_html=True)

_auto_run_g = st.session_state.pop("auto_run_general", False)
_has_results = bool(st.session_state.get("results") or st.session_state.get("unmatched"))

# ── 변환 결과 있으면 매핑 테이블 최우선 표시 ──────────────────
if _has_results:
    results_g    = st.session_state.get("results", [])
    unmatched_g  = st.session_state.get("unmatched", [])
    master_lookup_g = st.session_state.get("master_lookup", {})
    all_master_names_g = ["(건너뜀)"] + [v[2] for v in master_lookup_g.values()]

    _hh1, _hh2 = st.columns([4, 1])
    with _hh1:
        ok_cnt = len(results_g); fail_cnt = len(unmatched_g)
        st.markdown(f"### 🔧 매핑 확인 &nbsp;<span style='font-size:0.85rem;color:#6b7280;'>✅ {ok_cnt}건 성공 {'/ ❌ '+str(fail_cnt)+'건 실패' if fail_cnt else ''}</span>", unsafe_allow_html=True)
        st.caption("마스터 매핑 열 클릭 → 드롭다운에서 올바른 항목으로 변경하세요")
    with _hh2:
        if st.button("🔄 다시 변환", use_container_width=True, key="reset_conv"):
            for k in ["results", "unmatched", "master_lookup", "master_name_list", "general_staged_df"]:
                st.session_state.pop(k, None)
            st.rerun()

    map_rows_g = []
    for r in results_g:
        map_rows_g.append({"원본 상품명": r["_출고상품명"], "수량": r["수량"],
                            "마스터 매핑": r["_마스터매칭명"], "유사도": f'{r["_유사도(%)"]}%'})
    for u in unmatched_g:
        map_rows_g.append({"원본 상품명": u["출고상품명"], "수량": u["수량"],
                            "마스터 매핑": u["가장유사한마스터"] or "(건너뜀)", "유사도": f'{u["유사도(%)"]}% ❌'})
    map_df_g = pd.DataFrame(map_rows_g)

    edited_map_g = st.data_editor(
        map_df_g,
        use_container_width=True,
        hide_index=True,
        height=min(60 + len(map_df_g) * 38, 500),
        column_config={
            "원본 상품명": st.column_config.TextColumn("원본 상품명", disabled=True, width="medium"),
            "수량":        st.column_config.NumberColumn("수량", disabled=True, width="small"),
            "마스터 매핑": st.column_config.SelectboxColumn("마스터 매핑 (클릭해서 변경)", options=all_master_names_g, width="large"),
            "유사도":      st.column_config.TextColumn("유사도", disabled=True, width="small"),
        },
        key="general_map_editor",
    )

    # 매핑 결과로 staged_df 구성
    _final_g = []
    for i, row in edited_map_g.iterrows():
        choice = row["마스터 매핑"]; qty = map_rows_g[i]["수량"]
        if choice and choice != "(건너뜀)":
            for sku, price, orig in master_lookup_g.values():
                if orig == choice:
                    _final_g.append({"SKU": sku, "수량": int(qty), "단가": price})
                    break
    if _final_g:
        _out_g = (pd.DataFrame(_final_g).groupby(["SKU","단가"], as_index=False)["수량"].sum())[["SKU","수량","단가"]]
        st.session_state["general_staged_df"] = _out_g.copy()

    st.markdown("---")

# ── 업로드 / 변환 섹션 (결과 없을 때 or 결과 있어도 재변환용) ──
_order_src = None
run_btn = False

if not _has_results:
    g_c1, g_c2, g_c3 = st.columns([3, 2, 4], gap="medium")

    with g_c1:
        st.markdown('<div class="col-header">📁 출고 파일</div>', unsafe_allow_html=True)
        order_file = st.file_uploader("출고 파일", type=["xlsx","xls"], key="order_up", label_visibility="collapsed")
        if st.session_state.get("general_slack_bytes"):
            st.caption("✅ Slack에서 파일 로드됨")
        if st.session_state.get("general_gmail_bytes"):
            st.caption("✅ Gmail에서 파일 로드됨")

        _gmail_general = render_gmail_loader("general")
        if _gmail_general:
            st.session_state["general_gmail_bytes"] = _gmail_general
            st.rerun()

    _order_src = order_file or (
        io.BytesIO(st.session_state["general_gmail_bytes"])
        if st.session_state.get("general_gmail_bytes") else (
            io.BytesIO(st.session_state["general_slack_bytes"])
            if st.session_state.get("general_slack_bytes") else None
        )
    )

    with g_c2:
        st.markdown('<div class="col-header">⚙️ 매칭 옵션</div>', unsafe_allow_html=True)
        threshold = st.slider("유사도 임계값", 40, 100, 70, format="%d%%", label_visibility="collapsed")
        st.caption(f"현재 {threshold}% — 낮을수록 더 많이 매칭")

        if _order_src is not None:
            try:
                order_df = pd.read_excel(_order_src)
                cols = list(order_df.columns)
                name_col = next((c for c in cols if "상품명" in c or "name" in c.lower()), cols[0])
                qty_col  = next((c for c in cols if "수량" in c or "qty" in c.lower() or "내품개수" in c), cols[1] if len(cols)>1 else cols[0])
                name_col = st.selectbox("상품명 컬럼", cols, index=cols.index(name_col))
                qty_col  = st.selectbox("수량 컬럼",  cols, index=cols.index(qty_col))
                if st.button("🔄 변환 시작", type="primary", use_container_width=True):
                    run_btn = True
                if _auto_run_g:
                    run_btn = True
            except Exception as e:
                st.error(f"파일 오류: {e}")
                st.stop()

    with g_c3:
        st.markdown('<div class="col-header">🔄 변환 결과</div>', unsafe_allow_html=True)
        if _order_src is None:
            st.info("← 출고 파일을 업로드하세요.")
        else:
            preview_df = (
                order_df.groupby(name_col)[qty_col].sum().reset_index()
                .rename(columns={name_col:"상품명", qty_col:"수량"})
                .sort_values("수량", ascending=False).reset_index(drop=True)
            )
            preview_df.index += 1
            st.caption(f"총 {len(preview_df)}종")
            st.dataframe(preview_df, use_container_width=True, height=min(45+len(preview_df)*32, 200))

if _order_src is not None and run_btn:
    master_bytes = st.session_state.get("master_bytes")
    if master_bytes:
        master_df = load_master_from_bytes(master_bytes)
    elif DEFAULT_MASTER:
        master_df = load_master(DEFAULT_MASTER)
    else:
        st.error("← 사이드바에서 마스터 파일을 업로드해주세요.")
        st.stop()

    master_cols = list(master_df.columns)
    m_sku_col   = next((c for c in master_cols if c.strip() == "SKU"), master_cols[0])
    m_name_col  = next((c for c in master_cols if "제품명" in c or "상품명" in c), master_cols[2])
    m_price_col = next((c for c in master_cols if "구매가" in c), None)

    grouped = (order_df.groupby(name_col)[qty_col].sum().reset_index()
               .rename(columns={name_col:"상품명", qty_col:"수량"}))
    master_lookup = {}
    for _, row in master_df.iterrows():
        orig = str(row[m_name_col]); norm = normalize(orig)
        master_lookup[norm] = (row[m_sku_col], row[m_price_col] if m_price_col else "", orig)
    norm_names = list(master_lookup.keys())

    results, unmatched = [], []
    prog = st.progress(0, text="매칭 중...")
    for i, (_, row) in enumerate(grouped.iterrows()):
        raw_name = row["상품명"]; qty = row["수량"]
        best_key, score = best_match(raw_name, norm_names)
        if score >= threshold:
            sku, price, matched_name = master_lookup[best_key]
            results.append({"SKU":sku,"수량":int(qty),"단가":price,"_출고상품명":raw_name,"_마스터매칭명":matched_name,"_유사도(%)":score})
        else:
            unmatched.append({"출고상품명":raw_name,"수량":int(qty),"유사도(%)":score,
                "가장유사한마스터":master_lookup.get(best_key,("","",""))[2],"_best_key":best_key})
        prog.progress((i+1)/len(grouped))
    prog.empty()
    st.session_state["results"] = results
    st.session_state["unmatched"] = unmatched
    st.session_state["master_lookup"] = master_lookup
    st.session_state["master_name_list"] = [v[2] for v in master_lookup.values()]
    st.session_state.pop("general_staged_df", None)
    st.rerun()

# ── 스테이징 그리드 ───────────────────────────────────────
staged_df = st.session_state.get("general_staged_df")
if staged_df is None:
    if st.button("✏️ 수기 입력", key="manual_input_g", help="변환 없이 SKU·수량을 직접 입력합니다"):
        st.session_state["general_staged_df"] = pd.DataFrame({"SKU": [""], "수량": [0], "단가": [0]})
        st.rerun()
if staged_df is not None:
    st.markdown('<hr style="margin:1rem 0;">', unsafe_allow_html=True)
    _hdr_c1, _hdr_c2 = st.columns([3, 1])
    with _hdr_c1:
        st.markdown('<div class="section-header">📋 스테이징 그리드 <span>전송 전 수량 · SKU 검토 및 수정</span></div>', unsafe_allow_html=True)
    with _hdr_c2:
        if st.button("🗑️ 초기화", key="clear_g", help="그리드를 비웁니다"):
            st.session_state.pop("general_staged_df", None)
            st.rerun()
    edited_df = st.data_editor(
        staged_df,
        use_container_width=True,
        num_rows="dynamic",
        height=min(60 + len(staged_df) * 35, 320),
        column_config={
            "SKU":  st.column_config.TextColumn("SKU", width="medium"),
            "수량": st.column_config.NumberColumn("수량", min_value=0, step=1, format="%d"),
            "단가": st.column_config.NumberColumn("단가", format="%.0f"),
        },
        key="general_grid_editor",
    )
    edited_df = edited_df.fillna({"SKU": "", "수량": 0, "단가": 0})
    edited_df = edited_df[edited_df["SKU"].astype(str).str.strip() != ""].reset_index(drop=True)
    st.session_state["general_staged_df"] = edited_df.copy()
    edited_df = edited_df[edited_df["수량"] > 0].reset_index(drop=True)

    col_stat, col_dl, col_api = st.columns([2, 1, 2], gap="small")
    with col_stat:
        total_sku = len(edited_df)
        total_qty = int(edited_df["수량"].sum()) if total_sku else 0
        st.markdown(f"""<div style="display:flex;gap:10px;">
            <div class="stat-box stat-ok" style="flex:1;">{total_sku}<div class="stat-label">SKU 종류</div></div>
            <div class="stat-box" style="flex:1;background:#eff6ff;color:#1e40af;border-color:#bfdbfe;">{total_qty}<div class="stat-label">총 출고 수량</div></div>
        </div>""", unsafe_allow_html=True)
    with col_dl:
        if total_sku > 0:
            buf = io.BytesIO()
            _detail_df = pd.DataFrame(st.session_state.get("results", []))
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                edited_df.to_excel(w, sheet_name="변경양식", index=False)
                if not _detail_df.empty:
                    _detail_df.to_excel(w, sheet_name="매칭상세", index=False)
            buf.seek(0)
            st.download_button("⬇️ 엑셀 다운로드", data=buf, file_name="변경양식_출력.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    with col_api:
        if total_sku > 0:
            render_api_send_section(edited_df, memo_key="general_memo")
