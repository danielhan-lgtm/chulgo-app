import io
import os
import re
import json
import base64
import datetime
import functools
import shutil
import threading
import email.utils
import requests
from typing import Optional
from rapidfuzz import fuzz

BASE_URL = "https://rest.boxhero-app.com"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
_CONFIG_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "config_backups")
# 동시 저장(여러 스레드/요청) 시 read-modify-write 경쟁으로 파일이 통째로 날아가는 것을 막는 락
_config_lock = threading.RLock()

# ── BH TX 목록 파일 캐시 ─────────────────────────────────────────────────────
_BH_TX_LIST_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bh_txlist_cache.json")
_bh_txlist_cache: dict = {}
_bh_txlist_cache_loaded = False

def _load_bh_txlist_cache():
    global _bh_txlist_cache, _bh_txlist_cache_loaded
    if _bh_txlist_cache_loaded:
        return
    try:
        if os.path.exists(_BH_TX_LIST_CACHE_PATH):
            with open(_BH_TX_LIST_CACHE_PATH, "r", encoding="utf-8") as f:
                _bh_txlist_cache = json.load(f)
    except Exception:
        _bh_txlist_cache = {}
    # 월 청크 도입 전의 범위 키(시작일이 1일이 아님)는 더 이상 조회되지 않음 — 파일 비대 방지 위해 제거
    for k in [k for k in _bh_txlist_cache
              if len(k.split("|")) == 5 and len(k.split("|")[2]) == 10 and k.split("|")[2][8:10] != "01"]:
        del _bh_txlist_cache[k]
    _bh_txlist_cache_loaded = True

def _flush_bh_txlist_cache():
    try:
        os.makedirs(os.path.dirname(_BH_TX_LIST_CACHE_PATH), exist_ok=True)
        with open(_BH_TX_LIST_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_bh_txlist_cache, f, ensure_ascii=False)
    except Exception:
        pass

def _save_bh_txlist_cache(key: str, txs: list, flush: bool = True):
    global _bh_txlist_cache
    _bh_txlist_cache[key] = {"txs": txs, "ts": datetime.datetime.now().isoformat()}
    if flush:
        _flush_bh_txlist_cache()

def _get_bh_txlist_cache(key: str, to_date: str) -> Optional[list]:
    _load_bh_txlist_cache()
    entry = _bh_txlist_cache.get(key)
    if not entry:
        return None
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if to_date < today:
        return entry["txs"]  # 완전 과거 → 영구
    # 오늘 포함 → 1시간 TTL
    try:
        ts = datetime.datetime.fromisoformat(entry["ts"])
        if (datetime.datetime.now() - ts).seconds < 3600:
            return entry["txs"]
    except Exception:
        pass
    return None

def invalidate_bh_txlist_cache(from_date: str = "", to_date: str = ""):
    """BH TX 목록 캐시 무효화 (강제 새로고침용).
    범위 지정 시 그 범위와 겹치는 월 청크 키(`…|m|YYYY-MM|…`)와 범위 키를 모두 삭제."""
    global _bh_txlist_cache
    _load_bh_txlist_cache()
    if from_date and to_date:
        fm, tm = from_date[:7], to_date[:7]
        keys_to_del = []
        for k in _bh_txlist_cache:
            parts = k.split("|")
            if len(parts) != 5:
                continue
            if parts[2] == "m":
                if fm <= parts[3] <= tm:
                    keys_to_del.append(k)
            elif parts[2] <= to_date and parts[3] >= from_date:
                keys_to_del.append(k)
        for k in keys_to_del:
            del _bh_txlist_cache[k]
    else:
        _bh_txlist_cache = {}
    _flush_bh_txlist_cache()

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


def _read_config_raw() -> tuple[dict, bool]:
    """config.json 로드. 반환 (cfg, ok). ok=False면 파일이 손상/파싱 불가."""
    if not os.path.exists(CONFIG_PATH):
        return {}, True  # 파일 없음은 정상(최초 실행)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f), True
    except Exception:
        return {}, False  # 파싱 실패 — 절대 이 위에 덮어쓰면 안 됨(데이터 유실)


def load_config() -> dict:
    with _config_lock:
        cfg, _ok = _read_config_raw()
        return cfg


