import streamlit as st
import pandas as pd
import io
from utils import *

# 플로우 바
has_naver = bool(st.session_state.get("naver_results") or st.session_state.get("naver_unmatched"))
has_staged_n = bool(st.session_state.get("naver_staged_df") is not None)
st.markdown(f"""
<div class="flow-bar">
    <div class="flow-step">
        <div class="flow-num">1</div>
        <div class="flow-label">📁 출고 파일</div>
    </div>
    <div class="flow-arrow">›</div>
    <div class="flow-step">
        <div class="flow-num {'done' if has_naver else ''}">2</div>
        <div class="flow-label">🔄 변환</div>
    </div>
    <div class="flow-arrow">›</div>
    <div class="flow-step">
        <div class="flow-num {'done' if has_staged_n else 'pending'}">3</div>
        <div class="flow-label">📋 스테이징</div>
    </div>
    <div class="flow-arrow">›</div>
    <div class="flow-step">
        <div class="flow-num {'done' if has_staged_n else 'pending'}">4</div>
        <div class="flow-label">⬇️ 전송</div>
    </div>
</div>
""", unsafe_allow_html=True)

_auto_run_n = st.session_state.pop("auto_run_naver", False)
naver_file = None
run_n = False

col_n1, col_n2 = st.columns([1, 1], gap="medium")
with col_n1:
    st.markdown('<div class="col-header">📁 출고 파일</div>', unsafe_allow_html=True)
    naver_file = st.file_uploader("네이버 출고 파일 선택", type=["xlsx","xls"], key="naver_up", label_visibility="collapsed")
    st.caption("'양식' + 'BoxHero' 시트 포함 파일")
    if st.session_state.get("naver_slack_bytes"):
        st.caption("✅ Slack에서 파일 로드됨")
    if st.session_state.get("naver_gmail_bytes"):
        st.caption("✅ Gmail에서 파일 로드됨")

    _gmail_naver = render_gmail_loader("naver")
    if _gmail_naver:
        st.session_state["naver_gmail_bytes"] = _gmail_naver
        st.rerun()

# 파일 소스 결정
_naver_src = naver_file or (
    io.BytesIO(st.session_state["naver_gmail_bytes"])
    if st.session_state.get("naver_gmail_bytes") else (
        io.BytesIO(st.session_state["naver_slack_bytes"])
        if st.session_state.get("naver_slack_bytes") else None
    )
)

with col_n2:
    st.markdown('<div class="col-header">🔄 변환 결과</div>', unsafe_allow_html=True)
    if _naver_src is None:
        st.info("← 파일을 업로드하면 결과가 표시됩니다.")
    else:
        try:
            naver_df, name_to_sku = load_naver(_naver_src)
        except Exception as e:
            st.error(f"파일 읽기 오류: {e}")
            st.stop()

        master_bytes = st.session_state.get("master_bytes")
        if master_bytes:
            master_df_n = load_master_from_bytes(master_bytes)
        elif DEFAULT_MASTER:
            master_df_n = load_master(DEFAULT_MASTER)
        else:
            st.error("← 사이드바에서 마스터 파일을 업로드해주세요.")
            st.stop()

        master_cols_n = list(master_df_n.columns)
        m_sku_col_n   = next((c for c in master_cols_n if c.strip() == "SKU"), master_cols_n[0])
        m_name_col_n  = next((c for c in master_cols_n if "제품명" in c or "상품명" in c), master_cols_n[2])
        m_price_col_n = next((c for c in master_cols_n if "구매가" in c), None)

        master_lookup_n = {}
        for _, row in master_df_n.iterrows():
            orig = str(row[m_name_col_n]); norm = normalize(orig)
            master_lookup_n[norm] = (row[m_sku_col_n], row[m_price_col_n] if m_price_col_n else "", orig)

        # 품목 미리보기 (컴팩트)
        preview_n = (
            naver_df.groupby("상품명")["수량"]
            .apply(lambda x: x.apply(lambda v: int(float(v)) if pd.notna(v) else 0).sum())
            .reset_index().rename(columns={"수량": "수량"})
            .sort_values("수량", ascending=False).reset_index(drop=True)
        )
        preview_n.index += 1
        st.caption(f"총 {len(preview_n)}종 · BoxHero 매핑 {len(name_to_sku)}개")
        st.dataframe(preview_n, use_container_width=True, height=min(45 + len(preview_n) * 32, 220))

        c_thresh, c_btn = st.columns([2, 3])
        with c_thresh:
            threshold_n = st.slider("임계값", 40, 100, 65, format="%d%%", key="naver_thresh", label_visibility="collapsed")
        with c_btn:
            if st.button("🔄 변환 시작", type="primary", use_container_width=True, key="naver_run"):
                run_n = True
            if _auto_run_n:
                run_n = True

        if run_n:
            results_n, unmatched_n = [], []
            prog = st.progress(0, text="변환 중...")
            rows_n = list(naver_df.iterrows())
            for i, (_, row) in enumerate(rows_n):
                sku, method = resolve_naver_sku(row["SKU_원본"], row["상품명"], name_to_sku, master_lookup_n)
                try: qty = int(float(row["수량"])) if pd.notna(row["수량"]) else 0
                except: qty = 0
                price = next((v[1] for v in master_lookup_n.values() if v[0] == sku), "")
                entry = {"SKU": sku, "수량": qty, "단가": price, "_상품명": row["상품명"], "_방법": method}
                (unmatched_n if sku.startswith("UNKNOWN") else results_n).append(entry)
                prog.progress((i+1)/len(rows_n))
            prog.empty()
            st.session_state["naver_results"] = results_n
            st.session_state["naver_unmatched"] = unmatched_n
            st.session_state["naver_master_lookup"] = master_lookup_n
            st.session_state.pop("naver_staged_df", None)
            st.rerun()

