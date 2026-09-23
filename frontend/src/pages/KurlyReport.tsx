import ChannelReport from './ChannelReport'

/** 컬리 발주 보고 — 공용 채널 보고 컴포넌트에 컬리 설정 주입 */
export default function KurlyReport() {
  return (
    <ChannelReport cfg={{
      base: '/kurly-report',
      title: '컬리 발주 보고', name: '컬리', icon: '🥬',
      desc: '발주 캘린더의 컬리 거래명세서(입고일·수량) + 아워박스/박스히어로 재고·마켓컬리 출고 → 의장 보고문 자동 생성. 거래명세서에 단가가 없어 상품공급정보 xlsx의 일반단가를 단가표로 올리면 수량×단가로 매입금액을 계산합니다.',
      poWord: '거래명세서', recvWord: '입고',
      uploadLabel: '',
      uploadHint: null,
      monthly: false,
      noUpload: true,
      noAmt: true,
      priceTable: true,
    }} />
  )
}
