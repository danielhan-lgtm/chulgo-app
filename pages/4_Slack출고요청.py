import streamlit as st
import pandas as pd
import io
from utils import *
st.markdown(APP_CSS, unsafe_allow_html=True)

slack_token_p = st.session_state.get("slack_token")
channel_id_p  = st.session_state.get("slack_channel_id")

if not slack_token_p:
    st.markdown('<div style="background:#fef2f2;border-left:4px solid #ef4444;border-radius:10px;padding:11px 16px;font-size:0.84rem;color:#991b1b;font-weight:500;">⚠️ <b>Slack 미연결</b> — 사이드바 연동 섹션에서 연결해주세요.</div>', unsafe_allow_html=True)
    st.stop()

if not channel_id_p:
    st.info("← 사이드바 Slack 섹션에서 채널을 선택해주세요.")
    st.stop()

# 새로고침 버튼
hdr_c1, hdr_c2 = st.columns([5, 1])
with hdr_c2:
    if st.button("🔄 새로고침", key="slack_page_refresh", use_container_width=True):
        st.session_state.pop("sb_slack_orders", None)

# 현재 선택된 채널 표시
_ch_name_p = next((k for k, v in st.session_state.get("slack_channels", {}).items()
                   if v == channel_id_p), channel_id_p)
st.caption(f"채널: **#{_ch_name_p}** `{channel_id_p}`")

if "sb_slack_orders" not in st.session_state:
    with st.spinner("Slack 메시지 불러오는 중..."):
        _o, _d = fetch_slack_orders(slack_token_p, channel_id_p)
        st.session_state["sb_slack_orders"] = _o
        st.session_state["sb_slack_debug"]  = _d

orders_p = st.session_state.get("sb_slack_orders", [])
debug_p  = st.session_state.get("sb_slack_debug", "")

if debug_p == "not_in_channel":
    ch_name = _ch_name_p
    st.markdown(f"""<div style="background:#fef2f2;border-left:4px solid #ef4444;border-radius:10px;
        padding:16px 20px;font-size:0.88rem;color:#991b1b;line-height:1.7;">
        <b>⚠️ 봇이 채널 <code>#{ch_name}</code> 에 접근할 수 없습니다</b><br>
        채널 ID: <code>{channel_id_p}</code><br><br>
        아래 버튼으로 채널 참여를 시도하거나, Slack에서 직접 초대해주세요.
    </div>""", unsafe_allow_html=True)
    if st.button("🔗 채널 참여 시도", key="try_join_channel", type="primary"):
        try:
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError
            _jclient = WebClient(token=slack_token_p)
            _jclient.conversations_join(channel=channel_id_p)
            st.session_state.pop("sb_slack_orders", None)
            st.success("✅ 채널 참여 완료! 새로고침합니다.")
            st.rerun()
        except SlackApiError as _je:
            _jerr = _je.response.get("error", "")
            if _jerr == "method_not_supported_for_channel_type":
                st.warning("Private 채널입니다. Slack에서 직접 `/invite @goramon` 을 입력해주세요.")
            else:
                st.error(f"참여 실패: {_jerr}")
    st.stop()
elif debug_p == "channel_not_found":
    st.error("채널을 찾을 수 없습니다. 사이드바에서 채널을 다시 선택해주세요.")
    st.stop()
elif debug_p:
    st.caption(f"🔍 {debug_p}")

if not orders_p:
    st.markdown('<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:32px;text-align:center;color:#9ca3af;">출고 요청 메시지가 없습니다.</div>', unsafe_allow_html=True)
    st.stop()

# ── 출고 대시보드 ─────────────────────────────────────────
_total   = len(orders_p)
_done    = sum(1 for o in orders_p if reaction_to_status(o.get("reactions",[])) and reaction_to_status(o.get("reactions",[]))["label"] == "완료")
_wip     = sum(1 for o in orders_p if reaction_to_status(o.get("reactions",[])) and reaction_to_status(o.get("reactions",[]))["label"] in ("처리중","진행중"))
_reject  = sum(1 for o in orders_p if reaction_to_status(o.get("reactions",[])) and reaction_to_status(o.get("reactions",[]))["label"] == "반려")
_pending = _total - _done - _wip - _reject

def _first_idx(orders, label_set):
    for i, o in enumerate(orders):
        s = reaction_to_status(o.get("reactions", []))
        if label_set == "미처리" and not s:
            return i
        elif s and s["label"] in label_set:
            return i
    return None

