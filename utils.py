import streamlit as st
import pandas as pd
import io
import os
import re
import json
import datetime
import requests
import base64
import email.utils
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs
from rapidfuzz import fuzz

# 무거운 패키지는 지연 로딩 (시작 메모리 절약)
def _get_slack():
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    return WebClient, SlackApiError

WebClient, SlackApiError = _get_slack()

def _get_google():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    return Credentials, GoogleAuthRequest, Flow, build

Credentials, GoogleAuthRequest, Flow, build = _get_google()

BASE_URL = "https://rest.boxhero-app.com"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_SENDERS = ["inn1246919@nate.com", "gy.lee12@cj.net", "lgl10910@lglpartner.com"]

DEFAULT_MASTER = r"C:\Users\User\OneDrive\Desktop\업무\AI 실험\박스 히어로 마스터 파일.xlsx"
if not os.path.exists(DEFAULT_MASTER):
    DEFAULT_MASTER = ""


def load_config() -> dict:
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    try:
        for key in ["api_token", "slack_token", "selected_location_id",
                    "selected_location_name", "gmail_client_id", "gmail_client_secret"]:
            if key not in cfg and key in st.secrets:
                cfg[key] = st.secrets[key]
        if "gmail_token" not in cfg and "gmail_token" in st.secrets:
            gt = st.secrets["gmail_token"]
            cfg["gmail_token"] = {
                "token":         gt.get("token", ""),
                "refresh_token": gt.get("refresh_token", ""),
                "token_uri":     gt.get("token_uri", "https://oauth2.googleapis.com/token"),
                "client_id":     gt.get("client_id", ""),
                "client_secret": gt.get("client_secret", ""),
                "scopes":        list(gt.get("scopes", ["https://www.googleapis.com/auth/gmail.readonly"])),
            }
    except Exception:
        pass
    return cfg


def save_config(data: dict):
    cfg = load_config()
    cfg.update(data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def api_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@st.cache_data(ttl=300)
def fetch_all_items(token: str) -> dict:
    """SKU → item_id 매핑 (페이지네이션 처리)"""
    sku_to_id = {}
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE_URL}/v1/items", headers=api_headers(token), params=params)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            sku = str(item.get("sku", "")).strip()
            if sku:
                sku_to_id[sku] = item["id"]
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return sku_to_id


@st.cache_data(ttl=300)
def fetch_locations(token: str) -> list:
    """locations 목록 반환"""
    r = requests.get(f"{BASE_URL}/v1/locations", headers=api_headers(token))
    r.raise_for_status()
    return r.json().get("items", [])


@st.cache_data(ttl=300)
def fetch_partners(token: str) -> list:
    """거래처 목록 반환 (페이지네이션)"""
    items, _cursor = [], None
    while True:
        params = {"limit": 100}
        if _cursor:
            params["cursor"] = _cursor
        r = requests.get(f"{BASE_URL}/v1/partners", headers=api_headers(token), params=params)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        _cursor = data.get("cursor")
    return items


def post_transaction(token: str, payload: dict) -> dict:
    r = requests.post(f"{BASE_URL}/v1/location-txs",
                      headers=api_headers(token), json=payload)
    r.raise_for_status()
    return r.json()


def reaction_to_status(reactions: list[dict]) -> dict | None:
    """Slack reactions → 업무 상태 변환. 가장 의미있는 상태 1개 반환."""
    STATUS_MAP = {
        "완료": ("완료", "#10b981", "#d1fae5"),
        "white_check_mark": ("완료", "#10b981", "#d1fae5"),
        "heavy_check_mark": ("완료", "#10b981", "#d1fae5"),
        "check": ("완료", "#10b981", "#d1fae5"),
        "100": ("완료", "#10b981", "#d1fae5"),
        "done": ("완료", "#10b981", "#d1fae5"),
        "진행중": ("진행중", "#f59e0b", "#fef3c7"),
        "hourglass_flowing_sand": ("진행중", "#f59e0b", "#fef3c7"),
        "hourglass": ("진행중", "#f59e0b", "#fef3c7"),
        "arrows_counterclockwise": ("진행중", "#f59e0b", "#fef3c7"),
        "loading": ("진행중", "#f59e0b", "#fef3c7"),
        "spinner": ("진행중", "#f59e0b", "#fef3c7"),
        "x": ("반려", "#ef4444", "#fee2e2"),
        "negative_squared_cross_mark": ("반려", "#ef4444", "#fee2e2"),
        "반려": ("반려", "#ef4444", "#fee2e2"),
        "취소": ("취소", "#ef4444", "#fee2e2"),
        "eyes": ("확인중", "#6366f1", "#ede9fe"),
        "확인": ("확인중", "#6366f1", "#ede9fe"),
    }
    priority = ["완료", "반려", "취소", "진행중", "확인중"]
    found = {}
    for r in reactions:
        name = r["name"].lower()
        if name in STATUS_MAP:
            label, color, bg = STATUS_MAP[name]
            found[label] = (color, bg, r["count"])
    for p in priority:
        if p in found:
            return {"label": p, "color": found[p][0], "bg": found[p][1], "count": found[p][2]}
    return None


