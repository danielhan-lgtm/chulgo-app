import streamlit as st
import pandas as pd
import io
from utils import *
st.markdown(APP_CSS, unsafe_allow_html=True)

gmail_token_p = st.session_state.get("gmail_token")

if not gmail_token_p:
    st.markdown('<div style="background:#fef2f2;border-left:4px solid #ef4444;border-radius:10px;padding:11px 16px;font-size:0.84rem;color:#991b1b;font-weight:500;">⚠️ <b>Gmail 미연결</b> — 사이드바 연동 섹션에서 연결해주세요.</div>', unsafe_allow_html=True)
    st.stop()

# 새로고침 버튼
hdr_c1, hdr_c2 = st.columns([5, 1])
with hdr_c2:
    if st.button("🔄 새로고침", key="gmail_page_refresh", use_container_width=True):
        st.session_state.pop("gm_orders", None)

st.caption(f"조회 대상: {' · '.join(GMAIL_SENDERS)}")

if "gm_orders" not in st.session_state:
    with st.spinner("Gmail 메일 불러오는 중..."):
        _go, _gd = fetch_gmail_orders(gmail_token_p)
        st.session_state["gm_orders"] = _go
        st.session_state["gm_debug"]  = _gd

gm_orders = st.session_state.get("gm_orders", [])
gm_debug  = st.session_state.get("gm_debug", "")

if gm_debug:
    st.caption(f"🔍 {gm_debug}")

if not gm_orders:
    st.markdown('<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:32px;text-align:center;color:#9ca3af;">출고 관련 메일이 없습니다.</div>', unsafe_allow_html=True)
    st.stop()

# ── 요약 카드 ─────────────────────────────────────────────
_total_gm  = len(gm_orders)
_with_file = sum(1 for o in gm_orders if o["files"])
_no_file   = _total_gm - _with_file