_dash_items = [
    ("전체",   _total,   "#f9fafb", "#e5e7eb", "#111827", None,                  "dash_all",     "📋 전체 보기"),
    ("미처리", _pending, "#fef9c3", "#fde047", "#92400e", "미처리",              "dash_pending", "⏳ 미처리 이동"),
    ("처리중", _wip,     "#eff6ff", "#bfdbfe", "#1d4ed8", ("처리중","진행중"),   "dash_wip",     "🔄 처리중 이동"),
    ("완료",   _done,    "#f0fdf4", "#bbf7d0", "#15803d", ("완료",),             "dash_done",    "✅ 완료 이동"),
    ("반려",   _reject,  "#fef2f2", "#fecaca", "#dc2626", ("반려",),             "dash_reject",  "❌ 반려 이동"),
]
_dash_cols = st.columns(5)
for _col, (_label, _cnt, _bg, _border, _color, _filter, _key, _btn_label) in zip(_dash_cols, _dash_items):
    with _col:
        st.markdown(
            f'<div style="background:{_bg};border:1px solid {_border};border-radius:10px;'
            f'padding:12px 8px 6px 8px;text-align:center;margin-bottom:4px;">'
            f'<div style="font-size:1.4rem;font-weight:800;color:{_color};line-height:1;">{_cnt}</div>'
            f'<div style="font-size:0.75rem;color:{_color};margin-top:4px;font-weight:600;">{_label}</div>'
            f'</div>',
            unsafe_allow_html=True
        )
        _disabled = (_cnt == 0)
        if st.button(_btn_label, key=_key, use_container_width=True, disabled=_disabled):
            if _filter is None:
                st.session_state["slack_page_sel"] = 0
            else:
                _idx = _first_idx(orders_p, _filter)
                if _idx is not None:
                    st.session_state["slack_page_sel"] = _idx
            st.rerun()

# 미처리 항목 경고
if _pending > 0:
    _pending_items = [extract_summary_fields(o["parsed"]).get("목적","") or o["title"][:30] for o in orders_p
                      if not reaction_to_status(o.get("reactions",[]))]
    _pending_str = " · ".join(_pending_items[:5]) + (" 외..." if len(_pending_items) > 5 else "")
    st.markdown(f'<div style="background:#fefce8;border-left:4px solid #eab308;border-radius:8px;padding:10px 14px;font-size:0.82rem;color:#854d0e;margin:8px 0;">⚠️ <b>미처리 {_pending}건</b> &nbsp;—&nbsp; {_pending_str}</div>', unsafe_allow_html=True)

st.markdown('<hr style="margin:0.8rem 0;">', unsafe_allow_html=True)

# ── 요청 목록 + 상세 ────────────────────────────────────
list_col, detail_col = st.columns([1, 2], gap="medium")