def clean_slack_text(text: str) -> str:
    """Slack 마크다운 제거 후 핵심 텍스트만 추출"""
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r":\w+:", "", text)
    text = re.sub(r"^\s*[\*\-•]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r":\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


def extract_summary_fields(parsed: dict) -> dict:
    """파싱된 섹션에서 핵심 값만 1줄로 추출"""
    summary = {}

    sched = clean_slack_text(parsed.get("일정", ""))
    dates = re.findall(r"\d{4}[./\-]\d{1,2}[./\-]\d{1,2}|\d{1,2}[./]\d{1,2}", sched)
    summary["일정"] = "  ".join(dates) if dates else (sched[:30] if sched else "")

    purpose = clean_slack_text(parsed.get("목적", ""))
    summary["목적"] = purpose.split("\n")[0][:30] if purpose else ""

    items_raw = clean_slack_text(parsed.get("품목", ""))
    item_lines = [l.strip() for l in items_raw.split("\n") if l.strip()]
    summary["품목"] = ", ".join(item_lines[:3]) + ("…" if len(item_lines) > 3 else "")

    contact = clean_slack_text(parsed.get("담당자", ""))
    name_m = re.search(r"[가-힣]{2,4}(?=\s|$|\d)", contact)
    summary["담당자"] = name_m.group() if name_m else contact.split("\n")[0][:20]

    ship = clean_slack_text(parsed.get("운송정보", ""))
    ship_m = re.search(r"택배|직배|퀵|화물|CJ|한진|롯데|우체국", ship)
    summary["운송"] = ship_m.group() if ship_m else ship.split("\n")[0][:20]

    return {k: v for k, v in summary.items() if v}


def parse_order_message(text: str) -> dict:
    """출고 요청 메시지 텍스트를 섹션별로 파싱"""
    info = {}
    lines = text.strip().split("\n")
    info["제목"] = lines[0].strip() if lines else ""

    section_patterns = {
        "목적":    r"1\).*?목적(.*?)(?=2\)|$)",
        "일정":    r"2\).*?일정(.*?)(?=3\)|$)",
        "품목":    r"3\).*?품목(.*?)(?=4\)|$)",
        "담당자":  r"4\).*?담당자.*?(.*?)(?=5\)|$)",
        "운송정보": r"5\).*?운송(.*?)(?=6\)|$)",
    }
    for key, pattern in section_patterns.items():
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            content = m.group(1).strip()
            lines_sec = [l.strip().lstrip("-•●*").strip() for l in content.split("\n") if l.strip()]
            info[key] = "\n".join(lines_sec)
    return info


def fetch_slack_orders(token: str, channel_id: str) -> tuple[list[dict], str]:
    """채널에서 출고 요청 메시지 목록 반환. (orders, debug_msg) 반환"""
    client = WebClient(token=token)
    orders = []
    debug = ""
    try:
        result = client.conversations_history(channel=channel_id, limit=100)
        all_msgs = result.get("messages", [])
        debug = f"총 {len(all_msgs)}개 메시지 조회됨"
        for msg in all_msgs:
            text = msg.get("text", "")
            has_excel = any(
                f.get("name", "").endswith((".xlsx", ".xls"))
                for f in msg.get("files", [])
            )
            if "출고" not in text and not has_excel:
                continue
            excel_files = [
                {
                    "name": f.get("name", ""),
                    "url":  f.get("url_private_download"),
                    "size": f.get("size", 0),
                }
                for f in msg.get("files", [])
                if f.get("name", "").endswith((".xlsx", ".xls"))
            ]
            parsed = parse_order_message(text)
            ts = float(msg.get("ts", 0))
            dt = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
            raw_reactions = msg.get("reactions", [])
            reactions = [{"name": r["name"], "count": r["count"]} for r in raw_reactions]
            orders.append({
                "ts":        msg.get("ts", ""),
                "dt":        dt,
                "title":     parsed.get("제목", text[:40]) if text else "(파일만 첨부)",
                "parsed":    parsed,
                "files":     excel_files,
                "raw":       text,
                "reactions": reactions,
            })
    except SlackApiError as e:
        err = e.response['error']
        if err == "not_in_channel":
            debug = "not_in_channel"
        elif err == "channel_not_found":
            debug = "channel_not_found"
        else:
            debug = f"API 오류: {err}"
    orders.sort(key=lambda x: x["ts"], reverse=True)
    return orders, debug


def download_slack_file(url: str, token: str) -> bytes:
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.content


def render_slack_loader(file_key: str) -> bytes | None:
    """Slack 출고 요청 목록 UI. 선택된 파일 bytes 반환."""
    slack_token = st.session_state.get("slack_token")
    channel_id  = st.session_state.get("slack_channel_id")
    if not slack_token or not channel_id:
        return None

    with st.expander("📨 Slack 출고 요청에서 불러오기", expanded=False):
        if st.button("🔄 목록 새로고침", key=f"slack_refresh_{file_key}"):
            st.session_state[f"slack_orders_{file_key}"], st.session_state[f"slack_debug_{file_key}"] = fetch_slack_orders(slack_token, channel_id)

        if f"slack_orders_{file_key}" not in st.session_state:
            with st.spinner("Slack 메시지 불러오는 중..."):
                st.session_state[f"slack_orders_{file_key}"], st.session_state[f"slack_debug_{file_key}"] = fetch_slack_orders(slack_token, channel_id)

        orders = st.session_state[f"slack_orders_{file_key}"]
        debug_msg = st.session_state.get(f"slack_debug_{file_key}", "")
        if debug_msg:
            st.caption(f"🔍 {debug_msg}")
        if not orders:
            st.caption("출고 요청 메시지가 없습니다.")
            return None

        order_labels = [f"{o['dt']}  {o['title'][:35]}" for o in orders]
        sel_idx = st.selectbox(
            "출고 요청 선택",
            range(len(order_labels)),
            format_func=lambda i: order_labels[i],
            key=f"slack_order_sel_{file_key}",
            label_visibility="collapsed",
        )
        order = orders[sel_idx]

        parsed = order["parsed"]
        summary_rows = []
        for key in ["목적", "일정", "품목", "담당자", "운송정보"]:
            val = parsed.get(key, "")
            if val:
                val_html = val.replace("\n", "<br>")
                summary_rows.append(f"<tr><td style='color:#8b8fa8;font-size:0.8rem;white-space:nowrap;padding:4px 12px 4px 0;vertical-align:top;'>{key}</td><td style='font-size:0.85rem;padding:4px 0;'>{val_html}</td></tr>")

        if summary_rows:
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse;margin:8px 0;'>{''.join(summary_rows)}</table>",
                unsafe_allow_html=True,
            )

        excel_files = order["files"]
        if not excel_files:
            st.caption("⚠️ 이 메시지에 Excel 첨부파일이 없습니다.")
            return None

        st.markdown("**📎 첨부파일**")
        file_labels = [f["name"] for f in excel_files]
        sel_file_idx = st.selectbox(
            "파일 선택",
            range(len(file_labels)),
            format_func=lambda i: file_labels[i],
            key=f"slack_file_sel_{file_key}",
            label_visibility="collapsed",
        )
        sel_file = excel_files[sel_file_idx]

        if st.button(f"⬇️ {sel_file['name']} 불러오기", key=f"slack_load_{file_key}", type="primary", use_container_width=True):
            try:
                with st.spinner("다운로드 중..."):
                    data = download_slack_file(sel_file["url"], slack_token)
                st.success(f"✅ {sel_file['name']} 로드 완료")
                return data
            except Exception as e:
                st.error(f"다운로드 실패: {e}")
    return None


