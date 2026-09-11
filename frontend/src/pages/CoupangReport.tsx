import ChannelReport from './ChannelReport'

/** 쿠팡 발주 보고 — 공용 채널 보고 컴포넌트에 쿠팡 설정 주입 */
export default function CoupangReport() {
  return (
    <ChannelReport cfg={{
      base: '/coupang-report',
      title: '쿠팡 발주 보고', name: '쿠팡', icon: '🛒',
      desc: '발주 캘린더의 쿠팡 발주서(납기·확정·입고) + 아워박스/박스히어로 재고·출고 + 쿠팡 판매/재고 리포트 → 의장 보고문 자동 생성',
      poWord: '발주서', recvWord: '입고등록',
      uploadLabel: '판매·재고 리포트 업로드',
      uploadHint: <>쿠팡 윙 &gt; 리포트에서 내려받은 <b>basic_operation_rocket_*.csv</b>(일별 센터 출고·재고)와 <b>*-forecast-*.xlsx</b>(주간 판매 예측)를 상단 버튼으로 올리면 품목별 추이가 표시됩니다.</>,
      monthly: false,
    }} />
  )
}