with list_col:
    st.markdown('<div class="col-header">📋 요청 목록</div>', unsafe_allow_html=True)
    for idx, o in enumerate(orders_p):
        is_sel      = st.session_state.get("slack_page_sel", 0) == idx
        summ        = extract_summary_fields(o["parsed"])
        tag_purpose = summ.get("목적", "") or o["title"][:28]
        tag_items   = summ.get("품목", "")[:22]
        tag_date    = summ.get("일정", "")
        has_file    = "📎" if o["files"] else ""
        status      = reaction_to_status(o.get("reactions", []))

        if status:
            s_badge = '<span style="background:' + status["bg"] + ';color:' + status["color"] + ';border-radius:4px;padding:1px 8px;font-size:0.7rem;font-weight:700;">● ' + status["label"] + '</span>'
        else:
            s_badge = '<span style="background:#f3f4f6;color:#9ca3af;border-radius:4px;padding:1px 8px;font-size:0.7rem;">미처리</span>'

        tags = ""
        if tag_date:
            tags += '<span style="background:#eff6ff;color:#1d4ed8;border-radius:4px;padding:1px 6px;font-size:0.68rem;font-weight:600;">📅 ' + tag_date + '</span> '
        if tag_items:
            tags += '<span style="background:#f0fdf4;color:#15803d;border-radius:4px;padding:1px 6px;font-size:0.68rem;font-weight:600;">📦 ' + tag_items + '</span>'

        border = "1.5px solid #10b981" if is_sel else "1px solid #e5e7eb"
        bg     = "#f0fdf4" if is_sel else "#fff"
        card_html = (
            '<div style="border:' + border + ';background:' + bg + ';border-radius:10px;padding:10px 13px;margin-bottom:4px;">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
            '<span style="font-size:0.68rem;color:#9ca3af;">' + o["dt"] + ' ' + has_file + '</span>'
            + s_badge +
            '</div>'
            '<div style="font-size:0.82rem;font-weight:700;color:#111827;margin-bottom:5px;line-height:1.3;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">' + tag_purpose + '</div>'
            '<div style="display:flex;flex-wrap:wrap;gap:4px;">' + tags + '</div>'
            '</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)
        btn_label = "✓ 선택됨" if is_sel else "선택"
        if st.button(btn_label, key=f"slack_order_btn_{idx}", use_container_width=True,
                     type="primary" if is_sel else "secondary"):
            st.session_state["slack_page_sel"] = idx
            st.rerun()

# 선택된 요청
sel_idx_p = st.session_state.get("slack_page_sel", 0)
if sel_idx_p >= len(orders_p):
    sel_idx_p = 0
order_p = orders_p[sel_idx_p]

with detail_col:
    st.markdown('<div class="col-header">📄 요청 상세</div>', unsafe_allow_html=True)

    summ_d = extract_summary_fields(order_p["parsed"])
    status_d = reaction_to_status(order_p.get("reactions", []))

    _status_span = ""
    if status_d:
        _status_span = '<span style="background:' + status_d["bg"] + ';color:' + status_d["color"] + ';border-radius:6px;padding:4px 12px;font-size:0.82rem;font-weight:800;">● ' + status_d["label"] + '</span>'
        for r in order_p.get("reactions", []):
            _status_span += ' <span style="background:#f3f4f6;color:#4b5563;border-radius:12px;padding:2px 8px;font-size:0.74rem;">:' + r["name"] + ': ' + str(r["count"]) + '</span>'
    else:
        _status_span = '<span style="background:#f3f4f6;color:#6b7280;border-radius:6px;padding:4px 12px;font-size:0.82rem;font-weight:600;">⏳ 미처리</span>'

    st.markdown('<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px;">' + _status_span + '</div>', unsafe_allow_html=True)

    badge_items = []
    if summ_d.get("일정"):
        badge_items.append('<span style="background:#eff6ff;color:#1d4ed8;border-radius:6px;padding:3px 10px;font-size:0.78rem;font-weight:700;">📅 ' + summ_d["일정"] + '</span>')
    if summ_d.get("목적"):
        badge_items.append('<span style="background:#f0fdf4;color:#15803d;border-radius:6px;padding:3px 10px;font-size:0.78rem;font-weight:700;">🎯 ' + summ_d["목적"] + '</span>')
    if summ_d.get("담당자"):
        badge_items.append('<span style="background:#faf5ff;color:#7e22ce;border-radius:6px;padding:3px 10px;font-size:0.78rem;font-weight:700;">👤 ' + summ_d["담당자"] + '</span>')
    if summ_d.get("운송"):
        badge_items.append(f'<span style="background:#fff7ed;color:#c2410c;border-radius:6px;padding:4px 12px;font-size:0.8rem;font-weight:700;">🚚 {summ_d["운송"]}</span>')

    if badge_items:
        st.markdown(
            '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;">' + "".join(badge_items) + '</div>',
            unsafe_allow_html=True,
        )

    if summ_d.get("품목"):
        items_clean = clean_slack_text(order_p["parsed"].get("품목", ""))
        item_lines = [l.strip() for l in items_clean.split("\n") if l.strip()]
        rows_html = "".join(
            f'<tr><td style="padding:5px 0;font-size:0.83rem;color:#374151;border-bottom:1px solid #f3f4f6;">📦 {l}</td></tr>'
            for l in item_lines
        )
        st.markdown(
            f'<div class="card" style="margin-bottom:10px;"><div class="card-title">품목</div>'
            f'<table style="width:100%;border-collapse:collapse;">{rows_html}</table></div>',
            unsafe_allow_html=True,
        )

    # ── 상태 변경 버튼 ──────────────────────────────────────
    st.markdown('<div class="section-header" style="margin-top:12px;">🏷 처리 상태 변경</div>', unsafe_allow_html=True)
    _ts_p  = order_p["ts"]
    _sl_ch_p = st.session_state.get("slack_channel_id")
    _sl_tk_p = st.session_state.get("slack_token")
    _btn1, _btn2, _btn3 = st.columns(3, gap="small")

    def _toggle_reaction(emoji_name: str):
        """이미 달린 reaction이면 제거, 없으면 추가"""
        try:
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError
            _rc = WebClient(token=_sl_tk_p)
            existing = [r["name"] for r in order_p.get("reactions", [])]
            if emoji_name in existing:
                _rc.reactions_remove(channel=_sl_ch_p, timestamp=_ts_p, name=emoji_name)
            else:
                _rc.reactions_add(channel=_sl_ch_p, timestamp=_ts_p, name=emoji_name)
            st.session_state.pop("sb_slack_orders", None)
        except SlackApiError as _e:
            _err = _e.response['error']
            if _err == "missing_scope":
                st.error("권한 부족: Slack 앱에 **reactions:write** 스코프를 추가해야 합니다.\napi.slack.com → 앱 → OAuth & Permissions → Bot Token Scopes에서 추가 후 재설치")
            elif _err == "already_reacted":
                st.session_state.pop("sb_slack_orders", None)
                st.rerun()
            else:
                st.error(f"Slack 오류: {_err}")

    _cur_reactions = [r["name"] for r in order_p.get("reactions", [])]

    with _btn1:
        _wip_active = "hourglass_flowing_sand" in _cur_reactions
        if st.button(
            "✅ 처리중" if _wip_active else "⏳ 처리중",
            key=f"btn_wip_{sel_idx_p}",
            use_container_width=True,
            type="primary" if _wip_active else "secondary",
        ):
            _toggle_reaction("hourglass_flowing_sand")
            st.rerun()

    with _btn2:
        _done_active = "white_check_mark" in _cur_reactions
        if st.button(
            "✅ 완료됨" if _done_active else "☑️ 완료",
            key=f"btn_done_{sel_idx_p}",
            use_container_width=True,
            type="primary" if _done_active else "secondary",
        ):
            _toggle_reaction("white_check_mark")
            st.rerun()

    with _btn3:
        _cancel_active = "x" in _cur_reactions
        if st.button(
            "✅ 반려됨" if _cancel_active else "❌ 반려",
            key=f"btn_cancel_{sel_idx_p}",
            use_container_width=True,
            type="primary" if _cancel_active else "secondary",
        ):
            _toggle_reaction("x")
            st.rerun()

    # 첨부파일
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown('<div class="section-header">📎 첨부파일</div>', unsafe_allow_html=True)
    excel_files_p = order_p["files"]
    if not excel_files_p:
        st.caption("이 메시지에 Excel 첨부파일이 없습니다.")
    else:
        for fi, f in enumerate(excel_files_p):
            size_kb = round(f["size"] / 1024, 1) if f.get("size") else "?"

            _cache_key = f"preview_bytes_{sel_idx_p}_{fi}"

            st.markdown(
                f'<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;'
                f'padding:8px 12px;font-size:0.82rem;font-weight:600;color:#374151;margin-bottom:6px;">'
                f'📊 {f["name"]} &nbsp;<span style="font-size:0.72rem;color:#9ca3af;font-weight:400;">{size_kb} KB</span></div>',
                unsafe_allow_html=True,
            )
            fb, fc, fd = st.columns(3, gap="small")
            with fb:
                if st.button("👁 미리보기", key=f"preview_{sel_idx_p}_{fi}", use_container_width=True):
                    with st.spinner("불러오는 중..."):
                        _bytes = download_slack_file(f["url"], slack_token_p)
                    st.session_state[_cache_key] = _bytes
            with fc:
                if st.button("👁 원본보기", key=f"preview2_{sel_idx_p}_{fi}", use_container_width=True):
                    with st.spinner("불러오는 중..."):
                        _bytes = download_slack_file(f["url"], slack_token_p)
                    st.session_state[_cache_key] = _bytes
            with fd:
                if st.button("🔄 변환", key=f"load_conv_{sel_idx_p}_{fi}", use_container_width=True, type="primary"):
                    with st.spinner("다운로드 중..."):
                        _d = st.session_state.get(_cache_key) or download_slack_file(f["url"], slack_token_p)
                    st.session_state[_cache_key] = _d
                    st.session_state[f"do_convert_{sel_idx_p}_{fi}"] = True

            _preview_bytes = st.session_state.get(_cache_key)
            if _preview_bytes:
                try:
                    _xl = pd.ExcelFile(io.BytesIO(_preview_bytes))
                    _sheet = st.selectbox("시트", _xl.sheet_names,
                                          key=f"preview_sheet_{sel_idx_p}_{fi}")
                    _prev_df = pd.read_excel(io.BytesIO(_preview_bytes), sheet_name=_sheet, dtype=str)
                    st.dataframe(_prev_df, use_container_width=True,
                                 height=min(60 + len(_prev_df) * 32, 240))
                    st.download_button(
                        f"⬇️ {f['name']} 원본 다운로드",
                        data=_preview_bytes,
                        file_name=f["name"],
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_{sel_idx_p}_{fi}",
                        use_container_width=True,
                    )
                except Exception as _pe:
                    st.error(f"미리보기 오류: {_pe}")

            # ── 인라인 변환 + 전송 ──────────────────────────────
            if st.session_state.get(f"do_convert_{sel_idx_p}_{fi}") and _preview_bytes:
                st.markdown('<hr style="margin:10px 0;">', unsafe_allow_html=True)
                st.markdown('<div class="section-header">🔄 변환 & 전송</div>', unsafe_allow_html=True)

                _fmt = st.radio("양식 선택", ["📄 일반 (컬리 등)", "🛒 네이버"],
                                horizontal=True, key=f"fmt_{sel_idx_p}_{fi}",
                                label_visibility="collapsed")
                _thresh_s = st.slider("유사도 임계값", 40, 100, 70, format="%d%%",
                                      key=f"thresh_s_{sel_idx_p}_{fi}", label_visibility="collapsed")

                _mb = st.session_state.get("master_bytes")
                _master_df_s = None
                if _mb:
                    _master_df_s = load_master_from_bytes(_mb)
                elif DEFAULT_MASTER:
                    _master_df_s = load_master(DEFAULT_MASTER)

                if _master_df_s is None:
                    st.warning("← 사이드바에서 마스터 파일을 연결해주세요.")
                else:
                    _mc = list(_master_df_s.columns)
                    _m_sku  = next((c for c in _mc if c.strip() == "SKU"), _mc[0])
                    _m_name = next((c for c in _mc if "제품명" in c or "상품명" in c), _mc[2])
                    _m_price= next((c for c in _mc if "구매가" in c), None)
                    _mlookup = {}
                    for _, _r in _master_df_s.iterrows():
                        _orig = str(_r[_m_name]); _norm = normalize(_orig)
                        _mlookup[_norm] = (_r[_m_sku], _r[_m_price] if _m_price else "", _orig)

                    _staged_key = f"slack_staged_{sel_idx_p}_{fi}"
                    _detail_key = f"slack_detail_{sel_idx_p}_{fi}"

                    st.caption(f"마스터 파일: {len(_mlookup)}개 항목 로드됨 · 파일: {len(_preview_bytes)//1024}KB")

                    _map_key = f"slack_map_{sel_idx_p}_{fi}"

                    if _fmt == "🛒 네이버":
                        # 네이버: 변환 시작 버튼 하나로 즉시 매핑
                        _run_s = st.button("🔄 변환 시작", type="primary",
                                           use_container_width=True, key=f"run_s_{sel_idx_p}_{fi}")
                        if _run_s:
                            _raw_map_s = []
                            try:
                                _ndf, _n2s = load_naver(io.BytesIO(_preview_bytes))
                                _prog_s = st.progress(0, text="변환 중...")
                                _rows_s = list(_ndf.iterrows())
                                for _ii, (_, _rr) in enumerate(_rows_s):
                                    _sku_s, _ = resolve_naver_sku(_rr["SKU_원본"], _rr["상품명"], _n2s, _mlookup)
                                    try: _qty_s = int(float(_rr["수량"])) if pd.notna(_rr["수량"]) else 0
                                    except: _qty_s = 0
                                    _matched_name = next((v[2] for v in _mlookup.values() if v[0] == _sku_s), "(건너뜀)")
                                    _ok = not _sku_s.startswith("UNKNOWN")
                                    _raw_map_s.append({"원본 상품명": _rr["상품명"], "수량": _qty_s,
                                                        "마스터 매핑": _matched_name if _ok else "(건너뜀)",
                                                        "유사도": "✅" if _ok else "❌"})
                                    _prog_s.progress((_ii+1)/len(_rows_s))
                                _prog_s.empty()
                            except Exception as _ex:
                                st.error(f"네이버 파일 오류: {_ex}")
                            if _raw_map_s:
                                st.session_state[_map_key] = _raw_map_s
                                st.session_state.pop(_staged_key, None)
                                st.rerun()
                            else:
                                st.warning("매칭된 항목이 없습니다.")
                    else:
                        # 일반: 시트·컬럼 선택 UI는 항상 표시 (rerun해도 유지)
                        try:
                            _xf = pd.ExcelFile(io.BytesIO(_preview_bytes))
                            _sheets = _xf.sheet_names
                            _sheet_sel = st.selectbox("📋 시트 선택", _sheets, key=f"sheet_{sel_idx_p}_{fi}")
                            _odf = pd.read_excel(io.BytesIO(_preview_bytes), sheet_name=_sheet_sel)
                            _ocols = list(_odf.columns)
                            _nc_auto = next((c for c in _ocols if str(c).strip() == "상품명"), None) or \
                                       next((c for c in _ocols if "상품명" in str(c) or "품명" in str(c) or "제품명" in str(c)), _ocols[0])
                            _qc_auto = next((c for c in _ocols if str(c).strip() == "수량"), None) or \
                                       next((c for c in _ocols if "수량" in str(c) or "qty" in str(c).lower()), _ocols[1] if len(_ocols)>1 else _ocols[0])
                            # 시트명을 키에 포함 → 시트 바뀌면 새 selectbox로 초기화
                            _safe_sheet = str(_sheet_sel).replace(" ", "_")[:30]
                            _nc_key = f"nc_{sel_idx_p}_{fi}_{_safe_sheet}"
                            _qc_key = f"qc_{sel_idx_p}_{fi}_{_safe_sheet}"
                            st.dataframe(_odf.head(3), use_container_width=True, hide_index=True)
                            st.caption("📌 올바른 컬럼을 선택한 후 매핑 시작 버튼을 누르세요.")
                            _col1, _col2, _col3 = st.columns([2, 2, 1])
                            with _col1:
                                _nc = st.selectbox("📦 상품명 컬럼", _ocols, index=_ocols.index(_nc_auto), key=_nc_key)
                            with _col2:
                                _qc = st.selectbox("🔢 수량 컬럼", _ocols, index=_ocols.index(_qc_auto), key=_qc_key)
                            with _col3:
                                st.write("")
                                st.write("")
                                _do_map = st.button("▶ 매핑 시작", key=f"domap_{sel_idx_p}_{fi}", type="primary", use_container_width=True)
                            if _do_map:
                                _raw_map_s = []
                                _odf[_qc] = pd.to_numeric(_odf[_qc], errors='coerce').fillna(1)
                                _grp = _odf.groupby(_nc)[_qc].sum().reset_index().rename(columns={_nc:"상품명",_qc:"수량"})
                                _nnames = list(_mlookup.keys())
                                _prog_s = st.progress(0, text="매칭 중...")
                                for _ii, (_, _rr) in enumerate(_grp.iterrows()):
                                    _best, _score = best_match(_rr["상품명"], _nnames)
                                    _ok = _score >= _thresh_s
                                    _matched_name = _mlookup[_best][2] if _ok else "(건너뜀)"
                                    try:
                                        _qty_val = int(float(str(_rr["수량"]).replace(',',''))) if pd.notna(_rr["수량"]) else 1
                                    except (ValueError, TypeError):
                                        _qty_val = 1
                                    _raw_map_s.append({"원본 상품명": _rr["상품명"], "수량": _qty_val,
                                                       "마스터 매핑": _matched_name,
                                                       "유사도": f"{_score}%" if _ok else f"{_score}% ❌"})
                                    _prog_s.progress((_ii+1)/len(_grp))
                                _prog_s.empty()
                                if _raw_map_s:
                                    st.session_state[_map_key] = _raw_map_s
                                    st.session_state.pop(_staged_key, None)
                                    st.rerun()
                                else:
                                    st.warning("매칭된 항목이 없습니다. 임계값을 낮추거나 양식을 확인해주세요.")
                        except Exception as _ex:
                            st.error(f"파일 오류: {_ex}")

                    # ── 매핑 확인 테이블
                    _raw_map_s = st.session_state.get(_map_key)
                    if _raw_map_s:
                        _all_mnames = ["(건너뜀)"] + [v[2] for v in _mlookup.values()]
                        _mh1, _mh2 = st.columns([4, 1])
                        with _mh1:
                            st.markdown("**🔧 매핑 확인** — 마스터 매핑 열 클릭해서 수정")
                        with _mh2:
                            if st.button("🔄 다시 변환", key=f"reset_s_{sel_idx_p}_{fi}", use_container_width=True):
                                st.session_state.pop(_map_key, None)
                                st.session_state.pop(_staged_key, None)
                                st.rerun()
                        _map_df_s = pd.DataFrame(_raw_map_s)
                        _edited_map_s = st.data_editor(
                            _map_df_s,
                            use_container_width=True, hide_index=True,
                            height=min(60 + len(_map_df_s) * 38, 400),
                            column_config={
                                "원본 상품명": st.column_config.TextColumn("원본 상품명 (수정 가능)", width="medium"),
                                "수량":        st.column_config.NumberColumn("수량", width="small"),
                                "마스터 매핑": st.column_config.SelectboxColumn("마스터 매핑 (클릭해서 변경)", options=_all_mnames, width="large"),
                                "유사도":      st.column_config.TextColumn("유사도", disabled=True, width="small"),
                            },
                            key=f"slack_map_editor_{sel_idx_p}_{fi}",
                        )
                        if st.button("🔍 수정된 상품명으로 재매핑", key=f"remap_s_{sel_idx_p}_{fi}"):
                            _nnames2 = list(_mlookup.keys())
                            _new_map = []
                            for _, _rr2 in _edited_map_s.iterrows():
                                _best2, _score2 = best_match(str(_rr2["원본 상품명"]), _nnames2)
                                _ok2 = _score2 >= _thresh_s
                                _new_map.append({
                                    "원본 상품명": _rr2["원본 상품명"],
                                    "수량": _rr2["수량"],
                                    "마스터 매핑": _mlookup[_best2][2] if _ok2 else "(건너뜀)",
                                    "유사도": f"{_score2}%" if _ok2 else f"{_score2}% ❌",
                                })
                            st.session_state[_map_key] = _new_map
                            st.rerun()
                        _results_s = []
                        for _ri, _rrow in _edited_map_s.iterrows():
                            _ch = _rrow["마스터 매핑"]; _qty_s = _raw_map_s[_ri]["수량"]
                            if _ch and _ch != "(건너뜀)":
                                for _sv, _pv, _ov in _mlookup.values():
                                    if _ov == _ch:
                                        _results_s.append({"SKU": _sv, "수량": int(_qty_s), "단가": _pv})
                                        break
                        if _results_s:
                            _out_s = (pd.DataFrame(_results_s).groupby(["SKU","단가"], as_index=False)["수량"].sum())[["SKU","수량","단가"]]
                            st.session_state[_staged_key] = _out_s

                    # ── 스테이징 그리드
                    _staged_s = st.session_state.get(_staged_key)
                    if _staged_s is not None and not st.session_state.get(_map_key) is None:
                        st.markdown("---")
                    if _staged_s is not None:
                        _edited_s = st.data_editor(
                            _staged_s, use_container_width=True, num_rows="dynamic",
                            height=min(60 + len(_staged_s)*35, 280),
                            column_config={
                                "SKU":  st.column_config.TextColumn("SKU"),
                                "수량": st.column_config.NumberColumn("수량", min_value=0, step=1, format="%d"),
                                "단가": st.column_config.NumberColumn("단가", format="%.0f"),
                            },
                            key=f"slack_grid_{sel_idx_p}_{fi}",
                        )
                        _edited_s = _edited_s[_edited_s["수량"] > 0].reset_index(drop=True)

                        _dl_col, _api_col = st.columns(2, gap="small")
                        with _dl_col:
                            _buf_s = io.BytesIO()
                            with pd.ExcelWriter(_buf_s, engine="openpyxl") as _w:
                                _edited_s.to_excel(_w, sheet_name="변경양식", index=False)
                                st.session_state.get(_detail_key, _edited_s).to_excel(_w, sheet_name="매칭상세", index=False)
                            _buf_s.seek(0)
                            st.download_button("⬇️ 엑셀 다운로드", data=_buf_s,
                                file_name="출고_변경양식.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True, key=f"dl_out_{sel_idx_p}_{fi}")
                        with _api_col:
                            render_api_send_section(_edited_s, memo_key=f"slack_memo_{sel_idx_p}_{fi}")
