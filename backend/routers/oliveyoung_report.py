"""올리브영 발주·납품 보고 — 채널 공용 엔진(channel_report)에 올리브영 스펙을 주입한 라우터.

발주 데이터: 발주 캘린더에 올린 올리브영 '납품확인서 목록' (채널=올리브영).
  납품예정수량(conf)만 납품하므로 잔량·금액은 납품예정 기준(qty_is_conf). recv=납품수량(실제 입고), 발주수량은 order_qty로 보관.
판매/재고: 올리브영 파트너 '상품별 매출_재고 조회' xlsx — 한 달치씩 업로드(period=YYYY-MM). 상품코드가 곧 바코드(BH SKU).
출고 실적: BoxHero 거래처 '올리브영' 출고. 메모 '# 10일 올리브영' 의 '10일'을 납기 힌트로 사용.
"""
from routers.channel_report import ChannelSpec, make_router

SPEC = ChannelSpec(
    key="oliveyoung",
    channel="올리브영",
    display="올리브영",
    partner_match=lambda p: "올리브" in (p or ""),
    aliases={},
    amt_includes_vat=False,      # 납품확인서 원단가/원가금액 = 공급가액(부가세 제외) → 그대로 사용 (협력사정산 마감내역과 일치)
    has_conf=True,
    conf_word="납품예정",
    exec_name="올리브영",
    exec_metric="order",
    history_by_order_date=False,  # 월별 추이 발주 수량은 납기(입고예정일) 월 기준 — 다른 채널과 동일 원칙. True 로 바꾸면 납품확인서 '발주일자' 월 기준(7월 25,137 / 8월 35,439)
    qty_is_conf=True,            # 납품확인서의 '납품예정수량'만 납품 → 발주잔량·금액·납품가능 모두 이 기준
    derive_conf_from_amt=True,   # 구버전 저장 행은 원가금액/원단가로 납품예정수량 역산
    recv_word="입고확정",
    recv_hint="납품확인서 기준 · 납품확인서 재다운로드 시 갱신",
    po_word="납품확인서",
    upload_mode="monthly",
    upload_hint="올리브영 파트너시스템 > 상품별 매출_재고 조회 xlsx (월별 · 해당 월을 선택해 업로드)",
)

router = make_router(SPEC)
