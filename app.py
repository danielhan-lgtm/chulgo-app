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
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

BASE_URL = "https://rest.boxhero-app.com"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_SENDERS = ["inn1246919@nate.com", "gy.lee12@cj.net", "lgl10910@lglpartner.com"]


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


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
    # 우선순위: 완료 > 진행중 > 반려/취소 > 확인중
    STATUS_MAP = {
        # 완료
        "완료": ("완료", "#10b981", "#d1fae5"),
        "white_check_mark": ("완료", "#10b981", "#d1fae5"),
        "heavy_check_mark": ("완료", "#10b981", "#d1fae5"),
        "check": ("완료", "#10b981", "#d1fae5"),
        "100": ("완료", "#10b981", "#d1fae5"),
        "done": ("완료", "#10b981", "#d1fae5"),
        # 진행중
        "진행중": ("진행중", "#f59e0b", "#fef3c7"),
        "hourglass_flowing_sand": ("진행중", "#f59e0b", "#fef3c7"),
        "hourglass": ("진행중", "#f59e0b", "#fef3c7"),
        "arrows_counterclockwise": ("진행중", "#f59e0b", "#fef3c7"),
        "loading": ("진행중", "#f59e0b", "#fef3c7"),
        "spinner": ("진행중", "#f59e0b", "#fef3c7"),
        # 반려/취소
        "x": ("반려", "#ef4444", "#fee2e2"),
        "negative_squared_cross_mark": ("반려", "#ef4444", "#fee2e2"),
        "반려": ("반려", "#ef4444", "#fee2e2"),
        "취소": ("취소", "#ef4444", "#fee2e2"),
        # 확인중
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
    text = re.sub(r"\*([^*]+)\*", r"\1", text)   # *bold* → bold
    text = re.sub(r"_([^_]+)_", r"\1", text)      # _italic_
    text = re.sub(r"`([^`]+)`", r"\1", text)       # `code`
    text = re.sub(r"<[^>]+>", "", text)            # <links>
    text = re.sub(r":\w+:", "", text)              # :emoji:
    # 줄 앞의 라벨(예: *출고 요청일:*) 제거
    text = re.sub(r"^\s*[\*\-•]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r":\s*$", "", text, flags=re.MULTILINE)
    # 빈 줄 압축
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


def extract_summary_fields(parsed: dict) -> dict:
    """파싱된 섹션에서 핵심 값만 1줄로 추출"""
    summary = {}

    # 일정: 날짜만 추출
    sched = clean_slack_text(parsed.get("일정", ""))
    dates = re.findall(r"\d{4}[./\-]\d{1,2}[./\-]\d{1,2}|\d{1,2}[./]\d{1,2}", sched)
    summary["일정"] = "  ".join(dates) if dates else (sched[:30] if sched else "")

    # 목적: 첫 줄만
    purpose = clean_slack_text(parsed.get("목적", ""))
    summary["목적"] = purpose.split("\n")[0][:30] if purpose else ""

    # 품목: 품목명만 추출 (숫자·건 단위)
    items_raw = clean_slack_text(parsed.get("품목", ""))
    item_lines = [l.strip() for l in items_raw.split("\n") if l.strip()]
    summary["품목"] = ", ".join(item_lines[:3]) + ("…" if len(item_lines) > 3 else "")

    # 담당자: 이름만 (첫 줄)
    contact = clean_slack_text(parsed.get("담당자", ""))
    name_m = re.search(r"[가-힣]{2,4}(?=\s|$|\d)", contact)
    summary["담당자"] = name_m.group() if name_m else contact.split("\n")[0][:20]

    # 운송: 방식만
    ship = clean_slack_text(parsed.get("운송정보", ""))
    ship_m = re.search(r"택배|직배|퀵|화물|CJ|한진|롯데|우체국", ship)
    summary["운송"] = ship_m.group() if ship_m else ship.split("\n")[0][:20]

    return {k: v for k, v in summary.items() if v}


def parse_order_message(text: str) -> dict:
    """출고 요청 메시지 텍스트를 섹션별로 파싱"""
    info = {}
    # 제목 (첫 줄)
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
            # 각 줄 앞의 - 또는 • 제거 후 정리
            lines_sec = [l.strip().lstrip("-•●*").strip() for l in content.split("\n") if l.strip()]
            info[key] = "\n".join(lines_sec)
    return info


def fetch_slack_orders(token: str, channel_id: str) -> tuple[list[dict], str]:
    """채널에서 출고 요청 메시지 목록 반환 (메시지 요약 + 첨부파일 포함). (orders, debug_msg) 반환"""
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
            # 출고 관련 텍스트 OR Excel 첨부파일이 있는 메시지 모두 포함
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
            # reactions 파싱
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

        # 출고 요청 목록
        order_labels = [f"{o['dt']}  {o['title'][:35]}" for o in orders]
        sel_idx = st.selectbox(
            "출고 요청 선택",
            range(len(order_labels)),
            format_func=lambda i: order_labels[i],
            key=f"slack_order_sel_{file_key}",
            label_visibility="collapsed",
        )
        order = orders[sel_idx]

        # 요약 카드
        parsed = order["parsed"]
        summary_rows = []
        for key in ["목적", "일정", "품목", "담당자", "운송정보"]:
            val = parsed.get(key, "")
            if val:
                # 줄바꿈을 <br>로
                val_html = val.replace("\n", "<br>")
                summary_rows.append(f"<tr><td style='color:#8b8fa8;font-size:0.8rem;white-space:nowrap;padding:4px 12px 4px 0;vertical-align:top;'>{key}</td><td style='font-size:0.85rem;padding:4px 0;'>{val_html}</td></tr>")

        if summary_rows:
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse;margin:8px 0;'>{''.join(summary_rows)}</table>",
                unsafe_allow_html=True,
            )

        # 첨부파일 목록
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

def gmail_build_flow(client_id: str, client_secret: str) -> Flow:
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
    # 토큰 만료 시 자동 갱신 후 config에 저장
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
    st.session_state["tx_logs"].insert(0, entry)  # 최신순
    # 일별 카운터
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

    # 위치 목록 로드
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

    # 출고 위치 선택 (항상 페이지 내에서 선택 가능)
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

    # 거래처 선택
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
            # 저장된 출고 위치 복원
            _saved_loc_id = _cfg.get("selected_location_id")
            if _saved_loc_id:
                st.session_state["selected_location_id"] = _saved_loc_id
            elif _locs:
                st.session_state["selected_location_id"] = _locs[0]["id"]
        except Exception:
            pass  # 토큰 만료 등 — 사용자가 수동 재연결

    # ── Slack 자동 연결 ──
    _sl_token = _cfg.get("slack_token", "")
    if _sl_token and "slack_token" not in st.session_state:
        try:
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
            pass  # 토큰 만료 등

    # ── Gmail 자동 연결 ──
    _gmail_token = _cfg.get("gmail_token")
    if _gmail_token and "gmail_token" not in st.session_state:
        st.session_state["gmail_token"] = _gmail_token

    st.session_state["config_loaded"] = True

# ── CSS ────────────────────────────────────────────────────
st.markdown("""
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
""", unsafe_allow_html=True)

DEFAULT_MASTER = r"C:\Users\User\OneDrive\Desktop\업무\AI 실험\박스 히어로 마스터 파일.xlsx"

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

    # 컬럼 유연 매핑
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

    # 블랙리스트 로드
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

    # 누적 판매 데이터 로드
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

    # 당일 동일 도로명 집중 패턴
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
    text = re.sub(r"\[.*?\]", "", text)           # [DJ&A] 태그 제거
    text = re.sub(r"DJA\d+-", "", text)
    text = re.sub(r"BTB\d+-", "", text)
    text = re.sub(r"디제이앤에이\s*", "", text)
    text = re.sub(r"\d+\s*(g|kg|ml|l|mg)\b", lambda m: m.group().replace(" ","").lower(), text, flags=re.IGNORECASE)  # 용량 공백 제거 후 소문자 통일
    text = re.sub(r"[\s\-_]+", " ", text)
    # 자주 혼용되는 표기 통일
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

        # 후보에만 있는 추가 토큰 수에 따라 패널티
        c_tokens   = set(choice.split())
        extra      = len(c_tokens - q_tokens)
        penalty    = extra * 5  # 추가 단어 1개당 5점 차감

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

    # BoxHero 시트에서 제품명 → SKU 매핑 추출
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
    # 1) SKU_원본이 있으면 그대로
    if pd.notna(sku_raw) and str(sku_raw).strip() not in ("", "nan"):
        return str(sku_raw).strip(), "SKU_원본"

    if pd.isna(product_name):
        return "UNKNOWN", "없음"

    name = str(product_name).strip()

    # 2) BoxHero 시트 정확 매치
    if name in name_to_sku:
        return name_to_sku[name], "BoxHero 정확"

    # 3) BoxHero 시트 부분 매치
    for bh_name, bh_sku in name_to_sku.items():
        if bh_name in name or name in bh_name:
            return bh_sku, "BoxHero 부분"

    # 4) 코드 제거 후 BoxHero 재매치
    clean = re.sub(r"^[A-Za-z0-9]+-", "", name)
    for bh_name, bh_sku in name_to_sku.items():
        bh_clean = re.sub(r"^[A-Za-z0-9]+-", "", bh_name)
        if clean == bh_clean or clean in bh_name or bh_clean in clean:
            return bh_sku, "BoxHero 코드제거"

    # 5) 마스터 파일 퍼지 매칭
    norm_names = list(master_lookup.keys())
    if norm_names:
        best, score = best_match(name, norm_names)
        if score >= 65:
            sku = master_lookup[best][0]
            return sku, f"마스터 퍼지({score}%)"

    return f"UNKNOWN_{name[:20]}", "실패"


master_ok = os.path.exists(DEFAULT_MASTER)

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
        del st.session_state["nav_target_idx"]   # 한 번만 적용 후 제거
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
            # 저장된 토큰으로 처음 로드된 경우 채널 목록 조회
            if not st.session_state.get("slack_channels"):
                try:
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

        # 어떤 스테이지 데이터를 쓸지 선택
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

        # 선택된 데이터 요약
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
                        "type": "out",
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

# ════════════════════════════════════════════════════════════
# 페이지: 대시보드
# ════════════════════════════════════════════════════════════
if page == "📊  대시보드":
    bh_ok     = bool(st.session_state.get("api_token"))
    slack_ok  = bool(st.session_state.get("slack_token"))
    gmail_ok  = bool(st.session_state.get("gmail_token"))
    master_ok_flag = master_ok or bool(st.session_state.get("master_bytes"))
    bh_locs  = len(st.session_state.get("api_locations", []))
    master_status = "자동 연결" if master_ok else ("업로드됨" if st.session_state.get("master_bytes") else "미연결")

    # 알림 배너
    if not bh_ok:
        st.markdown('<div style="background:#fef2f2;border-left:4px solid #ef4444;border-radius:10px;padding:11px 16px;margin-bottom:14px;font-size:0.84rem;color:#991b1b;font-weight:500;">⚠️ <b>박스히어로 API 미연결</b> — 사이드바 연동 섹션에서 연결해주세요.</div>', unsafe_allow_html=True)

    # ── 파이프라인 상태 ──────────────────────────────────────
    def _pipe_node(icon, label, ok, sub):
        dot_cls = "on" if ok else "off"
        return f"""<div class="pipe-node">
            <div style="font-size:1.5rem;margin-bottom:4px;">{icon}</div>
            <div class="pipe-name"><span class="pipe-dot {dot_cls}"></span>{label}</div>
            <div class="pipe-sub">{sub}</div>
        </div>"""

    st.markdown(f"""
    <div class="pipe-wrap">
        {_pipe_node("💬", "Slack", slack_ok, "채널 연결됨" if slack_ok else "미연결")}
        <div class="pipe-arrow">→</div>
        {_pipe_node("📧", "Gmail", gmail_ok, "연결됨" if gmail_ok else "미연결")}
        <div class="pipe-arrow">→</div>
        {_pipe_node("📂", "마스터 파일", master_ok_flag, master_status)}
        <div class="pipe-arrow">→</div>
        {_pipe_node("⚙️", "변환 엔진", True, "대기 중")}
        <div class="pipe-arrow">→</div>
        {_pipe_node("📦", "박스히어로", bh_ok, f"{bh_locs}개 위치" if bh_ok else "미연결")}
    </div>
    """, unsafe_allow_html=True)

    # ── 지표 카드 4개 ────────────────────────────────────────
    today      = datetime.date.today().isoformat()
    cnt        = st.session_state.get("tx_counter", {}).get(today, {"total": 0, "success": 0, "error": 0})
    warn_count = sum(1 for l in st.session_state.get("tx_logs", [])
                     if l["level"] == "warning" and l["ts"].startswith(datetime.date.today().strftime("%m-%d")))

    m1, m2, m3, m4 = st.columns(4, gap="small")
    for col, label, val, icon, icon_bg, badge_txt, badge_cls in [
        (m1, "오늘 총 처리",  cnt["total"],    "📋", "#eff6ff", "건", "badge-gray"),
        (m2, "전송 성공",     cnt["success"],  "✅", "#ecfdf5", "건", "badge-green"),
        (m3, "전송 실패",     cnt["error"],    "❌", "#fef2f2", "건", "badge-red"),
        (m4, "경고",          warn_count,      "⚠️", "#fffbeb", "건", "badge-yellow"),
    ]:
        col.markdown(f"""<div class="metric-card">
            <div class="metric-icon" style="background:{icon_bg};">{icon}</div>
            <div class="metric-label">{label}</div>
            <div class="metric-value">{val}</div>
            <div><span class="metric-badge {badge_cls}">오늘 {val}{badge_txt}</span></div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── 로그 + Payload Inspector ────────────────────────────
    logs = st.session_state.get("tx_logs", [])
    log_col, inspect_col = st.columns([3, 2], gap="medium")

    with log_col:
        st.markdown('<div class="section-header">📋 전송 이력 / 오류 로그</div>', unsafe_allow_html=True)
        lf1, lf2, lf3 = st.columns([3, 2, 1])
        with lf1:
            log_filter = st.selectbox("필터", ["전체", "성공", "오류", "경고"], label_visibility="collapsed", key="log_filter")
        with lf3:
            if st.button("초기화", key="clear_logs"):
                st.session_state["tx_logs"] = []
                st.session_state["tx_counter"] = {}
                st.rerun()

        filtered = [l for l in logs if log_filter == "전체" or
                    (log_filter == "성공" and l["level"] == "success") or
                    (log_filter == "오류" and l["level"] == "error") or
                    (log_filter == "경고" and l["level"] == "warning")]

        if not filtered:
            st.markdown('<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:20px;text-align:center;color:#9ca3af;font-size:0.85rem;">전송 이력이 없습니다. 출고 전송 후 여기에 기록됩니다.</div>', unsafe_allow_html=True)
        else:
            level_cfg = {
                "success": ("✅", "log-success", "#065f46"),
                "error":   ("❌", "log-error",   "#991b1b"),
                "warning": ("⚠️", "log-warning", "#92400e"),
                "info":    ("ℹ️", "log-info",    "#1e40af"),
            }
            for i, log in enumerate(filtered):
                icon, css_cls, color = level_cfg.get(log["level"], ("•","","#374151"))
                cols = st.columns([7, 1])
                with cols[0]:
                    st.markdown(f"""<div class="log-item {css_cls}">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                            <span style="font-weight:700;font-size:0.82rem;color:{color};">{icon} {log['message']}</span>
                            <span style="font-size:0.7rem;color:#9ca3af;white-space:nowrap;margin-left:8px;">{log['ts']}</span>
                        </div>
                        {f'<div style="font-size:0.74rem;color:#6b7280;margin-top:3px;">{log["detail"]}</div>' if log.get("detail") else ""}
                    </div>""", unsafe_allow_html=True)
                with cols[1]:
                    if log["level"] == "error" and log.get("payload") and not log.get("retried"):
                        if st.button("↺", key=f"retry_{i}", use_container_width=True, help="재시도"):
                            token = st.session_state.get("api_token")
                            if token:
                                try:
                                    with st.spinner("재전송 중..."):
                                        result = post_transaction(token, log["payload"])
                                    log["retried"] = True
                                    add_log("success", f"재시도 성공 — 트랜잭션 {result.get('id','')}", source="retry")
                                    st.rerun()
                                except Exception as e:
                                    add_log("error", f"재시도 실패: {str(e)[:80]}", source="retry")
                                    st.rerun()

    with inspect_col:
        st.markdown('<div class="section-header">🔍 Payload Inspector</div>', unsafe_allow_html=True)
        payload_logs = [l for l in logs if l.get("payload")]
        if not payload_logs:
            st.markdown('<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:20px;text-align:center;color:#9ca3af;font-size:0.85rem;">전송된 payload가 없습니다.</div>', unsafe_allow_html=True)
        else:
            options = [f"{l['ts']}  {l['message'][:28]}" for l in payload_logs]
            sel = st.selectbox("기록 선택", range(len(options)), format_func=lambda i: options[i],
                               label_visibility="collapsed", key="payload_sel")
            sel_log = payload_logs[sel]
            lvl_badge = {"success":"✅","error":"❌","warning":"⚠️"}.get(sel_log["level"],"ℹ️")
            st.caption(f"{lvl_badge} {sel_log['ts']} · source: {sel_log.get('source','—')}")
            st.json(sel_log["payload"])

# ════════════════════════════════════════════════════════════
# 페이지: 네이버 형식
# ════════════════════════════════════════════════════════════
if page == "🛒  네이버 형식":
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
            elif master_ok:
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
            # 편집 결과를 session_state에 저장 (rerun 후에도 유지)
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

# ════════════════════════════════════════════════════════════
# 페이지: 일반 형식
# ════════════════════════════════════════════════════════════
if page == "📄  일반 형식 (컬리 등)":
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
        elif master_ok:
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
            best, score = best_match(raw_name, norm_names)
            if score >= threshold:
                sku, price, matched_name = master_lookup[best]
                results.append({"SKU":sku,"수량":int(qty),"단가":price,"_출고상품명":raw_name,"_마스터매칭명":matched_name,"_유사도(%)":score})
            else:
                unmatched.append({"출고상품명":raw_name,"수량":int(qty),"유사도(%)":score,
                    "가장유사한마스터":master_lookup.get(best,("","",""))[2],"_best_key":best})
            prog.progress((i+1)/len(grouped))
        prog.empty()
        st.session_state["results"] = results
        st.session_state["unmatched"] = unmatched
        st.session_state["master_lookup"] = master_lookup
        st.session_state["master_name_list"] = [v[2] for v in master_lookup.values()]
        # 변환 직후 staged_df 초기화 (mapping table에서 다시 구성)
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

# ════════════════════════════════════════════════════════════
# 페이지: 출고 요청 (Slack)
# ════════════════════════════════════════════════════════════
if page == "📨  출고 요청 (Slack)":
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

    # 각 상태별 첫 번째 인덱스 미리 계산
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

            # 상태 뱃지
            if status:
                s_badge = '<span style="background:' + status["bg"] + ';color:' + status["color"] + ';border-radius:4px;padding:1px 8px;font-size:0.7rem;font-weight:700;">● ' + status["label"] + '</span>'
            else:
                s_badge = '<span style="background:#f3f4f6;color:#9ca3af;border-radius:4px;padding:1px 8px;font-size:0.7rem;">미처리</span>'

            # 날짜/품목 태그
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

        # 상태 + 요약 한 줄
        _status_span = ""
        if status_d:
            _status_span = '<span style="background:' + status_d["bg"] + ';color:' + status_d["color"] + ';border-radius:6px;padding:4px 12px;font-size:0.82rem;font-weight:800;">● ' + status_d["label"] + '</span>'
            # 이모지 반응 목록
            for r in order_p.get("reactions", []):
                _status_span += ' <span style="background:#f3f4f6;color:#4b5563;border-radius:12px;padding:2px 8px;font-size:0.74rem;">:' + r["name"] + ': ' + str(r["count"]) + '</span>'
        else:
            _status_span = '<span style="background:#f3f4f6;color:#6b7280;border-radius:6px;padding:4px 12px;font-size:0.82rem;font-weight:600;">⏳ 미처리</span>'

        st.markdown('<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px;">' + _status_span + '</div>', unsafe_allow_html=True)

        # 요약 뱃지 카드
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

        # 품목 상세
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
                _rc = WebClient(token=_sl_tk_p)
                existing = [r["name"] for r in order_p.get("reactions", [])]
                if emoji_name in existing:
                    _rc.reactions_remove(channel=_sl_ch_p, timestamp=_ts_p, name=emoji_name)
                else:
                    _rc.reactions_add(channel=_sl_ch_p, timestamp=_ts_p, name=emoji_name)
                st.session_state.pop("sb_slack_orders", None)  # 목록 새로고침
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

                # 파일 캐시 키
                _cache_key = f"preview_bytes_{sel_idx_p}_{fi}"

                # 파일 행 — 이름 + 버튼들
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

                # 미리보기 패널
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

                    # 마스터 파일 로드
                    _mb = st.session_state.get("master_bytes")
                    _master_df_s = None
                    if _mb:
                        _master_df_s = load_master_from_bytes(_mb)
                    elif master_ok:
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

                        # 마스터 상태 표시
                        st.caption(f"마스터 파일: {len(_mlookup)}개 항목 로드됨 · 파일: {len(_preview_bytes)//1024}KB")
                        _run_s = st.button("🔄 변환 시작", type="primary",
                                           use_container_width=True, key=f"run_s_{sel_idx_p}_{fi}")

                        _map_key = f"slack_map_{sel_idx_p}_{fi}"

                        if _run_s:
                            _raw_map_s = []  # 매핑 raw 결과 (원본명 포함)
                            if _fmt == "🛒 네이버":
                                try:
                                    _ndf, _n2s = load_naver(io.BytesIO(_preview_bytes))
                                    _prog_s = st.progress(0, text="변환 중...")
                                    _rows_s = list(_ndf.iterrows())
                                    for _ii, (_, _rr) in enumerate(_rows_s):
                                        _sku_s, _ = resolve_naver_sku(_rr["SKU_원본"], _rr["상품명"], _n2s, _mlookup)
                                        try: _qty_s = int(float(_rr["수량"])) if pd.notna(_rr["수량"]) else 0
                                        except: _qty_s = 0
                                        _matched_name = next((v[2] for v in _mlookup.values() if v[0] == _sku_s), "(건너뜀)")
                                        _price_s = next((v[1] for v in _mlookup.values() if v[0] == _sku_s), "")
                                        _ok = not _sku_s.startswith("UNKNOWN")
                                        _raw_map_s.append({"원본 상품명": _rr["상품명"], "수량": _qty_s,
                                                            "마스터 매핑": _matched_name if _ok else "(건너뜀)",
                                                            "유사도": "✅" if _ok else "❌"})
                                        _prog_s.progress((_ii+1)/len(_rows_s))
                                    _prog_s.empty()
                                except Exception as _ex:
                                    st.error(f"네이버 파일 오류: {_ex}")
                            else:
                                try:
                                    _odf = pd.read_excel(io.BytesIO(_preview_bytes))
                                    _ocols = list(_odf.columns)
                                    _nc = next((c for c in _ocols if "상품명" in c or "name" in c.lower()), _ocols[0])
                                    _qc = next((c for c in _ocols if "수량" in c or "qty" in c.lower()), _ocols[1] if len(_ocols)>1 else _ocols[0])
                                    _grp = _odf.groupby(_nc)[_qc].sum().reset_index().rename(columns={_nc:"상품명",_qc:"수량"})
                                    _nnames = list(_mlookup.keys())
                                    _prog_s = st.progress(0, text="매칭 중...")
                                    for _ii, (_, _rr) in enumerate(_grp.iterrows()):
                                        _best, _score = best_match(_rr["상품명"], _nnames)
                                        _ok = _score >= _thresh_s
                                        _matched_name = _mlookup[_best][2] if _ok else "(건너뜀)"
                                        _raw_map_s.append({"원본 상품명": _rr["상품명"], "수량": int(_rr["수량"]),
                                                            "마스터 매핑": _matched_name,
                                                            "유사도": f"{_score}%" if _ok else f"{_score}% ❌"})
                                        _prog_s.progress((_ii+1)/len(_grp))
                                    _prog_s.empty()
                                except Exception as _ex:
                                    st.error(f"파일 오류: {_ex}")

                            if _raw_map_s:
                                st.session_state[_map_key] = _raw_map_s
                                st.session_state.pop(_staged_key, None)
                                st.rerun()
                            else:
                                st.warning("매칭된 항목이 없습니다. 임계값을 낮추거나 양식을 확인해주세요.")

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
                                    "원본 상품명": st.column_config.TextColumn("원본 상품명", disabled=True, width="medium"),
                                    "수량":        st.column_config.NumberColumn("수량", disabled=True, width="small"),
                                    "마스터 매핑": st.column_config.SelectboxColumn("마스터 매핑 (클릭해서 변경)", options=_all_mnames, width="large"),
                                    "유사도":      st.column_config.TextColumn("유사도", disabled=True, width="small"),
                                },
                                key=f"slack_map_editor_{sel_idx_p}_{fi}",
                            )
                            # 매핑 결과로 staged 구성
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


# ════════════════════════════════════════════════════════════
# 페이지: 출고 요청 (Gmail)
# ════════════════════════════════════════════════════════════
if page == "📧  출고 요청 (Gmail)":
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
            # 발신자에서 이름만 추출
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

        # 발신자 + 제목 카드
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

        # 첨부파일 섹션
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

                # 미리보기 패널
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
                        # 참조 파일 상태 안내
                        _ref_notes = []
                        if not _rg_stats["has_bl"]:   _ref_notes.append("블랙리스트 없음")
                        if not _rg_stats["has_past"]: _ref_notes.append("누적데이터 없음")
                        if _ref_notes:
                            st.caption(f"⚠️ 패턴 분석만 적용 ({', '.join(_ref_notes)} — dashboard/data/ 폴더 확인)")
                        # 요약 카드
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
                        # 위험 행 상세 표시
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
                    elif master_ok:
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
                                            _best, _bscore = None, 0
                                            for _nk, _nv in _mlookup_gm.items():
                                                _sc = fuzz.token_sort_ratio(normalize(_oname), _nk)
                                                if _sc > _bscore:
                                                    _bscore, _best = _sc, _nv
                                            _raw_map_gm.append({"원본 상품명": _oname, "수량": _oqty,
                                                                 "마스터 매핑": _best[2] if _best and _bscore >= _thresh_gm else "(건너뜀)",
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
                                            _best, _bscore = None, 0
                                            for _nk, _nv in _mlookup_gm.items():
                                                _sc = fuzz.token_sort_ratio(normalize(_oname), _nk)
                                                if _sc > _bscore:
                                                    _bscore, _best = _sc, _nv
                                            _raw_map_gm.append({"원본 상품명": _oname, "수량": _oqty,
                                                                 "마스터 매핑": _best[2] if _best and _bscore >= _thresh_gm else "(건너뜀)",
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