# ── Gmail ────────────────────────────────────────────────────

def gmail_build_flow(client_id: str, client_secret: str) -> "Flow":
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=GMAIL_SCOPES, redirect_uri="http://localhost"
    )


def gmail_auth_url(client_id: str, client_secret: str) -> str:
    flow = gmail_build_flow(client_id, client_secret)
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    st.session_state["gmail_flow"] = flow
    return auth_url


def gmail_exchange_code(code: str) -> dict:
    flow = st.session_state.get("gmail_flow")
    if not flow:
        raise ValueError("인증 흐름이 만료되었습니다. URL을 다시 생성해주세요.")
    flow.fetch_token(code=code)
    creds = flow.credentials
    token_info = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or GMAIL_SCOPES),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    return token_info


def gmail_get_service(token_info: dict):
    expiry = None
    if token_info.get("expiry"):
        try:
            expiry = datetime.datetime.fromisoformat(token_info["expiry"])
        except Exception:
            pass

    creds = Credentials(
        token=token_info["token"],
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_info.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_info["client_id"],
        client_secret=token_info["client_secret"],
        scopes=token_info.get("scopes", GMAIL_SCOPES),
        expiry=expiry,
    )
    if (creds.expired or not creds.valid) and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        token_info["token"] = creds.token
        token_info["expiry"] = creds.expiry.isoformat() if creds.expiry else None
        save_config({"gmail_token": token_info})
        st.session_state["gmail_token"] = token_info
    return build("gmail", "v1", credentials=creds)


def _extract_gmail_parts(payload: dict) -> list:
    """메시지 payload에서 모든 파트를 재귀적으로 추출"""
    parts = []
    if "parts" in payload:
        for p in payload["parts"]:
            parts.extend(_extract_gmail_parts(p))
    else:
        parts.append(payload)
    return parts


def fetch_gmail_orders(token_info: dict) -> tuple[list[dict], str]:
    """3개 발신자에서 온 메일 목록 반환. 메시지 상세를 병렬로 조회해 속도 개선."""
    query = " OR ".join(f"from:{s}" for s in GMAIL_SENDERS)
    orders = []
    debug = ""
    try:
        service = gmail_get_service(token_info)
        result = service.users().messages().list(
            userId="me", q=query, maxResults=30
        ).execute()
        messages = result.get("messages", [])
        debug = f"총 {len(messages)}개 메일 조회됨"

        def _fetch_one(msg_ref):
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full",
                fields="id,payload"
            ).execute()
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            subject  = headers.get("Subject", "(제목 없음)")
            sender   = headers.get("From", "")
            date_str = headers.get("Date", "")
            dt = ""
            try:
                dt = email.utils.parsedate_to_datetime(date_str).strftime("%m-%d %H:%M")
            except Exception:
                dt = date_str[:16] if date_str else ""
            excel_files = []
            for part in _extract_gmail_parts(msg["payload"]):
                filename = part.get("filename", "")
                if filename.lower().endswith((".xlsx", ".xls")):
                    att_id = part.get("body", {}).get("attachmentId")
                    if att_id:
                        excel_files.append({
                            "name": filename,
                            "attachment_id": att_id,
                            "message_id": msg_ref["id"],
                        })
            return {
                "id":      msg_ref["id"],
                "dt":      dt,
                "subject": subject,
                "sender":  sender,
                "files":   excel_files,
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_one, m): m for m in messages}
            for fut in as_completed(futures):
                try:
                    orders.append(fut.result())
                except Exception:
                    pass

        orders.sort(key=lambda x: x["dt"], reverse=True)
    except Exception as e:
        debug = f"오류: {str(e)[:120]}"
    return orders, debug


def download_gmail_attachment(token_info: dict, message_id: str, attachment_id: str) -> bytes:
    service = gmail_get_service(token_info)
    att = service.users().messages().attachments().get(
        userId="me", messageId=message_id, id=attachment_id
    ).execute()
    return base64.urlsafe_b64decode(att["data"])


