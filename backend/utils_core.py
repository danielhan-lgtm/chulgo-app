import io
import os
import re
import json
import base64
import datetime
import email.utils
import functools
import requests
from typing import Optional
from rapidfuzz import fuzz

BASE_URL = "https://rest.boxhero-app.com"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_SENDERS = [
    "inn1246919@nate.com",
    "gy.lee12@cj.net",
    "lgl10910@lglpartner.com",
    "wonkyoung.hwang@cj.net",
]

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
    return cfg


def save_config(data: dict):
    cfg = load_config()
    cfg.update(data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def api_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def fetch_all_items(token: str) -> dict:
    """SKU → item_id 매핑 (페이지네이션)"""
    sku_to_id = {}
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE_URL}/v1/items", headers=api_headers(token), params=params, timeout=15)
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


def fetch_all_items_list(token: str) -> list:
    """BoxHero 전체 상품 목록 (id, name, sku 포함)"""
    items, cursor = [], None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE_URL}/v1/items", headers=api_headers(token), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return items


def fetch_locations(token: str) -> list:
    r = requests.get(f"{BASE_URL}/v1/locations", headers=api_headers(token), timeout=15)
    r.raise_for_status()
    return r.json().get("items", [])


def fetch_partners(token: str) -> list:
    items, cursor = [], None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE_URL}/v1/partners", headers=api_headers(token), params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return items


def post_transaction(token: str, payload: dict) -> dict:
    r = requests.post(f"{BASE_URL}/v1/location-txs", headers=api_headers(token), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def delete_transaction(token: str, tx_id: int) -> dict:
    r = requests.delete(f"{BASE_URL}/v1/location-txs/{tx_id}", headers=api_headers(token), timeout=15)
    r.raise_for_status()
    return r.json() if r.content else {}


# ── Slack ─────────────────────────────────────────────────────────────────────

def reaction_to_status(reactions: list) -> Optional[dict]:
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
        "x": ("반려", "#ef4444", "#fee2e2"),
        "negative_squared_cross_mark": ("반려", "#ef4444", "#fee2e2"),
        "반려": ("반려", "#ef4444", "#fee2e2"),
        "eyes": ("확인중", "#6366f1", "#ede9fe"),
    }
    priority = ["완료", "반려", "진행중", "확인중"]
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
            sec_lines = [l.strip().lstrip("-•●*").strip() for l in content.split("\n") if l.strip()]
            info[key] = "\n".join(sec_lines)
    return info


def fetch_slack_channels(token: str) -> dict:
    from slack_sdk import WebClient
    client = WebClient(token=token)
    channels = {}
    cursor = None
    while True:
        result = client.conversations_list(
            types="public_channel,private_channel", limit=1000, cursor=cursor
        )
        for ch in result["channels"]:
            channels[ch["name"]] = ch["id"]
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return channels


def fetch_slack_orders(token: str, channel_id: str) -> tuple:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    client = WebClient(token=token)
    orders = []
    debug = ""
    try:
        result = client.conversations_history(channel=channel_id, limit=100)
        all_msgs = result.get("messages", [])
        debug = f"총 {len(all_msgs)}개 메시지 조회됨"
        for msg in all_msgs:
            text = msg.get("text", "")
            excel_files = [
                {"name": f.get("name", ""), "url": f.get("url_private_download"), "size": f.get("size", 0)}
                for f in msg.get("files", [])
                if f.get("name", "").endswith((".xlsx", ".xls"))
            ]
            if not text.strip() and not excel_files and not msg.get("files"):
                continue
            parsed = parse_order_message(text)
            ts = float(msg.get("ts", 0))
            dt = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
            raw_reactions = msg.get("reactions", [])
            reactions = [{"name": r["name"], "count": r["count"]} for r in raw_reactions]
            orders.append({
                "ts": msg.get("ts", ""),
                "dt": dt,
                "title": parsed.get("제목", text[:40]) if text else "(파일만 첨부)",
                "parsed": parsed,
                "files": excel_files,
                "raw": text,
                "reactions": reactions,
            })
    except SlackApiError as e:
        err = e.response["error"]
        debug = err
    orders.sort(key=lambda x: x["ts"], reverse=True)
    return orders, debug


def download_slack_file(url: str, token: str) -> bytes:
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return resp.content


def slack_toggle_reaction(token: str, channel_id: str, ts: str, emoji_name: str) -> str:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    client = WebClient(token=token)
    try:
        client.reactions_add(channel=channel_id, timestamp=ts, name=emoji_name)
        return "added"
    except SlackApiError as e:
        if e.response["error"] == "already_reacted":
            client.reactions_remove(channel=channel_id, timestamp=ts, name=emoji_name)
            return "removed"
        raise


def slack_join_channel(token: str, channel_id: str):
    from slack_sdk import WebClient
    client = WebClient(token=token)
    client.conversations_join(channel=channel_id)


# ── Gmail ─────────────────────────────────────────────────────────────────────

def gmail_build_flow(client_id: str, client_secret: str, redirect_uri: str):
    from google_auth_oauthlib.flow import Flow
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(client_config, scopes=GMAIL_SCOPES, redirect_uri=redirect_uri)


