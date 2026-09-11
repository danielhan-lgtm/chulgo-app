"""쿠팡 발주·납품 보고 — 채널 공용 엔진(channel_report)에 쿠팡 스펙을 주입한 라우터.

데이터 파일: data/coupang_report_state.json (메모·입고예정·별칭), data/coupang_report_uploads.json (rocket csv · forecast xlsx)
"""
from routers.channel_report import ChannelSpec, make_router

SPEC = ChannelSpec(
    key="coupang",
    channel="쿠팡",
    display="쿠팡",
    partner_match=lambda p: ("쿠팡" in (p or "")) and ("그로스" not in (p or "")),
    aliases={},
    has_conf=True,
    recv_word="입고등록",
    recv_hint="센터 검수 후 반영 · 발주서 재다운로드 시 갱신",
    po_word="발주서",
    upload_mode="daily",
    upload_hint="쿠팡 윙 > 리포트: basic_operation_rocket_*.csv (일별 센터 출고·재고), *-forecast-*.xlsx (주간 판매 예측)",
)

router = make_router(SPEC)