def render_gmail_loader(file_key: str) -> bytes | None:
    """Gmail 출고 메일 목록 UI. 선택된 첨부파일 bytes 반환."""
    token_info = st.session_state.get("gmail_token")
    if not token_info:
        return None

    with st.expander("📧 Gmail 출고 요청에서 불러오기", expanded=False):
        if st.button("🔄 목록 새로고침", key=f"gmail_refresh_{file_key}"):
            st.session_state.pop(f"gmail_orders_{file_key}", None)

        if f"gmail_orders_{file_key}" not in st.session_state:
            with st.spinner("Gmail 메일 불러오는 중..."):
                st.session_state[f"gmail_orders_{file_key}"], st.session_state[f"gmail_debug_{file_key}"] = fetch_gmail_orders(token_info)

        orders = st.session_state[f"gmail_orders_{file_key}"]
        debug_msg = st.session_state.get(f"gmail_debug_{file_key}", "")
        if debug_msg:
            st.caption(f"🔍 {debug_msg}")
        if not orders:
            st.caption("출고 관련 메일이 없습니다.")
            return None

        order_labels = [f"{o['dt']}  {o['subject'][:40]}" for o in orders]
        sel_idx = st.selectbox(
            "메일 선택",
            range(len(order_labels)),
            format_func=lambda i: order_labels[i],
            key=f"gmail_order_sel_{file_key}",
            label_visibility="collapsed",
        )
        order = orders[sel_idx]
        st.caption(f"발신: {order['sender']}")

        excel_files = order["files"]
        if not excel_files:
            st.caption("⚠️ 이 메일에 Excel 첨부파일이 없습니다.")
            return None

        st.markdown("**📎 첨부파일**")
        file_labels = [f["name"] for f in excel_files]
        sel_file_idx = st.selectbox(
            "파일 선택",
            range(len(file_labels)),
            format_func=lambda i: file_labels[i],
            key=f"gmail_file_sel_{file_key}",
            label_visibility="collapsed",
        )
        sel_file = excel_files[sel_file_idx]

        if st.button(f"⬇️ {sel_file['name']} 불러오기", key=f"gmail_load_{file_key}", type="primary", use_container_width=True):
            try:
                with st.spinner("다운로드 중..."):
                    data = download_gmail_attachment(
                        token_info, sel_file["message_id"], sel_file["attachment_id"]
                    )
                st.success(f"✅ {sel_file['name']} 로드 완료")
                return data
            except Exception as e:
                st.error(f"다운로드 실패: {e}")
    return None


def add_log(level: str, message: str, detail: str = "", payload: dict = None, source: str = ""):
    """로그를 session_state에 누적. level: 'success' | 'error' | 'warning' | 'info'"""
    if "tx_logs" not in st.session_state:
        st.session_state["tx_logs"] = []
    entry = {
        "ts":      datetime.datetime.now().strftime("%m-%d %H:%M:%S"),
        "level":   level,
        "message": message,
        "detail":  detail,
        "payload": payload,
        "source":  source,
        "retried": False,
    }
    st.session_state["tx_logs"].insert(0, entry)
    today = datetime.date.today().isoformat()
    cnt = st.session_state.setdefault("tx_counter", {})
    cnt.setdefault(today, {"success": 0, "error": 0, "total": 0})
    cnt[today]["total"] += 1
    if level == "success":
        cnt[today]["success"] += 1
    elif level == "error":
        cnt[today]["error"] += 1


def render_api_send_section(output_df: pd.DataFrame, memo_key: str):
    """API 전송 UI 공통 렌더링"""
    token = st.session_state.get("api_token", "")

    if not token:
        st.info("← 사이드바에서 API 토큰을 입력하면 박스히어로로 직접 전송할 수 있습니다.")
        return

    locs = st.session_state.get("api_locations", [])
    if not locs:
        try:
            locs = fetch_locations(token)
            st.session_state["api_locations"] = locs
        except Exception:
            pass
    if not locs:
        st.warning("⚠️ 박스히어로 위치 목록을 불러올 수 없습니다.")
        return

    st.markdown("### 📡 박스히어로 출고 전송")

    loc_options = {l["name"]: l["id"] for l in locs}
    loc_names = list(loc_options.keys())
    _saved_id = st.session_state.get("selected_location_id")
    _default_idx = 0
    if _saved_id:
        for _i, _n in enumerate(loc_names):
            if loc_options[_n] == _saved_id:
                _default_idx = _i
                break
    selected_loc = st.selectbox("📍 출고 위치", loc_names, index=_default_idx, key=f"loc_{memo_key}")
    from_loc_id = loc_options[selected_loc]
    if st.session_state.get("selected_location_id") != from_loc_id:
        st.session_state["selected_location_id"] = from_loc_id
        save_config({"selected_location_id": from_loc_id, "selected_location_name": selected_loc})

    partners = st.session_state.get("api_partners")
    if partners is None:
        try:
            partners = fetch_partners(token)
            st.session_state["api_partners"] = partners
        except Exception:
            partners = []
    partner_id = None
    if partners:
        partner_options = {"(선택 안 함)": None} | {p["name"]: p["id"] for p in partners}
        selected_partner = st.selectbox("🏢 거래처", list(partner_options.keys()), key=f"partner_{memo_key}")
        partner_id = partner_options[selected_partner]

    memo = st.text_input("전송 메모 (선택)", key=memo_key, placeholder="예) 컬리 4월 7일 출고")

    if st.button("🚀 박스히어로로 출고 전송", type="primary", use_container_width=True, key=f"send_{memo_key}"):
        try:
            with st.spinner("상품 목록 조회 중..."):
                sku_to_id = fetch_all_items(token)

            items_payload = []
            missing = []
            for _, row in output_df.iterrows():
                sku = str(row["SKU"]).strip()
                qty = int(row["수량"])
                item_id = sku_to_id.get(sku)
                if item_id:
                    items_payload.append({"item_id": item_id, "quantity": -qty})
                else:
                    missing.append(sku)

            if missing:
                st.warning(f"⚠️ SKU 미등록 ({len(missing)}건): {', '.join(missing)}")
                for sku in missing:
                    add_log("warning", f"SKU 미등록: {sku}", "박스히어로에 등록되지 않은 SKU", source=memo_key)

            if not items_payload:
                st.error("전송할 항목이 없습니다.")
                return

            payload = {
                "type": "out",
                "to_location_id": int(from_loc_id),
                "items": items_payload,
                "memo": memo or "",
            }
            if partner_id:
                payload["partner_id"] = int(partner_id)

            with st.spinner("전송 중..."):
                result = post_transaction(token, payload)

            tx_id = result.get("id", "")
            st.success(f"✅ 전송 완료! 트랜잭션 ID: `{tx_id}`")
            st.caption(f"총 {len(items_payload)}개 SKU, {output_df['수량'].sum()}개 출고")
            add_log("success",
                    f"출고 전송 완료 ({len(items_payload)}개 SKU, {int(output_df['수량'].sum())}개)",
                    f"트랜잭션 ID: {tx_id} | 메모: {memo}",
                    payload=payload, source=memo_key)

        except requests.HTTPError as e:
            err_msg = f"{e.response.status_code} — {e.response.text[:200]}"
            st.error(f"API 오류: {err_msg}")
            add_log("error", f"API 전송 실패: {err_msg}", detail=str(e),
                    payload={"memo": memo, "items_count": len(output_df)}, source=memo_key)
        except Exception as e:
            st.error(f"오류: {e}")
            add_log("error", f"전송 오류: {str(e)[:100]}", source=memo_key)


