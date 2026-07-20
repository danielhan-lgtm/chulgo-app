# -*- coding: utf-8 -*-
"""박스히어로 일일 출고 요약 → 슬랙 포스팅 (GitHub Actions용 단독 실행 스크립트).

backend/routers/slack_outbound.py 의 post_daily_summary 와 동일한 메시지를 생성한다.
로컬 백엔드와 동시에 켜져 있어도 채널에 오늘 요약이 이미 있으면 skip (양방향 중복방지).

env:
  BOXHERO_API_TOKEN  (필수) BoxHero REST API 토큰
  SLACK_BOT_TOKEN    (필수) Slack Bot Token (xoxb-...)
  SLACK_CHANNEL      채널 이름 또는 ID (기본: 물류_출고)

사용: python scripts/daily_outbound_summary.py [--dry-run]
"""
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

import requests

BH = "https://rest.boxhero-app.com"
SLACK = "https://slack.com/api"
KST = timezone(timedelta(hours=9))
MARKER = "박스히어로 출고 정리"


def _kst_date(ts: str) -> str:
    """BoxHero 타임스탬프는 UTC('...Z') — KST 날짜로 변환해 비교해야
    KST 새벽 0~9시 처리분(UTC로는 전날)이 누락되지 않는다."""
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%Y-%m-%d")
    except Exception:
        return (ts or "")[:10]


def _bh_get(token: str, path: str, params: dict = None) -> dict:
    """BoxHero GET — 429/5xx는 지수 백오프로 재시도 (Retry-After 존중)."""
    import time
    last = None
    for attempt in range(6):
        r = requests.get(f"{BH}{path}", headers={"Authorization": f"Bearer {token}"},
                         params=params or {}, timeout=20)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After") or 2.0 * (2 ** attempt))
            print(f"WARN: BH {r.status_code} — retry in {wait:.0f}s (attempt {attempt + 1}/6)")
            time.sleep(min(wait, 120))
            last = r
            continue
        r.raise_for_status()
        time.sleep(0.3)  # 페이지/거래 조회 사이 완충 — 429 예방
        return r.json()
    last.raise_for_status()


def _fetch_tx_items(token: str, tx_id) -> list:
    try:
        data = _bh_get(token, f"/v1/location-txs/{tx_id}")
        out = []
        for i in data.get("item", {}).get("items", []):
            nm = i.get("name") or (i.get("item") or {}).get("name", "")
            sku = i.get("sku") or (i.get("item") or {}).get("sku", "")
            out.append({"name": nm, "sku": sku, "qty": abs(int(i.get("quantity") or 0))})
        return out
    except Exception:
        return []


