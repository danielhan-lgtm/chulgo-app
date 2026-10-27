import { useMemo, useState } from 'react'
import { Button, Tag, Tooltip } from 'antd'
import type { StockDiffTrace, TraceCause } from '../services/api'

// 거래 정밀 대사 결과를 "결론 → 원인 요약 → 상세" 순으로 읽기 쉽게 보여주는 리포트
interface Props { data: StockDiffTrace; months?: number; onExtend?: () => void }

const fmt = (n: number) => n.toLocaleString()
const sign = (n: number) => (n > 0 ? `+${fmt(n)}` : fmt(n))

// 원인 유형 → 쉬운 말 설명
const TYPE_INFO: Record<string, { label: string; color: string; meaning: string; action: string }> = {
  '시점차(짝)': { label: '기록 시점 차이', color: 'green', meaning: 'BH는 주문일 기준으로 묶어 출고 처리하고, OB는 실제 발송일에 기록해서 날짜만 어긋난 같은 거래', action: '수량이 거의 같으면 문제 없음. 남는 몇 개 차이만 확인' },
  '선차감(가용외확인)': { label: 'BH 먼저 출고 (OB 발송 대기)', color: 'gold', meaning: 'BH는 주문 들어오자마자 출고로 뺐는데, OB는 아직 발송 전(할당·가용외 상태)', action: 'OB가 발송하면 저절로 맞음. 조치 불필요' },
  'BH만 기록': { label: 'BH에만 있는 거래', color: 'geekblue', meaning: 'OB를 거치지 않은 처리(직배송·세트 변경·수기 조정)이거나 OB 기록 누락', action: '메모를 보고 직배송/수기 처리인지 확인. 아니면 OB 누락' },
  'OB만 기록': { label: 'OB에만 있는 거래', color: 'volcano', meaning: 'OB(창고)는 움직였는데 BH에 입력이 없음', action: 'BH에 해당 입·출고 입력 필요' },
  '기간경계(시점차가능)': { label: '기간 경계 (상대 기록이 기간 밖)', color: 'default', meaning: '조회 시작·끝 근처라 상대쪽 기록이 조회 기간 밖에 있을 수 있음', action: '기간을 늘려 다시 대사하면 대부분 사라짐' },
  '기간이전차이': { label: '조회 시작 전부터 있던 차이', color: 'orange', meaning: '이번 기간 거래와 무관하게 이미 벌어져 있던 차이', action: '기간을 더 길게 잡아 발생 시점을 좁히기' },
  '교차기록의심': { label: '다른 품목으로 잘못 기록 의심', color: 'magenta', meaning: '같은 수량이 비슷한 날 다른 품목에 기록됨', action: '상대 품목과 같이 확인' },
  '근사일치 수량차': { label: '같은 거래인데 수량이 몇 개 다름', color: 'purple', meaning: 'BH는 주문 수집 수량, OB는 실제 발송 수량으로 기록해 몇 개씩 어긋남', action: '취소·품절 반려된 주문이 BH에서 되돌려지는지 확인' },
  '선차감(가용외추정)': { label: 'BH 먼저 출고 (OB 발송 대기 추정)', color: 'gold', meaning: '기간 끝 무렵 BH만 출고했고 OB에 그만큼 가용외가 남아 있음', action: 'OB가 발송하면 저절로 맞음' },
  '선차감→발송(상쇄)': { label: 'BH 먼저 출고 → OB 나중 발송 (상쇄됨)', color: 'green', meaning: '같은 거래를 BH가 먼저, OB가 나중에 기록해 이미 서로 상쇄됨', action: '조치 불필요' },
}
function infoOf(t: string) {
  return TYPE_INFO[t] || (t.startsWith('기간이전 선반영')
    ? { label: t, color: 'cyan', meaning: '기간 시작 전 거래가 한쪽에만 미리 반영됨', action: '기간을 늘려 확인' }
    : { label: t, color: 'default', meaning: '', action: '' })
}

type Group = { type: string; items: TraceCause[]; impact: number }

