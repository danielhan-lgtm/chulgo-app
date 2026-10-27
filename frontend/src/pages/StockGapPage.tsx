import { useEffect, useMemo, useState } from 'react'
import { Button, Radio, Select, Table, Tag, Tooltip, Input, Switch, Drawer, Spin, Collapse, Alert, message } from 'antd'
import { PlayCircleOutlined, SearchOutlined, LineChartOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { AppConfig, Location } from '../types'
import { getLocations, runStockGap, getStockGapLast, getStockGapItem } from '../services/api'
import type { GapResult, GapItem, GapDetail, GapChange, GapObserved } from '../services/api'
import StockTraceReport from '../components/StockTraceReport'

interface Props { config: AppConfig }

const fmt = (n: number | null | undefined) => (n === null || n === undefined ? '—' : n.toLocaleString())
const sign = (n: number | null | undefined) => (n === null || n === undefined ? '—' : n > 0 ? `+${n.toLocaleString()}` : n.toLocaleString())
const diffColor = (n: number | null | undefined) => (n === null || n === undefined || n === 0 ? '#6b7280' : n > 0 ? '#2563eb' : '#dc2626')

// 원인 유형 → 짧은 라벨/색 (StockTraceReport와 같은 체계)
const TYPE_META: Record<string, { label: string; color: string }> = {
  '시점차(짝)': { label: '기록 시점 차이', color: 'green' },
  '선차감(가용외확인)': { label: 'BH 먼저 출고(OB 대기)', color: 'gold' },
  '선차감(가용외추정)': { label: 'BH 먼저 출고(추정)', color: 'gold' },
  '선차감→발송(상쇄)': { label: '선차감→발송 상쇄', color: 'green' },
  'BH만 기록': { label: 'BH에만 있는 거래', color: 'geekblue' },
  'OB만 기록': { label: 'OB에만 있는 거래', color: 'volcano' },
  '기간경계(시점차가능)': { label: '기간 경계', color: 'default' },
  '기간이전차이': { label: '조회 시작 전 차이', color: 'orange' },
  '기간이전 선반영(상쇄)': { label: '시작 전 선반영 상쇄', color: 'green' },
  '교차기록의심': { label: '다른 품목으로 기록 의심', color: 'magenta' },
  '근사일치 수량차': { label: '같은 거래 수량차', color: 'purple' },
}
const typeMeta = (t: string) => TYPE_META[t] || { label: t, color: 'default' }

// ── 차이 타임라인 차트 (복원 계단선 + 실측 점) ───────────────────────────────
function GapChart({ from, to, opening, series, changes, observed }: {
  from: string; to: string; opening: number
  series: { date: string; gap: number }[]
  changes: GapChange[]
  observed: GapObserved[]
}) {
  const W = 860, H = 240, PL = 56, PR = 16, PT = 14, PB = 34
  const t0 = dayjs(from).valueOf(), t1 = dayjs(to).add(1, 'day').valueOf()
  const span = Math.max(1, t1 - t0)
  const x = (d: string) => PL + ((dayjs(d).valueOf() - t0) / span) * (W - PL - PR)
  const vals = [0, opening, ...series.map(s => s.gap), ...observed.map(o => o.diff)]
  let lo = Math.min(...vals), hi = Math.max(...vals)
  if (hi === lo) { hi += 1; lo -= 1 }
  const pad = (hi - lo) * 0.12
  lo -= pad; hi += pad
  const y = (v: number) => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB)

  // 계단선: from 시점 opening → 각 변동일에 수직 점프
  let path = `M ${x(from).toFixed(1)} ${y(opening).toFixed(1)}`
  let cur = opening
  for (const s of series) {
    path += ` H ${x(s.date).toFixed(1)} V ${y(s.gap).toFixed(1)}`
    cur = s.gap
  }
  path += ` H ${(W - PR).toFixed(1)}`
  const kindOf: Record<string, GapChange['kind']> = {}
  for (const c of changes) kindOf[c.date] = c.kind

  // 눈금: 월 초
  const ticks: string[] = []
  let m = dayjs(from).startOf('month').add(1, 'month')
  while (m.valueOf() < t1) { ticks.push(m.format('YYYY-MM-DD')); m = m.add(1, 'month') }
  const yTicks = Array.from(new Set([0, Math.round(lo + pad), Math.round(hi - pad)]))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8 }}>
      {yTicks.map(v => (
        <g key={v}>
          <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke={v === 0 ? '#9ca3af' : '#f3f4f6'} strokeDasharray={v === 0 ? '4 3' : undefined} />
          <text x={PL - 6} y={y(v) + 4} fontSize={10} textAnchor="end" fill="#6b7280">{sign(v)}</text>
        </g>
      ))}
      {ticks.map(d => (
        <g key={d}>
          <line x1={x(d)} x2={x(d)} y1={PT} y2={H - PB} stroke="#f3f4f6" />
          <text x={x(d)} y={H - PB + 14} fontSize={10} textAnchor="middle" fill="#6b7280">{dayjs(d).format('M월')}</text>
        </g>
      ))}
      <text x={PL} y={H - PB + 28} fontSize={10} fill="#9ca3af">{from}</text>
      <text x={W - PR} y={H - PB + 28} fontSize={10} textAnchor="end" fill="#9ca3af">{to}</text>
      <path d={path} fill="none" stroke="#111827" strokeWidth={1.6} />
      {series.map(s => {
        const k = kindOf[s.date] || 'timing'
        const col = k === 'cause' ? '#dc2626' : k === 'partial' ? '#f59e0b' : '#9ca3af'
        return (
          <circle key={s.date} cx={x(s.date)} cy={y(s.gap)} r={k === 'timing' ? 2.5 : 4} fill={col} stroke="#fff" strokeWidth={1}>
            <title>{`${s.date} 복원 차이 ${sign(s.gap)} (${k === 'timing' ? '시점차·이후 상쇄' : '원인 확정'})`}</title>
          </circle>
        )
      })}
      {observed.map(o => (
        <g key={o.date}>
          <rect x={x(o.date) - 3.5} y={y(o.diff) - 3.5} width={7} height={7} fill="#2563eb" transform={`rotate(45 ${x(o.date)} ${y(o.diff)})`} opacity={0.9}>
            <title>{`${o.date} 실측 차이 ${sign(o.diff)} (BH ${fmt(o.bh)} / OB ${fmt(o.ob)}) · ${o.src === 'weekly' ? '주간 리포트' : '일별 스냅샷'}`}</title>
          </rect>
        </g>
      ))}
      <g transform={`translate(${PL + 8}, ${PT + 4})`} fontSize={10} fill="#6b7280">
        <line x1={0} x2={18} y1={5} y2={5} stroke="#111827" strokeWidth={1.6} /><text x={22} y={9}>거래로 복원한 차이(BH−OB)</text>
        <circle cx={168} cy={5} r={4} fill="#dc2626" /><text x={176} y={9}>원인 확정일</text>
        <circle cx={238} cy={5} r={2.5} fill="#9ca3af" /><text x={246} y={9}>시점차(상쇄)</text>
        <rect x={314} y={1.5} width={7} height={7} fill="#2563eb" transform="rotate(45 317.5 5)" /><text x={326} y={9}>실측(주간 리포트·일별 스냅샷)</text>
      </g>
    </svg>
  )
}