def gather_today_out(token: str, today: str):
    """오늘(KST) '처리(created_at)'된 출고를 (거래처, sku, 출고일자)별로 취합."""
    agg = OrderedDict()
    cursor, txcount, no_today = None, 0, 0
    for _ in range(12):
        params = {"type": "out", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = _bh_get(token, "/v1/location-txs", params)
        got = 0
        for tx in data.get("items", []):
            created = _kst_date(tx.get("created_at", ""))
            if created != today:
                continue
            got += 1
            txcount += 1
            partner = (tx.get("partner") or {}).get("name", "") or "(거래처 미지정)"
            out_date = _kst_date(tx.get("transaction_time", ""))
            for it in _fetch_tx_items(token, tx.get("id")):
                key = (partner, it["sku"] or it["name"], out_date)
                a = agg.setdefault(key, {"partner": partner, "name": it["name"],
                                         "sku": it["sku"], "qty": 0, "out_date": out_date})
                a["qty"] += it["qty"]
        no_today = no_today + 1 if got == 0 else 0
        if not data.get("has_more") or no_today >= 2:
            break
        cursor = data.get("cursor")
    return list(agg.values()), txcount


def _slack(token: str, method: str, http: str = "get", **kw) -> dict:
    if http == "post":
        r = requests.post(f"{SLACK}/{method}", json=kw, timeout=20,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json; charset=utf-8"})
    else:
        r = requests.get(f"{SLACK}/{method}", params=kw, timeout=20,
                         headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"slack {method}: {data.get('error')}")
    return data


def resolve_channel(token: str, ch: str) -> str:
    if re.fullmatch(r"[CGD][A-Z0-9]{6,}", ch):
        return ch
    cursor = None
    while True:
        kw = {"types": "public_channel,private_channel", "limit": 1000}
        if cursor:
            kw["cursor"] = cursor
        data = _slack(token, "conversations.list", **kw)
        for c in data.get("channels", []):
            if c.get("name") == ch:
                return c["id"]
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return ""


def already_posted_today(token: str, channel_id: str, today: str) -> bool:
    """채널에서 오늘 자정(KST) 이후 메시지에 오늘자 요약이 있는지 확인."""
    midnight = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=KST)
    data = _slack(token, "conversations.history", channel=channel_id,
                  oldest=str(midnight.timestamp()), limit=200)
    for m in data.get("messages", []):
        t = m.get("text", "")
        if MARKER in t and today in t:
            return True
    return False


def build_message(rows: list, today: str):
    by_p = OrderedDict()
    for r in rows:
        by_p.setdefault(r["partner"], []).append(r)
    lines = [f"📦 *{MARKER}* — 처리일 {today}"]
    kinds = qty_sum = 0
    for p, its in by_p.items():
        lines.append(f"🏢 *{p}*")
        for it in sorted(its, key=lambda x: x.get("out_date", "")):
            sku = f" `{it['sku']}`" if it["sku"] else ""
            od = it.get("out_date", "")
            od_txt = f" · 출고일 {od[5:]}" if od else ""
            if od and od != today:
                od_txt = f" · ⚠️출고일 {od}"
            lines.append(f"   • {it['name']}{sku} — *{int(it['qty'])}개*{od_txt}")
            kinds += 1
            qty_sum += int(it["qty"])
    lines.append(f"— 총 {kinds}종 · {qty_sum}개 · 거래처 {len(by_p)}곳")
    return "\n".join(lines), {"partners": len(by_p), "kinds": kinds, "qty": qty_sum}


def main():
    dry_run = "--dry-run" in sys.argv
    bh_token = os.environ.get("BOXHERO_API_TOKEN", "")
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    ch = os.environ.get("SLACK_CHANNEL", "물류_출고").strip()
    if not bh_token or not slack_token:
        print("ERROR: BOXHERO_API_TOKEN / SLACK_BOT_TOKEN env required")
        sys.exit(1)

    today = datetime.now(KST).strftime("%Y-%m-%d")
    print(f"[{today}] gathering today's outbound...")

    channel_id = resolve_channel(slack_token, ch)
    if not channel_id:
        print(f"ERROR: channel not found: {ch}")
        sys.exit(1)

    try:
        if already_posted_today(slack_token, channel_id, today):
            print("already posted today (by local backend or previous run) — skip")
            return
    except Exception as e:
        # 히스토리 조회 실패(권한 등)면 중복 감수하고 진행 — 누락보다 낫다
        print(f"WARN: history check failed ({e}) — proceeding anyway")

    rows, txcount = gather_today_out(bh_token, today)
    if not rows:
        print(f"no outbound today (tx={txcount}) — nothing to post")
        return

    msg, summary = build_message(rows, today)
    msg += f" (거래 {txcount}건)"
    print(f"summary: {summary}, tx={txcount}")

    if dry_run:
        print("--- dry run preview ---")
        print(msg)
        return

    # 데이터 수집에 시간이 걸리므로 포스팅 직전 한 번 더 중복 확인 (동시 실행 대비)
    try:
        if already_posted_today(slack_token, channel_id, today):
            print("posted by another run while gathering — skip")
            return
    except Exception:
        pass

    res = _slack(slack_token, "chat.postMessage", http="post", channel=channel_id, text=msg)
    print(f"posted ts={res.get('ts')}")


if __name__ == "__main__":
    main()