def gmail_get_service(token_info: dict):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from googleapiclient.discovery import build

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

    return build("gmail", "v1", credentials=creds)


def _extract_gmail_parts(payload: dict) -> list:
    parts = []
    if "parts" in payload:
        for p in payload["parts"]:
            parts.extend(_extract_gmail_parts(p))
    else:
        parts.append(payload)
    return parts


def fetch_gmail_orders(token_info: dict) -> tuple:
    query = " OR ".join(f"from:{s}" for s in GMAIL_SENDERS)
    orders = []
    debug = ""
    try:
        service = gmail_get_service(token_info)
        result = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
        messages = result.get("messages", [])
        debug = f"총 {len(messages)}개 메일 조회됨"
        if not messages:
            return orders, debug

        raw_msgs = {}

        def _batch_callback(request_id, response, exception=None):
            if response:
                raw_msgs[request_id] = response

        batch = service.new_batch_http_request(callback=_batch_callback)
        for m in messages:
            batch.add(
                service.users().messages().get(
                    userId="me", id=m["id"], format="full", fields="id,payload"
                ),
                request_id=m["id"],
            )
        batch.execute()

        for msg_id, msg in raw_msgs.items():
            try:
                headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
                subject = headers.get("Subject", "(제목 없음)")
                sender = headers.get("From", "")
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
                                "message_id": msg_id,
                            })
                orders.append({
                    "id": msg_id,
                    "dt": dt,
                    "subject": subject,
                    "sender": sender,
                    "files": excel_files,
                })
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


# ── Excel Processing ──────────────────────────────────────────────────────────

import pandas as pd


@functools.lru_cache(maxsize=4096)
def normalize(text: str) -> str:
    text = str(text)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"DJA\d+-", "", text)
    text = re.sub(r"BTB\d+-", "", text)
    text = re.sub(r"디제이앤에이\s*", "", text)
    text = re.sub(
        r"\d+\s*(g|kg|ml|l|mg)\b",
        lambda m: m.group().replace(" ", "").lower(),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[\s\-_]+", " ", text)
    text = text.replace("프레첼", "프레젤").replace("프레즐", "프레젤").replace("머쉬룸", "머시룸")
    return text.strip()


def best_match(query: str, choices: list) -> tuple:
    norm_q = normalize(query)
    q_tokens = set(norm_q.split())
    candidates = {}
    for choice in choices:
        sort_score = fuzz.token_sort_ratio(norm_q, choice)
        set_score = fuzz.token_set_ratio(norm_q, choice)
        c_tokens = set(choice.split())
        extra = len(c_tokens - q_tokens)
        penalty = extra * 5
        score = max(sort_score, set_score - penalty)
        candidates[choice] = max(0, score)
    if not candidates:
        return "", 0
    best = max(candidates, key=candidates.get)
    return best, candidates[best]


def load_master_from_bytes(data: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(data))


def load_master_from_path(path: str) -> pd.DataFrame:
    return pd.read_excel(path)


def build_master_lookup(master_df: pd.DataFrame) -> dict:
    """Returns {norm_name: (sku, price, orig_name)}"""
    cols = list(master_df.columns)
    m_sku_col = next((c for c in cols if str(c).strip() == "SKU"), cols[0])
    m_name_col = next((c for c in cols if "제품명" in str(c) or "상품명" in str(c)), cols[2] if len(cols) > 2 else cols[0])
    m_price_col = next((c for c in cols if "구매가" in str(c)), None)

    lookup = {}
    for _, row in master_df.iterrows():
        orig = str(row[m_name_col])
        norm = normalize(orig)
        lookup[norm] = (
            str(row[m_sku_col]) if pd.notna(row[m_sku_col]) else "",
            float(row[m_price_col]) if m_price_col and pd.notna(row[m_price_col]) else 0,
            orig,
        )
    return lookup


def load_naver(data: bytes) -> tuple:
    """네이버 출고 파일 로드 → (DataFrame, boxhero_name_to_sku)"""
    buf = io.BytesIO(data)
    raw = pd.read_excel(buf, sheet_name="양식", header=None, dtype=str)
    df = raw.iloc[11:].copy().reset_index(drop=True)
    df.columns = [
        "idx", "출고요청일", "주문번호", "SKU_원본", "상품명", "수량",
        "출고지", "수취인", "연락처", "배송지주소", "상세주소",
        "배송방법", "택배사", "송장번호", "거래처", "출고사유", "담당자", "발송인정보",
    ]
    df = df[df["상품명"].notna() & (df["상품명"].str.strip() != "")].copy()

    name_to_sku = {}
    try:
        buf.seek(0)
        bh = pd.read_excel(buf, sheet_name="BoxHero", header=0, dtype=str)
        for _, row in bh.iterrows():
            if pd.notna(row.get("제품명")) and pd.notna(row.get("SKU")):
                name_to_sku[str(row["제품명"]).strip()] = str(row["SKU"]).strip()
    except Exception:
        pass

    return df, name_to_sku


def resolve_naver_sku(sku_raw, product_name, name_to_sku: dict, master_lookup: dict) -> tuple:
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
