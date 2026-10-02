import ChannelReport from './ChannelReport'

/** 올리브영 발주 보고 — 공용 채널 보고 컴포넌트에 올리브영 설정 주입 */
export default function OliveyoungReport() {
  return (
    <ChannelReport cfg={{
      base: '/oliveyoung-report',
      title: '올리브영 발주 보고', name: '올리브영', icon: '🫒',
      desc: '발주 캘린더의 올리브영 납품확인서(납기·납품수량) + 아워박스/박스히어로 재고·출고 + 올리브영 상품별 매출·재고 → 의장 보고문 자동 생성',
      poWord: '납품확인서', recvWord: '입고확정',
      uploadLabel: '매출·재고 파일 업로드',
      uploadHint: <>올리브영 파트너시스템 &gt; <b>상품별 매출_재고 조회</b> xlsx를 내려받아, 포털에서 조회한 매출조회기간(시작일~종료일)을 왼쪽에 똑같이 지정한 뒤 올리면 기간별 매출 추이·일평균·재고·재고일수가 표시됩니다.</>,
      monthly: true,
    }} />
  )
}
