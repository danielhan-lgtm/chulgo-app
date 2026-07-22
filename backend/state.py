import datetime
from typing import Optional

master_bytes: Optional[bytes] = None
gmail_flow = None
logs: list = []
tx_counter: dict = {}

# 재고 대사 드릴다운용 마지막 조회 원시 라인아이템 캐시
# {"key": str, "items": [flat_item, ...]}
reconcile_cache: dict = {}

# OB 채널 캐시 (수집에 ~30초 걸리므로 한 번 수집 후 재사용)
ob_channels_cache: list = []

# full_match 결과 캐시 (key → {"result": dict, "ts": datetime})
full_match_cache: dict = {}

# BH TX 상세 items 캐시 (tx_id → items, 15분 TTL)
# 같은 TX를 여러 번 조회하지 않도록 서버 메모리에 보관
bh_tx_items_cache: dict = {}  # {tx_id: {"items": [...], "ts": datetime}}


def add_log(level: str, message: str, detail: str = "", payload: dict = None, source: str = ""):
    global logs, tx_counter
    entry = {
        "ts": datetime.datetime.now().strftime("%m-%d %H:%M:%S"),
        "level": level,
        "message": message,
        "detail": detail,
        "payload": payload,
        "source": source,
    }
    logs.insert(0, entry)
    today = datetime.date.today().isoformat()
    if today not in tx_counter:
        tx_counter[today] = {"success": 0, "error": 0, "total": 0}
    tx_counter[today]["total"] += 1
    if level == "success":
        tx_counter[today]["success"] += 1
    elif level == "error":
        tx_counter[today]["error"] += 1
