"""박스히어로 입고 전용 API 함수 (utils.py 의존성 없음)"""
import requests
import datetime

API_BASE = "https://rest.boxhero-app.com/v1"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def create_in_transaction(token: str, location_id: int, items: list, memo: str = "", tx_time: str = None) -> dict:
    payload = {
        "type": "in",
        "to_location_id": location_id,
        "items": items,
        "memo": memo,
    }
    if tx_time:
        payload["tx_time"] = tx_time
    r = requests.post(f"{API_BASE}/location-txs", headers=_headers(token), json=payload)
    r.raise_for_status()
    return r.json()


def delete_transaction(token: str, tx_id: int) -> dict:
    r = requests.delete(f"{API_BASE}/location-txs/{tx_id}", headers=_headers(token))
    r.raise_for_status()
    return r.json() if r.content else {}


def _get_with_retry(url: str, token: str, params: dict, tries: int = 3, timeout: int = 20):
    """BoxHero API가 간헐적으로 지연/5xx를 내므로 타임아웃+재시도."""
    import time
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=_headers(token), params=params, timeout=timeout)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(f"BoxHero {r.status_code}: {r.text[:200]}", response=r)
            r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def fetch_all_items_list(token: str) -> list:
    items, cursor = [], None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = _get_with_retry(f"{API_BASE}/items", token, params)
        data = r.json()
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return items
