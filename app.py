import streamlit as st
from utils import (
    load_config, save_config, fetch_locations, fetch_all_items, add_log,
    post_transaction, _get_slack, GMAIL_SENDERS,
    gmail_auth_url, gmail_exchange_code, APP_CSS, DEFAULT_MASTER,
)
from urllib.parse import urlparse, parse_qs
import requests

# ── 페이지 설정 ──────────────────────────────────────────────
st.set_page_config(
    page_title="출고 라몬",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 저장된 설정 자동 로드 (최초 1회) ──────────────────────────
if "config_loaded" not in st.session_state:
    _cfg = load_config()

    # ── 박스히어로 자동 연결 ──
    _bh_token = _cfg.get("api_token", "")
    if _bh_token and "api_token" not in st.session_state:
        try:
            _locs = fetch_locations(_bh_token)
            st.session_state["api_token"]     = _bh_token
            st.session_state["api_locations"] = _locs
            _saved_loc_id = _cfg.get("selected_location_id")
            if _saved_loc_id:
                st.session_state["selected_location_id"] = _saved_loc_id
            elif _locs:
                st.session_state["selected_location_id"] = _locs[0]["id"]
        except Exception:
            pass

    # ── Slack 자동 연결 ──
    _sl_token = _cfg.get("slack_token", "")
    if _sl_token and "slack_token" not in st.session_state:
        try:
            WebClient, SlackApiError = _get_slack()
            _sl_client = WebClient(token=_sl_token)
            _sl_chs = {}
            _sl_cur = None
            while True:
                _sl_res = _sl_client.conversations_list(
                    types="public_channel,private_channel", limit=1000, cursor=_sl_cur
                )
                for ch in _sl_res["channels"]:
                    _sl_chs[ch["name"]] = ch["id"]
                _sl_cur = _sl_res.get("response_metadata", {}).get("next_cursor")
                if not _sl_cur:
                    break
            st.session_state["slack_token"]    = _sl_token
            st.session_state["slack_channels"] = _sl_chs
        except Exception:
            pass

    # ── Gmail 자동 연결 ──
    _gmail_token = _cfg.get("gmail_token")
    if _gmail_token and "gmail_token" not in st.session_state:
        st.session_state["gmail_token"] = _gmail_token

    st.session_state["config_loaded"] = True

# ── CSS ────────────────────────────────────────────────────
st.markdown(APP_CSS, unsafe_allow_html=True)

master_ok = bool(DEFAULT_MASTER)

# ── 사이드바 ─────────────────────────────────────────────────
with st.sidebar:
    # 로고
    st.markdown("""
    <div style="display:flex; align-items:center; gap:10px; padding:0.6rem 0 1.2rem 0; border-bottom:1px solid #2e2f45; margin-bottom:1rem;">
        <div style="background:#e84c4c; border-radius:8px; width:34px; height:34px;
                    display:flex; align-items:center; justify-content:center; font-size:1.1rem; flex-shrink:0;">📦</div>
        <div>
            <div style="color:#ffffff; font-weight:800; font-size:1rem; letter-spacing:-0.3px; line-height:1.2;">출고 라몬</div>
            <div style="color:#6b6e8a; font-size:0.7rem;">박스히어로 출고 변환기</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 변환 메뉴 ──
    _NAV_OPTIONS = ["📊  대시보드", "📄  일반 형식 (컬리 등)", "🛒  네이버 형식", "📨  출고 요청 (Slack)", "📧  출고 요청 (Gmail)"]
    _nav_idx = st.session_state.get("nav_target_idx", 0)
    if "nav_target_idx" in st.session_state:
        del st.session_state["nav_target_idx"]
    st.markdown('<div style="color:#6b6e8a; font-size:0.7rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px; padding-left:4px;">변환</div>', unsafe_allow_html=True)
    page = st.radio(
        "페이지 선택",
        _NAV_OPTIONS,
        index=_nav_idx,
        label_visibility="collapsed",
        key="nav_page",
    )

    st.markdown('<div style="border-top:1px solid #2e2f45; margin:1rem 0;"></div>', unsafe_allow_html=True)

    # ── 파일 설정 ──
    st.markdown('<div style="color:#6b6e8a; font-size:0.7rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px; padding-left:4px;">파일 설정</div>', unsafe_allow_html=True)

    with st.expander("📂  마스터 파일", expanded=not master_ok):
        if master_ok:
            st.caption("✅ 자동 연결: `박스 히어로 마스터 파일.xlsx`")
        else:
            st.caption("⚠️ 기본 파일 없음.")
        sidebar_master_upload = st.file_uploader(
            "다른 파일 사용 (선택)" if master_ok else "마스터 파일 업로드",
            type=["xlsx", "xls"],
            key="sidebar_master_up",
        )
        if sidebar_master_upload is not None:
            st.session_state["master_bytes"]    = sidebar_master_upload.read()
            st.session_state["master_filename"] = sidebar_master_upload.name
        if st.session_state.get("master_filename"):
            st.caption(f"📂 {st.session_state['master_filename']}")

    st.markdown('<div style="border-top:1px solid #2e2f45; margin:1rem 0;"></div>', unsafe_allow_html=True)

    # ── 연동 설정 ──
    st.markdown('<div style="color:#6b6e8a; font-size:0.7rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px; padding-left:4px;">연동</div>', unsafe_allow_html=True)

    # 박스히어로 API
    bh_connected = bool(st.session_state.get("api_token"))
    bh_label = "📦  박스히어로  ✅" if bh_connected else "📦  박스히어로"
    with st.expander(bh_label, expanded=not bh_connected):
        api_token = st.text_input(
            "API 토큰",
            type="password",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            help="박스히어로 설정 > API에서 발급",
            key="api_token_input",
        )
        connect_btn = st.button("🔗 연결", use_container_width=True, key="bh_connect")
        if connect_btn and api_token:
            try:
                with st.spinner("연결 중..."):
                    locs = fetch_locations(api_token)
                st.session_state["api_token"]     = api_token
                st.session_state["api_locations"] = locs
                save_config({"api_token": api_token})
                st.success(f"✅ {len(locs)}개 위치 연결됨")
                st.rerun()
            except Exception as e:
                st.error(f"연결 실패: {e}")

        if st.session_state.get("api_token"):
            if st.button("🔓 연결 해제", use_container_width=True, key="bh_disconnect"):
                for k in ["api_token", "api_locations", "selected_location_id"]:
                    st.session_state.pop(k, None)
                save_config({"api_token": ""})
                st.rerun()

    # ── 출고 위치 선택 (expander 밖 — 항상 표시) ──
    if st.session_state.get("api_token"):
        if not st.session_state.get("api_locations"):
            try:
                st.session_state["api_locations"] = fetch_locations(st.session_state["api_token"])
            except Exception:
                pass
        _locs = st.session_state.get("api_locations", [])
        if _locs:
            _loc_options = {l["name"]: l["id"] for l in _locs}
            _loc_names = list(_loc_options.keys())
            _saved_id = st.session_state.get("selected_location_id")
            _default_idx = 0
            if _saved_id:
                for _i, _n in enumerate(_loc_names):
                    if _loc_options[_n] == _saved_id:
                        _default_idx = _i
                        break
            _selected = st.selectbox("📍 출고 위치", _loc_names, index=_default_idx, key="loc_select")
            _sel_id = _loc_options[_selected]
            if st.session_state.get("selected_location_id") != _sel_id:
                st.session_state["selected_location_id"] = _sel_id
                save_config({"selected_location_id": _sel_id, "selected_location_name": _selected})

    # Slack 연동
    slack_connected = bool(st.session_state.get("slack_token"))
    slack_label = "💬  Slack  ✅" if slack_connected else "💬  Slack"
    with st.expander(slack_label, expanded=not slack_connected):
        slack_token = st.text_input(
            "Bot Token",
            type="password",
            placeholder="xoxb-...",
            key="slack_token_input",
        )
        slack_connect_btn = st.button("🔗 연결", use_container_width=True, key="slack_connect")
        if slack_connect_btn and slack_token:
            try:
                WebClient, SlackApiError = _get_slack()
                client = WebClient(token=slack_token)
                channels = {}
                cursor = None
                while True:
                    result = client.conversations_list(
                        types="public_channel,private_channel",
                        limit=1000,
                        cursor=cursor,
                    )
                    for ch in result["channels"]:
                        channels[ch["name"]] = ch["id"]
                    cursor = result.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
                st.session_state["slack_token"]    = slack_token
                st.session_state["slack_channels"] = channels
                save_config({"slack_token": slack_token})
                st.success(f"✅ {len(channels)}개 채널 연결됨")
                st.rerun()
            except SlackApiError as e:
                st.error(f"연결 실패: {e.response['error']}")

        if st.session_state.get("slack_token"):
            if not st.session_state.get("slack_channels"):
                try:
                    WebClient, SlackApiError = _get_slack()
                    _client = WebClient(token=st.session_state["slack_token"])
                    _chs = {}
                    _cur = None
                    while True:
                        _res = _client.conversations_list(types="public_channel,private_channel", limit=1000, cursor=_cur)
                        for ch in _res["channels"]:
                            _chs[ch["name"]] = ch["id"]
                        _cur = _res.get("response_metadata", {}).get("next_cursor")
                        if not _cur:
                            break
                    st.session_state["slack_channels"] = _chs
                except Exception:
                    pass
            channels = st.session_state.get("slack_channels", {})
            if channels:
                _ch_list = list(channels.keys())
                _default_ch = "물류_출고"
                _default_idx = _ch_list.index(_default_ch) if _default_ch in _ch_list else 0
                selected_ch = st.selectbox("채널 선택", _ch_list, index=_default_idx, key="slack_channel_select")
                st.session_state["slack_channel_id"] = channels[selected_ch]
            if st.button("🔓 해제", use_container_width=True, key="slack_disconnect"):
                for k in ["slack_token", "slack_channels", "slack_channel_id"]:
                    st.session_state.pop(k, None)
                save_config({"slack_token": ""})
                st.rerun()

    # Gmail 연동
    gmail_connected = bool(st.session_state.get("gmail_token"))
    gmail_label = "📧  Gmail  ✅" if gmail_connected else "📧  Gmail"
    with st.expander(gmail_label, expanded=not gmail_connected):
        if not gmail_connected:
            st.caption("Google Cloud Console에서 OAuth 클라이언트 ID를 생성하세요.")
            gmail_client_id = st.text_input(
                "Client ID",
                type="password",
                placeholder="xxxx.apps.googleusercontent.com",
                key="gmail_client_id_input",
            )
            gmail_client_secret = st.text_input(
                "Client Secret",
                type="password",
                placeholder="GOCSPX-...",
                key="gmail_client_secret_input",
            )
            if st.button("🔗 인증 URL 생성", use_container_width=True, key="gmail_gen_url"):
                if gmail_client_id and gmail_client_secret:
                    try:
                        url = gmail_auth_url(gmail_client_id.strip(), gmail_client_secret.strip())
                        st.session_state["gmail_auth_url"] = url
                        save_config({"gmail_client_id": gmail_client_id.strip(), "gmail_client_secret": gmail_client_secret.strip()})
                    except Exception as e:
                        st.error(f"오류: {e}")
                else:
                    st.warning("Client ID와 Secret을 입력해주세요.")

            if st.session_state.get("gmail_auth_url"):
                st.markdown(f"**1단계:** 아래 링크를 클릭해서 Google 계정 인증")
                st.markdown(f"[🔑 Google 인증 페이지 열기]({st.session_state['gmail_auth_url']})")
                st.markdown("**2단계:** 인증 후 주소창의 전체 URL을 복사해서 붙여넣기")
                st.caption("예: `http://localhost/?code=4/0A...&scope=...`")
                redirect_url = st.text_input(
                    "리다이렉트 URL 붙여넣기",
                    placeholder="http://localhost/?code=...",
                    key="gmail_redirect_url_input",
                )
                if st.button("✅ 연결 완료", use_container_width=True, key="gmail_connect"):
                    if redirect_url:
                        try:
                            parsed = urlparse(redirect_url)
                            code = parse_qs(parsed.query).get("code", [None])[0]
                            if not code:
                                st.error("URL에서 code를 찾을 수 없습니다.")
                            else:
                                with st.spinner("인증 중..."):
                                    token_info = gmail_exchange_code(code)
                                st.session_state["gmail_token"] = token_info
                                st.session_state.pop("gmail_auth_url", None)
                                save_config({"gmail_token": token_info})
                                st.success("✅ Gmail 연결됨")
                                st.rerun()
                        except Exception as e:
                            st.error(f"연결 실패: {e}")
                    else:
                        st.warning("URL을 입력해주세요.")
        else:
            token_info = st.session_state["gmail_token"]
            st.caption(f"✅ 연결됨 (client: `{str(token_info.get('client_id',''))[:20]}...`)")
            st.caption(f"대상: {', '.join(GMAIL_SENDERS)}")
            if st.button("🔓 해제", use_container_width=True, key="gmail_disconnect"):
                for k in ["gmail_token", "gmail_auth_url"]:
                    st.session_state.pop(k, None)
                save_config({"gmail_token": None})
                st.rerun()

    st.markdown('<div style="border-top:1px solid #2e2f45; margin:1rem 0;"></div>', unsafe_allow_html=True)

    # ── 빠른 전송 (스테이징 데이터가 있고 API 연결된 경우) ──
    _api_token_sb   = st.session_state.get("api_token")
    _from_loc_sb    = st.session_state.get("selected_location_id")
    _staged_g       = st.session_state.get("general_staged_df")
    _staged_n       = st.session_state.get("naver_staged_df")

    if _api_token_sb and (_staged_g is not None or _staged_n is not None):
        st.markdown('<div style="color:#6b6e8a; font-size:0.7rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px; padding-left:4px;">빠른 전송</div>', unsafe_allow_html=True)

        _send_options = {}
        if _staged_g is not None:
            _g_sku = len(_staged_g); _g_qty = int(_staged_g["수량"].sum())
            _send_options[f"📄 일반 ({_g_sku}종 / {_g_qty}개)"] = _staged_g
        if _staged_n is not None:
            _n_sku = len(_staged_n); _n_qty = int(_staged_n["수량"].sum())
            _send_options[f"🛒 네이버 ({_n_sku}종 / {_n_qty}개)"] = _staged_n

        _sel_label = st.selectbox(
            "전송할 데이터",
            list(_send_options.keys()),
            key="sb_send_sel",
            label_visibility="collapsed",
        )
        _send_df = _send_options[_sel_label]

        _sku_count = len(_send_df)
        _qty_total = int(_send_df["수량"].sum())
        st.markdown(
            f'<div style="background:#1a1d27;border-radius:8px;padding:8px 12px;margin:6px 0;font-size:0.78rem;">'
            f'<span style="color:#9ca3af;">SKU</span> <b style="color:#e5e7eb;">{_sku_count}종</b>'
            f'&nbsp;&nbsp;<span style="color:#9ca3af;">수량</span> <b style="color:#10b981;">{_qty_total}개</b></div>',
            unsafe_allow_html=True,
        )

        _sb_memo = st.text_input(
            "메모",
            placeholder="예) 컬리 4월 출고",
            key="sb_memo",
            label_visibility="collapsed",
        )

        if st.button("🚀 박스히어로 출고 전송", type="primary", use_container_width=True, key="sb_quick_send"):
            try:
                with st.spinner("상품 목록 조회 중..."):
                    sku_to_id = fetch_all_items(_api_token_sb)

                items_payload = []
                missing = []
                for _, row in _send_df.iterrows():
                    sku = str(row["SKU"]).strip()
                    qty = int(row["수량"])
                    item_id = sku_to_id.get(sku)
                    if item_id:
                        items_payload.append({"item_id": item_id, "quantity": -qty})
                    else:
                        missing.append(sku)

                if missing:
                    st.warning(f"⚠️ SKU 미등록 {len(missing)}건")
                    for sku in missing:
                        add_log("warning", f"SKU 미등록: {sku}", source="sidebar")

                if not items_payload:
                    st.error("전송할 항목이 없습니다.")
                else:
                    _payload = {
                        "type": "sale",
                        "to_location_id": int(_from_loc_sb),
                        "items": items_payload,
                        "memo": _sb_memo or "",
                    }
                    with st.spinner("전송 중..."):
                        _result = post_transaction(_api_token_sb, _payload)
                    _tx_id = _result.get("id", "")
                    st.success(f"✅ 완료! ID: `{_tx_id}`")
                    add_log("success",
                            f"출고 전송 완료 ({len(items_payload)}종, {_qty_total}개)",
                            f"트랜잭션 ID: {_tx_id} | 메모: {_sb_memo}",
                            payload=_payload, source="sidebar")
            except requests.HTTPError as e:
                err_msg = f"{e.response.status_code} — {e.response.text[:150]}"
                st.error(f"API 오류: {err_msg}")
                add_log("error", f"전송 실패: {err_msg}", source="sidebar")
            except Exception as e:
                st.error(f"오류: {e}")
                add_log("error", f"전송 오류: {str(e)[:80]}", source="sidebar")

        st.markdown('<div style="border-top:1px solid #2e2f45; margin:1rem 0;"></div>', unsafe_allow_html=True)

    st.caption("⚠️ 토큰은 비밀번호입니다. 타인과 공유하지 마세요.")


# ── 페이지 헤더 ──────────────────────────────────────────────
page_meta = {
    "📊  대시보드":            ("대시보드",    "파이프라인 상태 · 전송 이력 · 오류 로그"),
    "📄  일반 형식 (컬리 등)": ("일반 형식",   "컬리 등 일반 출고 파일 → 박스히어로 변경양식 변환"),
    "🛒  네이버 형식":         ("네이버 형식", "네이버 출고 파일 → 박스히어로 변경양식 변환"),
    "📨  출고 요청 (Slack)":   ("출고 요청",   "Slack 채널에서 출고 요청 확인 및 파일 로드"),
    "📧  출고 요청 (Gmail)":   ("Gmail 출고 요청", "Gmail에서 출고 요청 메일 확인 및 파일 로드"),
}
p_title, p_desc = page_meta[page]
st.markdown(f'<div class="page-header"><div class="page-title">{p_title}</div><div class="page-desc">{p_desc}</div></div>', unsafe_allow_html=True)

# ── 페이지 라우팅 ─────────────────────────────────────────────
if page == "📊  대시보드":
    exec(open("pages/1_대시보드.py", encoding="utf-8").read())
elif page == "📄  일반 형식 (컬리 등)":
    exec(open("pages/2_일반형식.py", encoding="utf-8").read())
elif page == "🛒  네이버 형식":
    exec(open("pages/3_네이버형식.py", encoding="utf-8").read())
elif page == "📨  출고 요청 (Slack)":
    exec(open("pages/4_Slack출고요청.py", encoding="utf-8").read())
elif page == "📧  출고 요청 (Gmail)":
    exec(open("pages/5_Gmail출고요청.py", encoding="utf-8").read())