def _backup_config():
    """저장 직전 현재 config.json을 타임스탬프 백업 (최근 30개 유지)."""
    try:
        if not os.path.exists(CONFIG_PATH) or os.path.getsize(CONFIG_PATH) < 5:
            return
        os.makedirs(_CONFIG_BACKUP_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(CONFIG_PATH, os.path.join(_CONFIG_BACKUP_DIR, f"config_{ts}.json"))
        backups = sorted(f for f in os.listdir(_CONFIG_BACKUP_DIR) if f.startswith("config_"))
        for old in backups[:-30]:
            try:
                os.remove(os.path.join(_CONFIG_BACKUP_DIR, old))
            except Exception:
                pass
    except Exception:
        pass


def save_config(data: dict):
    """config.json 병합 저장 — 락으로 동시성 보호 + 원자적 쓰기 + 자동 백업.

    핵심 안전장치: 기존 파일이 손상돼 파싱 안 되면(빈 dict 반환+ok=False),
    그 위에 덮어쓰지 않고 예외를 던진다. (예전엔 손상 파일을 {}로 읽어
    새 키만 써버려 전체 설정이 날아가는 버그가 있었음.)
    """
    if not data:
        return
    with _config_lock:
        cfg, ok = _read_config_raw()
        if not ok:
            # 파일이 손상됨 — 백업만 남기고 병합 저장 중단(데이터 유실 방지)
            _backup_config()
            raise RuntimeError("config.json 파싱 실패 — 손상 의심. 덮어쓰기 중단(수동 확인 필요).")
        _backup_config()
        cfg.update(data)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)  # 원자적 교체 — 중간에 끊겨도 원본 보존


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


def fetch_bh_adjust(token: str, from_date: str, to_date: str) -> list:
    """BoxHero 조정(adjust) 내역 조회 — /v1/txs?type=adjust 엔드포인트 사용."""
    import time as _time
    txs = []
    cursor = None
    from_dt = datetime.datetime.fromisoformat(from_date)
    to_dt = datetime.datetime.fromisoformat(to_date) + datetime.timedelta(days=1)
    while True:
        params = {"type": "adjust", "limit": 100}
        if cursor: params["cursor"] = cursor
        for attempt in range(5):
            try:
                r = requests.get(f"{BASE_URL}/v1/txs", headers=api_headers(token), params=params, timeout=20)
                if r.status_code == 429:
                    _time.sleep(2.0 * (2 ** attempt))
                    continue
                r.raise_for_status()
                break
            except requests.exceptions.HTTPError:
                raise
            except Exception:
                if attempt == 4: raise
                _time.sleep(2.0 * (attempt + 1))
        data = r.json()
        items = data.get("items", [])
        stop = False
        for tx in items:
            tx_time_str = tx.get("transaction_time") or tx.get("created_at", "")
            try: tx_dt = datetime.datetime.fromisoformat(tx_time_str[:19])
            except: continue
            if tx_dt < from_dt: stop = True; break
            if tx_dt <= to_dt: txs.append(tx)
        if stop or not data.get("has_more"): break
        cursor = data.get("cursor")
    return txs


