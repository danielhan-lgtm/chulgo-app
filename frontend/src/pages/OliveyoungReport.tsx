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
      uploadHint: <>올리브영 파트너시스템 &gt; <b>상품별 매출_재고 조회</b> xlsx를 내려받아, 파일명에 <b>9월</b> 또는 <b>10월 1일 ~ 11일</b>처럼 조회기간을 적어 올리면 그 기간으로 자동 저장됩니다 (파일명에 기간이 없으면 왼쪽 날짜 범위를 씁니다). 같은 기간을 다시 올리면 덮어씁니다.</>,
      monthly: true,
    }} />
  )
}
