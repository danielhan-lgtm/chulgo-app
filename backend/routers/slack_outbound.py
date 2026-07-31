# -*- coding: utf-8 -*-
"""BH 출고(location-txs type=out) 폴링 → 출고 슬랙 채널 자동 포스팅.

우리 앱뿐 아니라 BoxHero에서 직접 처리한 출고까지 전부 감지해 올린다.
- bh_outbound_posted 테이블로 이미 올린 tx는 skip
- 최초 활성화(테이블 비어있음) 시 기존 tx는 baseline(기록만, 포스팅X) → 과거분 무더기 방지
- 중복판정: 상품+수량+거래처(partner) — utils_core.notify_outbound_to_slack
config: slack_outbound_notify(마스터), slack_outbound_channel, bh_notify_interval_min
"""
import sys
import os
import re
import json
import time
import threading
from datetime import datetime

from datetime import timezone, timedelta

import requests as R
from fastapi import APIRouter

KST = timezone(timedelta(hours=9))


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

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import utils_core as U
import receiving_db as db
import state

router = APIRouter()
BH = "https://rest.boxhero-app.com"

with db._conn() as _c:
    _c.execute("""
        CREATE TABLE IF NOT EXISTS bh_outbound_posted (
            tx_id TEXT PRIMARY KEY,
            partner TEXT,
            memo TEXT,
            posted INTEGER DEFAULT 0,
            slack_ts TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _c.commit()

_lock = threading.Lock()
_status = {"last_scan": None, "last_error": None, "posted": 0, "running": False, "enabled": False}


def _cfg():
    try:
        with open(os.path.join(_ROOT, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _bh_get_retry(token: str, url: str, params: dict = None):
    """BoxHero GET — 429/5xx 지수 백오프 재시도. 요약 수집이 rate limit에 죽지 않게."""
    last = None
    for attempt in range(6):
        r = R.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=20)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After") or 2.0 * (2 ** attempt))
            time.sleep(min(wait, 120))
            last = r
            continue
        time.sleep(0.3)  # 호출 간 완충
        return r
    return last


def _fetch_recent_out(token: str, limit: int = 50) -> list:
    r = _bh_get_retry(token, f"{BH}/v1/location-txs", {"type": "out", "limit": limit})
    r.raise_for_status()
    return r.json().get("items", [])


def _fetch_tx_items(token: str, tx_id) -> list:
    try:
        r = _bh_get_retry(token, f"{BH}/v1/location-txs/{tx_id}")
        if not r.ok:
            return []
        its = r.json().get("item", {}).get("items", [])
        out = []
        for i in its:
            nm = i.get("name") or (i.get("item") or {}).get("name", "")
            sku = i.get("sku") or (i.get("item") or {}).get("sku", "")
            out.append({"name": nm, "sku": sku, "qty": abs(int(i.get("quantity") or 0))})
        return out
    except Exception:
        return []


def _gather_today_out(token: str):
    """오늘 '처리(created_at)'된 출고를 (거래처, sku, 출고일자)별로 취합.

    출고일자(transaction_time)가 처리일과 다를 수 있어 키에 포함해 별도 집계.
    반환: [{partner, name, sku, qty, out_date}], 처리 tx수
    """
    from collections import OrderedDict
    today = datetime.now(KST).strftime("%Y-%m-%d")
    agg = OrderedDict()
    cursor, txcount, no_today = None, 0, 0
    for _ in range(12):  # 페이지 안전 상한
        params = {"type": "out", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = _bh_get_retry(token, f"{BH}/v1/location-txs", params)
        if not r.ok:
            break
        data = r.json()
        items = data.get("items", [])
        got = 0
        for tx in items:
            created = _kst_date(tx.get("created_at", ""))
            if created != today:      # 오늘 처리된 것만 (KST 기준)
                continue
            got += 1
            txcount += 1
            partner = (tx.get("partner") or {}).get("name", "") or "(거래처 미지정)"
            out_date = _kst_date(tx.get("transaction_time", ""))  # 출고일자
            for it in _fetch_tx_items(token, tx.get("id")):
                key = (partner, it["sku"] or it["name"], out_date)
                a = agg.setdefault(key, {"partner": partner, "name": it["name"],
                                         "sku": it["sku"], "qty": 0, "out_date": out_date})
                a["qty"] += it["qty"]
        no_today = no_today + 1 if got == 0 else 0
        if not data.get("has_more") or no_today >= 2:  # 오늘분 없는 페이지 2연속이면 종료
            break
        cursor = data.get("cursor")
    return list(agg.values()), txcount


def _summary_already_in_channel(slack: str, channel_id: str, today: str) -> bool:
    """채널에 오늘자 요약 메시지가 이미 있는지 (GitHub Actions 등 외부 실행과 중복방지)."""
    from datetime import timezone, timedelta
    midnight = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=9)))
    r = R.get("https://slack.com/api/conversations.history",
              headers={"Authorization": f"Bearer {slack}"},
              params={"channel": channel_id, "oldest": str(midnight.timestamp()), "limit": 200},
              timeout=15)
    data = r.json()
    if not data.get("ok"):
        return False
    return any("박스히어로 출고 정리" in (m.get("text") or "") and today in (m.get("text") or "")
               for m in data.get("messages", []))


def _item_report_lines(rows: list) -> list:
    """품목별 종합 리포트 — 거래처 구분 없이 품목 단위 총 출고량을 합산해
    거래처별 상세 위에 표시. 여러 거래처로 나간 품목은 거래처 분해를 병기."""
    from collections import OrderedDict
    items = OrderedDict()
    for r in rows:
        key = r["sku"] or r["name"]
        it = items.setdefault(key, {"name": r["name"], "sku": r["sku"], "qty": 0, "by_partner": OrderedDict()})
        it["qty"] += int(r["qty"])
        it["by_partner"][r["partner"]] = it["by_partner"].get(r["partner"], 0) + int(r["qty"])
    total_qty = sum(it["qty"] for it in items.values())
    lines = [f"📊 *품목별 종합* — {len(items)}종 · 총 {total_qty:,}개"]
    for it in sorted(items.values(), key=lambda x: -x["qty"]):
        sku = f" `{it['sku']}`" if it["sku"] else ""
        bp = ""
        if len(it["by_partner"]) > 1:
            bp = " (" + " · ".join(f"{p} {q:,}" for p, q in sorted(it["by_partner"].items(), key=lambda kv: -kv[1])) + ")"
        lines.append(f"   • {it['name']}{sku} — *{it['qty']:,}개*{bp}")
    return lines


def post_daily_summary(dry_run: bool = False) -> dict:
    """오늘 처리된 출고를 거래처별로 취합해 한 메시지로 1회 포스팅.
    중복체크 없이 전부 올린다. 출고일자(다를 수 있음)를 항목별로 표기.
    """
    from collections import OrderedDict
    cfg = _cfg()
    if not cfg.get("slack_outbound_notify"):
        return {"reason": "disabled"}
    token = cfg.get("api_token", "")
    slack = cfg.get("slack_token", "")
    ch = str(cfg.get("slack_outbound_channel") or "물류_출고").strip()
    if not token or not slack:
        return {"reason": "no_token"}

    with _lock:
        try:
            rows, txcount = _gather_today_out(token)
        except Exception as e:
            _status.update(last_error=str(e)[:150])
            return {"reason": f"fetch_error:{str(e)[:100]}"}
        today = datetime.now(KST).strftime("%Y-%m-%d")
        if not rows:
            if not dry_run:
                _status.update(last_scan=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), posted=0, last_error=None)
            return {"reason": "no_outbound_today", "tx": txcount}

        channel_id = ch
        if not re.fullmatch(r"[CGD][A-Z0-9]{6,}", ch):
            try:
                channel_id = U.fetch_slack_channels(slack).get(ch, "")
            except Exception:
                channel_id = ""
        if not channel_id:
            return {"reason": "channel_not_found"}

        if not dry_run:
            try:
                if _summary_already_in_channel(slack, channel_id, today):
                    _status.update(last_scan=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), last_error=None)
                    return {"reason": "already_posted_in_channel"}
            except Exception:
                pass  # 확인 실패 시 그냥 진행 (누락보다 중복이 낫다)

        # 거래처별 그룹 (중복체크 없음 — 전부 표기)
        by_p = OrderedDict()
        for r in rows:
            by_p.setdefault(r["partner"], []).append(r)

        lines = [f"📦 *박스히어로 출고 정리* — 처리일 {today}"]
        lines += _item_report_lines(rows)
        lines.append("")
        kinds = qty_sum = 0
        for p, its in by_p.items():
            lines.append(f"🏢 *{p}*")
            for it in sorted(its, key=lambda x: x.get("out_date", "")):
                sku = f" `{it['sku']}`" if it["sku"] else ""
                od = it.get("out_date", "")
                od_txt = f" · 출고일 {od[5:]}" if od else ""
                # 출고일이 처리일과 다르면 강조
                if od and od != today:
                    od_txt = f" · ⚠️출고일 {od}"
                lines.append(f"   • {it['name']}{sku} — *{int(it['qty'])}개*{od_txt}")
                kinds += 1
                qty_sum += int(it["qty"])
        lines.append(f"— 총 {kinds}종 · {qty_sum}개 · 거래처 {len(by_p)}곳 (거래 {txcount}건)")
        msg = "\n".join(lines)

        summary = {"partners": len(by_p), "kinds": kinds, "qty": qty_sum, "tx": txcount}
        if dry_run:
            return {"dry_run": True, **summary, "preview": msg[:1500]}
        try:
            res = U.slack_post_message(slack, channel_id, msg)
        except Exception as e:
            _status.update(last_error=str(e)[:150])
            return {"reason": f"post_error:{str(e)[:100]}"}
        _status.update(last_scan=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), posted=kinds, last_error=None)
        return {"posted": True, "ts": res.get("ts"), **summary}


def _daily_loop():
    """매일 지정 시각(bh_notify_time, 기본 18:00)에 일일 출고 요약 1회.
    그 시각에 백엔드가 꺼져 있었어도 켜지면 오늘분을 따라잡아 실행.
    성공(포스팅/출고없음/이미있음)한 날만 완료 기록 — 일시 오류는 5분 간격 재시도.
    같은 날 중복 포스팅은 채널 히스토리 확인으로 방지."""
    _DONE = ("no_outbound_today", "already_posted_in_channel", "disabled")
    next_retry = 0.0
    while True:
        try:
            cfg = _cfg()
            _status["enabled"] = bool(cfg.get("slack_outbound_notify"))
            if _status["enabled"]:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                hm = now.strftime("%H:%M")
                t = str(cfg.get("bh_notify_time") or "18:00")
                if hm >= t and cfg.get("bh_notify_last_summary") != today and time.time() >= next_retry:
                    _status["running"] = True
                    try:
                        res = post_daily_summary(dry_run=False)
                        if res.get("posted") or res.get("reason") in _DONE:
                            U.save_config({"bh_notify_last_summary": today})
                        else:  # fetch_error/post_error/no_token 등 — 5분 후 재시도
                            next_retry = time.time() + 300
                    except Exception as e:
                        _status.update(last_error=str(e)[:150])
                        next_retry = time.time() + 300
                    finally:
                        _status["running"] = False
        except Exception:
            pass
        time.sleep(30)


def start_poller():
    threading.Thread(target=_daily_loop, daemon=True, name="bh-slack-daily").start()


@router.get("/status")
def status():
    cfg = _cfg()
    return {**_status, "channel": cfg.get("slack_outbound_channel", "물류_출고"),
            "notify_time": cfg.get("bh_notify_time", "18:00"),
            "last_summary_date": cfg.get("bh_notify_last_summary")}


@router.post("/summary")
def summary(dry_run: bool = False):
    """일일 출고 요약 수동 실행 (dry_run=true면 포스팅 없이 미리보기)."""
    return post_daily_summary(dry_run=dry_run)
