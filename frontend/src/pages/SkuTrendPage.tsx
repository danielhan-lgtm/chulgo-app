import { useEffect, useMemo, useState } from 'react'
import { Button, Select, Switch, Tag, Empty, Spin, Alert, Tooltip } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import type { OrderPlanItem, SalesTrend, SalesTrendRow, NaverSkuDailyItem } from '../services/api'
import { getOrderPlan, getSalesTrend, getNaverSkuDaily, downloadBlob } from '../services/api'

const NAVER = '네이버'

// 같은 상품인데 표기만 다른 행을 합친다 (마스터 매핑이 없을 때).
//  - 채널 내 구분 꼬리표 '(온)'·'(오프)', 끝의 'x'·',' 같은 군더더기
//  - 컬리 OCR 오인식('표고버,±것' → '표고버섯', '세다지즈' → '체다치즈', '슬티' → '솔티')
//  - 브랜드 표기 차이('[DJ&A]'·'디제이앤에이' → 'DJ&A'), 단어 사이 공백('크리스 프' → '크리스프')
// 마스터에 매핑된 항목은 SKU로, 미매핑은 위 정리를 거친 이름으로 묶는다.
const NAME_TAGS = /\s*\((온|오프|온라인|오프라인|매장|온라인몰)\)\s*/g
const OCR_FIX: [RegExp, string][] = [
  [/버[,•·]*±것/g, '버섯'], [/세다지즈/g, '체다치즈'], [/슬티/g, '솔티'],
]
function normName(name: string): string {
  let n = name.replace(NAME_TAGS, ' ')
  n = n.replace(/^\s*\[[^\]]*\]\s*/, '')                  // [브랜드] 접두
  n = n.replace(/디제이앤에이|dj&a|dja/gi, '')                 // 브랜드 표기는 묶음 키에서 제외
  for (const [re, to] of OCR_FIX) n = n.replace(re, to)
  n = n.replace(/[\s,]*x?\s*$/i, '')                       // 끝의 ' x', ','
  n = n.replace(/\s*&\s*/g, '&').replace(/\s+/g, '')       // 공백 제거
  return n.toLowerCase()
}
// 화면 표시용 이름 정리 — OCR 오인식·끊긴 단어·군더더기만 고치고 띄어쓰기는 유지
const WORD_FIX: [RegExp, string][] = [
  [/크리스\s+프/g, '크리스프'], [/아보카\s*도\s*오\s*일/g, '아보카도오일'], [/핑크\s*솔\s*트/g, '핑크솔트'],
  [/체다치\s*즈\s*향/g, '체다치즈향'], [/브로콜\s+리/g, '브로콜리'], [/프\s+레첼/g, '프레첼'], [/멜라\s+토닌/g, '멜라토닌'],
]
function cleanName(name: string): string {
  let n = name.replace(NAME_TAGS, ' ')
  for (const [re, to] of OCR_FIX) n = n.replace(re, to)
  for (const [re, to] of WORD_FIX) n = n.replace(re, to)
  n = n.replace(/[\s,]*x\s*$/i, '').replace(/,\s*$/, '')
  return n.replace(/\s+/g, ' ').trim()
}
// 표기가 얼마나 지저분한지 — 대표 이름 고를 때 낮은 쪽 우선
function messiness(name: string): number {
  let m = 0
  if (/[±•]/.test(name)) m += 10
  if (NAME_TAGS.test(name)) m += 3
  NAME_TAGS.lastIndex = 0
  if (/[\s,]x?\s*$/i.test(name) && /x$|,$/.test(name.trim())) m += 2
  m += (name.match(/(^|\s)\S(\s|$)/g) || []).length * 2   // 한 글자짜리 토큰(끊긴 단어)
  return m
}
function mergeKeyOf(it: OrderPlanItem): string {
  if (it.matched && it.sku) return `sku:${it.sku}`
  return `name:${normName(it.name)}`
}
function mergeItems(items: OrderPlanItem[]): OrderPlanItem[] {
  const map = new Map<string, OrderPlanItem>()
  for (const it of items) {
    const k = mergeKeyOf(it)
    const cur = map.get(k)
    if (!cur) { map.set(k, { ...it, by_date: { ...it.by_date }, by_channel: { ...it.by_channel }, raw_names: [...it.raw_names], raw_breakdown: { ...(it.raw_breakdown || {}) } }); continue }
    for (const [d, q] of Object.entries(it.by_date)) cur.by_date[d] = (cur.by_date[d] || 0) + q
    for (const [c, q] of Object.entries(it.by_channel)) cur.by_channel[c] = (cur.by_channel[c] || 0) + q
    cur.total += it.total
    for (const n of it.raw_names) if (!cur.raw_names.includes(n)) cur.raw_names.push(n)
    Object.assign(cur.raw_breakdown!, it.raw_breakdown || {})
    // 대표 이름은 덜 지저분한 표기, 같으면 수량 많은 쪽
    if (!cur.matched) {
      const a = messiness(cur.name), b = messiness(it.name)
      if (b < a || (b === a && it.total > cur.total - it.total)) cur.name = it.name
    }
  }
  return Array.from(map.values()).map(it => it.matched ? it : { ...it, name: cleanName(it.name) })
}
// 네이버 도착보장 출고(박스히어로 거래 내역)를 발주 캘린더 항목 모양으로 변환 — 발주가 아니라 출고수량
function naverToItems(items: NaverSkuDailyItem[]): OrderPlanItem[] {
  return items.map(it => ({
    sku: it.sku, name: it.name, matched: true, match_score: 1, by_date: it.by_date, by_channel: { [NAVER]: it.total }, total: it.total,
    sources: ['네이버 출고 보고'], raw_names: [it.raw_name],
    raw_breakdown: { [it.raw_name]: { by_date: it.by_date, by_channel: { [NAVER]: it.total }, by_channel_date: { [NAVER]: it.by_date }, total: it.total } },
  }))
}