_gm_cols = st.columns(3)
for _col, _label, _val, _bg, _border, _color in [
    (_gm_cols[0], "전체 메일",    _total_gm,  "#f9fafb", "#e5e7eb", "#111827"),
    (_gm_cols[1], "첨부파일 있음", _with_file, "#f0fdf4", "#bbf7d0", "#15803d"),
    (_gm_cols[2], "첨부파일 없음", _no_file,   "#fef9c3", "#fde047", "#92400e"),
]:
    _col.markdown(
        f'<div style="background:{_bg};border:1px solid {_border};border-radius:10px;'
        f'padding:12px 8px 6px 8px;text-align:center;margin-bottom:8px;">'
        f'<div style="font-size:1.4rem;font-weight:800;color:{_color};line-height:1;">{_val}</div>'
        f'<div style="font-size:0.75rem;color:{_color};margin-top:4px;font-weight:600;">{_label}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.markdown('<hr style="margin:0.8rem 0;">', unsafe_allow_html=True)

# ── 목록 + 상세 ───────────────────────────────────────────
gm_list_col, gm_detail_col = st.columns([1, 2], gap="medium")

with gm_list_col:
    st.markdown('<div class="col-header">📋 메일 목록</div>', unsafe_allow_html=True)
    for idx, o in enumerate(gm_orders):
        is_sel   = st.session_state.get("gm_page_sel", 0) == idx
        has_file = "📎" if o["files"] else ""
        border   = "1.5px solid #10b981" if is_sel else "1px solid #e5e7eb"
        bg       = "#f0fdf4" if is_sel else "#fff"
        sender_name = o["sender"].split("<")[0].strip() or o["sender"]
        card_html = (
            f'<div style="border:{border};background:{bg};border-radius:10px;'
            f'padding:10px 13px;margin-bottom:4px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
            f'<span style="font-size:0.68rem;color:#9ca3af;">{o["dt"]} {has_file}</span>'
            f'<span style="background:#eff6ff;color:#1d4ed8;border-radius:4px;padding:1px 7px;font-size:0.68rem;font-weight:600;">{sender_name[:14]}</span>'
            f'</div>'
            f'<div style="font-size:0.82rem;font-weight:700;color:#111827;line-height:1.3;'
            f'overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">{o["subject"]}</div>'
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)
        btn_label = "✓ 선택됨" if is_sel else "선택"
        if st.button(btn_label, key=f"gm_order_btn_{idx}", use_container_width=True,
                     type="primary" if is_sel else "secondary"):
            st.session_state["gm_page_sel"] = idx
            st.rerun()

# 선택된 메일 상세
sel_gm_idx = st.session_state.get("gm_page_sel", 0)
if sel_gm_idx >= len(gm_orders):
    sel_gm_idx = 0
order_gm = gm_orders[sel_gm_idx]

with gm_detail_col:
    st.markdown('<div class="col-header">📄 메일 상세</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div style="background:#f8f9fb;border:1px solid #e5e7eb;border-radius:10px;'
        f'padding:14px 18px;margin-bottom:12px;">'
        f'<div style="font-size:0.75rem;color:#6b7280;margin-bottom:4px;">발신자</div>'
        f'<div style="font-size:0.88rem;font-weight:600;color:#111827;margin-bottom:10px;">{order_gm["sender"]}</div>'
        f'<div style="font-size:0.75rem;color:#6b7280;margin-bottom:4px;">제목</div>'
        f'<div style="font-size:0.92rem;font-weight:700;color:#111827;">{order_gm["subject"]}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-header">📎 첨부파일</div>', unsafe_allow_html=True)
    excel_files_gm = order_gm["files"]

    if not excel_files_gm:
        st.caption("이 메일에 Excel 첨부파일이 없습니다.")
    else:
        for fi, f in enumerate(excel_files_gm):
            st.markdown(
                f'<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;'
                f'padding:8px 12px;font-size:0.82rem;font-weight:600;color:#374151;margin-bottom:6px;">'
                f'📊 {f["name"]}</div>',
                unsafe_allow_html=True,
            )

            _gm_cache_key = f"gm_preview_bytes_{sel_gm_idx}_{fi}"
            fb, fc, fd = st.columns(3, gap="small")

            with fb:
                if st.button("👁 미리보기", key=f"gm_preview_{sel_gm_idx}_{fi}", use_container_width=True):
                    with st.spinner("불러오는 중..."):
                        _bytes = download_gmail_attachment(gmail_token_p, f["message_id"], f["attachment_id"])
                    st.session_state[_gm_cache_key] = _bytes
            with fc:
                if st.button("⬇️ 다운로드", key=f"gm_dl_{sel_gm_idx}_{fi}", use_container_width=True):
                    with st.spinner("불러오는 중..."):
                        _bytes = download_gmail_attachment(gmail_token_p, f["message_id"], f["attachment_id"])
                    st.session_state[_gm_cache_key] = _bytes
            with fd:
                if st.button("🔄 변환", key=f"gm_conv_{sel_gm_idx}_{fi}", use_container_width=True, type="primary"):
                    with st.spinner("다운로드 중..."):
                        _d = st.session_state.get(_gm_cache_key) or download_gmail_attachment(gmail_token_p, f["message_id"], f["attachment_id"])
                    st.session_state[_gm_cache_key] = _d
                    st.session_state[f"gm_do_convert_{sel_gm_idx}_{fi}"] = True

            _gm_preview_bytes = st.session_state.get(_gm_cache_key)
            if _gm_preview_bytes:
                try:
                    _xl = pd.ExcelFile(io.BytesIO(_gm_preview_bytes))
                    _sheet = st.selectbox("시트", _xl.sheet_names, key=f"gm_preview_sheet_{sel_gm_idx}_{fi}")
                    _prev_df = pd.read_excel(io.BytesIO(_gm_preview_bytes), sheet_name=_sheet, dtype=str)
                    st.dataframe(_prev_df, use_container_width=True, height=min(60 + len(_prev_df) * 32, 240))
                    st.download_button(
                        f"⬇️ {f['name']} 원본 다운로드",
                        data=_gm_preview_bytes,
                        file_name=f["name"],
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"gm_dl_btn_{sel_gm_idx}_{fi}",
                        use_container_width=True,
                    )
                except Exception as _pe:
                    st.error(f"미리보기 오류: {_pe}")

            # ── 리셀러 사전 검사 ─────────────────────────────────
            if _gm_preview_bytes:
                st.markdown('<hr style="margin:8px 0;">', unsafe_allow_html=True)
                _rg_key      = f"gm_rg_result_{sel_gm_idx}_{fi}"
                _rg_done_key = f"gm_rg_done_{sel_gm_idx}_{fi}"
                _rg_hdr_c1, _rg_hdr_c2 = st.columns([3, 1])
                with _rg_hdr_c1:
                    st.markdown('<div class="section-header">🛡 리셀러 사전 검사</div>', unsafe_allow_html=True)
                with _rg_hdr_c2:
                    if st.session_state.get(_rg_done_key):
                        if st.button("↺ 재검사", key=f"gm_rg_reset_{sel_gm_idx}_{fi}", use_container_width=True):
                            st.session_state.pop(_rg_key, None)
                            st.session_state.pop(_rg_done_key, None)
                            st.rerun()
                if not st.session_state.get(_rg_done_key):
                    if st.button("🔍 리셀러 검사 실행", key=f"gm_rg_run_{sel_gm_idx}_{fi}", use_container_width=True):
                        with st.spinner("리셀러 패턴 분석 중..."):
                            try:
                                _rg_df, _rg_stats = analyze_reseller(_gm_preview_bytes)
                                st.session_state[_rg_key]      = (_rg_df, _rg_stats)
                                st.session_state[_rg_done_key] = True
                                st.rerun()
                            except Exception as _rge:
                                st.error(f"검사 오류: {_rge}")
                if st.session_state.get(_rg_done_key):
                    _rg_df, _rg_stats = st.session_state[_rg_key]
                    _rg_confirmed = _rg_stats["confirmed"]
                    _rg_suspected = _rg_stats["suspected"]
                    _rg_normal    = _rg_stats["normal"]
                    _ref_notes = []
                    if not _rg_stats["has_bl"]:   _ref_notes.append("블랙리스트 없음")
                    if not _rg_stats["has_past"]: _ref_notes.append("누적데이터 없음")
                    if _ref_notes:
                        st.caption(f"⚠️ 패턴 분석만 적용 ({', '.join(_ref_notes)} — dashboard/data/ 폴더 확인)")
                    _rc1, _rc2, _rc3 = st.columns(3)
                    for _col, _label, _val, _bg, _bd, _fc in [
                        (_rc1, "확정 리셀러", _rg_confirmed, "#fef2f2", "#fca5a5", "#991b1b"),
                        (_rc2, "의심",        _rg_suspected, "#fffbeb", "#fcd34d", "#92400e"),
                        (_rc3, "정상",        _rg_normal,    "#f0fdf4", "#86efac", "#15803d"),
                    ]:
                        _col.markdown(
                            f'<div style="background:{_bg};border:1px solid {_bd};border-radius:8px;'
                            f'padding:10px 6px 6px 6px;text-align:center;margin-bottom:6px;">'
                            f'<div style="font-size:1.3rem;font-weight:800;color:{_fc};">{_val}</div>'
                            f'<div style="font-size:0.72rem;color:{_fc};font-weight:600;">{_label}</div>'
                            f'</div>', unsafe_allow_html=True)
                    _rg_flagged = _rg_df[_rg_df['리셀러_판정'] != '정상']
                    if not _rg_flagged.empty:
                        _show_cols = [c for c in ['수취인','주소','연락처','상품명','수량','리셀러_판정','위험점수','탐지사유'] if c in _rg_flagged.columns]
                        st.dataframe(
                            _rg_flagged[_show_cols].reset_index(drop=True),
                            use_container_width=True,
                            height=min(60 + len(_rg_flagged)*34, 280),
                            column_config={
                                "리셀러_판정": st.column_config.TextColumn("판정", width="small"),
                                "위험점수":    st.column_config.NumberColumn("점수", width="small"),
                                "탐지사유":    st.column_config.TextColumn("사유", width="large"),
                            },
                        )
                        if _rg_confirmed > 0:
                            st.markdown(
                                f'<div style="background:#fef2f2;border-left:4px solid #ef4444;'
                                f'border-radius:6px;padding:8px 12px;font-size:0.83rem;color:#991b1b;font-weight:600;">'
                                f'⚠️ 확정 리셀러 {_rg_confirmed}건이 포함되어 있습니다. 변환 전 검토 후 진행하세요.</div>',
                                unsafe_allow_html=True)
                    else:
                        st.markdown(
                            '<div style="background:#f0fdf4;border-left:4px solid #10b981;'
                            'border-radius:6px;padding:8px 12px;font-size:0.83rem;color:#15803d;font-weight:600;">'
                            '✅ 리셀러 의심 대상이 없습니다.</div>',
                            unsafe_allow_html=True)

            # ── 인라인 변환 + 전송 ──────────────────────────────
            if st.session_state.get(f"gm_do_convert_{sel_gm_idx}_{fi}") and _gm_preview_bytes:
                st.markdown('<hr style="margin:10px 0;">', unsafe_allow_html=True)
                st.markdown('<div class="section-header">🔄 변환 & 전송</div>', unsafe_allow_html=True)

                _fmt_gm = st.radio("양식 선택", ["📄 일반 (컬리 등)", "🛒 네이버"],
                                   horizontal=True, key=f"gm_fmt_{sel_gm_idx}_{fi}",
                                   label_visibility="collapsed")
                _thresh_gm = st.slider("유사도 임계값", 40, 100, 70, format="%d%%",
                                       key=f"gm_thresh_{sel_gm_idx}_{fi}", label_visibility="collapsed")

                _mb_gm = st.session_state.get("master_bytes")
                _master_df_gm = None
                if _mb_gm:
                    _master_df_gm = load_master_from_bytes(_mb_gm)
                elif DEFAULT_MASTER:
                    _master_df_gm = load_master(DEFAULT_MASTER)

                if _master_df_gm is None:
                    st.warning("← 사이드바에서 마스터 파일을 연결해주세요.")
                else:
                    _mcols_gm   = list(_master_df_gm.columns)
                    _msku_gm    = next((c for c in _mcols_gm if c.strip() == "SKU"), _mcols_gm[0])
                    _mname_gm   = next((c for c in _mcols_gm if "제품명" in c or "상품명" in c), _mcols_gm[2])
                    _mprice_gm  = next((c for c in _mcols_gm if "구매가" in c), None)
                    _mlookup_gm = {}
                    for _, _row in _master_df_gm.iterrows():
                        _orig = str(_row[_mname_gm]); _norm = normalize(_orig)
                        _mlookup_gm[_norm] = (_row[_msku_gm], _row[_mprice_gm] if _mprice_gm else "", _orig)

                    _map_key_gm    = f"gm_map_{sel_gm_idx}_{fi}"
                    _staged_key_gm = f"gm_staged_{sel_gm_idx}_{fi}"
                    _detail_key_gm = f"gm_detail_{sel_gm_idx}_{fi}"

                    if not st.session_state.get(_map_key_gm):
                        if st.button("▶ 변환 실행", key=f"gm_run_{sel_gm_idx}_{fi}", type="primary", use_container_width=True):
                            try:
                                if _fmt_gm == "🛒 네이버":
                                    _src_gm = io.BytesIO(_gm_preview_bytes)
                                    _ndf_gm, _n2s_gm = load_naver(_src_gm)
                                    _raw_map_gm = []
                                    for _, _nrow in _ndf_gm.iterrows():
                                        _oname = str(_nrow.get("상품명","")).strip()
                                        _oqty  = int(_nrow.get("수량", 0))
                                        _best_gm, _bscore = None, 0
                                        for _nk, _nv in _mlookup_gm.items():
                                            _sc = fuzz.token_sort_ratio(normalize(_oname), _nk)
                                            if _sc > _bscore:
                                                _bscore, _best_gm = _sc, _nv
                                        _raw_map_gm.append({"원본 상품명": _oname, "수량": _oqty,
                                                             "마스터 매핑": _best_gm[2] if _best_gm and _bscore >= _thresh_gm else "(건너뜀)",
                                                             "유사도": f"{_bscore}%"})
                                else:
                                    _odf_gm = pd.read_excel(io.BytesIO(_gm_preview_bytes))
                                    _ocols  = list(_odf_gm.columns)
                                    _oname_col = next((c for c in _ocols if "상품명" in c or "name" in c.lower()), _ocols[0])
                                    _oqty_col  = next((c for c in _ocols if "수량" in c or "qty" in c.lower()), _ocols[1] if len(_ocols)>1 else _ocols[0])
                                    _raw_map_gm = []
                                    for _, _orow in _odf_gm.iterrows():
                                        _oname = str(_orow.get(_oname_col,"")).strip()
                                        try: _oqty = int(float(str(_orow.get(_oqty_col, 0)).strip()))
                                        except: _oqty = 0
                                        _best_gm, _bscore = None, 0
                                        for _nk, _nv in _mlookup_gm.items():
                                            _sc = fuzz.token_sort_ratio(normalize(_oname), _nk)
                                            if _sc > _bscore:
                                                _bscore, _best_gm = _sc, _nv
                                        _raw_map_gm.append({"원본 상품명": _oname, "수량": _oqty,
                                                             "마스터 매핑": _best_gm[2] if _best_gm and _bscore >= _thresh_gm else "(건너뜀)",
                                                             "유사도": f"{_bscore}%"})
                                if _raw_map_gm:
                                    st.session_state[_map_key_gm] = _raw_map_gm
                                    st.session_state.pop(_staged_key_gm, None)
                                    st.rerun()
                                else:
                                    st.warning("매칭된 항목이 없습니다.")
                            except Exception as _ge:
                                st.error(f"변환 오류: {_ge}")

                    _raw_map_gm = st.session_state.get(_map_key_gm)
                    if _raw_map_gm:
                        _all_mnames_gm = ["(건너뜀)"] + [v[2] for v in _mlookup_gm.values()]
                        _gh1, _gh2 = st.columns([4, 1])
                        with _gh1:
                            st.markdown("**🔧 매핑 확인**")
                        with _gh2:
                            if st.button("🔄 다시 변환", key=f"gm_reset_{sel_gm_idx}_{fi}", use_container_width=True):
                                st.session_state.pop(_map_key_gm, None)
                                st.session_state.pop(_staged_key_gm, None)
                                st.rerun()
                        _map_df_gm = pd.DataFrame(_raw_map_gm)
                        _edited_map_gm = st.data_editor(
                            _map_df_gm, use_container_width=True, hide_index=True,
                            height=min(60 + len(_map_df_gm) * 38, 400),
                            column_config={
                                "원본 상품명": st.column_config.TextColumn("원본 상품명", disabled=True, width="medium"),
                                "수량":        st.column_config.NumberColumn("수량", min_value=0, step=1, format="%d", width="small"),
                                "마스터 매핑": st.column_config.SelectboxColumn("마스터 매핑", options=_all_mnames_gm, width="large"),
                                "유사도":      st.column_config.TextColumn("유사도", disabled=True, width="small"),
                            },
                            key=f"gm_map_editor_{sel_gm_idx}_{fi}",
                        )
                        _results_gm = []
                        for _ri, _rrow in _edited_map_gm.iterrows():
                            _ch = _rrow["마스터 매핑"]; _qty_gm = _rrow["수량"]
                            if _ch and _ch != "(건너뜀)":
                                for _sv, _pv, _ov in _mlookup_gm.values():
                                    if _ov == _ch:
                                        _results_gm.append({"SKU": _sv, "수량": int(_qty_gm), "단가": _pv})
                                        break
                        if _results_gm:
                            _out_gm = (pd.DataFrame(_results_gm).groupby(["SKU","단가"], as_index=False)["수량"].sum())[["SKU","수량","단가"]]
                            st.session_state[_staged_key_gm] = _out_gm

                    _staged_gm = st.session_state.get(_staged_key_gm)
                    if _staged_gm is not None:
                        _edited_gm = st.data_editor(
                            _staged_gm, use_container_width=True, num_rows="dynamic",
                            height=min(60 + len(_staged_gm)*35, 280),
                            column_config={
                                "SKU":  st.column_config.TextColumn("SKU"),
                                "수량": st.column_config.NumberColumn("수량", min_value=0, step=1, format="%d"),
                                "단가": st.column_config.NumberColumn("단가", format="%.0f"),
                            },
                            key=f"gm_grid_{sel_gm_idx}_{fi}",
                        )
                        _edited_gm = _edited_gm[_edited_gm["수량"] > 0].reset_index(drop=True)
                        _gdl_col, _gapi_col = st.columns(2, gap="small")
                        with _gdl_col:
                            _buf_gm = io.BytesIO()
                            with pd.ExcelWriter(_buf_gm, engine="openpyxl") as _w:
                                _edited_gm.to_excel(_w, sheet_name="변경양식", index=False)
                            _buf_gm.seek(0)
                            st.download_button("⬇️ 엑셀 다운로드", data=_buf_gm,
                                file_name="출고_변경양식.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True, key=f"gm_dl_out_{sel_gm_idx}_{fi}")
                        with _gapi_col:
                            render_api_send_section(_edited_gm, memo_key=f"gm_memo_{sel_gm_idx}_{fi}")
