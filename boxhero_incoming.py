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


def fetch_all_items_list(token: str) -> list:
    items, cursor = [], None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{API_BASE}/items", headers=_headers(token), params=params)
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return items