# ── 리셀러 가드 ────────────────────────────────────────────────
_RG_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard", "data")

def _rg_normalize_address(addr):
    if pd.isna(addr): return ""
    addr = str(addr)
    for k, v in {
        '서울특별시':'서울','서울시':'서울','경기도':'경기','인천광역시':'인천',
        '대전광역시':'대전','대구광역시':'대구','광주광역시':'광주','부산광역시':'부산',
        '울산광역시':'울산','세종특별자치시':'세종','강원특별자치도':'강원','강원도':'강원',
        '제주특별자치도':'제주','제주도':'제주','전북특별자치도':'전북','전라북도':'전북',
        '전라남도':'전남','충청남도':'충남','충청북도':'충북','경상남도':'경남','경상북도':'경북',
    }.items():
        addr = addr.replace(k, v)
    addr = re.sub(r'\(.*?\)', '', addr)
    addr = re.sub(r'\s+', '', addr)
    return addr.lower()

def _rg_extract_road(addr_norm):
    m = re.search(r'(.+(?:로|길))', addr_norm)
    if m: return m.group(1)
    m = re.search(r'(.+(?:동|가|리))', addr_norm)
    return m.group(1) if m else addr_norm[:12]

def _rg_normalize_phone(phone):
    if pd.isna(phone): return ""
    s = str(phone).strip()
    if s.endswith('.0'): s = s[:-2]
    s = re.sub(r'\D', '', s)
    if len(s) == 10 and s.startswith('10'): s = '0' + s
    return s

def _rg_clean_name(name):
    if pd.isna(name): return ""
    s = re.sub(r'\(.*?\)', '', str(name)).replace('*', '').strip()
    return re.sub(r'\s+', '', s)

