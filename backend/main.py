import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import config_router, boxhero, convert, slack_router, gmail_router, logs, receiving, doc_review, invoice, reconcile, channel, mapping, order_plan, disposal, coupang_load, kurly_label, outbound, ob_orders, coupang_growth_load, slack_outbound

app = FastAPI(title="출고 라몬 API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router.router, prefix="/api")
app.include_router(boxhero.router, prefix="/api/boxhero", tags=["boxhero"])
app.include_router(convert.router, prefix="/api/convert", tags=["convert"])
app.include_router(slack_router.router, prefix="/api/slack", tags=["slack"])
app.include_router(gmail_router.router, prefix="/api/gmail", tags=["gmail"])
app.include_router(logs.router, prefix="/api", tags=["logs"])
app.include_router(receiving.router, prefix="/api/receiving", tags=["receiving"])
app.include_router(doc_review.router, prefix="/api/doc-review", tags=["doc-review"])
app.include_router(invoice.router, prefix="/api/invoice", tags=["invoice"])
app.include_router(reconcile.router, prefix="/api/reconcile", tags=["reconcile"])
app.include_router(channel.router, prefix="/api/channel", tags=["channel"])
app.include_router(mapping.router, prefix="/api/mapping", tags=["mapping"])
app.include_router(order_plan.router, prefix="/api/order-plan", tags=["order-plan"])
app.include_router(disposal.router, prefix="/api/disposal", tags=["disposal"])
app.include_router(coupang_load.router, prefix="/api/coupang-load", tags=["coupang-load"])
app.include_router(kurly_label.router, prefix="/api/kurly-label", tags=["kurly-label"])
app.include_router(outbound.router, prefix="/api/outbound", tags=["outbound"])
app.include_router(ob_orders.router, prefix="/api/ob-orders", tags=["ob-orders"])
app.include_router(coupang_growth_load.router, prefix="/api/coupang-growth-load", tags=["coupang-growth-load"])
app.include_router(slack_outbound.router, prefix="/api/slack-outbound", tags=["slack-outbound"])


@app.get("/")
def root():
    return {"status": "ok", "app": "출고 라몬 API v2"}


# ── OB 가용외 스냅샷 자동 추적 (2시간 간격) ──────────────────────────────────
# OurBox 가용→가용외(할당) 전환 시점을 시계열로 캡처. 서버 가동 중 백그라운드로 동작.
_SNAPSHOT_INTERVAL_SEC = 2 * 3600

def _snapshot_scheduler():
    import time
    from routers import reconcile as _rec
    # 시작 직후 1회 (최근 90분 내 스냅샷 있으면 _capture가 알아서 skip → 리로드 중복 방지)
    while True:
        try:
            _rec._capture_ob_stock_snapshot(force=False, min_gap_min=90)
        except Exception:
            pass
        time.sleep(_SNAPSHOT_INTERVAL_SEC)

@app.on_event("startup")
def _start_snapshot_scheduler():
    import threading
    threading.Thread(target=_snapshot_scheduler, daemon=True, name="ob-snapshot").start()


@app.on_event("startup")
def _start_ob_auto_scheduler():
    # OB 주문 완전자동화 스케줄러 (config의 ob_auto_enabled로 ON/OFF 제어)
    ob_orders.start_auto_scheduler()


@app.on_event("startup")
def _start_slack_outbound_poller():
    # BH 출고(직접 처리분 포함) → 출고 슬랙 자동 포스팅 폴러 (slack_outbound_notify로 제어)
    slack_outbound.start_poller()


@app.on_event("startup")
def _start_ourbox_session_keepalive():
    # 아워박스 OMS 저장 세션(ourbox_session.json) 유휴 만료 방지 — 10분 간격 핑.
    # 만료 감지 시 슬랙 알림 + 앱 로그. 상태는 /api/ob-orders/session-status 로 조회.
    ob_orders.start_session_keepalive()
