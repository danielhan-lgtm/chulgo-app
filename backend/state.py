import datetime
from typing import Optional

master_bytes: Optional[bytes] = None
gmail_flow = None
logs: list = []
tx_counter: dict = {}


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