def analyze_reseller(raw_bytes: bytes) -> tuple[pd.DataFrame, dict]:
    """
    엑셀 바이트를 받아 리셀러 판별 결과 DataFrame과 통계 dict 반환.
    대시보드 data/ 폴더의 블랙리스트·누적데이터를 자동 참조.
    """
    df = pd.read_excel(io.BytesIO(raw_bytes))

    col_map = {}
    for c in df.columns:
        cl = str(c).strip()
        if cl in ['배송지','배송지(지번)','배송주소','배송지주소'] and '주소' not in col_map.values(): col_map[c]='주소'
        elif cl in ['고객명','구매자이름','주문자명','수취인명','수하인','받는분'] and '수취인' not in col_map.values(): col_map[c]='수취인'
        elif cl in ['전화번호','핸드폰','수취인연락처','배송지전화','수취인전화','휴대폰'] and '연락처' not in col_map.values(): col_map[c]='연락처'
        elif cl in ['품명','제품명','품목명','상품'] and '상품명' not in col_map.values(): col_map[c]='상품명'
        elif cl in ['주문수량','구매수량','출고수량','배송수량'] and '수량' not in col_map.values(): col_map[c]='수량'
    df = df.rename(columns=col_map)
    for c in df.columns:
        cl = str(c).strip()
        if '주소' in cl and '주소' not in df.columns: df = df.rename(columns={c:'주소'})
        elif ('수취' in cl or '수하' in cl or '받는' in cl) and '수취인' not in df.columns: df = df.rename(columns={c:'수취인'})
        elif '상품' in cl and '상품명' not in df.columns: df = df.rename(columns={c:'상품명'})
        elif '수량' in cl and '수량' not in df.columns: df = df.rename(columns={c:'수량'})
        elif ('연락' in cl or '전화' in cl or '핸드' in cl) and '연락처' not in df.columns: df = df.rename(columns={c:'연락처'})
    for col in ['수취인','주소','연락처','상품명','수량']:
        if col not in df.columns: df[col] = ""
    df['수량'] = pd.to_numeric(df['수량'], errors='coerce').fillna(1).astype(int)
    df['addr_norm'] = df['주소'].apply(_rg_normalize_address)

    black_addr_set, black_road_set, black_phone_set = set(), set(), set()
    bl_path = os.path.join(_RG_DATA_DIR, "리셀러_리스트.xlsx")
    if os.path.exists(bl_path):
        try:
            df_bl = pd.read_excel(bl_path)
            if '주소' not in df_bl.columns:
                for i in range(1, 5):
                    tmp = pd.read_excel(bl_path, header=i)
                    if '주소' in tmp.columns or '업체명' in tmp.columns: df_bl = tmp; break
            if '주소' in df_bl.columns:
                bl_addrs = df_bl['주소'].apply(_rg_normalize_address).dropna().unique()
                black_addr_set = {a for a in bl_addrs if len(a) > 6}
                black_road_set = {_rg_extract_road(a) for a in black_addr_set if len(_rg_extract_road(a)) >= 5}
            for pc in ['연락처','전화번호','핸드폰']:
                if pc in df_bl.columns:
                    black_phone_set.update(p for p in df_bl[pc].apply(_rg_normalize_phone).dropna().unique() if len(p) >= 8)
        except Exception: pass

    past_addr_set, past_road_set = set(), set()
    past_path = os.path.join(_RG_DATA_DIR, "누적_판매데이터.xlsx")
    if os.path.exists(past_path):
        try:
            df_past = pd.read_excel(past_path)
            for c in df_past.columns:
                if '주소' in str(c) or '배송' in str(c): df_past = df_past.rename(columns={c:'주소'}); break
            if '주소' in df_past.columns:
                df_past['addr_norm'] = df_past['주소'].apply(_rg_normalize_address)
                past_addr_set = set(df_past['addr_norm'].unique())
                past_road_set = {_rg_extract_road(a) for a in past_addr_set if len(_rg_extract_road(a)) >= 5}
        except Exception: pass

    df['위험점수'] = 0
    df['탐지사유'] = ""
    df['리셀러_판정'] = '정상'

    bulk_kws = ['10개입','10개','12개입','12개','10ea','10박스','대량','벌크']
    corp_kws = ['지식','테크노','산업센터','타워','빌딩','벤처','물류','비즈','에이스','테라','단지','오피스텔']

    for idx, row in df.iterrows():
        reasons, score = [], 0
        is_confirmed, is_suspected = False, False
        curr_addr  = row['addr_norm']
        curr_road  = _rg_extract_road(curr_addr)
        curr_prod  = str(row['상품명'])
        curr_qty   = row['수량']
        curr_phone = _rg_normalize_phone(row.get('연락처',''))

        if curr_addr in black_addr_set:
            is_confirmed = True; reasons.append("블랙리스트 주소 일치"); score += 100
        if not is_confirmed and len(curr_road) >= 5 and curr_road in black_road_set:
            is_confirmed = True; reasons.append("블랙리스트 도로명 일치"); score += 100
        if curr_phone and len(curr_phone) >= 8 and curr_phone in black_phone_set:
            is_confirmed = True; reasons.append("블랙리스트 연락처 일치"); score += 100
        if 'xxxxx' in curr_addr or 'XXXXX' in str(row['주소']):
            is_confirmed = True; reasons.append("비정상 주소(XXXXX)"); score += 100

        has_past = curr_addr in past_addr_set or (len(curr_road)>=5 and curr_road in past_road_set)
        is_bulk  = any(kw in curr_prod for kw in bulk_kws)
        if has_past and (is_bulk or curr_qty >= 3):
            is_suspected = True; reasons.append(f"과거이력+대량패턴(수량:{curr_qty})"); score += 60
        if any(kw in str(row['주소']) for kw in corp_kws):
            is_suspected = True; reasons.append("기업체/산업단지 주소"); score += 60

        if is_confirmed or is_suspected:
            df.at[idx, '위험점수'] = score
            df.at[idx, '탐지사유'] = " / ".join(reasons)
            df.at[idx, '리셀러_판정'] = '확정' if is_confirmed else '의심'

    df['_road'] = df['addr_norm'].apply(_rg_extract_road)
    df['_is_bulk'] = df['상품명'].apply(lambda x: any(kw in str(x) for kw in bulk_kws))
    bulk_by_road = df[df['_is_bulk']].groupby('_road').size()
    all_by_road  = df.groupby('_road').size()
    confirmed_roads    = set(bulk_by_road[bulk_by_road >= 5].index)
    suspected_bulk     = set(bulk_by_road[(bulk_by_road >= 3) & (bulk_by_road < 5)].index)
    suspected_all      = set(all_by_road[all_by_road >= 3].index)
    for idx, row in df.iterrows():
        road = row['_road']
        if len(road) < 5: continue
        if road in confirmed_roads:
            cnt = bulk_by_road.get(road, 0)
            if df.at[idx,'리셀러_판정'] != '확정':
                df.at[idx,'위험점수'] += 100
                suf = f"동일주소 패키지 {cnt}건 집중(확정)"
                df.at[idx,'탐지사유'] = (df.at[idx,'탐지사유'] + " / " + suf).lstrip(" / ")
                df.at[idx,'리셀러_판정'] = '확정'
        elif road in suspected_bulk:
            cnt = bulk_by_road.get(road, 0)
            if df.at[idx,'리셀러_판정'] == '정상':
                df.at[idx,'위험점수'] += 60
                df.at[idx,'탐지사유'] = f"동일주소 패키지 {cnt}건 집중(의심)"
                df.at[idx,'리셀러_판정'] = '의심'
        elif road in suspected_all:
            cnt = all_by_road.get(road, 0)
            if df.at[idx,'리셀러_판정'] == '정상':
                df.at[idx,'위험점수'] += 50
                df.at[idx,'탐지사유'] = f"당일 동일주소 {cnt}건 집중"
                if df.at[idx,'위험점수'] >= 60:
                    df.at[idx,'리셀러_판정'] = '의심'

    df = df.drop(columns=['addr_norm','_road','_is_bulk'], errors='ignore')
    df = df.sort_values('위험점수', ascending=False).reset_index(drop=True)

    stats = {
        "total":     len(df),
        "confirmed": int((df['리셀러_판정']=='확정').sum()),
        "suspected": int((df['리셀러_판정']=='의심').sum()),
        "normal":    int((df['리셀러_판정']=='정상').sum()),
        "has_bl":    os.path.exists(bl_path),
        "has_past":  os.path.exists(past_path),
    }
    return df, stats


def normalize(text: str) -> str:
    text = str(text)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"DJA\d+-", "", text)
    text = re.sub(r"BTB\d+-", "", text)
    text = re.sub(r"디제이앤에이\s*", "", text)
    text = re.sub(r"\d+\s*(g|kg|ml|l|mg)\b", lambda m: m.group().replace(" ","").lower(), text, flags=re.IGNORECASE)
    text = re.sub(r"[\s\-_]+", " ", text)
    text = text.replace("프레첼", "프레젤")
    text = text.replace("프레즐", "프레젤")
    text = text.replace("머쉬룸", "머시룸")
    return text.strip()


def best_match(query: str, choices: list[str]) -> tuple[str, int]:
    """
    token_sort_ratio 기본 + token_set_ratio 보조.
    단, 후보에 쿼리에 없는 단어가 많을수록 패널티 적용.
    """
    norm_q = normalize(query)
    q_tokens = set(norm_q.split())
    candidates = {}
    for choice in choices:
        sort_score = fuzz.token_sort_ratio(norm_q, choice)
        set_score  = fuzz.token_set_ratio(norm_q, choice)
        c_tokens   = set(choice.split())
        extra      = len(c_tokens - q_tokens)
        penalty    = extra * 5
        score = max(sort_score, set_score - penalty)
        candidates[choice] = max(0, score)

    best = max(candidates, key=candidates.get)
    return best, candidates[best]