// 메모/채널 문자열에서 채널 이름만 뽑기 ("# 스마트 스토어 (이알하나) 9월 26일" → "스마트 스토어 (이알하나)")
function channelOf(c: { memo?: string; channel?: string }): string {
  const memo = (c.memo || '').trim()
  const tags = memo.split('/').map(t => t.trim()).filter(t => t.startsWith('#'))
  if (tags.length) {
    const names = tags.map(t => t.replace(/^#\s*/, '').replace(/\s*\d{1,2}월\s*\d{1,2}일.*$/, '').trim()).filter(Boolean)
    const uniq = [...new Set(names.map(n => n.toLowerCase().replace(/\s+/g, ' ')))]
    if (uniq.length === 1) return names[0]
    if (uniq.length > 1) return '여러 채널 묶음'
  }
  const ch = (c.channel || '').trim()
  if (ch) return ch.includes('/') ? '여러 채널 묶음' : ch.replace(/\(도착보장\)-.*$/, '').trim()
  if (/cafe24|카페24/i.test(memo)) return '카페24'
  if (/kakao|카카오/i.test(memo)) return '카카오'
  return '기타'
}

// 같은 채널의 다른 표기를 하나로: "스마트 스토어 (이알하나)" / "스마트스토어(도착보장)-이알하나" / "sk" / "SK 스토아"
function normChannel(name: string): string {
  let k = name.toLowerCase().replace(/\s+/g, '')
  k = k.replace(/\(도착보장\)/g, '').replace(/[-_]?이알하나/g, '').replace(/\(\)/g, '')
  if (k.startsWith('스마트스토어') || k.includes('smartstore') || k.includes('네이버')) return '스마트스토어'
  if (k.startsWith('sk')) return 'sk'
  if (k.includes('카카오') || k.includes('kakao')) return '카카오'
  if (k.includes('카페24') || k.includes('cafe24') || k.includes('자사몰')) return '자사몰(카페24)'
  if (k.includes('cj')) return 'cj'
  return k
}

// 전체 이벤트에서 일별·주별 BH−OB 순변동 계산 (언제 벌어졌는지)
type DayDelta = { date: string; bh: number; ob: number; delta: number; cum: number }
type WeekDelta = { week: string; delta: number; cum: number }
function driftSeries(d: StockDiffTrace): { days: DayDelta[]; weeks: WeekDelta[] } {
  const bh: Record<string, number> = {}, ob: Record<string, number> = {}
  const add = (m: Record<string, number>, e: { date: string; qty: number }, sign: number) => { m[e.date] = (m[e.date] || 0) + sign * e.qty }
  const mp = d.matched_pairs
  if (!mp) return { days: [], weeks: [] }
  for (const grp of ['in', 'out'] as const) {
    const sg = grp === 'in' ? 1 : -1
    for (const p of mp[grp] || []) { p.bh.forEach(e => add(bh, e, sg)); p.ob.forEach(e => add(ob, e, sg)) }
  }
  for (const c of d.cancelled || []) { c.bh.forEach(e => add(bh, e, -1)); c.ob.forEach(e => add(ob, e, -1)) }
  const u = d.unmatched
  u.bh_in.forEach(e => add(bh, e, 1)); u.ob_in.forEach(e => add(ob, e, 1))
  u.bh_out.filter(e => !e.synthetic).forEach(e => add(bh, e, -1)); u.ob_out.filter(e => !e.synthetic).forEach(e => add(ob, e, -1))
  const dates = [...new Set([...Object.keys(bh), ...Object.keys(ob)])].sort()
  if (!dates.length) return { days: [], weeks: [] }
  // 일별: BH−OB 변동이 0이 아닌 날만
  let cum = d.opening_gap ?? 0
  const days: DayDelta[] = []
  for (const k of dates) {
    const delta = (bh[k] || 0) - (ob[k] || 0)
    if (delta === 0) continue
    cum += delta
    days.push({ date: k, bh: bh[k] || 0, ob: ob[k] || 0, delta, cum })
  }
  // 주별: 첫 거래 주 ~ 마지막 거래 주까지 빈 주도 0으로 채움 (시간축이 끊기지 않게)
  const mondayOf = (k: string) => { const dt = new Date(k + 'T00:00:00'); dt.setDate(dt.getDate() - (dt.getDay() + 6) % 7); return dt }
  const iso = (dt: Date) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`
  const wk: Record<string, number> = {}
  for (const k of dates) { const w = iso(mondayOf(k)); wk[w] = (wk[w] || 0) + (bh[k] || 0) - (ob[k] || 0) }
  const weeks: WeekDelta[] = []
  let c2 = d.opening_gap ?? 0
  for (let dt = mondayOf(dates[0]), end = mondayOf(dates[dates.length - 1]); dt <= end; dt.setDate(dt.getDate() + 7)) {
    const w = iso(dt); c2 += wk[w] || 0
    weeks.push({ week: w, delta: wk[w] || 0, cum: c2 })
  }
  return { days, weeks }
}
// ── 유력 원인 자동 진단 ─────────────────────────────────────────────
// 손으로 대사할 때 쓰던 판단 규칙을 그대로 코드화. 패턴이 맞으면 "무엇이 원인이고 어떻게 확인하나"를 한 상자로 보여줌.
export type Diagnosis = { key: string; title: string; level: '유력' | '가능'; impact: number; evidence: string[]; action: string; color: string }
function diagnose(d: StockDiffTrace, days: DayDelta[], byChannel: { ch: string; net: number; n: number }[]): Diagnosis[] {
  const out: Diagnosis[] = []
  const diffNow = d.diff_now ?? 0
  const opening = d.opening_gap ?? 0
  const during = diffNow - opening
  const unav = d.avail_basis?.ob_unav ?? d.ob_unav ?? 0
  const bhStock = d.bh_stock, obStock = d.ob_total
  const periodDays = Math.max(1, Math.round((new Date(d.to).getTime() - new Date(d.from).getTime()) / 86400000))
  const absNow = Math.max(1, Math.abs(diffNow))
  // 문턱 기준: 가용외(발송 대기)를 뺀 실제 차이. 가용외가 수천 개면 그 뒤에 숨은 작은 패턴을 놓치지 않게.
  const diffAvail = d.avail_basis?.diff_avail
  const absBase = Math.max(1, Math.min(absNow, diffAvail != null ? Math.abs(diffAvail) : absNow))
  const causes = d.causes

  // A. 한쪽 재고가 0 — 품목명이 달라 다른 행으로 잡혔을 가능성 (매핑)
  if (bhStock !== null && obStock !== null && (bhStock === 0) !== (obStock === 0) && diffNow !== 0) {
    const zeroSide = bhStock === 0 ? 'BH' : 'OB'
    const otherSide = bhStock === 0 ? 'OB' : 'BH'
    const moves = causes.filter(c => c.type === `${zeroSide}만 기록` && /이동|기타|조정|세트/.test(c.ev_type || ''))
    const moveQty = moves.reduce((s, c) => s + c.qty, 0)
    const ev: string[] = [`${zeroSide} 재고는 0, ${otherSide}에만 ${fmt(Math.abs(diffNow))}개 있음.`]
    if (moves.length) ev.push(`${zeroSide}에서 ${moves.map(c => `${c.date} ${c.ev_type} ${fmt(c.qty)}`).join(', ')}로 빠졌는데 ${otherSide}엔 같은 거래가 없음.`)
    if (Math.abs(opening) >= absNow * 0.5) ev.push(`조회 시작 시점에 이미 ${sign(opening)} 차이가 있었음 (이번 기간 거래와 무관).`)
    out.push({
      key: 'mapping', title: `${zeroSide}에선 다른 이름의 품목으로 관리 중일 가능성`, level: moves.length || Math.abs(opening) >= absNow * 0.5 ? '유력' : '가능',
      impact: diffNow, color: '#7c3aed',
      evidence: ev,
      action: `재고 현황에서 "${d.name}"과 이름만 다른 품목(괄호·띄어쓰기·샘플 표기 차이)이 ${zeroSide} 쪽에 ${fmt(Math.abs(diffNow))}개로 있는지 찾아 드래그 매칭으로 연결. ${moveQty ? `이동·기타 출고 ${fmt(moveQty)}개가 그 품목으로 옮겨진 것인지 메모 확인.` : ''}`.trim(),
    })
  }

  // B. 근사일치 수량차가 한 방향으로 반복 — 취소·품절 반려 미환원 의심 (메노포즈 패턴)
  const near = causes.filter(c => c.type === '근사일치 수량차')
  if (near.length >= 3) {
    const neg = near.filter(c => c.impact < 0), pos = near.filter(c => c.impact > 0)
    const major = neg.length >= pos.length ? neg : pos
    const ratio = major.length / near.length
    const sum = major.reduce((s, c) => s + c.impact, 0)
    if (ratio >= 0.75 && Math.abs(sum) >= Math.max(10, absBase * 0.15)) {
      const chCount = new Map<string, number>()
      for (const c of major) { const k = normChannel(channelOf(c)); chCount.set(k, (chCount.get(k) || 0) + 1) }
      const topCh = [...chCount.entries()].sort((a, b) => b[1] - a[1])[0]
      const chLabel = topCh && topCh[1] / major.length >= 0.7 ? channelOf(major.find(c => normChannel(channelOf(c)) === topCh[0])!) : ''
      const bhMore = sum < 0
      const perPair = Math.abs(sum) / major.length
      out.push({
        key: 'cancel', title: bhMore ? '주문 취소·품절 반려분이 BH에서 되돌려지지 않음 (의심)' : 'OB 발송 수량이 BH 주문 수량보다 반복해서 많음', level: major.length >= 5 ? '유력' : '가능',
        impact: sum, color: '#db2777',
        evidence: [
          `같은 거래로 짝지어진 ${near.length}건 중 ${major.length}건(${Math.round(ratio * 100)}%)에서 ${bhMore ? 'BH가 OB보다 많이' : 'OB가 BH보다 많이'} 기록됨. 합계 ${sign(sum)}.`,
          `짝당 평균 ${perPair.toFixed(1)}개씩, 기간 ${periodDays}일 동안 하루 평균 ${(Math.abs(sum) / periodDays).toFixed(1)}개꼴로 쌓임.`,
          chLabel ? `대부분 "${chLabel}" 채널 출고에서 발생.` : '여러 채널에 흩어져 있음.',
          `최근 예: ${[...major].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 3).map(c => `${c.date} ${sign(c.impact)}`).join(', ')}`,
        ],
        action: bhMore
          ? `${chLabel ? chLabel + '의 ' : ''}취소·품절 반려 주문 목록을 뽑아 BH 출고 수량과 대조. BH가 주문 수집 수량 그대로 출고 처리하고 취소분을 되돌려 입고하는 절차가 없다면 그것이 근본 원인. 되돌림 절차를 만들거나 발송 완료 수량으로 출고 등록하도록 변경.`
          : `OB 발송 수량이 주문보다 많은 건(추가 증정·교환 재발송)을 확인하고 BH에 그만큼 출고 등록.`,
      })
    }
  }

  // C. OB 가용외(발송 대기)로 설명되는 부분
  const preDed = causes.filter(c => c.type.startsWith('선차감(가용외'))
  const preSum = preDed.reduce((s, c) => s + c.impact, 0)
  if (unav > 0 && preDed.length && Math.abs(preSum) >= Math.max(10, absNow * 0.15)) {
    out.push({
      key: 'unav', title: 'OB 발송 대기(가용외) 수량 — 발송되면 저절로 맞음', level: '유력', impact: preSum, color: '#ca8a04',
      evidence: [`BH만 먼저 출고한 ${preDed.length}건 ${sign(preSum)}이 OB 가용외 ${fmt(unav)}개와 대응.`, `BH는 주문 접수 즉시, OB는 실제 발송 시 출고 기록하는 시점 차이.`],
      action: '조치 불필요. 며칠 뒤 다시 대사해서 이 항목이 사라지는지 확인.',
    })
  }

  // D. 한 번에 생긴 큰 차이 — 그날의 한쪽 거래를 바로 지목
  if (days.length && during !== 0) {
    const top = [...days].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))[0]
    if (Math.sign(top.delta) === Math.sign(during) && Math.abs(top.delta) >= Math.abs(during) * 0.6 && Math.abs(top.delta) >= 20) {
      const onDay = causes.filter(c => c.date === top.date && /만 기록|교차기록/.test(c.type)).sort((a, b) => Math.abs(b.impact) - Math.abs(a.impact))
      if (onDay.length) {
        const c = onDay[0]
        const who = c.type.startsWith('BH') ? 'BH' : c.type.startsWith('OB') ? 'OB' : '한쪽'
        out.push({
          key: 'oneshot', title: `${top.date} ${who}에만 기록된 거래 한 건이 차이의 대부분`, level: '유력', impact: top.delta, color: '#dc2626',
          evidence: [`그날 BH−OB 변동 ${sign(top.delta)} (기간 변동 ${sign(during)}의 ${Math.round(Math.abs(top.delta) / Math.abs(during) * 100)}%).`, `${c.date} ${who} ${c.ev_type || ''} ${fmt(c.qty)}${c.memo ? ` · ${c.memo}` : ''}${c.partner ? ` ↔ ${c.partner}` : ''}`],
          action: c.type === '교차기록의심' ? `같은 날 "${c.partner}"에 반대로 기록됨. 실제 출고 품목을 확인해 한쪽을 수정.` : who === 'BH' ? '메모를 보고 직배송·세트 변경·수기 조정인지 확인. OB를 거쳐야 할 거래였다면 OB 누락.' : 'OB(창고)는 움직였는데 BH 입력이 없음. BH에 해당 입·출고 등록.',
        })
      }
    }
  }

  // E. 조회 시작 전 차이가 대부분
  if (Math.abs(opening) >= absNow * 0.5 && Math.abs(opening) >= 10 && !out.some(x => x.key === 'mapping')) {
    out.push({
      key: 'opening', title: '차이 대부분이 조회 기간 시작 전에 이미 있었음', level: '유력', impact: opening, color: '#ea580c',
      evidence: [`${d.from} 시점 차이 ${sign(opening)} — 지금 차이 ${sign(diffNow)}의 ${Math.round(Math.abs(opening) / absNow * 100)}%.`],
      action: '기간을 늘려 다시 대사해 발생 시점을 좁히기.',
    })
  }

  return out.sort((a, b) => (a.level === b.level ? Math.abs(b.impact) - Math.abs(a.impact) : a.level === '유력' ? -1 : 1))
}

type Pair = { bh: TraceCause; ob: TraceCause; net: number; dayGap: number }

// BH만·OB만 기록 중 수량이 거의 같고 날짜가 ±7일 안이면 "같은 거래, 기록 시점만 다름"으로 짝지음
function pairTimingShifts(causes: TraceCause[]): { pairs: Pair[]; rest: TraceCause[] } {
  const bh = causes.filter(c => c.type === 'BH만 기록').sort((a, b) => b.qty - a.qty)
  const ob = causes.filter(c => c.type === 'OB만 기록').sort((a, b) => b.qty - a.qty)
  const used = new Set<TraceCause>()
  const pairs: Pair[] = []
  for (const b of bh) {
    let best: { o: TraceCause; score: number; gap: number } | null = null
    for (const o of ob) {
      if (used.has(o)) continue
      const gap = Math.abs((new Date(b.date).getTime() - new Date(o.date).getTime()) / 86400000)
      const tol = Math.max(3, Math.round(Math.max(b.qty, o.qty) * 0.03))
      if (gap <= 7 && Math.abs(b.qty - o.qty) <= tol) {
        const score = Math.abs(b.qty - o.qty) * 10 + gap
        if (!best || score < best.score) best = { o, score, gap }
      }
    }
    if (best) { used.add(b); used.add(best.o); pairs.push({ bh: b, ob: best.o, net: b.impact + best.o.impact, dayGap: best.gap }) }
  }
  return { pairs, rest: causes.filter(c => !used.has(c)) }
}

export default function StockTraceReport({ data: d, months, onExtend }: Props) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const toggle = (k: string) => setOpen(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })

  const diffNow = d.diff_now ?? 0
  const opening = d.opening_gap ?? 0
  const during = diffNow - opening
  const residual = d.residual ?? 0
  const eqOk = residual === 0
  const inDiff = d.totals.bh_in - d.totals.ob_in
  const outDiff = d.totals.bh_out - d.totals.ob_out
  const unav = d.avail_basis?.ob_unav ?? d.ob_unav ?? 0
  const diffAvail = d.avail_basis?.diff_avail ?? null

  const { pairs, rest } = useMemo(() => pairTimingShifts(d.causes), [d.causes])
  const groups: Group[] = useMemo(() => {
    const m = new Map<string, Group>()
    for (const c of rest) {
      const g = m.get(c.type) || { type: c.type, items: [], impact: 0 }
      g.items.push(c); g.impact += c.impact; m.set(c.type, g)
    }
    const arr = [...m.values()]
    if (pairs.length) arr.push({ type: '시점차(짝)', items: [], impact: pairs.reduce((s, p) => s + p.net, 0) })
    return arr.sort((a, b) => Math.abs(b.impact) - Math.abs(a.impact))
  }, [rest, pairs])

  // ── 한 줄 결론 ──
  const verdict: string[] = []
  if (inDiff === 0) verdict.push('입고는 BH·OB가 똑같이 기록됐습니다.')
  else verdict.push(`입고에서 BH가 OB보다 ${fmt(Math.abs(inDiff))}개 ${inDiff > 0 ? '더' : '덜'} 기록됐습니다.`)
  if (outDiff === 0) verdict.push('출고도 똑같습니다.')
  else verdict.push(`출고에서 BH가 OB보다 ${fmt(Math.abs(outDiff))}개 ${outDiff > 0 ? '더' : '덜'} 빠졌습니다${outDiff > 0 ? ' (→ BH 재고가 그만큼 적음)' : ' (→ BH 재고가 그만큼 많음)'}.`)
  if (opening !== 0) verdict.push(`조회 시작 시점에 이미 ${sign(opening)} 차이가 있었습니다.`)
  if (unav > 0 && diffAvail !== null) verdict.push(`OB 가용외 ${fmt(unav)}개는 주문이 잡혀 발송 대기 중인 수량이라 발송되면 저절로 맞습니다. 그걸 빼고 보면 실제 차이는 ${sign(diffAvail)}입니다.`)
  if (pairs.length) {
    const net = pairs.reduce((s, p) => s + p.net, 0)
    verdict.push(`BH·OB 한쪽에만 보이던 거래 ${pairs.length * 2}건은 같은 거래를 날짜만 다르게 기록한 것으로 보입니다 (짝지은 뒤 남는 차이 ${sign(net)}).`)
  }

  const impactColor = (n: number) => (n > 0 ? '#2563eb' : n < 0 ? '#ef4444' : '#9ca3af')

  // 채널별 순차이: 기간 밖·조회 이전 항목을 뺀 나머지 원인을 채널로 묶음
  const byChannel = useMemo(() => {
    const m = new Map<string, { bh: number; ob: number; net: number; n: number }>()
    const labelOf = new Map<string, string>()
    for (const c of d.causes) {
      if (c.type === '기간이전차이' || c.type.startsWith('기간경계')) continue
      const raw = channelOf(c)
      const key = normChannel(raw)
      if (!labelOf.has(key)) labelOf.set(key, raw)
      const ch = labelOf.get(key)!
      const g = m.get(ch) || { bh: 0, ob: 0, net: 0, n: 0 }
      if (c.impact < 0) g.bh += Math.abs(c.impact); else g.ob += Math.abs(c.impact)
      g.net += c.impact; g.n += 1; m.set(ch, g)
    }
    return [...m.entries()].map(([ch, v]) => ({ ch, ...v })).sort((a, b) => Math.abs(b.net) - Math.abs(a.net))
  }, [d.causes])
  const { days, weeks } = useMemo(() => driftSeries(d), [d])
  const diagnoses = useMemo(() => diagnose(d, days, byChannel), [d, days, byChannel])
  const topCh = byChannel[0]
  if (topCh && Math.abs(topCh.net) >= Math.max(20, Math.abs(during) * 0.5)) {
    verdict.push(`기간 중 차이는 대부분 "${topCh.ch}" 출고에서 생겼습니다 (이 채널만 ${sign(topCh.net)}).`)
  }
  // 언제 벌어졌나: 기간 중 변동(during)과 같은 방향으로 가장 크게 움직인 날들
  const whenLine = useMemo(() => {
    if (during === 0 || !days.length) return ''
    const dir = Math.sign(during)
    const same = days.filter(x => Math.sign(x.delta) === dir).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    if (!same.length) return ''
    const top = same.slice(0, 3)
    const topSum = top.reduce((s, x) => s + x.delta, 0)
    const share = Math.abs(topSum) / Math.abs(during)
    const how = dir < 0 ? 'BH가 더 빠짐' : 'OB가 더 빠짐'
    if (same.length === 1 || Math.abs(top[0].delta) / Math.abs(during) >= 0.8)
      return `차이는 ${top[0].date}에 한 번에 생겼습니다 (${sign(top[0].delta)}, ${how}).`
    if (share >= 0.7)
      return `차이는 주로 ${top.map(x => `${x.date}(${sign(x.delta)})`).join(', ')}에 생겼습니다 (${how}, 합쳐서 기간 변동의 ${Math.round(share * 100)}%).`
    const first = [...same].sort((a, b) => a.date.localeCompare(b.date))[0]
    return `차이가 ${first.date}부터 ${same.length}일에 걸쳐 조금씩 벌어졌습니다 (가장 큰 날 ${top[0].date} ${sign(top[0].delta)}).`
  }, [days, during])
  if (whenLine) verdict.push(whenLine)
  // 기간 밖 항목(조회 시작 전 차이·기간 경계)이 차이의 30% 이상이면 기간을 늘리라고 권함
  const boundaryImpact = d.causes.filter(c => c.type === '기간이전차이' || c.type.startsWith('기간경계')).reduce((s, c) => s + Math.abs(c.impact), 0)
  const needExtend = !!onExtend && boundaryImpact > 0 && boundaryImpact >= Math.max(30, Math.abs(diffNow) * 0.3)

  return (
    <div style={{ fontSize: '0.8rem' }}>
      {/* 숫자 카드 */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        {[
          { l: '지금 차이 (BH−OB)', v: diffNow, c: impactColor(diffNow) },
          { l: '조회 시작 때 차이', v: opening, c: impactColor(opening) },
          { l: '기간 중 벌어진 차이', v: during, c: impactColor(during) },
          { l: '설명 안 된 잔차', v: residual, c: eqOk ? '#166534' : '#b91c1c', ok: eqOk },
        ].map(k => (
          <div key={k.l} style={{ flex: '1 1 120px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px' }}>
            <div style={{ fontSize: '0.7rem', color: '#6b7280' }}>{k.l}</div>
            <div style={{ fontSize: '1.15rem', fontWeight: 700, color: k.c }}>{sign(k.v)}{k.ok ? ' ✅' : ''}</div>
          </div>
        ))}
      </div>

      {/* 유력 원인 자동 진단 */}
      {diagnoses.length > 0 && (
        <div style={{ background: '#fff', border: '2px solid #c4b5fd', borderRadius: 8, padding: '10px 12px', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>🔎 유력 원인 (자동 진단)</div>
          {diagnoses.map(g => (
            <div key={g.key} style={{ borderLeft: `4px solid ${g.color}`, padding: '4px 10px', marginBottom: 8, lineHeight: 1.65 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Tag color={g.level === '유력' ? 'purple' : 'default'} style={{ margin: 0 }}>{g.level}</Tag>
                <b>{g.title}</b>
                <span style={{ color: impactColor(g.impact), fontWeight: 700 }}>{sign(g.impact)}</span>
              </div>
              {g.evidence.map((e, i) => <div key={i} style={{ color: '#374151' }}>· {e}</div>)}
              <div style={{ color: '#065f46', marginTop: 2 }}>→ {g.action}</div>
            </div>
          ))}
          <div style={{ fontSize: '0.68rem', color: '#9ca3af' }}>수동 대사에서 쓰던 판단 규칙으로 자동 추정한 것입니다. 아래 원인표에서 근거 거래를 직접 확인하세요.</div>
        </div>
      )}

      {/* 결론 */}
      <div style={{ background: eqOk ? '#f0fdf4' : '#fef2f2', border: `1px solid ${eqOk ? '#bbf7d0' : '#fecaca'}`, borderRadius: 8, padding: '10px 12px', marginBottom: 10, lineHeight: 1.7 }}>
        <div style={{ fontWeight: 700, marginBottom: 2 }}>{eqOk ? '✅ 차이가 전부 설명됩니다' : '⚠ 설명 안 된 차이가 남아 있습니다'}</div>
        {verdict.map((s, i) => <div key={i}>· {s}</div>)}
        {needExtend && (
          <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: '#b45309' }}>조회 기간 밖 항목이 {fmt(boundaryImpact)}개로 큽니다{months ? ` (현재 ${months}개월)` : ''}.</span>
            <Button size="small" type="primary" onClick={onExtend}>기간 늘려 다시 대사</Button>
          </div>
        )}
      </div>

      {/* 채널별 순차이 + 주별 변동 */}
      {(byChannel.length > 0 || weeks.length > 0) && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
          {byChannel.length > 0 && (
            <div style={{ flex: '1 1 300px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px' }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>어느 채널에서 벌어졌나</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.76rem' }}>
                <thead><tr style={{ color: '#6b7280' }}>
                  <th style={{ textAlign: 'left', padding: '2px 4px' }}>채널</th>
                  <th style={{ textAlign: 'right', padding: '2px 4px' }}>BH가 더 뺀 양</th>
                  <th style={{ textAlign: 'right', padding: '2px 4px' }}>OB가 더 뺀 양</th>
                  <th style={{ textAlign: 'right', padding: '2px 4px' }}>순차이</th>
                </tr></thead>
                <tbody>
                  {byChannel.slice(0, 8).map(r => (
                    <tr key={r.ch} style={{ borderTop: '1px solid #f3f4f6' }}>
                      <td style={{ padding: '3px 4px' }}>{r.ch} <span style={{ color: '#9ca3af' }}>({r.n}건)</span></td>
                      <td style={{ padding: '3px 4px', textAlign: 'right', color: '#1e3a8a' }}>{fmt(r.bh)}</td>
                      <td style={{ padding: '3px 4px', textAlign: 'right', color: '#92400e' }}>{fmt(r.ob)}</td>
                      <td style={{ padding: '3px 4px', textAlign: 'right', fontWeight: 700, color: impactColor(r.net) }}>{sign(r.net)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ fontSize: '0.68rem', color: '#9ca3af', marginTop: 4 }}>순차이가 음수면 BH가 그 채널 출고를 더 많이 뺀 것. 조회 시작 전 차이·기간 경계 항목은 제외.</div>
            </div>
          )}
          {weeks.length > 0 && (
            <div style={{ flex: '1 1 300px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px' }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>언제 벌어졌나 (주별 BH−OB 변동)</div>
              {(() => {
                const mx = Math.max(1, ...weeks.map(w => Math.abs(w.delta)))
                const showAll = weeks.length <= 14
                const mmdd = (w: string) => w.slice(5).replace('-', '/')
                return (
                  <>
                    <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 70, borderBottom: '1px solid #e5e7eb' }}>
                      {weeks.map(w => (
                        <Tooltip key={w.week} title={`${w.week} 주: ${w.delta === 0 ? '변동 없음' : sign(w.delta)} → 누적 ${sign(w.cum)}`}>
                          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center', height: '100%' }}>
                            {w.delta !== 0 && showAll && <span style={{ fontSize: '0.62rem', color: impactColor(w.delta), lineHeight: 1 }}>{sign(w.delta)}</span>}
                            <div style={{ width: '100%', height: `${w.delta === 0 ? 2 : Math.max(4, Math.abs(w.delta) / mx * 85)}%`, background: w.delta === 0 ? '#e5e7eb' : w.delta < 0 ? '#fca5a5' : '#93c5fd', borderRadius: 2 }} />
                          </div>
                        </Tooltip>
                      ))}
                    </div>
                    <div style={{ display: 'flex', gap: 3, fontSize: '0.6rem', color: '#9ca3af', marginTop: 2 }}>
                      {weeks.map((w, i) => (
                        <div key={w.week} style={{ flex: 1, textAlign: 'center', whiteSpace: 'nowrap', overflow: 'hidden' }}>
                          {showAll || i === 0 || i === weeks.length - 1 || i === Math.floor(weeks.length / 2) ? mmdd(w.week) : ''}
                        </div>
                      ))}
                    </div>
                  </>
                )
              })()}
              <div style={{ fontSize: '0.68rem', color: '#9ca3af', marginTop: 2 }}>막대 = 그 주(월요일 시작)에 벌어진 변동. 빨강 = BH가 더 빠짐 · 파랑 = OB가 더 빠짐 · 회색 = 변동 없음</div>
              {days.length > 0 && (
                <div style={{ marginTop: 8, borderTop: '1px dashed #e5e7eb', paddingTop: 6 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.74rem', marginBottom: 2 }}>차이가 생긴 날 {days.length > 6 ? `(큰 순 6일 / 총 ${days.length}일)` : ''}</div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.74rem' }}>
                    <thead><tr style={{ color: '#6b7280' }}>
                      <th style={{ textAlign: 'left', padding: '1px 4px' }}>날짜</th>
                      <th style={{ textAlign: 'right', padding: '1px 4px' }}>BH 변동</th>
                      <th style={{ textAlign: 'right', padding: '1px 4px' }}>OB 변동</th>
                      <th style={{ textAlign: 'right', padding: '1px 4px' }}>그날 차이</th>
                      <th style={{ textAlign: 'right', padding: '1px 4px' }}>누적 차이</th>
                    </tr></thead>
                    <tbody>
                      {[...days].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 6).sort((a, b) => a.date.localeCompare(b.date)).map(x => (
                        <tr key={x.date} style={{ borderTop: '1px solid #f3f4f6' }}>
                          <td style={{ padding: '2px 4px' }}>{x.date}</td>
                          <td style={{ padding: '2px 4px', textAlign: 'right', color: '#1e3a8a' }}>{sign(x.bh)}</td>
                          <td style={{ padding: '2px 4px', textAlign: 'right', color: '#92400e' }}>{sign(x.ob)}</td>
                          <td style={{ padding: '2px 4px', textAlign: 'right', fontWeight: 700, color: impactColor(x.delta) }}>{sign(x.delta)}</td>
                          <td style={{ padding: '2px 4px', textAlign: 'right', color: impactColor(x.cum) }}>{sign(x.cum)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ fontSize: '0.66rem', color: '#9ca3af', marginTop: 2 }}>BH·OB 변동 = 그날 입고(+)·출고(−) 합. 그날 차이 = BH 변동 − OB 변동. 조회 시작 때 차이({sign(opening)})부터 누적.</div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 원인 요약표 */}
      <table style={{ width: '100%', borderCollapse: 'collapse', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
        <thead>
          <tr style={{ background: '#f9fafb', fontSize: '0.72rem', color: '#6b7280' }}>
            <th style={{ textAlign: 'left', padding: '6px 10px' }}>원인</th>
            <th style={{ textAlign: 'right', padding: '6px 10px', width: 60 }}>건수</th>
            <th style={{ textAlign: 'right', padding: '6px 10px', width: 80 }}>차이 영향</th>
            <th style={{ textAlign: 'left', padding: '6px 10px' }}>무슨 뜻인가 · 어떻게 할까</th>
          </tr>
        </thead>
        <tbody>
          {groups.length === 0 && (
            <tr><td colSpan={4} style={{ padding: 10, color: '#166534' }}>남는 거래 없음. 기간 내 모든 입·출고가 양쪽에서 일치합니다.</td></tr>
          )}
          {groups.map(g => {
            const info = infoOf(g.type)
            const isPair = g.type === '시점차(짝)'
            const count = isPair ? pairs.length : g.items.length
            const isOpen = open.has(g.type)
            return [
              <tr key={g.type} onClick={() => toggle(g.type)} style={{ cursor: 'pointer', borderTop: '1px solid #f3f4f6' }}>
                <td style={{ padding: '7px 10px', whiteSpace: 'nowrap' }}>
                  <span style={{ color: '#9ca3af', marginRight: 4 }}>{isOpen ? '▾' : '▸'}</span>
                  <Tag color={info.color} style={{ margin: 0 }}>{info.label}</Tag>
                </td>
                <td style={{ padding: '7px 10px', textAlign: 'right' }}>{count}건</td>
                <td style={{ padding: '7px 10px', textAlign: 'right', fontWeight: 700, color: impactColor(g.impact) }}>{sign(g.impact)}</td>
                <td style={{ padding: '7px 10px', color: '#374151', lineHeight: 1.5 }}>
                  {info.meaning}{info.action && <span style={{ color: '#6b7280' }}> → {info.action}</span>}
                </td>
              </tr>,
              isOpen && (
                <tr key={g.type + '_d'}>
                  <td colSpan={4} style={{ padding: '0 10px 8px 28px', background: '#fafafa' }}>
                    {isPair ? pairs.map((p, i) => (
                      <div key={i} style={{ display: 'flex', gap: 10, padding: '4px 0', borderBottom: '1px solid #f3f4f6', alignItems: 'baseline', flexWrap: 'wrap' }}>
                        <span style={{ color: '#1e3a8a', minWidth: 190 }}>BH {p.bh.date} 출고 {fmt(p.bh.qty)}</span>
                        <span style={{ color: '#9ca3af' }}>↔</span>
                        <span style={{ color: '#92400e', minWidth: 190 }}>OB {p.ob.date} 출고 {fmt(p.ob.qty)}</span>
                        <b style={{ color: impactColor(p.net), minWidth: 50, textAlign: 'right' }}>{sign(p.net)}</b>
                        <span style={{ color: '#9ca3af', fontSize: '0.72rem' }}>{p.dayGap}일 차 · {p.bh.memo || ''}</span>
                      </div>
                    )) : g.items.map((c, i) => (
                      <div key={i} style={{ display: 'flex', gap: 10, padding: '4px 0', borderBottom: '1px solid #f3f4f6', alignItems: 'baseline', flexWrap: 'wrap' }}>
                        <span style={{ color: '#6b7280', minWidth: 80 }}>{c.date}</span>
                        <b style={{ color: impactColor(c.impact), minWidth: 56, textAlign: 'right' }}>{sign(c.impact)}</b>
                        <Tooltip title={c.desc}>
                          <span style={{ color: '#374151' }}>
                            {c.ev_type || ''} {c.memo ? `· ${c.memo}` : ''}{c.channel ? ` · ${c.channel}` : ''}
                            {c.partner && <b style={{ color: '#be185d' }}> ↔ {c.partner}</b>}
                          </span>
                        </Tooltip>
                      </div>
                    ))}
                  </td>
                </tr>
              ),
            ]
          })}
        </tbody>
      </table>

      {/* 참고 */}
      <div style={{ marginTop: 8, fontSize: '0.72rem', color: '#9ca3af', lineHeight: 1.6 }}>
        <div>
          기간 합계 — 입고 BH {fmt(d.totals.bh_in)} / OB {fmt(d.totals.ob_in)} · 출고 BH {fmt(d.totals.bh_out)} / OB {fmt(d.totals.ob_out)} · 짝지어진 거래 입고 {d.matched_in}건 · 출고 {d.matched_out}건
        </div>
        {d.avail_basis && d.avail_basis.unav_events.length > 0 && (
          <div>
            <span style={{ cursor: 'pointer', textDecoration: 'underline' }} onClick={() => toggle('_unav')}>
              {open.has('_unav') ? '▾' : '▸'} OB 가용외(발송 대기) 변동 기록 {d.avail_basis.unav_events.length}건
            </span>
            {open.has('_unav') && (
              <div style={{ marginTop: 2 }}>
                {d.avail_basis.unav_events.map(u => `${u.date} ${u.delta > 0 ? '+' : ''}${fmt(u.delta)}`).join(' · ')}
                <span> (+는 주문 할당, −는 발송 완료)</span>
              </div>
            )}
          </div>
        )}
        {(d.errors || []).length > 0 && <div style={{ color: '#b45309' }}>⚠ {d.errors.join(' · ')}</div>}
      </div>
    </div>
  )
}
