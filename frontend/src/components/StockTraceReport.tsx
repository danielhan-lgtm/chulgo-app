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

// 전체 이벤트에서 주별 BH−OB 순변동 계산 (언제 벌어졌는지)
function weeklySeries(d: StockDiffTrace): { week: string; delta: number; cum: number }[] {
  const bh: Record<string, number> = {}, ob: Record<string, number> = {}
  const add = (m: Record<string, number>, e: { date: string; qty: number }, sign: number) => { m[e.date] = (m[e.date] || 0) + sign * e.qty }
  const mp = d.matched_pairs
  if (!mp) return []
  for (const grp of ['in', 'out'] as const) {
    const sg = grp === 'in' ? 1 : -1
    for (const p of mp[grp] || []) { p.bh.forEach(e => add(bh, e, sg)); p.ob.forEach(e => add(ob, e, sg)) }
  }
  for (const c of d.cancelled || []) { c.bh.forEach(e => add(bh, e, -1)); c.ob.forEach(e => add(ob, e, -1)) }
  const u = d.unmatched
  u.bh_in.forEach(e => add(bh, e, 1)); u.ob_in.forEach(e => add(ob, e, 1))
  u.bh_out.filter(e => !e.synthetic).forEach(e => add(bh, e, -1)); u.ob_out.filter(e => !e.synthetic).forEach(e => add(ob, e, -1))
  const weeks: Record<string, number> = {}
  for (const k of new Set([...Object.keys(bh), ...Object.keys(ob)])) {
    const dt = new Date(k); const dow = (dt.getDay() + 6) % 7
    dt.setDate(dt.getDate() - dow)
    const w = dt.toISOString().slice(0, 10)
    weeks[w] = (weeks[w] || 0) + (bh[k] || 0) - (ob[k] || 0)
  }
  let cum = d.opening_gap ?? 0
  return Object.keys(weeks).sort().map(w => { cum += weeks[w]; return { week: w, delta: weeks[w], cum } })
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
  const weeks = useMemo(() => weeklySeries(d), [d])
  const topCh = byChannel[0]
  if (topCh && Math.abs(topCh.net) >= Math.max(20, Math.abs(during) * 0.5)) {
    verdict.push(`기간 중 차이는 대부분 "${topCh.ch}" 출고에서 생겼습니다 (이 채널만 ${sign(topCh.net)}).`)
  }
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
              <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 70, borderBottom: '1px solid #e5e7eb' }}>
                {(() => {
                  const mx = Math.max(1, ...weeks.map(w => Math.abs(w.delta)))
                  return weeks.map(w => (
                    <Tooltip key={w.week} title={`${w.week} 주: ${sign(w.delta)} → 누적 ${sign(w.cum)}`}>
                      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', height: '100%' }}>
                        <div style={{ height: `${Math.max(2, Math.abs(w.delta) / mx * 100)}%`, background: w.delta < 0 ? '#fca5a5' : '#93c5fd', borderRadius: 2 }} />
                      </div>
                    </Tooltip>
                  ))
                })()}
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.68rem', color: '#9ca3af', marginTop: 2 }}>
                <span>{weeks[0].week}</span><span>빨강 = 그 주에 BH가 더 빠짐 · 파랑 = OB가 더 빠짐</span><span>{weeks[weeks.length - 1].week}</span>
              </div>
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
