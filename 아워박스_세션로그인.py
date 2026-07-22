# -*- coding: utf-8 -*-
"""아워박스 수동 로그인 → 자동화용 세션 저장.

CAPTCHA(보안 인증) 때문에 자동 로그인이 막혔을 때 사용:
  1. 이 스크립트를 실행하면 일반 Chrome 창이 뜸 (자동화 도구 미연결 → '사람인가요' 통과됨)
  2. 직접 로그인 (보안문자 포함)
  3. 메인 화면 도달이 감지되면 세션을 저장하고 창을 닫음
이후 자동화(OB 주문·재고 스냅샷 등)는 저장된 세션을 재사용하므로 로그인 불필요.

실행: python 아워박스_세션로그인.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

from ourbox_scraper import OURBOX_BASE, SESSION_PATH

TIMEOUT_SEC = 600  # 10분 안에 로그인하면 됨
DEBUG_PORT = 9230
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ourbox_login_profile")
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ourbox_login.lock")


def _other_instance_running() -> bool:
    """중복 실행 방지 — 창이 여러 개 뜨지 않도록."""
    try:
        if not os.path.exists(LOCK_PATH):
            return False
        pid = int(open(LOCK_PATH).read().strip() or "0")
        if not pid or pid == os.getpid():
            return False
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
        return "python" in (out or "").lower()
    except Exception:
        return False


if _other_instance_running():
    print("이미 로그인 창이 열려 있습니다. 기존 창을 사용해 주세요.")
    sys.exit(0)
with open(LOCK_PATH, "w") as _f:
    _f.write(str(os.getpid()))

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
if not chrome:
    print("Chrome 설치 경로를 찾지 못했습니다.")
    sys.exit(1)

# 일반 Chrome 프로세스로 실행 — 로그인 중에는 어떤 자동화도 붙지 않음
proc = subprocess.Popen([
    chrome,
    f"--user-data-dir={PROFILE_DIR}",
    f"--remote-debugging-port={DEBUG_PORT}",
    "--no-first-run", "--no-default-browser-check",
    "--new-window", f"{OURBOX_BASE}/om/login/login.do",
])
print("Chrome 창에서 아워박스에 로그인해 주세요 (보안문자 포함).")
print("로그인이 감지되면 자동으로 세션을 저장하고 창을 닫습니다...")


def _pages():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


logged_in = False
for _ in range(TIMEOUT_SEC):
    time.sleep(1)
    pages = _pages()
    if pages is None:
        if proc.poll() is not None:
            print("Chrome 창이 닫혔습니다. 다시 실행해 주세요.")
            sys.exit(1)
        continue
    urls = [p.get("url", "") for p in pages if p.get("type") == "page"]
    if any("oms.ourbox.co.kr" in u and "login" not in u for u in urls):
        logged_in = True
        break

if not logged_in:
    print("시간 초과(10분): 로그인이 감지되지 않았습니다.")
    proc.terminate()
    sys.exit(1)

time.sleep(2)  # 로그인 직후 쿠키 확정 대기

# 로그인이 끝난 뒤에만 연결해서 세션(쿠키)만 꺼내온다
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
    ctx = browser.contexts[0]
    state = ctx.storage_state()
    with open(SESSION_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f)
    browser.close()

proc.terminate()
print(f"✅ 세션 저장 완료: {SESSION_PATH}")
print("이제 OB 주문 자동화가 이 세션으로 동작합니다.")

# 백엔드에 즉시 알림 → 복구 감지 시 실패했던 자동화가 바로 재실행됨 (실패해도 무해)
try:
    urllib.request.urlopen("http://127.0.0.1:8081/api/ob-orders/session-status?live=true", timeout=20)
except Exception:
    pass

try:
    os.remove(LOCK_PATH)
except Exception:
    pass
