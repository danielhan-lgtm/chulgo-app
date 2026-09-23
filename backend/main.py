import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import config_router, boxhero, convert, slack_router, gmail_router, logs, receiving, doc_review, invoice, reconcile, channel, mapping, order_plan, disposal, coupang_load, kurly_label, outbound, ob_orders, coupang_growth_load, slack_outbound, inventory, report, coupang_report, oliveyoung_report, kurly_report, naver_report

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
app.include_router(inventory.router, prefix="/api/inventory", tags=["inventory"])
app.include_router(report.router, prefix="/api/exec-report", tags=["exec-report"])
app.include_router(coupang_report.router, prefix="/api/coupang-report", tags=["coupang-report"])
app.include_router(oliveyoung_report.router, prefix="/api/oliveyoung-report", tags=["oliveyoung-report"])
app.include_router(kurly_report.router, prefix="/api/kurly-report", tags=["kurly-report"])
app.include_router(naver_report.router, prefix="/api/naver-report", tags=["naver-report"])


# ── 4채널(쿠팡·올리브영·컬리 + 네이버) 경영 요약 보고문 — 채널별 보고 엔진을 한 번에 돌려 번호 붙여 묶는다 ──
@app.get("/api/channel-report/exec", tags=["channel-report"])
def channel_exec_report(as_of: str = "", month: str = ""):
    from datetime import date, datetime
    from routers.channel_report import build_exec_text
    as_of = (as_of or date.today().isoformat())[:10]
    try:
        datetime.fromisoformat(as_of)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(400, "as_of 형식 오류 (YYYY-MM-DD)")
    # 네이버는 발주 엔진(ChannelSpec)이 아니라 BH 거래 내역 업로드 기반이므로 블록 생성기로 4번에 이어 붙인다
    return build_exec_text([coupang_report.SPEC, oliveyoung_report.SPEC, kurly_report.SPEC], as_of, month,
                           extra_blocks=[naver_report.build_exec_block])


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