// ── 변동일 당일 거래 요약 ─────────────────────────────────────────────────
function DayEvents({ evs, side }: { evs: GapChange['bh_events']; side: 'BH' | 'OB' }) {
  if (!evs.length) return <span style={{ color: '#d1d5db' }}>기록 없음</span>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {evs.map((e, i) => (
        <div key={i} style={{ fontSize: '0.74rem', lineHeight: 1.35 }}>
          <b style={{ color: e.dir === '+' ? '#059669' : '#dc2626' }}>{e.dir}{fmt(e.qty)}</b>
          <span style={{ color: '#6b7280' }}> {e.type}</span>
          {(e.channel || e.memo) && <span style={{ color: '#9ca3af' }}> · {side === 'OB' ? (e.channel || e.memo) : (e.memo || e.channel)}</span>}
        </div>
      ))}
    </div>
  )
}

export default function StockGapPage({ config }: Props) {
  const [locations, setLocations] = useState<Location[]>([])
  const [locIds, setLocIds] = useState<number[]>([228640])
  const [months, setMonths] = useState(3)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<GapResult | null>(null)
  const [onlyDiff, setOnlyDiff] = useState(true)
  const [q, setQ] = useState('')
  const [typeFilter, setTypeFilter] = useState<string | null>(null)
  const [selKey, setSelKey] = useState<string | null>(null)
  const [detail, setDetail] = useState<GapDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailErr, setDetailErr] = useState('')
  const [showTiming, setShowTiming] = useState(false)   // 상쇄된 시점차 날까지 변동일 표에 표시

  useEffect(() => {
    if (config.api_token) {
      getLocations(config.api_token).then((d: { locations?: Location[] }) => setLocations(d.locations || [])).catch(() => {})
    }
    getStockGapLast().then(r => { if (r && r.items && r.items.length) { setResult(r); setMonths(r.months || 3) } }).catch(() => {})
  }, [config.api_token])

  async function run() {
    if (!config.api_token) { message.warning('박스히어로 토큰이 없습니다. 설정에서 연결해 주세요.'); return }
    setRunning(true); setSelKey(null); setDetail(null)
    try {
      const r = await runStockGap({ token: config.api_token, location_ids: locIds.join(','), months, tol_days: 3 })
      setResult(r)
      message.success(`분석 완료 — 차이 품목 ${r.summary?.diff ?? 0}개 (${r.elapsed_sec}초)`)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '분석 실패'
      message.error(msg)
    } finally { setRunning(false) }
  }

  async function openItem(key: string) {
    setSelKey(key); setDetail(null); setDetailErr(''); setDetailLoading(true)
    try { setDetail(await getStockGapItem(key)) }
    catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '상세를 불러오지 못했습니다'
      setDetailErr(msg)
    } finally { setDetailLoading(false) }
  }

  const items = useMemo(() => {
    let list = result?.items || []
    if (onlyDiff) list = list.filter(i => i.diff !== null && i.diff !== 0)
    if (typeFilter) list = list.filter(i => (i.cause_types || []).some(c => c.type === typeFilter))
    if (q.trim()) {
      const k = q.trim().toLowerCase()
      list = list.filter(i => i.name.toLowerCase().includes(k) || (i.bh_skus || []).some(s => s.toLowerCase().includes(k)) || (i.ob_codes || []).some(s => s.toLowerCase().includes(k)))
    }
    return list
  }, [result, onlyDiff, typeFilter, q])

  const s = result?.summary
  const selItem = result?.items.find(i => i.key === selKey) || null

  const columns = [
    { title: '상품', dataIndex: 'name', key: 'name', ellipsis: true, width: 250,
      render: (v: string, r: GapItem) => (
        <div>
          <div style={{ fontWeight: 600, color: '#111827' }}>{v}</div>
          <div style={{ fontSize: '0.68rem', color: '#9ca3af' }}>{(r.bh_skus || []).slice(0, 2).join(', ')}{(r.bh_skus || []).length > 2 ? ` 외 ${r.bh_skus.length - 2}` : ''}</div>
        </div>) },
    { title: '박스히어로', dataIndex: 'bh_stock', key: 'bh', width: 95, align: 'right' as const, render: (v: number | null) => <b style={{ color: '#1e3a8a' }}>{fmt(v)}</b> },
    { title: '아워박스', dataIndex: 'ob_total', key: 'ob', width: 120, align: 'right' as const,
      render: (v: number | null, r: GapItem) => (
        <div><b style={{ color: '#991b1b' }}>{fmt(v)}</b>
          {v !== null && <div style={{ fontSize: '0.66rem', color: '#9ca3af' }}>가용 {fmt(r.ob_avail ?? (v - (r.ob_unav || 0)))} · 가용외 {fmt(r.ob_unav)}</div>}
        </div>) },
    { title: <Tooltip title="BH − OB총재고. +면 BH가 많음, −면 OB가 많음">차이</Tooltip>, dataIndex: 'diff', key: 'diff', width: 90, align: 'right' as const,
      sorter: (a: GapItem, b: GapItem) => Math.abs(a.diff ?? 0) - Math.abs(b.diff ?? 0),
      render: (v: number | null) => <b style={{ color: diffColor(v), fontSize: '0.95rem' }}>{sign(v)}</b> },
    { title: <Tooltip title="조회 시작 시점에 이미 있던 차이 (거래로 역산). 0이 아니면 기간을 늘려야 발생 시점이 보입니다">시작 전</Tooltip>, dataIndex: 'opening_gap', key: 'open', width: 80, align: 'right' as const,
      render: (v: number | null) => <span style={{ color: v ? '#b45309' : '#9ca3af' }}>{sign(v)}</span> },
    { title: <Tooltip title="조회 기간 동안 새로 벌어진 차이 (BH 순흐름 − OB 순흐름)">기간 중</Tooltip>, dataIndex: 'flow_diff', key: 'flow', width: 80, align: 'right' as const,
      render: (v: number | null) => <span style={{ color: diffColor(v) }}>{sign(v)}</span> },
    { title: '최초 발생', dataIndex: 'first_gap_date', key: 'first', width: 105,
      sorter: (a: GapItem, b: GapItem) => (a.first_gap_date || '').localeCompare(b.first_gap_date || ''),
      render: (v: string) => v ? <span style={{ color: v.includes('이전') ? '#b45309' : '#111827' }}>{v}</span> : <span style={{ color: '#d1d5db' }}>—</span> },
    { title: '최근 변동', dataIndex: 'last_change_date', key: 'last', width: 100,
      sorter: (a: GapItem, b: GapItem) => (a.last_change_date || '').localeCompare(b.last_change_date || ''),
      render: (v: string) => v || <span style={{ color: '#d1d5db' }}>—</span> },
    { title: '왜 (주요 원인)', key: 'why', width: 260,
      render: (_: unknown, r: GapItem) => {
        const cts = (r.cause_types || []).filter(c => c.impact !== 0).slice(0, 3)
        if (!cts.length) return r.diff ? <span style={{ color: '#9ca3af', fontSize: '0.74rem' }}>기간 내 거래 없음 (시작 전 차이)</span> : <span style={{ color: '#d1d5db' }}>—</span>
        return <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
          {cts.map(c => { const m = typeMeta(c.type); return <Tag key={c.type} color={m.color} style={{ margin: 0, fontSize: '0.7rem' }}>{m.label} {sign(c.impact)}</Tag> })}
        </div>
      } },
    { title: '설명', key: 'status', width: 92, align: 'center' as const,
      render: (_: unknown, r: GapItem) => {
        if (r.diff === null) return <Tag style={{ margin: 0 }}>한쪽만</Tag>
        if (r.diff === 0) return <Tag color="success" style={{ margin: 0 }}>일치</Tag>
        if (r.residual === 0) return <Tag color="success" style={{ margin: 0 }}>✓ 설명됨</Tag>
        return <Tooltip title={`원인으로 설명되지 않은 잔차 ${sign(r.residual)}`}><Tag color="warning" style={{ margin: 0 }}>잔차 {sign(r.residual)}</Tag></Tooltip>
      } },
  ]

  return (
    <div style={{ padding: '22px 26px', maxWidth: 1500 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 14 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 800, color: '#111827' }}><LineChartOutlined /> 재고 차이 추적 <span style={{ color: '#6b7280', fontWeight: 500, fontSize: '0.9rem' }}>박스히어로 ↔ 아워박스</span></h2>
          <div style={{ color: '#6b7280', fontSize: '0.8rem', marginTop: 4 }}>
            현재 재고 차이를 품목마다 <b>무엇이</b>(수량) · <b>언제</b>(거래를 거꾸로 걷어 복원한 날짜별 차이) · <b>왜</b>(짝이 없는 거래의 원인 분류)로 한 번에 풀어 보여줍니다.
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <Radio.Group size="small" value={months} onChange={e => setMonths(e.target.value)}>
            <Radio.Button value={1}>1개월</Radio.Button><Radio.Button value={3}>3개월</Radio.Button>
            <Radio.Button value={6}>6개월</Radio.Button><Radio.Button value={12}>1년</Radio.Button>
          </Radio.Group>
          <Select size="small" mode="multiple" style={{ minWidth: 180 }} placeholder="BH 위치 (비우면 전체)" value={locIds}
            onChange={v => setLocIds(v)} options={locations.map(l => ({ value: l.id, label: l.name }))} maxTagCount={1} />
          <Button type="primary" icon={<PlayCircleOutlined />} loading={running} onClick={run}>{running ? '분석 중…' : '분석 실행'}</Button>
        </div>
      </div>

      {running && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="양쪽 현재 재고 조회 → 기간 거래 수집(캐시된 달은 즉시) → 품목별 거래 짝짓기·역산 중입니다. 처음 실행하거나 긴 기간이면 몇 분 걸릴 수 있습니다." />
      )}
      {result?.errors?.length ? <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={result.errors.join(' · ')} /> : null}

      {!result && !running && (
        <div style={{ border: '1px dashed #d1d5db', borderRadius: 10, padding: 40, textAlign: 'center', color: '#6b7280' }}>
          아직 분석 결과가 없습니다. 기간을 고르고 <b>분석 실행</b>을 눌러 주세요.
        </div>
      )}

      {s && result && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 12 }}>
            {[
              { label: '비교 품목', val: fmt(s.total), sub: `일치 ${fmt(s.ok)} · 한쪽만 ${fmt(s.only_bh + s.only_ob)}`, bg: '#f3f4f6', color: '#111827' },
              { label: '차이 품목', val: fmt(s.diff), sub: `BH 많음 ${fmt(s.bh_more)} · OB 많음 ${fmt(s.ob_more)}`, bg: '#fef3c7', color: '#92400e' },
              { label: '차이 수량 합(절대값)', val: fmt(s.abs_gap), sub: 'BH − OB총재고', bg: '#fee2e2', color: '#991b1b' },
              { label: '발생 시점 특정됨', val: `${fmt(s.explained)} / ${fmt(s.diff)}`, sub: '차이가 전부 조회 기간 안에서 생겨 날짜·원인이 잡힌 품목', bg: '#d1fae5', color: '#065f46' },
              { label: '조회 시작 전부터 차이', val: fmt(s.opening), sub: `기간을 늘리면 발생 시점이 좁혀짐${s.unresolved ? ` · 잔차 있음 ${fmt(s.unresolved)}` : ''}`, bg: '#ffedd5', color: '#9a3412' },
            ].map(t => (
              <div key={t.label} style={{ background: t.bg, borderRadius: 10, padding: '10px 14px' }}>
                <div style={{ fontSize: '0.7rem', color: t.color, opacity: 0.8 }}>{t.label}</div>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: t.color, lineHeight: 1.2 }}>{t.val}</div>
                <div style={{ fontSize: '0.68rem', color: t.color, opacity: 0.75, marginTop: 2 }}>{t.sub}</div>
              </div>
            ))}
          </div>

          {s.cause_summary.length > 0 && (
            <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, padding: '10px 14px', marginBottom: 12 }}>
              <div style={{ fontSize: '0.74rem', color: '#6b7280', marginBottom: 6 }}>왜 벌어졌나 — 원인 유형별 합계 (클릭하면 해당 원인이 있는 품목만 표시)</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {s.cause_summary.map(c => { const m = typeMeta(c.type); const on = typeFilter === c.type; return (
                  <Tag key={c.type} color={on ? m.color : undefined} style={{ cursor: 'pointer', margin: 0, padding: '3px 9px', border: on ? undefined : '1px solid #e5e7eb' }}
                    onClick={() => setTypeFilter(on ? null : c.type)}>
                    <b>{m.label}</b> <span style={{ opacity: 0.8 }}>{c.items}개 품목 · {c.count}건 · 영향 {sign(c.impact)}</span>
                  </Tag>) })}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
            <Input size="small" prefix={<SearchOutlined />} placeholder="상품명 · SKU · OB코드 검색" allowClear value={q} onChange={e => setQ(e.target.value)} style={{ width: 260 }} />
            <span style={{ fontSize: '0.78rem', color: '#6b7280' }}><Switch size="small" checked={onlyDiff} onChange={setOnlyDiff} /> 차이 있는 품목만</span>
            <span style={{ fontSize: '0.74rem', color: '#9ca3af', marginLeft: 'auto' }}>
              {result.from} ~ {result.to} · 실행 {result.ran_at} · {result.elapsed_sec}초 · OB {result.ob_source} · 행을 클릭하면 언제·왜 상세
            </span>
          </div>

          <Table size="small" rowKey="key" columns={columns} dataSource={items} pagination={{ pageSize: 30, showSizeChanger: true }} scroll={{ x: 1280 }}
            onRow={r => ({ onClick: () => openItem(r.key), style: { cursor: 'pointer', background: r.key === selKey ? '#eff6ff' : undefined } })}
            expandable={{ expandedRowRender: (r: GapItem) => <div style={{ fontSize: '0.8rem', color: '#374151', padding: '2px 8px' }}>💬 {r.headline}</div>, rowExpandable: r => !!r.headline }} />
        </>
      )}

      <Drawer open={!!selKey} onClose={() => { setSelKey(null); setDetail(null) }} width="min(1100px, 92vw)" destroyOnClose
        title={selItem ? <span>{selItem.name} <span style={{ color: '#6b7280', fontWeight: 400, fontSize: '0.85rem' }}>— 차이 {sign(selItem.diff)} (BH {fmt(selItem.bh_stock)} / OB {fmt(selItem.ob_total)})</span></span> : '상세'}>
        {detailLoading && <div style={{ textAlign: 'center', padding: 40 }}><Spin /> <div style={{ color: '#9ca3af', marginTop: 8 }}>상세 불러오는 중…</div></div>}
        {detailErr && <Alert type="error" showIcon message={detailErr} action={<Button size="small" onClick={run} loading={running}>다시 분석</Button>} />}
        {detail && result && (
          <div style={{ fontSize: '0.84rem', lineHeight: 1.7 }}>
            <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: '10px 14px', marginBottom: 12 }}>
              <div style={{ fontWeight: 700, color: '#92400e', marginBottom: 2 }}>한 줄 결론</div>
              <div>{detail.headline}</div>
            </div>

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
              {[
                { l: `${result.from} 시작 전 차이`, v: sign(detail.opening_gap), c: detail.opening_gap ? '#b45309' : '#6b7280' },
                { l: '＋ 기간 중 발생', v: sign(detail.flow_diff), c: diffColor(detail.flow_diff) },
                { l: '＝ 현재 차이', v: sign(detail.diff), c: diffColor(detail.diff) },
                { l: '원인으로 설명', v: sign(detail.explained), c: '#374151' },
                { l: '잔차', v: sign(detail.residual), c: detail.residual ? '#dc2626' : '#059669' },
              ].map(t => (
                <div key={t.l} style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8, padding: '6px 12px', minWidth: 120 }}>
                  <div style={{ fontSize: '0.68rem', color: '#6b7280' }}>{t.l}</div>
                  <div style={{ fontWeight: 800, fontSize: '1.05rem', color: t.c }}>{t.v}</div>
                </div>
              ))}
            </div>

            <div style={{ fontWeight: 700, marginBottom: 4 }}>📅 언제 — 날짜별 차이 추이</div>
            <GapChart from={result.from} to={result.to} opening={detail.opening_gap ?? 0} series={detail.series} changes={detail.changes} observed={detail.observed || []} />
            {(detail.observed || []).length === 0 && (
              <div style={{ fontSize: '0.72rem', color: '#9ca3af', marginTop: 4 }}>실측 점이 없습니다 — 이 화면을 실행할 때마다 오늘 양쪽 재고가 일별 스냅샷으로 저장되고, 백엔드가 켜져 있으면 매일 07시 이후 자동으로도 저장됩니다. 쌓일수록 복원 선과 실측이 맞는지 검증할 수 있습니다.</div>
            )}

            {(() => {
              const causeDays = detail.changes.filter(c => c.kind !== 'timing')
              const timingDays = detail.changes.length - causeDays.length
              const rows = (showTiming ? detail.changes : causeDays).slice().reverse()
              return (<>
            <div style={{ fontWeight: 700, margin: '14px 0 4px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span>🧭 차이가 벌어진 날 {causeDays.length}일</span>
              <span style={{ fontWeight: 400, color: '#6b7280', fontSize: '0.76rem' }}>— 지금 남아 있는 차이의 원인이 된 날짜와 그날 양쪽 거래</span>
              {timingDays > 0 && <span style={{ fontWeight: 400, fontSize: '0.76rem', color: '#6b7280', marginLeft: 'auto' }}><Switch size="small" checked={showTiming} onChange={setShowTiming} /> 며칠 뒤 상쇄된 시점차 {timingDays}일도 표시</span>}
            </div>
            {rows.length === 0 ? (
              <div style={{ color: '#9ca3af' }}>{detail.changes.length === 0
                ? <>조회 기간 안에 양쪽 순흐름이 어긋난 날이 없습니다. 차이는 {result.from} 이전에 생긴 것입니다 — 기간을 늘려 다시 실행해 보세요.</>
                : <>기간 안의 어긋남은 모두 며칠 뒤 상쇄된 시점차뿐입니다. 남은 차이 {sign(detail.diff)}는 {result.from} 이전에 생긴 것입니다 — 기간을 늘려 다시 실행해 보세요.</>}</div>
            ) : (
              <Table size="small" rowKey="date" pagination={rows.length > 40 ? { pageSize: 40 } : false}
                dataSource={rows}
                rowClassName={(r: GapChange) => r.kind === 'cause' ? 'row-error' : r.kind === 'partial' ? 'row-warn' : ''}
                columns={[
                  { title: '날짜', dataIndex: 'date', key: 'date', width: 100 },
                  { title: 'Δ차이', dataIndex: 'd_gap', key: 'd', width: 80, align: 'right' as const, render: (v: number) => <b style={{ color: diffColor(v) }}>{sign(v)}</b> },
                  { title: '그날 이후 차이', dataIndex: 'gap_after', key: 'g', width: 100, align: 'right' as const, render: (v: number) => <span style={{ color: diffColor(v) }}>{sign(v)}</span> },
                  { title: 'BH 당일 거래', key: 'bh', width: 220, render: (_: unknown, r: GapChange) => <DayEvents evs={r.bh_events} side="BH" /> },
                  { title: 'OB 당일 거래', key: 'ob', width: 220, render: (_: unknown, r: GapChange) => <DayEvents evs={r.ob_events} side="OB" /> },
                  { title: '판정', key: 'v', render: (_: unknown, r: GapChange) => (
                    <div>
                      <Tag color={r.kind === 'cause' ? 'red' : r.kind === 'partial' ? 'orange' : 'default'} style={{ margin: 0 }}>{r.verdict}</Tag>
                      {r.causes.map((c, i) => <div key={i} style={{ fontSize: '0.72rem', color: '#374151', marginTop: 2 }}><Tag color={typeMeta(c.type).color} style={{ fontSize: '0.66rem', lineHeight: '16px', padding: '0 4px' }}>{typeMeta(c.type).label}</Tag>{c.desc}</div>)}
                    </div>) },
                ]} />
            )}
              </>)
            })()}

            {detail.trace && (
              <Collapse ghost size="small" style={{ marginTop: 14 }} items={[{ key: 'trace', label: <b>🔬 왜 — 거래 정밀 대사 상세 (짝지어진 거래 · 남은 거래 · 원인 설명)</b>, children: <StockTraceReport data={detail.trace} months={result.months} /> }]} />
            )}
          </div>
        )}
      </Drawer>
    </div>
  )
}