/** SKU 출고 추이 — 발주 캘린더 저장소의 채널별 SKU 수량(발주 추이)에
 *  채널 보고서에 올린 판매/재고/예측 리포트(쿠팡 rocket csv·forecast xlsx, 올리브영 상품별 매출_재고 xlsx)를 붙여
 *  발주와 판매를 함께 보고 종합 판정한다. 발주 캘린더 화면의 수동 정리(삭제·합치기·이동)는 반영하지 않는다. */

// 품목의 날짜별 수량 — 거래처 필터 적용 시 raw_breakdown의 거래처×날짜 교차값 사용
// (구버전 데이터로 by_channel_date가 없으면, 해당 거래처 단독 raw만 by_date로 대체)
export function itemQtyByDate(it: OrderPlanItem, channel: string): Record<string, number> {
  if (!channel) return it.by_date
  const out: Record<string, number> = {}
  for (const rb of Object.values(it.raw_breakdown || {})) {
    const cd = rb.by_channel_date?.[channel]
    if (cd) {
      for (const [d, q] of Object.entries(cd)) out[d] = (out[d] || 0) + q
    } else if (rb.by_channel[channel] > 0) {
      const chs = Object.keys(rb.by_channel)
      if (chs.length === 1) {
        for (const [d, q] of Object.entries(rb.by_date)) out[d] = (out[d] || 0) + q
      }
    }
  }
  return out
}

// 거래처별 입고확정 수량 × 날짜 (raw 분해 기준; 단일 거래처 raw는 recv_by_date 그대로)
function itemRecvByDate(it: OrderPlanItem, channel: string): Record<string, number> | null {
  const out: Record<string, number> = {}
  let any = false
  for (const rb of Object.values(it.raw_breakdown || {})) {
    let use = channel ? rb.recv_by_channel_date?.[channel] : rb.recv_by_date
    if (channel && !use && rb.recv_by_date && Object.keys(rb.by_channel).length === 1 && rb.by_channel[channel] > 0) use = rb.recv_by_date
    if (!use) continue
    any = true
    for (const [d, q] of Object.entries(use)) out[d] = (out[d] || 0) + q
  }
  return any ? out : null
}