# ── 결과 (전체 너비) ────────────────────────────────────────
results_n   = st.session_state.get("naver_results", [])
unmatched_n = st.session_state.get("naver_unmatched", [])
master_lookup_n = st.session_state.get("naver_master_lookup", {})

if results_n or unmatched_n:
    s1, s2 = st.columns(2)
    with s1: st.markdown(f'<div class="stat-box stat-ok">{len(results_n)}건<div class="stat-label">✅ 변환 성공</div></div>', unsafe_allow_html=True)
    with s2: st.markdown(f'<div class="stat-box stat-err">{len(unmatched_n)}건<div class="stat-label">❌ 매핑 실패</div></div>', unsafe_allow_html=True)

    all_master_names_n = ["(건너뜀)"] + [v[2] for v in master_lookup_n.values()]
    final_results_n = []
    all_items_n = (
        [(i["_상품명"], i["수량"], i["SKU"], i.get("_방법",""), True)  for i in results_n] +
        [(i["_상품명"], i["수량"], i["SKU"], i.get("_방법",""), False) for i in unmatched_n]
    )
    fail_count_n = len(unmatched_n)
    map_rows_n = []
    for i_ok in results_n:
        cur_name = next((v[2] for v in master_lookup_n.values() if v[0] == i_ok["SKU"]), "(건너뜀)")
        map_rows_n.append({"원본 상품명": i_ok["_상품명"], "수량": i_ok["수량"],
                            "마스터 매핑": cur_name, "상태": "✅", "_method": i_ok.get("_방법","")})
    for i_fail in unmatched_n:
        map_rows_n.append({"원본 상품명": i_fail["_상품명"], "수량": i_fail["수량"],
                            "마스터 매핑": "(건너뜀)", "상태": "❌", "_method": ""})
    map_df_n = pd.DataFrame(map_rows_n)

    st.markdown("#### 🔧 원본 상품 → 마스터 매핑")
    st.caption("마스터 매핑 열의 드롭다운을 클릭해 잘못된 항목을 수정하세요.")

    edited_map_n = st.data_editor(
        map_df_n[["원본 상품명", "수량", "마스터 매핑", "상태"]],
        use_container_width=True,
        hide_index=True,
        height=min(60 + len(map_df_n) * 38, 400),
        column_config={
            "원본 상품명": st.column_config.TextColumn("원본 상품명", disabled=True, width="medium"),
            "수량":        st.column_config.NumberColumn("수량", disabled=True, width="small"),
            "마스터 매핑": st.column_config.SelectboxColumn("마스터 매핑", options=all_master_names_n, width="large"),
            "상태":        st.column_config.TextColumn("상태", disabled=True, width="small"),
        },
        key="naver_map_editor",
    )

    # edited_map_n 결과로 final_results_n 구성
    final_results_n = []
    for i, row in edited_map_n.iterrows():
        choice = row["마스터 매핑"]
        qty = map_rows_n[i]["수량"]
        if choice and choice != "(건너뜀)":
            for sku_v, price_v, orig_v in master_lookup_n.values():
                if orig_v == choice:
                    final_results_n.append({"SKU": sku_v, "수량": int(qty), "단가": price_v})
                    break

    if final_results_n:
        out_n = (pd.DataFrame(final_results_n).groupby(["SKU","단가"], as_index=False)["수량"].sum())[["SKU","수량","단가"]]
        st.session_state["naver_staged_df"] = out_n.copy()

    # ── 스테이징 그리드 ──────────────────────────────────────
    staged_n = st.session_state.get("naver_staged_df")
    if staged_n is None:
        if st.button("✏️ 수기 입력", key="manual_input_n", help="변환 없이 SKU·수량을 직접 입력합니다"):
            st.session_state["naver_staged_df"] = pd.DataFrame({"SKU": [""], "수량": [0], "단가": [0]})
            st.rerun()
    if staged_n is not None:
        st.markdown('<hr style="margin:1rem 0;">', unsafe_allow_html=True)
        _hdr_nc1, _hdr_nc2 = st.columns([3, 1])
        with _hdr_nc1:
            st.markdown('<div class="section-header">📋 스테이징 그리드 <span>전송 전 수량 · SKU 검토 및 수정</span></div>', unsafe_allow_html=True)
        with _hdr_nc2:
            if st.button("🗑️ 초기화", key="clear_n", help="그리드를 비웁니다"):
                st.session_state.pop("naver_staged_df", None)
                st.rerun()
        edited_n = st.data_editor(
            staged_n,
            use_container_width=True,
            num_rows="dynamic",
            height=min(60 + len(staged_n) * 35, 320),
            column_config={
                "SKU":  st.column_config.TextColumn("SKU", width="medium"),
                "수량": st.column_config.NumberColumn("수량", min_value=0, step=1, format="%d"),
                "단가": st.column_config.NumberColumn("단가", format="%.0f"),
            },
            key="naver_grid_editor",
        )
        edited_n = edited_n.fillna({"SKU": "", "수량": 0, "단가": 0})
        edited_n = edited_n[edited_n["SKU"].astype(str).str.strip() != ""].reset_index(drop=True)
        st.session_state["naver_staged_df"] = edited_n.copy()
        edited_n = edited_n[edited_n["수량"] > 0].reset_index(drop=True)

        col_stat_n, col_dl_n, col_api_n = st.columns([2, 1, 2], gap="small")
        with col_stat_n:
            total_sku_n = len(edited_n)
            total_qty_n = int(edited_n["수량"].sum()) if total_sku_n else 0
            st.markdown(f"""<div style="display:flex;gap:10px;">
                <div class="stat-box stat-ok" style="flex:1;">{total_sku_n}<div class="stat-label">SKU 종류</div></div>
                <div class="stat-box" style="flex:1;background:#eff6ff;color:#1e40af;border-color:#bfdbfe;">{total_qty_n}<div class="stat-label">총 출고 수량</div></div>
            </div>""", unsafe_allow_html=True)
        with col_dl_n:
            if total_sku_n > 0:
                buf_n = io.BytesIO()
                detail_df_n = st.session_state.get("naver_detail_df", edited_n)
                with pd.ExcelWriter(buf_n, engine="openpyxl") as w:
                    edited_n.to_excel(w, sheet_name="변경양식", index=False)
                    detail_df_n.to_excel(w, sheet_name="매칭상세", index=False)
                buf_n.seek(0)
                st.download_button("⬇️ 엑셀 다운로드", data=buf_n, file_name="네이버_변경양식_출력.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        with col_api_n:
            if total_sku_n > 0:
                render_api_send_section(edited_n, memo_key="naver_memo")