def _fetch_bh_txs_paginated(token: str, tx_type: str, from_date: str, to_date: str, location_id: int = None) -> list:
    """BoxHero 거래를 API에서 직접 페이지네이션 수집 (캐시 미사용).
    목록 API는 날짜 필터가 없어 최신 거래부터 from_date까지 역순으로 걷는다."""
    import time as _time
    if tx_type == "adjust":
        return fetch_bh_adjust(token, from_date, to_date)

    txs = []
    cursor = None
    from_dt = datetime.datetime.fromisoformat(from_date)
    to_dt = datetime.datetime.fromisoformat(to_date) + datetime.timedelta(days=1)

    while True:
        params = {"type": tx_type, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        if location_id:
            params["location_id"] = location_id

        # 429 레이트 리밋 재시도 (최대 5회, 지수 백오프)
        for attempt in range(5):
            try:
                r = requests.get(f"{BASE_URL}/v1/location-txs", headers=api_headers(token), params=params, timeout=20)
                if r.status_code == 429:
                    wait = 2.0 * (2 ** attempt)  # 2s, 4s, 8s, 16s, 32s
                    _time.sleep(wait)
                    continue
                r.raise_for_status()
                break
            except requests.exceptions.HTTPError:
                raise
            except Exception:
                if attempt == 4:
                    raise
                _time.sleep(2.0 * (attempt + 1))
        else:
            raise RuntimeError(f"BoxHero API 429 Too Many Requests — 재시도 초과 (tx_type={tx_type})")

        data = r.json()
        items = data.get("items", [])

        for tx in items:
            tx_time_str = tx.get("transaction_time") or tx.get("created_at", "")
            try:
                tx_dt = datetime.datetime.fromisoformat(tx_time_str[:19])
            except Exception:
                continue
            if tx_dt < from_dt:
                # from_date 이전 도달 = 수집 완료
                return txs
            if tx_dt < to_dt:
                txs.append(tx)

        if not data.get("has_more"):
            break
        cursor = data.get("cursor")

    return txs


def _bh_month_segments(from_date: str, to_date: str) -> list:
    """[from,to]를 월 세그먼트로 분해 — (YYYY-MM, 월시작, 월말일, 세그먼트끝) 목록.
    세그먼트끝은 완결 달이면 월말일, 기간 끝이 달 중간이면 to_date."""
    segs = []
    y, m = int(from_date[:4]), int(from_date[5:7])
    while True:
        ym = f"{y:04d}-{m:02d}"
        if m == 12:
            ny, nm = y + 1, 1
        else:
            ny, nm = y, m + 1
        m_end = (datetime.date(ny, nm, 1) - datetime.timedelta(days=1)).isoformat()
        segs.append((ym, f"{ym}-01", m_end, min(m_end, to_date)))
        if ym >= to_date[:7]:
            break
        y, m = ny, nm
    return segs


def fetch_transactions(token: str, tx_type: str, from_date: str, to_date: str, location_id: int = None, use_cache: bool = True) -> list:
    """BoxHero 거래 내역 조회 (in/out/move/adjust 지원). 날짜는 'YYYY-MM-DD' 형식.
    use_cache=True(기본): 월 단위 청크 캐시 — 완결된 달은 영구, 진행 중인 달은 1시간 TTL.
    기간이 아무리 길어도(6개월·1년) 캐시에 없는 달(보통 이번 달)만 API를 다시 걷는다."""
    if tx_type not in {"in", "out", "move", "adjust"}:
        return []
    if not use_cache or from_date > to_date:
        return _fetch_bh_txs_paginated(token, tx_type, from_date, to_date, location_id)

    _load_bh_txlist_cache()
    segs = []
    for ym, m_start, m_end, seg_end in _bh_month_segments(from_date, to_date):
        if seg_end == m_end:
            key = f"{token[:8]}|{tx_type}|m|{ym}|{location_id}"     # 완결 달 → 영구
        else:
            key = f"{token[:8]}|{tx_type}|{m_start}|{seg_end}|{location_id}"  # 부분 달(보통 이번 달)
        segs.append({"ym": ym, "start": m_start, "key": key,
                     "txs": _get_bh_txlist_cache(key, seg_end)})

    missing = [s for s in segs if s["txs"] is None]
    if missing:
        # API는 최신→과거 역순 페이지네이션뿐이라, 가장 오래된 미캐시 달부터 to까지
        # 한 번만 걷고 그 구간의 달들을 전부 새 데이터로 교체 저장
        fetch_from = min(s["start"] for s in missing)
        fresh = _fetch_bh_txs_paginated(token, tx_type, fetch_from, to_date, location_id)
        refreshed = {s["ym"]: s for s in segs if s["start"] >= fetch_from}
        for s in refreshed.values():
            s["txs"] = []
        for tx in fresh:
            ym = str(tx.get("transaction_time") or tx.get("created_at") or "")[:7]
            seg = refreshed.get(ym)
            if seg is not None:
                seg["txs"].append(tx)
        for s in refreshed.values():
            _save_bh_txlist_cache(s["key"], s["txs"], flush=False)
        _flush_bh_txlist_cache()

    out = []
    for s in segs:
        out.extend(s["txs"] or [])
    if from_date[8:10] != "01":
        # 첫 달은 월 전체가 캐시돼 있으므로 from_date 이전 거래 제거
        from_dt = datetime.datetime.fromisoformat(from_date)
        def _keep(tx):
            try:
                return datetime.datetime.fromisoformat(str(tx.get("transaction_time") or tx.get("created_at") or "")[:19]) >= from_dt
            except Exception:
                return False
        out = [tx for tx in out if _keep(tx)]
    out.sort(key=lambda tx: str(tx.get("transaction_time") or tx.get("created_at") or ""), reverse=True)
    return out


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


# 슬랙 사용자 ID → 표시 이름 캐시 (프로세스 생존 동안 유지; users:read 스코프 없으면 ID 그대로)
_slack_user_names: dict = {}

def _resolve_slack_user(client, uid: str) -> str:
    if uid in _slack_user_names:
        return _slack_user_names[uid]
    try:
        info = client.users_info(user=uid)
        u = info.get("user", {}) or {}
        prof = u.get("profile", {}) or {}
        name = prof.get("display_name") or prof.get("real_name") or u.get("real_name") or u.get("name") or uid
        _slack_user_names[uid] = name  # 성공만 캐시 — users:read 스코프 추가 즉시 반영되도록
        return name
    except Exception:
        return uid


def _replace_user_mentions(client, text: str) -> str:
    if "<@" not in text:
        return text
    return re.sub(r"<@([A-Z0-9]+)>", lambda m: "@" + _resolve_slack_user(client, m.group(1)), text)


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
            text = _replace_user_mentions(client, msg.get("text", ""))
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


def slack_post_message(token: str, channel_id: str, text: str) -> dict:
    """봇으로 채널에 메시지 전송 (chat.postMessage)."""
    from slack_sdk import WebClient
    client = WebClient(token=token)
    res = client.chat_postMessage(channel=channel_id, text=text)
    return {"ts": res.get("ts"), "channel": res.get("channel")}


def slack_delete_message(token: str, channel_id: str, ts: str) -> dict:
    """봇이 올린 메시지 삭제 (chat.delete)."""
    from slack_sdk import WebClient
    client = WebClient(token=token)
    r = client.chat_delete(channel=channel_id, ts=ts)
    return {"ok": r.get("ok")}


def slack_recent_texts(token: str, channel_id: str, limit: int = 100) -> list:
    """채널 최근 메시지 텍스트 목록 (중복 판정용)."""
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    client = WebClient(token=token)
    try:
        res = client.conversations_history(channel=channel_id, limit=limit)
        return [m.get("text", "") or "" for m in res.get("messages", [])]
    except SlackApiError:
        return []


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def notify_outbound_to_slack(token: str, channel_id: str, items: list, memo: str = "",
                             title: str = "", partner: str = "") -> dict:
    """BH 출고 내역을 출고 슬랙 채널에 요약 포스팅 (중복 항목 제외).

    items: [{"name": 상품명, "sku": sku, "qty": 수량}]
    partner: BH 거래처명 (예: '자사몰(카페24)') — 중복 판정에 반영.
    중복 판정: 최근 채널 메시지(사람·봇 모두)에 '같은 상품(이름 일부)+수량'이 있고,
      거래처가 지정된 경우 그 거래처(키워드)까지 함께 언급된 메시지만 중복으로 본다.
      → 같은 상품·수량이라도 거래처가 다르면 별개로 취급(중복 아님).
    반환: {posted: bool, ts, posted_items, skipped_items}
    """
    if not token or not channel_id or not items:
        return {"posted": False, "reason": "no_input"}
    recent = slack_recent_texts(token, channel_id, 100)
    recent_norm = [_norm_key(t) for t in recent]
    pk = _norm_key(partner)
    # 거래처명이 괄호를 포함하면 핵심 토큰(괄호 안/밖 중 긴 쪽)도 함께 대조
    pk_alt = ""
    if partner:
        toks = re.findall(r"[가-힣A-Za-z0-9]+", partner)
        pk_alt = _norm_key(max(toks, key=len)) if toks else ""

    def is_dup(name, qty):
        nm = _norm_key(name)
        base = re.split(r"\d", nm)[0]   # 첫 숫자 전까지 = 상품 기본명(옵션/용량 제외)
        key = base if len(base) >= 2 else nm[:6]
        q = str(qty)
        if not key:
            return False
        for t in recent_norm:
            if key not in t or q not in t:
                continue
            # 거래처 지정 시: 그 거래처가 함께 언급된 메시지만 중복으로 인정
            if pk:
                if (pk in t) or (pk_alt and pk_alt in t):
                    return True
                continue
            return True
        return False

    posted_items, skipped = [], []
    for it in items:
        name = it.get("name") or it.get("sku") or ""
        qty = int(it.get("qty") or it.get("quantity") or 0)
        if qty <= 0:
            continue
        (skipped if is_dup(name, qty) else posted_items).append(
            {"name": name, "sku": it.get("sku", ""), "qty": qty})

    if not posted_items:
        return {"posted": False, "reason": "all_duplicate", "skipped_items": skipped}

    lines = [title or "📦 *박스히어로 출고 등록*"]
    if partner:
        lines.append(f"🏢 거래처: *{partner}*")
    if memo:
        lines.append(f"_{memo}_")
    total = 0
    for it in posted_items:
        total += it["qty"]
        sku = f" `{it['sku']}`" if it["sku"] else ""
        lines.append(f"• {it['name']}{sku} — *{it['qty']}개*")
    lines.append(f"— 합계 {len(posted_items)}종 · {total}개")
    if skipped:
        lines.append(f"(중복 {len(skipped)}건 제외)")
    res = slack_post_message(token, channel_id, "\n".join(lines))
    return {"posted": True, "ts": res.get("ts"), "posted_items": posted_items,
            "skipped_items": skipped}


def notify_bh_outbound(items: list, memo: str = "", title: str = "", partner: str = "") -> dict:
    """설정 기반 BH 출고 슬랙 알림 (config: slack_outbound_notify/channel).

    실패해도 예외를 올리지 않는다(출고 자체를 막지 않기 위해).
    items: [{name?, sku, qty|quantity}], partner: BH 거래처명(중복판정 반영)
    """
    try:
        cfg = load_config()
        if not cfg.get("slack_outbound_notify"):
            return {"posted": False, "reason": "disabled"}
        token = cfg.get("slack_token", "")
        ch = str(cfg.get("slack_outbound_channel") or "물류_출고").strip()
        if not token:
            return {"posted": False, "reason": "no_token"}
        channel_id = ch
        if not re.fullmatch(r"[CGD][A-Z0-9]{6,}", ch):  # 이름이면 id로 변환
            try:
                channel_id = fetch_slack_channels(token).get(ch, "")
            except Exception:
                channel_id = ""
        if not channel_id:
            return {"posted": False, "reason": "channel_not_found"}
        return notify_outbound_to_slack(token, channel_id, items, memo, title, partner)
    except Exception as e:  # noqa: BLE001
        return {"posted": False, "reason": f"error:{str(e)[:100]}"}


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
    # 대괄호 태그 제거: [GWP], [증정] 등
    text = re.sub(r"\[.*?\]", "", text)
    # 선행 상품코드 접두어 제거: "DJA4025-", "BTB1002-", "DJA4087", "A1002_" 등
    text = re.sub(r"^\s*[A-Za-z]{2,5}\d{2,}\s*[-_]?\s*", "", text)
    # 본문 중간/잔여 코드 패턴도 한 번 더 정리
    text = re.sub(r"\b[A-Za-z]{2,5}\d{3,}[-_]", "", text)
    text = re.sub(r"디제이앤에이\s*", "", text)
    # 날짜/유통기한 패턴 제거 (OB에서 상품명 끝에 lot/expiry가 붙는 경우 처리)
    # YYYY-MM-DD, YYYY/MM/DD, YYYY MM DD 형태 모두 제거
    text = re.sub(r"\b20\d{2}[\s\-./]\d{1,2}[\s\-./]\d{1,2}\b", "", text)
    # 연도만 단독으로 끝에 붙은 경우 제거 (예: "메노포즈 2028")
    text = re.sub(r"\s+20\d{2}\s*$", "", text)
    text = re.sub(
        r"\d+\s*(g|kg|ml|l|mg)\b",
        lambda m: m.group().replace(" ", "").lower(),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[\s\-_]+", " ", text)
    text = text.replace("프레첼", "프레젤").replace("프레즐", "프레젤").replace("머쉬룸", "머시룸")
    return text.strip()


_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)(kg|g|mg|l|ml)\b", re.IGNORECASE)


def _extract_sizes(text: str) -> set:
    """문자열에서 규격(예: 60g, 1.5l, 100ml) 토큰만 추출."""
    out = set()
    for m in _SIZE_RE.finditer(text or ""):
        out.add(f"{m.group(1).lower()}{m.group(2).lower()}")
    return out


def best_match(query: str, choices: list) -> tuple:
    norm_q = normalize(query)
    q_tokens = set(norm_q.split())
    q_sizes = _extract_sizes(norm_q)
    candidates = {}
    for choice in choices:
        sort_score = fuzz.token_sort_ratio(norm_q, choice)
        set_score = fuzz.token_set_ratio(norm_q, choice)
        c_tokens = set(choice.split())
        # 양쪽 잉여 토큰 모두 페널티: 마스터의 잉여(저키/오리지널 같은) + 입력의 잉여
        extra_c = len(c_tokens - q_tokens)
        extra_q = len(q_tokens - c_tokens)
        penalty = extra_c * 8 + extra_q * 6
        # 규격 불일치 강제 감점 — 60g vs 65g 같은 케이스에서 무관 매칭 차단
        c_sizes = _extract_sizes(choice)
        if q_sizes and c_sizes and not (q_sizes & c_sizes):
            penalty += 40
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