@st.cache_data
def load_master(path: str) -> pd.DataFrame:
    return pd.read_excel(path)

@st.cache_data(show_spinner=False)
def load_master_from_bytes(data: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(data))


def load_naver(file) -> tuple[pd.DataFrame, dict]:
    """네이버 출고 파일 로드 → (데이터프레임, boxhero_name_to_sku)"""
    raw = pd.read_excel(file, sheet_name="양식", header=None, dtype=str)
    df  = raw.iloc[11:].copy().reset_index(drop=True)
    df.columns = [
        "idx", "출고요청일", "주문번호", "SKU_원본", "상품명", "수량",
        "출고지", "수취인", "연락처", "배송지주소", "상세주소",
        "배송방법", "택배사", "송장번호", "거래처", "출고사유", "담당자", "발송인정보"
    ]
    df = df[df["상품명"].notna() & (df["상품명"].str.strip() != "")].copy()

    name_to_sku = {}
    try:
        bh = pd.read_excel(file, sheet_name="BoxHero", header=0, dtype=str)
        for _, row in bh.iterrows():
            if pd.notna(row.get("제품명")) and pd.notna(row.get("SKU")):
                name_to_sku[str(row["제품명"]).strip()] = str(row["SKU"]).strip()
    except Exception:
        pass

    return df, name_to_sku


def resolve_naver_sku(sku_raw, product_name, name_to_sku: dict, master_lookup: dict) -> tuple[str, str]:
    """네이버 행의 SKU를 결정. (sku, 방법) 반환"""
    if pd.notna(sku_raw) and str(sku_raw).strip() not in ("", "nan"):
        return str(sku_raw).strip(), "SKU_원본"

    if pd.isna(product_name):
        return "UNKNOWN", "없음"

    name = str(product_name).strip()

    if name in name_to_sku:
        return name_to_sku[name], "BoxHero 정확"

    for bh_name, bh_sku in name_to_sku.items():
        if bh_name in name or name in bh_name:
            return bh_sku, "BoxHero 부분"

    clean = re.sub(r"^[A-Za-z0-9]+-", "", name)
    for bh_name, bh_sku in name_to_sku.items():
        bh_clean = re.sub(r"^[A-Za-z0-9]+-", "", bh_name)
        if clean == bh_clean or clean in bh_name or bh_clean in clean:
            return bh_sku, "BoxHero 코드제거"

    norm_names = list(master_lookup.keys())
    if norm_names:
        best, score = best_match(name, norm_names)
        if score >= 65:
            sku = master_lookup[best][0]
            return sku, f"마스터 퍼지({score}%)"

    return f"UNKNOWN_{name[:20]}", "실패"