// 주 시작(월요일) 날짜 키 — 'YYYY-MM-DD'
function weekKeyOf(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  const dow = (d.getDay() + 6) % 7   // 월=0
  d.setDate(d.getDate() - dow)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function Sparkline({ values, color, width = 120, height = 28 }: { values: number[]; color: string; width?: number; height?: number }) {
  const w = width, h = height, pad = 2
  const max = Math.max(1, ...values)
  const n = values.length
  if (n === 0) return null
  const pts = values.map((v, i) => {
    const x = n === 1 ? w / 2 : pad + (i * (w - pad * 2)) / (n - 1)
    const y = h - pad - (v / max) * (h - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const last = pts[pts.length - 1].split(',')
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r={2.2} fill={color} />
    </svg>
  )
}

type Unit = 'week' | 'month'
type Basis = 'order' | 'recv'
type TrendKind = 'up' | 'down' | 'flat' | 'new' | 'na'
// 종합 판정 — 발주 추이 × 판매 추이
type Verdict = 'expand' | 'slow' | 'stock_warn' | 'order_review' | 'flat' | 'mixed' | 'na'
interface TrendOpts { unit: Unit; basis: Basis; win: number; includeCurrent: boolean; currentPeriod: string }
interface SalesInfo {
  row: SalesTrendRow
  trend: TrendKind            // 판매 추세 (최근 vs 직전 일평균, ±10%)
  pct: number | null
  recentAvg: number; prevAvg: number | null
  stock: number | null; dos: number | null
  forecastNext: number | null
  series: number[]            // 판매 스파크라인 (쿠팡: 일별, 올리브영: 월별)
  latestLabel: string          // "최근 7일 일평균" / "9월 일평균"
}
interface TrendRow {
  key: string; sku: string; name: string; matched: boolean; rawNames: string[]
  series: number[]; total: number; recentSum: number; prevSum: number
  change: number | null; trend: TrendKind
  sales: SalesInfo | null
  verdict: Verdict; note: string
}
interface TrendResult { periods: string[]; rows: TrendRow[]; hasRecv: boolean; recent: string[]; prev: string[] }

const TREND_WORD: Record<TrendKind, string> = { up: '증가', down: '감소', flat: '보합', new: '신규', na: '비교불가' }
const TREND_COLOR: Record<TrendKind, string> = { up: '#16a34a', down: '#dc2626', flat: '#6b7280', new: '#2563eb', na: '#9ca3af' }
const VERDICT_META: Record<Verdict, { word: string; color: string; desc: string }> = {
  expand: { word: '확대', color: 'green', desc: '발주와 판매가 함께 늘고 있음' },
  slow: { word: '둔화', color: 'red', desc: '발주와 판매가 함께 꺾임' },
  stock_warn: { word: '재고 주의', color: 'orange', desc: '발주는 늘었는데 판매는 꺾임 → 채널 재고 쌓일 수 있음' },
  order_review: { word: '발주 검토', color: 'blue', desc: '판매는 느는데 발주는 줄어듦 → 품절·발주 확대 검토' },
  flat: { word: '보합', color: 'default', desc: '발주·판매 모두 큰 변화 없음' },
  mixed: { word: '혼조', color: 'default', desc: '한쪽만 움직임 (±10% 이내 변화 포함)' },
  na: { word: '판단 보류', color: 'default', desc: '비교할 기간이 부족' },
}
const SALES_KEY: Record<string, 'coupang' | 'oliveyoung'> = { '쿠팡': 'coupang', '올리브영': 'oliveyoung' }

function judge(recentSum: number, prevSum: number, comparable: boolean): { change: number | null; trend: TrendKind } {
  if (!comparable) return { change: null, trend: 'na' }
  if (prevSum > 0) {
    const change = (recentSum - prevSum) / prevSum
    return { change, trend: change >= 0.1 ? 'up' : change <= -0.1 ? 'down' : 'flat' }
  }
  if (recentSum > 0) return { change: Infinity, trend: 'new' }
  return { change: 0, trend: 'flat' }
}

function pctTrend(pct: number | null): TrendKind {
  if (pct === null || pct === undefined) return 'na'
  return pct >= 10 ? 'up' : pct <= -10 ? 'down' : 'flat'
}

function combine(order: TrendKind, sales: TrendKind, dos: number | null): { verdict: Verdict; note: string } {
  const notes: string[] = []
  if (dos !== null && dos < 14 && sales !== 'down') notes.push(`재고 ${dos}일분 → 품절 위험`)
  if (dos !== null && dos > 60) notes.push(`재고 ${Math.round(dos)}일분 → 과잉`)
  let v: Verdict
  const o = order === 'new' ? 'up' : order
  if (o === 'na' && sales === 'na') v = 'na'
  else if (o === 'na') v = sales === 'up' ? 'expand' : sales === 'down' ? 'slow' : 'flat'
  else if (sales === 'na') v = o === 'up' ? 'expand' : o === 'down' ? 'slow' : 'flat'
  else if (o === 'up' && sales === 'up') v = 'expand'
  else if (o === 'down' && sales === 'down') v = 'slow'
  else if (o === 'up' && sales === 'down') v = 'stock_warn'
  else if (o === 'down' && sales === 'up') v = 'order_review'
  else if (o === 'flat' && sales === 'flat') v = 'flat'
  else v = 'mixed'
  if (v === 'na' && notes.length === 0) return { verdict: v, note: '판매 자료 없음' }
  return { verdict: v, note: notes.join(' · ') }
}

// 판매 리포트 행 → 화면용 판매 정보
function salesInfoOf(st: SalesTrend, rows: SalesTrendRow[]): SalesInfo | null {
  if (rows.length === 0) return null
  // 라벨이 여러 개면 합산 (세트 라벨 등)
  const n = Math.max(...rows.map(r => r.sales_series.length))
  const series = Array.from({ length: n }, (_, i) => rows.reduce((a, r) => a + (r.sales_series[i] || 0), 0))
  const recentAvg = rows.reduce((a, r) => a + (r.avg_7d || 0), 0)
  const prevVals = rows.map(r => r.avg_prev_7d).filter((x): x is number => x !== null && x !== undefined)
  const prevAvg = prevVals.length ? prevVals.reduce((a, b) => a + b, 0) : null
  const pct = prevAvg ? Math.round((recentAvg - prevAvg) / prevAvg * 100) : null
  const stocks = rows.map(r => r.stock_last).filter((x): x is number => x !== null && x !== undefined)
  const stock = stocks.length ? stocks.reduce((a, b) => a + b, 0) : null
  const dos = stock !== null && recentAvg > 0 ? Math.round(stock / recentAvg * 10) / 10 : null
  const fc = rows.map(r => r.forecast_next).filter((x): x is number => x !== null && x !== undefined)
  const forecastNext = fc.length ? Math.round(fc.reduce((a, b) => a + b, 0)) : null
  const latestLabel = st.mode === 'daily'
    ? '최근 7일 일평균'
    : `${st.latest ? parseInt(st.latest.slice(5, 7)) + '월' : '최근 월'} 일평균${st.elapsed_days ? ` (1~${st.elapsed_days}일)` : ''}`
  return { row: rows[0], trend: pctTrend(pct), pct, recentAvg: Math.round(recentAvg * 10) / 10, prevAvg: prevAvg === null ? null : Math.round(prevAvg * 10) / 10, stock, dos, forecastNext, series, latestLabel }
}

// 채널 하나의 SKU별 기간 수량 + 판매 결합 + 판정
function computeTrend(items: OrderPlanItem[], channel: string, o: TrendOpts, st: SalesTrend | null): TrendResult {
  const keyOf = (d: string) => o.unit === 'week' ? weekKeyOf(d) : d.slice(0, 7)
  const periodSet = new Set<string>()
  let hasRecv = false
  const prelim = items.map(it => {
    const orderMap = itemQtyByDate(it, channel)
    const recvMap = itemRecvByDate(it, channel)
    if (recvMap) hasRecv = true
    const src = o.basis === 'recv' ? (recvMap || {}) : orderMap
    const byPeriod: Record<string, number> = {}
    for (const [d, q] of Object.entries(src)) {
      if (!q) continue
      const k = keyOf(d); byPeriod[k] = (byPeriod[k] || 0) + q; periodSet.add(k)
    }
    for (const d of Object.keys(orderMap)) if (orderMap[d]) periodSet.add(keyOf(d))
    return { it, byPeriod }
  })
  const periods = Array.from(periodSet).sort()
  const completed = periods.filter(p => o.includeCurrent ? p <= o.currentPeriod : p < o.currentPeriod)
  const recent = completed.slice(-o.win)
  const prev = completed.slice(-o.win * 2, -o.win)
  const comparable = prev.length === o.win && recent.length === o.win
  const byLabel = new Map<string, SalesTrendRow>()
  if (st) for (const r of st.rows) byLabel.set(r.label, r)
  const rows: TrendRow[] = prelim.map(({ it, byPeriod }) => {
    const series = periods.map(p => byPeriod[p] || 0)
    const total = series.reduce((a, b) => a + b, 0)
    const recentSum = recent.reduce((a, p) => a + (byPeriod[p] || 0), 0)
    const prevSum = prev.reduce((a, p) => a + (byPeriod[p] || 0), 0)
    const { change, trend } = judge(recentSum, prevSum, comparable)
    // 판매 행 연결 — 이 SKU에 속한 raw 상품명(이 채널) → 라벨 → 판매 행
    let sales: SalesInfo | null = null
    const rawNames = Object.entries(it.raw_breakdown || {}).filter(([, rb]) => !channel || rb.by_channel[channel] > 0).map(([n]) => n)
    if (st) {
      const labels = new Set<string>()
      for (const n of rawNames) { const lb = st.labels[n]; if (lb) labels.add(lb) }
      const srows = Array.from(labels).map(lb => byLabel.get(lb)).filter((x): x is SalesTrendRow => !!x)
      sales = salesInfoOf(st, srows)
    }
    const { verdict, note } = combine(trend, sales ? sales.trend : 'na', sales?.dos ?? null)
    return { key: `${it.sku}|${it.name}`, sku: it.sku, name: it.name, matched: it.matched, rawNames, series, total, recentSum, prevSum, change, trend, sales, verdict, note }
  }).filter(r => r.total > 0)
  return { periods, rows, hasRecv, recent, prev }
}

const pctText = (change: number | null) =>
  change === null ? '' : change === Infinity ? '신규' : `${change >= 0 ? '+' : ''}${Math.round(change * 100)}%`
const pctTextInt = (pct: number | null) => pct === null ? '-' : `${pct >= 0 ? '+' : ''}${pct}%`

function ChangeText({ change, trend }: { change: number | null; trend: TrendKind }) {
  if (trend === 'na' || change === null) return <span style={{ color: '#9ca3af' }}>-</span>
  const color = trend === 'up' || trend === 'new' ? '#166534' : trend === 'down' ? '#991b1b' : '#6b7280'
  const arrow = trend === 'up' || trend === 'new' ? '▲' : trend === 'down' ? '▼' : '─'
  return <span style={{ color, fontWeight: 600, display: 'inline-block', minWidth: 52, textAlign: 'right' }}>{arrow} {pctText(change)}</span>
}

function TrendTag({ change, trend }: { change: number | null; trend: TrendKind }) {
  if (trend === 'na' || change === null) return <Tag style={{ margin: 0 }}>비교불가</Tag>
  if (trend === 'new') return <Tag color="blue" style={{ margin: 0 }}>신규</Tag>
  const pct = pctText(change)
  if (trend === 'up') return <Tag color="green" style={{ margin: 0 }}>▲ 증가 {pct}</Tag>
  if (trend === 'down') return <Tag color="red" style={{ margin: 0 }}>▼ 감소 {pct}</Tag>
  return <Tag style={{ margin: 0 }}>─ 보합 {pct}</Tag>
}
function SalesTag({ s }: { s: SalesInfo | null }) {
  if (!s) return <span style={{ color: '#d1d5db' }}>-</span>
  if (s.trend === 'na') return <Tag style={{ margin: 0 }}>비교불가</Tag>
  const t = pctTextInt(s.pct)
  if (s.trend === 'up') return <Tag color="green" style={{ margin: 0 }}>▲ {t}</Tag>
  if (s.trend === 'down') return <Tag color="red" style={{ margin: 0 }}>▼ {t}</Tag>
  return <Tag style={{ margin: 0 }}>─ {t}</Tag>
}
function VerdictTag({ v }: { v: Verdict }) {
  const m = VERDICT_META[v]
  return <Tooltip title={m.desc}><Tag color={m.color} style={{ margin: 0 }}>{m.word}</Tag></Tooltip>
}

const fmt = (n: number) => n.toLocaleString()
const fmt1 = (n: number) => Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 1 })
const card: React.CSSProperties = { background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }

export default function SkuTrendPage() {
  const [items, setItems] = useState<OrderPlanItem[]>([])
  const [channels, setChannels] = useState<string[]>([])
  const [sales, setSales] = useState<Record<string, SalesTrend | null>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [channel, setChannel] = useState('')
  const [unit, setUnit] = useState<Unit>('week')
  const [basis, setBasis] = useState<Basis>('order')
  const [win, setWin] = useState(4)
  const [includeCurrent, setIncludeCurrent] = useState(false)
  const [sortKey, setSortKey] = useState<'total' | 'change' | 'sales' | 'verdict'>('total')

  const load = async () => {
    setLoading(true); setError('')
    try {
      // 발주 캘린더 화면과 같은 매칭 옵션을 쓰도록 localStorage 설정을 읽는다
      let opts: { threshold?: number; useMaster?: boolean; splitBundles?: boolean } = {}
      try {
        const raw = localStorage.getItem('orderplan_state_v1')
        if (raw) { const p = JSON.parse(raw); opts = { threshold: p.threshold, useMaster: p.useMaster, splitBundles: p.splitBundles } }
      } catch { /* 무시 */ }
      const [data, cp, oy, nv] = await Promise.all([
        getOrderPlan(opts),
        getSalesTrend('coupang').catch(() => null),
        getSalesTrend('oliveyoung').catch(() => null),
        getNaverSkuDaily().catch(() => null),
      ])
      const nvItems = naverToItems(nv?.items || [])
      setItems(mergeItems([...(data.items || []), ...nvItems]))
      const totals: Record<string, number> = { ...(data.total_by_channel || {}) }
      if (nvItems.length) totals[NAVER] = nvItems.reduce((a, x) => a + x.total, 0)
      const chs = Object.entries(totals).sort((a, b) => b[1] - a[1]).map(([c]) => c)
      setChannels(chs)
      setChannel(c => (c && chs.includes(c)) ? c : (chs[0] || ''))
      setSales({ '쿠팡': cp, '올리브영': oy })
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '불러오기 실패')
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])
  useEffect(() => { setWin(unit === 'week' ? 4 : 1) }, [unit])

  const today = new Date()
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  const currentPeriod = unit === 'week' ? weekKeyOf(todayKey) : todayKey.slice(0, 7)
  const opts: TrendOpts = { unit, basis, win, includeCurrent, currentPeriod }
  const unitWord = unit === 'week' ? '주' : '개월'
  const label = (p: string) => unit === 'month' ? `${p.slice(2, 4)}.${p.slice(5, 7)}` : `${parseInt(p.slice(5, 7))}/${parseInt(p.slice(8, 10))}~`

  // 채널별 결과 (요약용) — basis가 recv인데 그 채널에 확정수량이 없으면 발주수량으로 대체
  const perChannel = useMemo(() => channels.map(c => {
    const st = sales[c] || null
    const hasSales = !!st && st.rows.length > 0
    let r = computeTrend(items, c, opts, st)
    let usedBasis: Basis = basis
    if (basis === 'recv' && !r.hasRecv) { r = computeTrend(items, c, { ...opts, basis: 'order' }, st); usedBasis = 'order' }
    const counts: Record<TrendKind, number> = { up: 0, down: 0, flat: 0, new: 0, na: 0 }
    const vcounts: Record<Verdict, number> = { expand: 0, slow: 0, stock_warn: 0, order_review: 0, flat: 0, mixed: 0, na: 0 }
    for (const row of r.rows) { counts[row.trend]++; vcounts[row.verdict]++ }
    const recentSum = r.rows.reduce((a, x) => a + x.recentSum, 0)
    const prevSum = r.rows.reduce((a, x) => a + x.prevSum, 0)
    const comparable = r.prev.length === win && r.recent.length === win
    const totalSeries = r.periods.map((_, i) => r.rows.reduce((a, x) => a + x.series[i], 0))
    const { change, trend } = judge(recentSum, prevSum, comparable)
    const judged = r.rows.filter(x => x.change !== null && x.change !== Infinity && x.prevSum > 0)
    const topUp = [...judged].filter(x => x.trend === 'up').sort((a, b) => (b.recentSum - b.prevSum) - (a.recentSum - a.prevSum)).slice(0, 3)
    const topDown = [...judged].filter(x => x.trend === 'down').sort((a, b) => (a.recentSum - a.prevSum) - (b.recentSum - b.prevSum)).slice(0, 3)
    // 채널 판매 총량 — 판매 리포트 전체 행 기준 (발주 SKU와 연결 안 된 상품 포함)
    let salesTotal: { recentAvg: number; prevAvg: number | null; pct: number | null; trend: TrendKind; stock: number; dos: number | null; forecastNext: number | null; series: number[]; latestLabel: string } | null = null
    if (hasSales && st) {
      const info = salesInfoOf(st, st.rows)
      if (info) salesTotal = { recentAvg: info.recentAvg, prevAvg: info.prevAvg, pct: info.pct, trend: info.trend, stock: info.stock || 0, dos: info.dos, forecastNext: info.forecastNext, series: info.series, latestLabel: info.latestLabel }
    }
    const warn = r.rows.filter(x => x.verdict === 'stock_warn').sort((a, b) => b.total - a.total).slice(0, 3)
    const review = r.rows.filter(x => x.verdict === 'order_review').sort((a, b) => b.total - a.total).slice(0, 3)
    const lowStock = r.rows.filter(x => x.sales && x.sales.dos !== null && x.sales.dos < 14 && x.sales.recentAvg > 0).sort((a, b) => (a.sales!.dos! - b.sales!.dos!)).slice(0, 3)
    // 고정 비교 두 가지 — 주 대비(최근 1주 vs 직전 1주), 월 대비(최근 1개월 vs 직전 1개월). 화면 설정(주간/월간·비교구간)과 무관.
    const fixedCmp = (u: Unit) => {
      const cp = u === 'week' ? weekKeyOf(todayKey) : todayKey.slice(0, 7)
      const rr = computeTrend(items, c, { unit: u, basis: usedBasis, win: 1, includeCurrent, currentPeriod: cp }, null)
      const rs = rr.rows.reduce((a, x) => a + x.recentSum, 0)
      const ps = rr.rows.reduce((a, x) => a + x.prevSum, 0)
      const ok = rr.recent.length === 1 && rr.prev.length === 1
      const lb = (k: string) => u === 'week' ? `${parseInt(k.slice(5, 7))}/${parseInt(k.slice(8, 10))}~` : `${parseInt(k.slice(5, 7))}월`
      return { ...judge(rs, ps, ok), rows: rr.rows, recentSum: rs, prevSum: ps, recentLabel: rr.recent[0] ? lb(rr.recent[0]) : '-', prevLabel: rr.prev[0] ? lb(rr.prev[0]) : '-' }
    }
    const weekCmp = fixedCmp('week')
    const monthCmp = fixedCmp('month')
    // 유독 변동 큰 SKU만 추림: 변동률 ±30% 이상(신규·소멸 포함), 수량이 채널 합계의 2% 이상, 변동량 큰 순 5개
    const notable = (cmp: ReturnType<typeof fixedCmp>) => {
      const base = Math.max(cmp.recentSum, cmp.prevSum)
      return cmp.rows
        .filter(x => x.change !== null && Math.max(x.recentSum, x.prevSum) >= Math.max(30, base * 0.02))
        .filter(x => x.change === Infinity || Math.abs(x.change!) >= 0.3)
        .sort((a, b) => Math.abs(b.recentSum - b.prevSum) - Math.abs(a.recentSum - a.prevSum))
        .slice(0, 5)
    }
    const weekTop = notable(weekCmp)
    const monthTop = notable(monthCmp)
    return { channel: c, result: r, usedBasis, counts, vcounts, recentSum, prevSum, change, trend, totalSeries, topUp, topDown, st, hasSales, salesTotal, warn, review, lowStock, weekCmp, monthCmp, weekTop, monthTop }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [items, channels, sales, unit, basis, win, includeCurrent, currentPeriod, todayKey])

  const cur = perChannel.find(p => p.channel === channel)
  const rows = useMemo(() => {
    if (!cur) return []
    const rs = [...cur.result.rows]
    const chVal = (x: TrendRow) => x.change === null ? -Infinity : x.change === Infinity ? 1e9 : x.change
    const vOrder: Record<Verdict, number> = { stock_warn: 0, order_review: 1, slow: 2, expand: 3, mixed: 4, flat: 5, na: 6 }
    rs.sort((a, b) => {
      if (sortKey === 'change') return chVal(b) - chVal(a) || b.total - a.total
      if (sortKey === 'sales') return (b.sales?.pct ?? -Infinity) - (a.sales?.pct ?? -Infinity) || b.total - a.total
      if (sortKey === 'verdict') return vOrder[a.verdict] - vOrder[b.verdict] || b.total - a.total
      return b.total - a.total
    })
    return rs
  }, [cur, sortKey])
  const periods = cur?.result.periods || []
  const st = cur?.st || null
  const salesMode = st?.mode

  const exportCsv = () => {
    if (!cur) return
    const head = ['SKU', '품명', ...periods.map(label), '합계', `최근${win}${unitWord}`, `직전${win}${unitWord}`, '발주 변화율', '발주 추세',
      '판매 최근 일평균', '판매 직전 일평균', '판매 변화율', '채널 재고', '재고일수', '다음주 예측', '종합', '비고']
    const lines = [head.join(',')]
    for (const r of rows) {
      const s = r.sales
      lines.push([r.sku, `"${r.name.replace(/"/g, '""')}"`, ...r.series, r.total, r.recentSum, r.prevSum,
        r.change === null ? '' : r.change === Infinity ? '신규' : `${Math.round(r.change * 100)}%`, TREND_WORD[r.trend],
        s ? s.recentAvg : '', s?.prevAvg ?? '', s?.pct !== null && s?.pct !== undefined ? `${s.pct}%` : '', s?.stock ?? '', s?.dos ?? '', s?.forecastNext ?? '',
        VERDICT_META[r.verdict].word, `"${r.note.replace(/"/g, '""')}"`].join(','))
    }
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    downloadBlob(blob, `SKU출고추이_${channel}_${unit === 'week' ? '주간' : '월간'}_${todayKey}.csv`)
  }

  const cellStyle: React.CSSProperties = { padding: '4px 6px', textAlign: 'right', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }
  const anyHasRecv = perChannel.some(p => p.result.hasRecv)
  const compareText = cur
    ? `최근 ${win}${unitWord}(${cur.result.recent.map(label).join('·') || '-'}) vs 직전 ${win}${unitWord}(${cur.result.prev.map(label).join('·') || '-'})`
    : ''
  // 판매 증감률의 비교 기준 (일간: 최근 7일 vs 직전 7일, 월간: 이번달 vs 지난달 일평균)
  const salesCompareText = (p: typeof perChannel[number]) => {
    if (!p.st) return ''
    if (p.st.mode === 'daily') return '최근 7일 vs 직전 7일 일평균'
    const ms = p.st.months || []
    const mon = (m: string) => `${parseInt(m.slice(5, 7))}월`
    if (ms.length >= 2) return `${mon(ms[ms.length - 1])} vs ${mon(ms[ms.length - 2])} 일평균`
    return '전월 대비 일평균'
  }
  const orderCompareText = (p: typeof perChannel[number]) =>
    `최근 ${win}${unitWord} vs 직전 ${win}${unitWord}`
  const salesSourceText = (p: typeof perChannel[number]) => {
    if (!p.st || !p.hasSales) return '판매 자료 없음'
    const u = p.st.uploads
    if (p.st.mode === 'daily') {
      const rr = u.rocket_range
      return `쿠팡 판매·재고 ${rr ? `${rr[0].slice(5).replace('-', '/')}~${rr[1].slice(5).replace('-', '/')}` : '-'}` + (u.forecast ? ` · 예측 ${p.st.weeks[0] ? p.st.weeks[0].from.slice(5).replace('-', '/') + '~' : ''}` : '')
    }
    return `올리브영 월별 매출·재고 ${(p.st.months || []).map(m => parseInt(m.slice(5, 7)) + '월').join('·')}` + (p.st.period_label ? ` (${p.st.period_label})` : '')
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">📈 SKU 출고 추이</h1>
        <p className="page-desc">발주 캘린더의 쿠팡·올리브영·컬리 발주(납기일 기준)를 채널별·SKU별로 주간/월간 묶어 발주 추이를 보고, 채널 보고서에 올린 판매·재고·예측 리포트(쿠팡 rocket·forecast, 올리브영 상품별 매출_재고)를 붙여 종합 판정합니다. (발주 캘린더의 수동 정리는 미반영)</p>
      </div>

      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 12 }} />}

      {/* 옵션 */}
      <div style={{ ...card, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <Select size="small" value={unit} onChange={v => setUnit(v)} style={{ width: 90 }}
          options={[{ value: 'week', label: '주간' }, { value: 'month', label: '월간' }]} />
        <Select size="small" value={basis} onChange={v => setBasis(v)} style={{ width: 130 }}
          options={[{ value: 'order', label: '발주수량' }, { value: 'recv', label: '입고확정수량', disabled: !anyHasRecv }]} />
        <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>비교구간</span>
        <Select size="small" value={win} onChange={v => setWin(v)} style={{ width: 80 }}
          options={(unit === 'week' ? [2, 3, 4, 6, 8] : [1, 2, 3]).map(n => ({ value: n, label: `${n}${unitWord}` }))} />
        <span style={{ fontSize: '0.8rem', color: '#6b7280', display: 'flex', alignItems: 'center', gap: 4 }}>
          <Switch size="small" checked={includeCurrent} onChange={setIncludeCurrent} /> {unit === 'week' ? '이번 주' : '이번 달'} 포함
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>
          발주 판정: {compareText}, ±10% 이내는 보합.
          {!includeCurrent && periods.includes(currentPeriod) ? ` 진행 중인 ${unit === 'week' ? '이번 주' : '이번 달'}(${label(currentPeriod)})은 표에만 표시.` : ''}
          {' '}판매 판정: 쿠팡은 최근 7일 vs 직전 7일 일평균, 올리브영은 이달 vs 전월 일평균.
        </span>
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading}>새로고침</Button>
      </div>

      {loading && items.length === 0 ? <Spin /> : items.length === 0 ? (
        <Empty description="발주 캘린더에 쌓인 데이터가 없습니다. 발주 캘린더에 발주서를 먼저 올려주세요." />
      ) : (
        <>
          {/* 핵심 요약 — SKU별 주 대비 / 월 대비 발주 변동 */}
          <div style={card}>
            <div style={{ fontWeight: 700, color: '#111827', marginBottom: 4 }}>
              🧭 핵심 요약 <span style={{ fontWeight: 400, color: '#6b7280', fontSize: '0.8rem' }}>· SKU별 발주 변동 · {basis === 'recv' ? '입고확정수량' : '발주수량'} 기준</span>
            </div>
            <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 10 }}>
              주 대비 = 최근 완료 1주 vs 그 직전 1주 · 월 대비 = 최근 완료 1개월 vs 그 직전 1개월{includeCurrent ? ' (진행 중 기간 포함)' : ''} · 변동 ±30% 이상이고 수량이 채널 합계의 2% 이상인 SKU만, 변동량 큰 순 5개
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, perChannel.length)}, minmax(300px, 1fr))`, gap: 12 }}>
              {perChannel.map(p => (
                <div key={p.channel} style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 10, cursor: 'pointer', background: p.channel === channel ? '#f0f9ff' : '#fff' }}
                  onClick={() => setChannel(p.channel)}>
                  <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: 6 }}>{p.channel}{p.channel === NAVER && <span style={{ fontWeight: 400, fontSize: '0.75rem', color: '#9ca3af', marginLeft: 6 }}>도착보장 출고수량 기준 (발주 아님)</span>}</div>
                  {([['주 대비', p.weekCmp, p.weekTop], ['월 대비', p.monthCmp, p.monthTop]] as const).map(([title, cmp, top]) => (
                    <div key={title} style={{ marginBottom: 8 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.8rem', marginBottom: 3 }}>
                        <span style={{ fontWeight: 700 }}>{title}</span>
                        <span style={{ color: '#6b7280' }}>{cmp.recentLabel} vs {cmp.prevLabel}</span>
                        <span style={{ color: '#374151' }}>합계 {fmt(cmp.recentSum)} / {fmt(cmp.prevSum)}</span>
                        <ChangeText change={cmp.change} trend={cmp.trend} />
                      </div>
                      {top.length === 0 ? (
                        <div style={{ fontSize: '0.78rem', color: '#9ca3af', paddingLeft: 8 }}>유독 변동 큰 SKU 없음</div>
                      ) : top.map(x => (
                        <div key={x.key} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.78rem', paddingLeft: 8, lineHeight: 1.6 }}>
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={x.name}>{x.name}</span>
                          <span style={{ whiteSpace: 'nowrap' }}><span style={{ color: '#6b7280' }}>{fmt(x.prevSum)} → </span><b>{fmt(x.recentSum)}</b> <ChangeText change={x.change} trend={x.trend} /></span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>

          {/* 채널별 SKU 표 */}
          <div style={card}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 10 }}>
              <span style={{ fontWeight: 700, color: '#111827', marginRight: 4 }}>📋 SKU별 추이</span>
              <span style={{ display: 'flex', gap: 4 }}>
                {channels.map(c => (
                  <Button key={c} size="small" type={channel === c ? 'primary' : 'default'} onClick={() => setChannel(c)}>{c}</Button>
                ))}
              </span>
              <Select size="small" value={sortKey} onChange={v => setSortKey(v)} style={{ width: 130 }}
                options={[{ value: 'total', label: '발주 합계순' }, { value: 'change', label: '발주 변화율순' }, { value: 'sales', label: '판매 변화율순', disabled: !cur?.hasSales }, { value: 'verdict', label: '주의 항목 먼저', disabled: !cur?.hasSales }]} />
              <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>
                {channel} · {periods.length}개 {unit === 'week' ? '주' : '월'} · SKU {rows.length}개
                {cur && cur.usedBasis !== basis ? ' · 이 채널은 확정수량이 없어 발주수량 기준' : ''}
                {cur ? ` · ${salesSourceText(cur)}` : ''}
              </span>
              <span style={{ flex: 1 }} />
              <Button size="small" icon={<DownloadOutlined />} onClick={exportCsv} disabled={rows.length === 0}>CSV</Button>
            </div>
            {rows.length === 0 ? <Empty description="해당 거래처의 데이터가 없습니다" /> : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: '0.8rem', minWidth: '100%' }}>
                  <thead>
                    <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                      <th style={{ ...cellStyle, textAlign: 'left', position: 'sticky', left: 0, background: '#f9fafb', zIndex: 1, minWidth: 220 }}>SKU / 품명</th>
                      <th style={{ ...cellStyle, textAlign: 'center' }}>{channel === NAVER ? '출고' : '발주'} 추이</th>
                      {periods.map(p => (
                        <th key={p} style={{ ...cellStyle, color: p === currentPeriod && !includeCurrent ? '#9ca3af' : undefined }} title={p}>
                          {label(p)}{p === currentPeriod && !includeCurrent ? '*' : ''}
                        </th>
                      ))}
                      <th style={cellStyle}>합계</th>
                      <th style={cellStyle}>최근{win}{unitWord}</th>
                      <th style={cellStyle}>직전{win}{unitWord}</th>
                      <th style={{ ...cellStyle, textAlign: 'center' }}>{channel === NAVER ? '출고' : '발주'} 판정</th>
                      {cur?.hasSales && (
                        <>
                          <th style={{ ...cellStyle, textAlign: 'center', borderLeft: '2px solid #e5e7eb' }}>판매 추이</th>
                          <th style={cellStyle}>{salesMode === 'daily' ? '최근7일 일평균' : '이달 일평균'}</th>
                          <th style={cellStyle}>{salesMode === 'daily' ? '직전7일' : '전월'}</th>
                          <th style={{ ...cellStyle, textAlign: 'center' }}>판매 판정</th>
                          <th style={cellStyle}>채널 재고</th>
                          <th style={cellStyle}>재고일수</th>
                          {salesMode === 'daily' && <th style={cellStyle}>다음주 예측</th>}
                          <th style={{ ...cellStyle, textAlign: 'center', borderLeft: '2px solid #e5e7eb' }}>종합</th>
                          <th style={{ ...cellStyle, textAlign: 'left' }}>비고</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={r.key} style={{ borderBottom: '1px solid #f3f4f6', background: r.verdict === 'stock_warn' ? '#fff7ed' : r.verdict === 'order_review' ? '#eff6ff' : undefined }}>
                        <td style={{ ...cellStyle, textAlign: 'left', position: 'sticky', left: 0, background: 'inherit', zIndex: 1, whiteSpace: 'normal' }}>
                          {r.sku ? <span style={{ color: '#2563eb', fontFamily: 'monospace', marginRight: 6 }}>{r.sku}</span>
                            : <Tag style={{ marginRight: 6 }}>미매핑</Tag>}
                          {r.name}
                        </td>
                        <td style={{ ...cellStyle, textAlign: 'center' }}><Sparkline values={r.series} color={TREND_COLOR[r.trend]} /></td>
                        {r.series.map((v, i) => (
                          <td key={periods[i]} style={{ ...cellStyle, color: v === 0 ? '#d1d5db' : periods[i] === currentPeriod && !includeCurrent ? '#9ca3af' : undefined }}>{v === 0 ? '·' : fmt(v)}</td>
                        ))}
                        <td style={{ ...cellStyle, fontWeight: 600 }}>{fmt(r.total)}</td>
                        <td style={cellStyle}>{fmt(r.recentSum)}</td>
                        <td style={cellStyle}>{fmt(r.prevSum)}</td>
                        <td style={{ ...cellStyle, textAlign: 'center' }}><TrendTag change={r.change} trend={r.trend} /></td>
                        {cur?.hasSales && (
                          <>
                            <td style={{ ...cellStyle, textAlign: 'center', borderLeft: '2px solid #e5e7eb' }}>
                              {r.sales ? <Tooltip title={`${r.sales.row.label} · ${salesMode === 'daily' ? '일별 판매' : '월별 매출'}`}><span><Sparkline values={r.sales.series} color={TREND_COLOR[r.sales.trend]} width={90} /></span></Tooltip> : <span style={{ color: '#d1d5db' }}>연결 안 됨</span>}
                            </td>
                            <td style={cellStyle}>{r.sales ? fmt1(r.sales.recentAvg) : '-'}</td>
                            <td style={cellStyle}>{r.sales && r.sales.prevAvg !== null ? fmt1(r.sales.prevAvg) : '-'}</td>
                            <td style={{ ...cellStyle, textAlign: 'center' }}><SalesTag s={r.sales} /></td>
                            <td style={cellStyle}>{r.sales && r.sales.stock !== null ? fmt(r.sales.stock) : '-'}</td>
                            <td style={{ ...cellStyle, color: r.sales?.dos !== null && r.sales?.dos !== undefined && r.sales.dos < 14 ? '#b91c1c' : undefined }}>{r.sales && r.sales.dos !== null ? `${r.sales.dos}일` : '-'}</td>
                            {salesMode === 'daily' && <td style={cellStyle}>{r.sales && r.sales.forecastNext !== null ? fmt(r.sales.forecastNext) : '-'}</td>}
                            <td style={{ ...cellStyle, textAlign: 'center', borderLeft: '2px solid #e5e7eb' }}><VerdictTag v={r.verdict} /></td>
                            <td style={{ ...cellStyle, textAlign: 'left', whiteSpace: 'normal', minWidth: 140, color: '#6b7280' }}>{r.note}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {cur?.hasSales && (
              <div style={{ fontSize: '0.75rem', color: '#9ca3af', marginTop: 8 }}>
                종합 판정: 확대(발주↑판매↑) · 둔화(발주↓판매↓) · 재고 주의(발주↑판매↓) · 발주 검토(발주↓판매↑) · 보합/혼조. 재고일수 = 채널 재고 ÷ 최근 일평균 판매, 14일 미만은 빨간색. 판매 행 연결은 채널 보고서의 상품 별칭(라벨) 기준이며 '연결 안 됨'은 해당 채널 보고 화면에서 별칭을 맞추면 이어집니다.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
