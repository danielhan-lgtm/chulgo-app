# -*- coding: utf-8 -*-
"""적재리스트(쿠팡 밀크런/로켓그로스) 입수·팔레트 설정 저장소.

BOX 수량 추정 로직(1박스당 수량, 파레트당 박스 수 등)이 코드에 하드코딩되어 있으면
로직이 틀릴 때마다 코드를 고쳐야 하므로, 사용자가 화면에서 직접 설정/저장할 수 있게
data/load_settings.json 에 보관한다.

박스 수 계산 우선순위:
  1) rules — match(상품번호 전체 일치 또는 상품명 키워드 포함) → 1박스당 수량
  2) bundle_is_box=True 이고 상품명에 번들 표기(3개입·x3 등) → 박스수 = 수량
  3) default_per_box — 기본 1박스당 수량
"""
import copy
import json
import os
import re
import threading
import unicodedata

SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "load_settings.json"
)
_lock = threading.Lock()

DEFAULTS: dict = {
    # 쿠팡 밀크런 적재리스트
    "coupang": {
        "pallet_cap": 112,            # 파레트당 최대 박스 수
        "pallet_total_mode": "auto",  # auto=용량으로 계산 | attach=부착리스트 값 | fixed=고정값
        "pallet_total_fixed": 1,
        "default_per_box": 9,         # 기본 1박스당 수량
        "bundle_is_box": True,        # N개입/x3 등 번들 표기 상품은 수량=박스수
        "rules": [
            {"match": "스내피", "per_box": 10},
            {"match": "저키", "per_box": 12},
            {"match": "클로브스", "per_box": 12},
        ],
    },
    # 로켓그로스 적재리스트
    "growth": {
        "pallet_cap": 112,
        "pallet_total_mode": "auto",
        "pallet_total_fixed": 1,
        "default_per_box": 132,
        "bundle_is_box": False,
        "rules": [],
    },
}

# 번들(멀티팩) 표기 판별 — 3개 / 3개입 / 3ea / 3팩 / *3 / x3 ...
BUNDLE_RE = re.compile(
    r"(?:\d+\s*(?:개입|개|ea|입|팩|pack|pk|p(?![a-z])|구|세트|set|매|포))"
    r"|(?:[*xX×]\s*\d+)",
    re.IGNORECASE,
)


def _read_file() -> dict:
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_settings(section: str) -> dict:
    """저장된 설정에 기본값을 채워 반환한다."""
    base = copy.deepcopy(DEFAULTS.get(section, {}))
    saved = _read_file().get(section, {})
    if isinstance(saved, dict):
        base.update(saved)
    return base


def save_settings(section: str, data: dict) -> dict:
    with _lock:
        all_data = _read_file()
        all_data[section] = data
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
    return get_settings(section)


def guess_box(section_or_settings, sku: str, name: str, qty: int) -> int:
    """설정 기반으로 BOX 수량을 추정한다. (화면에서 언제든 수정 가능)"""
    if qty <= 0:
        return 0
    s = (
        section_or_settings
        if isinstance(section_or_settings, dict)
        else get_settings(section_or_settings)
    )
    n = unicodedata.normalize("NFKC", name or "")
    sku = str(sku or "").strip()

    # 1) 사용자 규칙 (위에서부터 먼저 매칭되는 규칙 적용)
    for rule in s.get("rules", []):
        match = str(rule.get("match", "")).strip()
        if not match:
            continue
        hit = (match == sku) if match.isdigit() else (match.lower() in n.lower())
        if hit:
            per = max(1, int(rule.get("per_box", 1) or 1))
            return -(-qty // per)  # ceil

    # 2) 번들 표기 → 1출고수 = 1박스
    if s.get("bundle_is_box") and BUNDLE_RE.search(n):
        return qty

    # 3) 기본 입수
    per = max(1, int(s.get("default_per_box", 1) or 1))
    return -(-qty // per)
