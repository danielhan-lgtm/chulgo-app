import { useEffect, useState } from 'react'
import { Spin, Tag, Tooltip, Button, Segmented, message } from 'antd'
import { ReloadOutlined, CopyOutlined } from '@ant-design/icons'
import { getChannelSkuTrend } from '../services/api'
import type { ChannelSkuTrend, SkuWinQty } from '../services/api'

const n = (v: number | null | undefined) => (v == null ? '-' : v.toLocaleString())
const SHORT: Record<string, string> = { coupang: '쿠팡', oliveyoung: '올리브영', kurly: '컬리', naver: '네이버' }
const chName = (c: { key: string; name: string }) => SHORT[c.key] || c.name.replace(/\(.*\)/, '').trim()
// 가로 스크롤 때 품목·채널 열 고정 (품목 폭 고정 → 채널 열이 그 옆에 붙음)
const STICKY: React.CSSProperties = { position: 'sticky', left: 0, zIndex: 1, minWidth: 176, maxWidth: 176, overflow: 'hidden', textOverflow: 'ellipsis' }
const STICKY2: React.CSSProperties = { position: 'sticky', left: 176, zIndex: 1, minWidth: 128, boxShadow: '2px 0 0 #e5e7eb' }
const pct = (cur: number, prev: number) => (prev > 0 ? `${cur >= prev ? '+' : ''}${Math.round(((cur - prev) / prev) * 100)}%` : '')