# ── CSS ────────────────────────────────────────────────────
APP_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    /* ── 기본 ── */
    html, body, [class*="css"] { font-family: 'Inter', 'Malgun Gothic', sans-serif !important; }
    .stApp { background: #f8f9fb !important; }
    .block-container { padding: 1.5rem 2rem 1rem 2rem !important; max-width: 100% !important; }
    #MainMenu, footer, header { visibility: hidden; }

    /* ── 사이드바 ── */
    [data-testid="stSidebar"] { background: #0f1117 !important; border-right: 1px solid #1e2130; }
    [data-testid="stSidebar"] * { color: #9ca3af !important; }
    [data-testid="stSidebar"] .stTextInput input,
    [data-testid="stSidebar"] [data-baseweb="select"] {
        background: #1a1d27 !important; border: 1px solid #2a2d3e !important;
        color: #e5e7eb !important; border-radius: 8px !important; font-size: 0.83rem !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        background: #10b981 !important; color: #fff !important;
        border: none !important; border-radius: 8px !important;
        font-weight: 600 !important; font-size: 0.83rem !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover { background: #059669 !important; }

    /* 사이드바 expander */
    [data-testid="stSidebar"] details summary {
        background: #1a1d27 !important; border: 1px solid #2a2d3e !important;
        border-radius: 8px !important; color: #d1d5db !important;
        font-weight: 600 !important; font-size: 0.83rem !important; padding: 8px 12px !important;
    }
    [data-testid="stSidebar"] details[open] summary { border-radius: 8px 8px 0 0 !important; }
    [data-testid="stSidebar"] details > div {
        background: #13151f !important; border: 1px solid #2a2d3e !important;
        border-top: none !important; border-radius: 0 0 8px 8px !important; padding: 10px !important;
    }

    /* 사이드바 라디오 (네비) */
    [data-testid="stSidebar"] .stRadio > div { gap: 1px !important; }
    [data-testid="stSidebar"] .stRadio label {
        border-radius: 8px !important; padding: 9px 14px !important;
        font-size: 0.86rem !important; font-weight: 500 !important;
        transition: all 0.15s !important; cursor: pointer !important; width: 100% !important;
    }
    [data-testid="stSidebar"] .stRadio label:hover { background: #1a1d27 !important; color: #f3f4f6 !important; }

    /* ── 메인 헤더 ── */
    .page-header { margin-bottom: 1.2rem; }
    .page-title { font-size: 1.6rem; font-weight: 800; color: #111827; letter-spacing: -0.6px; line-height: 1.2; }
    .page-desc  { font-size: 0.83rem; color: #6b7280; margin-top: 3px; }

    /* ── 카드 ── */
    .card {
        background: #ffffff; border-radius: 14px;
        border: 1px solid #e5e7eb;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02);
        padding: 1.2rem 1.4rem; margin-bottom: 0;
    }
    .card-title {
        font-size: 0.78rem; font-weight: 600; color: #6b7280;
        text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 8px;
    }

    /* ── 지표 카드 ── */
    .metric-card {
        background: #fff; border-radius: 14px; border: 1px solid #e5e7eb;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        padding: 1.1rem 1.3rem; position: relative; overflow: hidden;
    }
    .metric-label { font-size: 0.78rem; font-weight: 600; color: #6b7280; margin-bottom: 6px; }
    .metric-value { font-size: 2rem; font-weight: 800; color: #111827; line-height: 1; letter-spacing: -1px; }
    .metric-badge {
        display: inline-flex; align-items: center; gap: 3px;
        font-size: 0.72rem; font-weight: 600; border-radius: 20px;
        padding: 2px 8px; margin-top: 6px;
    }
    .badge-green { background: #d1fae5; color: #065f46; }
    .badge-red   { background: #fee2e2; color: #991b1b; }
    .badge-gray  { background: #f3f4f6; color: #4b5563; }
    .badge-yellow { background: #fef3c7; color: #92400e; }
    .metric-icon {
        position: absolute; top: 1rem; right: 1rem;
        width: 38px; height: 38px; border-radius: 10px;
        display: flex; align-items: center; justify-content: center; font-size: 1.1rem;
    }

    /* ── 파이프라인 노드 ── */
    .pipe-wrap { display: flex; align-items: center; gap: 0; background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
    .pipe-node { flex: 1; text-align: center; }
    .pipe-dot  { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 4px; vertical-align: middle; }
    .pipe-dot.on  { background: #10b981; box-shadow: 0 0 0 3px rgba(16,185,129,0.2); }
    .pipe-dot.off { background: #ef4444; box-shadow: 0 0 0 3px rgba(239,68,68,0.2); }
    .pipe-name { font-size: 0.82rem; font-weight: 700; color: #111827; }
    .pipe-sub  { font-size: 0.7rem; color: #9ca3af; margin-top: 1px; }
    .pipe-arrow { color: #d1d5db; font-size: 1.3rem; flex-shrink: 0; margin: 0 6px; }

    /* ── 섹션 헤더 ── */
    .section-header {
        font-size: 0.95rem; font-weight: 700; color: #111827;
        margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
    }
    .section-header span { font-size: 0.75rem; font-weight: 500; color: #9ca3af; margin-left: auto; }

    /* ── 플로우 바 ── */
    .flow-bar { display: flex; align-items: center; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 10px 18px; margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
    .flow-step { display: flex; align-items: center; gap: 7px; flex: 1; }
    .flow-num  { background: #10b981; color: #fff; border-radius: 50%; width: 22px; height: 22px; font-size: 0.72rem; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
    .flow-num.pending { background: #e5e7eb; color: #9ca3af; }
    .flow-label { font-size: 0.82rem; font-weight: 600; color: #374151; white-space: nowrap; }
    .flow-arrow { color: #d1d5db; font-size: 1.1rem; margin: 0 8px; flex-shrink: 0; }

    /* ── 컬럼 헤더 ── */
    .col-header { font-size: 0.75rem; font-weight: 700; color: #6b7280; text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 2px solid #10b981; }

    /* ── 통계 박스 ── */
    .stat-box  { border-radius: 10px; padding: 0.7rem 1rem; text-align: center; font-size: 1.5rem; font-weight: 800; border: 1px solid transparent; }
    .stat-ok   { background: #ecfdf5; color: #065f46; border-color: #a7f3d0; }
    .stat-err  { background: #fef2f2; color: #991b1b; border-color: #fecaca; }
    .stat-label { font-size: 0.72rem; font-weight: 600; margin-top: 3px; opacity: 0.8; }

    /* ── 로그 아이템 ── */
    .log-item { border-radius: 10px; padding: 9px 13px; margin-bottom: 5px; border: 1px solid; }
    .log-success { background: #ecfdf5; border-color: #a7f3d0; }
    .log-error   { background: #fef2f2; border-color: #fecaca; }
    .log-warning { background: #fffbeb; border-color: #fde68a; }
    .log-info    { background: #eff6ff; border-color: #bfdbfe; }

    /* ── 파일 업로더 ── */
    [data-testid="stFileUploader"] { border: 1.5px dashed #d1d5db; border-radius: 10px; background: #fafafa; }
    [data-testid="stFileUploader"]:hover { border-color: #10b981; }

    /* ── 버튼 ── */
    .stButton > button { border-radius: 8px; font-size: 0.85rem; font-weight: 600; padding: 0.4rem 1rem; transition: all 0.15s; border: 1px solid #e5e7eb !important; color: #374151 !important; background: #fff !important; }
    .stButton > button:hover { background: #f9fafb !important; border-color: #d1d5db !important; }
    .stButton > button[kind="primary"] { background: #10b981 !important; color: #fff !important; border: none !important; }
    .stButton > button[kind="primary"]:hover { background: #059669 !important; box-shadow: 0 4px 12px rgba(16,185,129,0.3); }
    .stDownloadButton > button { background: #10b981 !important; color: #fff !important; border: none !important; border-radius: 8px !important; font-weight: 600 !important; width: 100% !important; }
    .stDownloadButton > button:hover { background: #059669 !important; }

    /* ── 데이터프레임 ── */
    [data-testid="stDataFrame"] { border-radius: 10px; border: 1px solid #e5e7eb !important; overflow: hidden; }

    /* ── expander 메인 ── */
    .streamlit-expanderHeader { background: #fff !important; border-radius: 10px !important; font-weight: 600 !important; color: #374151 !important; border: 1px solid #e5e7eb !important; font-size: 0.88rem !important; }

    /* ── 알림 ── */
    .stAlert { border-radius: 10px !important; border-left-width: 4px !important; }

    /* ── 구분선 ── */
    hr { border: none; border-top: 1px solid #f3f4f6; margin: 0.6rem 0; }

    /* ── selectbox / input ── */
    [data-baseweb="select"] { border-radius: 8px !important; }
    .stTextInput input { border-radius: 8px !important; border-color: #e5e7eb !important; }
    .stSlider [data-baseweb="slider"] [role="slider"] { background: #10b981 !important; }
</style>
"""