// 전 채널 SKU별 월별 추이 — 경영 요약(전 채널) 형식에서 쿠팡·올리브영·컬리·네이버 보고 화면 공용.
// 품목마다 '합계' 줄 + 채널별 줄. 채널 수치는 각 채널 경영 요약 첫 줄과 같은 지표(쿠팡 입고등록 · 올리브영 발주 · 컬리 발주 · 네이버 출고).
export default function SkuChannelTrend({ asOf, month }: { asOf: string; month: string }) {
  const [data, setData] = useState<ChannelSkuTrend | null>(null)
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState<'all' | 'total'>('all')
  const [limit, setLimit] = useState(12)

  async function load() {
    setLoading(true)
    try { setData(await getChannelSkuTrend(asOf, month)) }
    catch (e: unknown) { message.error(e instanceof Error ? e.message : 'SKU별 채널 추이 조회 실패') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [asOf, month])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!data && loading) return <div className="panel"><Spin size="small" /> <span className="muted">SKU별 채널 추이 집계 중…</span></div>
  if (!data) return null
  const months = data.months
  const chans = data.channels
  const rows = data.rows.filter(r => r.sum > 0)
  const shown = rows.slice(0, limit)
  const lastFull = months.length >= 2 ? months[months.length - 2] : months[months.length - 1]
  const prevFull = months.length >= 3 ? months[months.length - 3] : null
  const W = data.windows
  const curM = W ? months.find(m => m.current) : undefined   // 옛 응답(windows 없음)이 남아 있으면 이달 비교 열 생략
  // 이달 비교: 같은 날짜 범위로만 — 전월 동기(1일~기준일) · 최근 7일 vs 직전 7일
  const cmp = (cur: number, prev: number) => {
    const p = pct(cur, prev)
    return <span style={{ color: p.startsWith('-') ? '#b91c1c' : p ? '#15803d' : undefined }}>{p || '-'}</span>
  }
  const winCell = (w: Partial<SkuWinQty> | undefined, k: 'mtd' | 'w1', pk: 'mtd_prev' | 'w0', muted = false) => {
    const cur = w?.[k] || 0, prev = w?.[pk] || 0
    if (!cur && !prev) return <td className="num muted">-</td>
    return <td className={`num${muted ? ' muted' : ''}`} style={{ whiteSpace: 'nowrap' }}>{n(cur)} <span className="muted" style={{ fontSize: '0.72rem' }}>/ {n(prev)}</span> {cmp(cur, prev)}</td>
  }

  const copyText = () => {
    const L: string[] = [`■ SKU별 채널 추이 (${months.map(m => m.label).join(' → ')}${months.some(m => m.current) ? ' · 이달 진행중' : ''})`]
    for (const r of shown) {
      const wtxt = curM ? ` · 이달 ${W.mtd.label} ${n(r.win.mtd)} vs 전월 동기 ${n(r.win.mtd_prev)} (${pct(r.win.mtd, r.win.mtd_prev) || '-'}) · 최근 7일 ${n(r.win.w1)} vs 직전 7일 ${n(r.win.w0)} (${pct(r.win.w1, r.win.w0) || '-'})` : ''
      L.push(`* ${r.label} — 합계 ${months.map(m => n(r.total[m.ym] || 0)).join(' → ')}개`
        + (prevFull && r.total[lastFull.ym] ? ` (${lastFull.label} 전월 대비 ${pct(r.total[lastFull.ym] || 0, r.total[prevFull.ym] || 0) || '-'})` : '') + wtxt)
      for (const c of chans) {
        const bm = r.by[c.key]
        if (!bm || !Object.values(bm).some(v => v > 0)) continue
        L.push(`   - ${chName(c)} ${c.metric}: ${months.map(m => n(bm[m.ym] || 0)).join(' → ')}`)
      }
    }
    navigator.clipboard.writeText(L.join('\n')).then(() => message.success('SKU별 채널 추이를 복사했습니다'))
  }

  return (
    <div className="panel">
      <h3 style={{ flexWrap: 'wrap' }}><span style={{ whiteSpace: 'nowrap' }}>■ SKU별 채널 추이</span> <span className="sub" style={{ flex: '1 1 200px', minWidth: 0 }}>최근 {months.length}개월 · 바코드로 채널을 묶음 · 채널 수치는 경영 요약 첫 줄과 같은 지표</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
          <Segmented size="small" value={view} onChange={v => setView(v as 'all' | 'total')} options={[{ label: '채널별', value: 'all' }, { label: '합계만', value: 'total' }]} />
          <Button size="small" icon={<CopyOutlined />} onClick={copyText}>텍스트 복사</Button>
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>다시 집계</Button>
        </span>
      </h3>
      {chans.filter(c => c.error).map(c => <div key={c.key} className="hint" style={{ color: '#b91c1c' }}>{c.name} 집계 실패: {c.error}</div>)}
      <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
      <table style={{ whiteSpace: 'nowrap' }}>
        <thead><tr>
          <th style={STICKY}>품목</th><th style={STICKY2}>채널</th>
          {months.map(m => <th key={m.ym} className="num">{m.label}{m.current && <span className="muted"> (진행중)</span>}</th>)}
          <th className="num"><Tooltip title={`${lastFull.label} 합계의 전월 대비`}>{lastFull.label} 전월비</Tooltip></th>
          {curM && <th className="num" style={{ borderLeft: '2px solid #e5e7eb' }}><Tooltip title={`이달 ${W.mtd.label} / 전월 동기 ${W.mtd_prev.label} · 같은 날짜 범위(1일~${W.day}일) 비교`}>이달 {W.mtd.label} / 전월 동기</Tooltip></th>}
          {curM && <th className="num"><Tooltip title={`최근 7일 ${W.w1.label} / 직전 7일 ${W.w0.label}`}>최근 7일 / 직전 7일</Tooltip></th>}
        </tr></thead>
        <tbody>
          {shown.map(r => {
            const chRows = view === 'all' ? chans.filter(c => r.by[c.key] && Object.values(r.by[c.key]).some(v => v > 0)) : []
            const p = prevFull ? pct(r.total[lastFull.ym] || 0, r.total[prevFull.ym] || 0) : ''
            return [
              <tr key={r.key} style={{ background: '#f9fafb', fontWeight: 600 }}>
                <td style={{ ...STICKY, background: '#f9fafb' }}>{r.label}{r.sku ? <Tooltip title={`바코드 ${r.sku}`}><span className="muted" style={{ fontWeight: 400, fontSize: '0.72rem' }}> …{r.sku.slice(-5)}</span></Tooltip> : <Tooltip title="바코드가 없어 상품명(라벨)으로만 묶음"><Tag style={{ marginLeft: 4 }}>라벨</Tag></Tooltip>}</td>
                <td style={{ ...STICKY2, background: '#f9fafb' }}>합계</td>
                {months.map(m => <td key={m.ym} className="num">{n(r.total[m.ym] || 0)}</td>)}
                <td className="num" style={{ color: p.startsWith('-') ? '#b91c1c' : p ? '#15803d' : undefined }}>{p || '-'}</td>
                {curM && winCell(r.win, 'mtd', 'mtd_prev')}
                {curM && winCell(r.win, 'w1', 'w0')}
              </tr>,
              ...chRows.map(c => {
                const bm = r.by[c.key]
                const cp = prevFull ? pct(bm[lastFull.ym] || 0, bm[prevFull.ym] || 0) : ''
                return (
                  <tr key={`${r.key}:${c.key}`}>
                    <td style={{ ...STICKY, background: '#fff' }} />
                    <td className="muted" style={{ ...STICKY2, background: '#fff' }}>{chName(c)} <span style={{ fontSize: '0.72rem' }}>({c.metric})</span></td>
                    {months.map(m => <td key={m.ym} className="num">{bm[m.ym] ? n(bm[m.ym]) : <span className="muted">-</span>}</td>)}
                    <td className="num muted">{cp || '-'}</td>
                    {curM && winCell(r.win_by[c.key], 'mtd', 'mtd_prev', true)}
                    {curM && winCell(r.win_by[c.key], 'w1', 'w0', true)}
                  </tr>
                )
              }),
            ]
          })}
        </tbody>
      </table>
      </div>
      {rows.length > limit && <Button size="small" type="link" onClick={() => setLimit(l => l + 12)}>더 보기 ({rows.length - limit}종 남음)</Button>}
      {limit > 12 && <Button size="small" type="link" onClick={() => setLimit(12)}>접기</Button>}
      <div className="hint" style={{ marginTop: 6 }}>
        쿠팡은 발주 캘린더의 입고등록 수량(납기월), 올리브영·컬리는 발주 수량(납기월), 네이버는 주문 관리 목록(없는 날은 박스히어로 거래 내역)의 출고 수량(주문일)입니다.
        정렬은 가장 최근 마감월({lastFull.label}) 합계 순이며, 바코드가 없는 상품명은 라벨로만 묶여 다른 채널과 합쳐지지 않을 수 있습니다.
        {curM && <> 이달 열은 같은 날짜 범위로만 비교합니다: 이달 {W.mtd.label} vs 전월 {W.mtd_prev.label}, 최근 7일 {W.w1.label} vs 직전 7일 {W.w0.label}. 쿠팡·올리브영·컬리는 납기일 기준이라 납기가 몰린 주에 따라 7일 비교가 출렁일 수 있고, 네이버는 주문일 기준입니다. 이달 월 합계에는 기준일 이후 납기 예정분도 들어 있습니다.</>}
      </div>
    </div>
  )
}
