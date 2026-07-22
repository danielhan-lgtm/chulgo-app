import { useState, useEffect } from 'react'
import { Button, DatePicker, Radio, Table, Tag, Alert, Spin, Input, InputNumber, Tooltip, Upload, Modal, Switch, message } from 'antd'
import { SearchOutlined, DownloadOutlined, UploadOutlined, ApiOutlined, AimOutlined, AppstoreAddOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import type { AppConfig } from '../types'
import {
  getOutboundForecast, getOutboundTargets, uploadOutboundTargets, deleteOutboundTargets,
  getPartnerTeamMap, savePartnerTeamMap,
  getSetBoms, createSetBom, deleteSetBom,
  setTargetOverride,
} from '../services/api'
import type { OutboundForecastResult, OutboundItemRow, OutboundPartnerRow, OutboundTargetRow, TargetStatus, SetBom } from '../services/api'
import TrendChart from '../components/TrendChart'
import dayjs from 'dayjs'

interface Props { config: AppConfig }

type GroupBy = 'item' | 'partner' | 'target'

const EXCLUDE = '__EXCLUDE__'  // 거래처-팀 매핑에서 '의도적 제외' 표식

// 전체 모드 병합 행: 팀별 월간 내역(_byTeam)을 보존해 셀에서 BD/BE를 나눠 표시
type MergedRow = OutboundTargetRow & {
  _teams?: string[]
  _byTeam?: Record<string, { tgt: Record<string, number>; act: Record<string, number> }>
}

function achColor(pct: number | null) {
  if (pct == null) return '#9ca3af'
  return pct >= 100 ? '#10b981' : pct >= 70 ? '#f59e0b' : '#dc2626'
}

function sumVals(o: Record<string, number>) { return Object.values(o).reduce((s, v) => s + v, 0) }

function fmt(n: number) { return n ? n.toLocaleString() : '' }

function bucketLabel(key: string, period: string) {
  if (period === 'month') return key            // YYYY-MM
  if (period === 'day') return key.slice(5)      // MM-DD
  return key.slice(5) + '~'                      // 주 시작(월요일) MM-DD~
}

/** 마지막 실측 버킷 다음부터 예측 버킷 키들을 생성. */
function futureBuckets(lastKey: string, period: 'day' | 'week' | 'month', count: number): string[] {
  const out: string[] = []
  let d = dayjs(lastKey.length === 7 ? lastKey + '-01' : lastKey)
  for (let i = 0; i < count; i++) {
    d = period === 'month' ? d.add(1, 'month') : period === 'week' ? d.add(7, 'day') : d.add(1, 'day')
    out.push(period === 'month' ? d.format('YYYY-MM') : d.format('YYYY-MM-DD'))
  }
  return out
}

export default function OutboundForecast({ config }: Props) {
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('month')
  const [groupBy, setGroupBy] = useState<GroupBy>('item')
  const [fromDate, setFromDate] = useState(dayjs().subtract(90, 'day').format('YYYY-MM-DD'))
  const [toDate, setToDate] = useState(dayjs().format('YYYY-MM-DD'))
  const [forecastMonths, setForecastMonths] = useState(3)
  const [expandSets, setExpandSets] = useState(false)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<OutboundForecastResult | null>(null)
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)  // 차트 대상 (null=전체합계)
  const [teamFilter, setTeamFilter] = useState<string>('')  // 목표 대비 팀 필터 ('' = 전체)
  const [shareProduct, setShareProduct] = useState<string | null>(null)  // 거래처 점유율 모달 대상 제품명
  const [shareMonth, setShareMonth] = useState<string | null>(null)  // 점유율 모달 대상 월 (null=전체 기간)
  const [editProduct, setEditProduct] = useState<string | null>(null)  // 목표 수정 모달 대상 제품명
  const [editVals, setEditVals] = useState<Record<string, number | null>>({})  // {`team|month`: qty}
  const [editOrig, setEditOrig] = useState<Record<string, number | null>>({})  // 원본 스냅샷 (변경분만 저장)
  const [editTeams, setEditTeams] = useState<string[]>([])  // 이 제품의 팀 목록
  const [savingEdit, setSavingEdit] = useState(false)

  // 목표치 + 거래처-팀 매핑
  const [targetStatus, setTargetStatus] = useState<TargetStatus>({ loaded: false })
  const [uploadingTarget, setUploadingTarget] = useState(false)
  const [mapOpen, setMapOpen] = useState(false)
  const [mapDraft, setMapDraft] = useState<Record<string, string>>({})
  const [mapPartners, setMapPartners] = useState<string[]>([])
  const [mapTeams, setMapTeams] = useState<string[]>([])
  const [mapTeamLabels, setMapTeamLabels] = useState<Record<string, string>>({})
  const [mapSearch, setMapSearch] = useState('')
  const [mapUnmappedOnly, setMapUnmappedOnly] = useState(false)
  const [savingMap, setSavingMap] = useState(false)

  const teamLabel = (code: string) => code === EXCLUDE ? '제외' : (mapTeamLabels[code] ? `${mapTeamLabels[code]}(${code})` : code)

  // 세트 → 단품 구성(BOM)
  const [setOpen, setSetOpen] = useState(false)
  const [setBoms, setSetBoms] = useState<SetBom[]>([])
  const [bomForm, setBomForm] = useState({ set_sku: '', set_name: '', component_sku: '', component_name: '', qty_per_set: 1, note: '' })
  const [savingBom, setSavingBom] = useState(false)

  useEffect(() => { getOutboundTargets().then(setTargetStatus).catch(() => {}) }, [])

  async function openSetModal() {
    try { setSetBoms(await getSetBoms()) } catch { /* ignore */ }
    setSetOpen(true)
  }

  async function addBom() {
    if (!bomForm.set_name.trim() && !bomForm.set_sku.trim()) { message.warning('세트명 또는 세트 SKU를 입력하세요'); return }
    if (!bomForm.component_name.trim() && !bomForm.component_sku.trim()) { message.warning('구성 단품명 또는 SKU를 입력하세요'); return }
    setSavingBom(true)
    try {
      await createSetBom({
        set_sku: bomForm.set_sku.trim(), set_name: bomForm.set_name.trim(),
        component_sku: bomForm.component_sku.trim(), component_name: bomForm.component_name.trim(),
        qty_per_set: Number(bomForm.qty_per_set) || 1, note: bomForm.note.trim(),
      })
      setSetBoms(await getSetBoms())
      setBomForm({ ...bomForm, component_sku: '', component_name: '', qty_per_set: 1, note: '' })
      message.success('세트 구성 추가됨')
    } catch {
      message.error('세트 구성 저장 실패')
    } finally {
      setSavingBom(false)
    }
  }

  async function removeBom(id: number) {
    await deleteSetBom(id)
    setSetBoms(await getSetBoms())
    message.success('삭제됨')
  }

  async function handleTargetUpload(file: File) {
    setUploadingTarget(true)
    try {
      const res = await uploadOutboundTargets(file)
      message.success(`목표 로드됨: ${res.product_count}개 품목 · 팀 ${res.teams.join(', ')}`)
      setTargetStatus(await getOutboundTargets())
    } catch (e: unknown) {
      message.error((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '목표 업로드 실패')
    } finally {
      setUploadingTarget(false)
    }
    return false
  }

  async function handleTargetDelete() {
    await deleteOutboundTargets()
    setTargetStatus({ loaded: false })
    message.success('목표 삭제됨')
  }

  async function openMapModal() {
    try {
      const data = await getPartnerTeamMap(config.api_token)
      // 거래처 후보: 박스히어로 거래처 ∪ 조회결과 거래처 ∪ 기존 매핑 키
      const fromResult = (result?.partners || []).map(p => p.partner)
      const all = Array.from(new Set([...data.partners, ...fromResult, ...Object.keys(data.mappings)]))
        .filter(p => p && p !== '(거래처 미지정)').sort()
      setMapPartners(all)
      setMapDraft(data.mappings)
      setMapTeams(data.teams || [])
      setMapTeamLabels(data.team_labels || {})
      setMapSearch('')
      setMapUnmappedOnly(false)
      setMapOpen(true)
    } catch {
      message.error('매핑 정보를 불러오지 못했습니다')
    }
  }

  // 추천 자동매핑: 잘 알려진 이커머스/오프라인 채널 키워드로 미매핑 거래처를 자동 제안
  function autoSuggestMap() {
    const ECOMMERCE = ['쿠팡', '네이버', '스마트스토어', '카카오', '토스', '11번가', '지마켓', 'g마켓', '옥션', '위메프', '티몬', 'ssg', '쓱', '컬리', '마켓컬리', '오늘의집', '무신사', '배민', 'b마트', '홈쇼핑', 'cj온스타일', '신세계', '롯데온', '에이블리', '지그재그']
    const OFFLINE = ['올리브영', '올영', '코스트코', '이마트', '롯데마트', '홈플러스', '하나로', '백화점']
    const ecoTeam = mapTeams.find(t => /이커머스|커머스|온라인|ecommerce|이콤/i.test(mapTeamLabels[t] || t)) || ''
    const offTeam = mapTeams.find(t => /올리브영|오프라인|리테일|매장/i.test(mapTeamLabels[t] || t)) || ''
    const next = { ...mapDraft }
    let n = 0
    for (const p of mapPartners) {
      if (next[p]) continue
      const lc = p.toLowerCase()
      if (offTeam && OFFLINE.some(k => lc.includes(k.toLowerCase()))) { next[p] = offTeam; n++ }
      else if (ecoTeam && ECOMMERCE.some(k => lc.includes(k.toLowerCase()))) { next[p] = ecoTeam; n++ }
    }
    setMapDraft(next)
    message.success(n ? `${n}곳 자동 추천 — 확인 후 저장하세요` : '추천할 거래처를 찾지 못했습니다')
  }

  async function saveMap() {
    setSavingMap(true)
    try {
      await savePartnerTeamMap(mapDraft)
      message.success('거래처-팀 매핑 저장됨')
      setMapOpen(false)
      if (result) await handleSearch()  // 달성률 갱신
    } catch {
      message.error('매핑 저장 실패')
    } finally {
      setSavingMap(false)
    }
  }

  // 목표 수기 수정 (팀 × 전체 목표월)
  function openEditModal(name: string) {
    const tg = result?.targets
    const months = tg?.months || []
    const rows = (tg?.rows || []).filter(r => r.name === name)
    const teams = Array.from(new Set(rows.map(r => r.team)))
    const vals: Record<string, number | null> = {}
    for (const r of rows) for (const m of months) {
      const v = r.target_by_month[m]
      vals[`${r.team}|${m}`] = (v === undefined || v === null) ? null : v
    }
    setEditTeams(teams)
    setEditVals(vals)
    setEditOrig({ ...vals })
    setEditProduct(name)
  }

  async function saveEdit() {
    if (!editProduct) return
    // 변경된 셀만 저장 (미변경분은 원본 목표 유지)
    const changed = Object.keys(editVals).filter(k => (editVals[k] ?? null) !== (editOrig[k] ?? null))
    if (!changed.length) { setEditProduct(null); return }
    setSavingEdit(true)
    try {
      await Promise.all(changed.map(k => {
        const [team, month] = k.split('|')
        return setTargetOverride({ team, name: editProduct, month, qty: editVals[k] == null ? null : Number(editVals[k]) })
      }))
      message.success(`목표 ${changed.length}건 수정 저장됨`)
      setEditProduct(null)
      if (result) await handleSearch()
    } catch {
      message.error('목표 저장 실패')
    } finally {
      setSavingEdit(false)
    }
  }

  async function handleSearch(expandOverride?: boolean) {
    if (!config.api_token) return
    setLoading(true)
    setError('')
    try {
      const data = await getOutboundForecast({
        token: config.api_token,
        from_date: fromDate,
        to_date: toDate,
        period,
        forecast_months: forecastMonths,
        expand_sets: expandOverride ?? expandSets,
      })
      setResult(data)
      setSelectedKey(null)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  const buckets = result?.buckets || []

  const bucketColumns = buckets.map(bk => ({
    title: <Tooltip title={`${bk} 실제 출고`}><span style={{ fontSize: '0.74rem' }}>{bucketLabel(bk, result!.period)}</span></Tooltip>,
    key: bk, width: 72, align: 'right' as const,
    render: (_: unknown, r: OutboundItemRow | OutboundPartnerRow) =>
      r.by_bucket[bk] ? <span style={{ color: '#374151' }}>{fmt(r.by_bucket[bk])}</span> : <span style={{ color: '#d1d5db' }}>·</span>,
  }))

  // 미래 월별 재고 소진 궤적 (현재 기점 기초재고 → 추세대로 빠지면 어떻게 줄어드는지)
  // 월 단위 뷰에서만, 현재월 다음달부터 예측개월 만큼.
  const futureMonths = (result?.period === 'month')
    ? Array.from({ length: result.forecast_months }, (_, i) => dayjs().add(i + 1, 'month').format('YYYY-MM'))
    : []
  const futureColumns = futureMonths.map((m, i) => ({
    title: <Tooltip title={`${m} 예상 잔여재고 (현 추세 기준)`}>
      <span style={{ fontSize: '0.74rem', color: '#2563eb' }}>{m.slice(5)}월<br /><span style={{ fontSize: '0.6rem', fontWeight: 400 }}>예측</span></span></Tooltip>,
    key: `fut_${m}`, width: 84, align: 'right' as const,
    onCell: () => ({ style: { background: '#f5f9ff', ...(i === 0 ? { borderLeft: '2px solid #bfdbfe' } : {}) } }),
    render: (_: unknown, r: OutboundItemRow) => {
      const out = Math.round(r.monthly_avg)
      if (out <= 0) return <span style={{ color: '#d1d5db' }}>·</span>
      const after = r.stock - out * (i + 1)
      const before = r.stock - out * i
      const depleted = after <= 0
      const justDepleted = depleted && (before > 0 || i === 0)
      return (
        <div>
          <div style={{ fontWeight: 700, color: depleted ? '#dc2626' : '#1e3a8a' }}>
            {depleted ? (justDepleted ? '소진' : '0') : after.toLocaleString()}
          </div>
          <div style={{ fontSize: '0.64rem', color: '#94a3b8' }}>-{out.toLocaleString()}</div>
        </div>
      )
    },
  }))

  const trendColumns = [
    {
      title: <span style={{ fontSize: '0.74rem' }}>기간합계</span>,
      key: 'total', width: 84, align: 'right' as const, fixed: 'right' as const,
      sorter: (a: OutboundForecastRowLike, b: OutboundForecastRowLike) => a.total_out - b.total_out,
      render: (_: unknown, r: OutboundForecastRowLike) => <span style={{ fontWeight: 800 }}>{fmt(r.total_out)}</span>,
    },
    {
      title: <Tooltip title="기간 내 일평균 출고량"><span style={{ fontSize: '0.74rem' }}>일평균</span></Tooltip>,
      key: 'daily', width: 70, align: 'right' as const,
      render: (_: unknown, r: OutboundForecastRowLike) => <span style={{ color: '#6b7280' }}>{r.daily_avg.toLocaleString()}</span>,
    },
    {
      title: <Tooltip title="추세 기준 월평균 환산"><span style={{ fontSize: '0.74rem' }}>월평균</span></Tooltip>,
      key: 'monthly', width: 76, align: 'right' as const,
      render: (_: unknown, r: OutboundForecastRowLike) => <span style={{ color: '#6b7280' }}>{r.monthly_avg.toLocaleString()}</span>,
    },
    {
      title: <Tooltip title={`이 추세면 향후 ${result?.forecast_months}개월 누적 예상 출고량`}>
        <span style={{ fontSize: '0.74rem', color: '#2563eb' }}>{result?.forecast_months}개월 예상</span></Tooltip>,
      key: 'forecast', width: 92, align: 'right' as const,
      sorter: (a: OutboundForecastRowLike, b: OutboundForecastRowLike) => a.forecast_total - b.forecast_total,
      render: (_: unknown, r: OutboundForecastRowLike) => <span style={{ fontWeight: 700, color: '#2563eb' }}>{fmt(r.forecast_total)}</span>,
    },
  ]

  const itemColumns = [
    {
      title: 'SKU', dataIndex: 'sku', key: 'sku', width: 120, fixed: 'left' as const,
      render: (v: string) => <span style={{ fontSize: '0.72rem', fontFamily: 'monospace' }}>{v}</span>,
    },
    {
      title: '상품명', dataIndex: 'name', key: 'name', width: 190, fixed: 'left' as const, ellipsis: true,
      render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
    },
    ...bucketColumns,
    {
      // 현재 기점: 과거(출고)와 미래(예측) 사이에 현재고를 둔다
      title: <Tooltip title="현재 박스히어로 재고 = 현재 기점 (이 시점부터 미래 차감 시작)">
        <span style={{ fontSize: '0.74rem', color: '#10b981' }}>현재고<br /><span style={{ fontSize: '0.6rem', fontWeight: 400 }}>현재 기점</span></span></Tooltip>,
      dataIndex: 'stock', key: 'stock', width: 84, align: 'right' as const,
      sorter: (a: OutboundItemRow, b: OutboundItemRow) => a.stock - b.stock,
      onCell: () => ({ style: { background: '#ecfdf5', borderLeft: '2px solid #6ee7b7', borderRight: '2px solid #6ee7b7' } }),
      render: (v: number) => <span style={{ fontWeight: 800, color: '#10b981' }}>{fmt(v)}</span>,
    },
    ...futureColumns,
    ...trendColumns,
    {
      title: <Tooltip title="현재고 ÷ 월평균 출고 = 소진까지 남은 개월수"><span style={{ fontSize: '0.74rem' }}>소진예상</span></Tooltip>,
      key: 'deplete', width: 96, align: 'right' as const, fixed: 'right' as const,
      sorter: (a: OutboundItemRow, b: OutboundItemRow) =>
        (a.deplete_months ?? 9999) - (b.deplete_months ?? 9999),
      render: (_: unknown, r: OutboundItemRow) => {
        if (r.deplete_months == null) return <span style={{ color: '#d1d5db' }}>—</span>
        const m = r.deplete_months
        const color = m <= 1 ? '#dc2626' : m <= (result?.forecast_months || 3) ? '#f59e0b' : '#6b7280'
        return (
          <Tooltip title={r.deplete_date ? `예상 소진일 ${r.deplete_date}` : ''}>
            <span style={{ fontWeight: 700, color }}>{m}개월</span>
          </Tooltip>
        )
      },
    },
    {
      title: <Tooltip title={`현재고 − ${result?.forecast_months}개월 예상출고. 음수면 재고 부족`}>
        <span style={{ fontSize: '0.74rem' }}>예상잔여</span></Tooltip>,
      key: 'remaining', width: 90, align: 'right' as const, fixed: 'right' as const,
      render: (_: unknown, r: OutboundItemRow) => (
        <span style={{ fontWeight: 700, color: r.remaining_after < 0 ? '#dc2626' : '#374151' }}>
          {r.remaining_after.toLocaleString()}
        </span>
      ),
    },
  ]

  const partnerColumns = [
    {
      title: '거래처', dataIndex: 'partner', key: 'partner', width: 200, fixed: 'left' as const, ellipsis: true,
      render: (v: string) => <span style={{ fontSize: '0.84rem', fontWeight: 500 }}>{v}</span>,
    },
    {
      title: <span style={{ fontSize: '0.74rem' }}>품목수</span>,
      dataIndex: 'sku_count', key: 'sku_count', width: 70, align: 'right' as const,
      render: (v: number) => <Tag style={{ fontSize: '0.7rem' }}>{v}</Tag>,
    },
    ...bucketColumns,
    ...trendColumns,
  ]

  // ── 목표 대비 ────────────────────────────────────────────────
  const targets = result?.targets
  const targetMonths = targets?.months || []
  const nowMonth = dayjs().format('YYYY-MM')
  const prevMonth = dayjs().subtract(1, 'month').format('YYYY-MM')
  const mon3ago = dayjs().subtract(3, 'month').format('YYYY-MM')   // 3개월 전 (3개월 추세 비교 기준)
  const yoyMonth = dayjs().subtract(1, 'year').format('YYYY-MM')   // 전년 동월
  // 증감 표시 (성장/후퇴): 현재값 cur vs 기준값 base
  function growthTag(cur: number, base: number, fromM: string, toM: string) {
    if (base <= 0) return cur > 0
      ? <Tooltip title={`${fromM} 0 → ${toM} ${cur.toLocaleString()}`}><span style={{ color: '#10b981', fontWeight: 700 }}>신규</span></Tooltip>
      : <span style={{ color: '#d1d5db' }}>—</span>
    const g = Math.round((cur - base) / base * 100)
    const color = g > 0 ? '#10b981' : g < 0 ? '#dc2626' : '#6b7280'
    const arrow = g > 0 ? '▲' : g < 0 ? '▼' : '–'
    return (
      <Tooltip title={`${fromM} ${base.toLocaleString()} → ${toM} ${cur.toLocaleString()} (현재월 진행중일 수 있음)`}>
        <span style={{ color, fontWeight: 700 }}>{arrow} {g > 0 ? '+' : ''}{g}%</span>
      </Tooltip>
    )
  }
  // 증감 인라인(툴팁 없는 컴팩트 버전) — 팀별 분리 표시용
  function growthInline(cur: number, base: number) {
    if (base <= 0) return cur > 0 ? <span style={{ color: '#10b981', fontWeight: 700 }}>신규</span> : <span style={{ color: '#d1d5db' }}>—</span>
    const g = Math.round((cur - base) / base * 100)
    const color = g > 0 ? '#10b981' : g < 0 ? '#dc2626' : '#6b7280'
    const arrow = g > 0 ? '▲' : g < 0 ? '▼' : '–'
    return <span style={{ color, fontWeight: 700 }}>{arrow}{g > 0 ? '+' : ''}{g}%</span>
  }
  // 증감 셀: 전체(병합) 행이면 BD·BE로 나눠 표시, 아니면 단일
  function growthCell(r: OutboundTargetRow, baseM: string) {
    const bt = (r as MergedRow)._byTeam
    const teams = (r as MergedRow)._teams || []
    if (bt && teams.length > 1) {
      return (
        <div>
          {teams.map(t => (
            <Tooltip key={t} title={`${targets?.team_labels?.[t] || t}: ${baseM} ${(bt[t].act[baseM] || 0).toLocaleString()} → ${nowMonth} ${(bt[t].act[nowMonth] || 0).toLocaleString()}`}>
              <div style={{ fontSize: '0.7rem', lineHeight: 1.35 }}>
                <span style={{ color: '#9ca3af' }}>{t}</span> {growthInline(bt[t].act[nowMonth] || 0, bt[t].act[baseM] || 0)}
              </div>
            </Tooltip>
          ))}
        </div>
      )
    }
    return growthTag(r.actual_by_month[nowMonth] || 0, r.actual_by_month[baseM] || 0, baseM, nowMonth)
  }
  const targetRows = (targets?.rows || [])
    .filter(r => !teamFilter || r.team === teamFilter)
    .filter(r => !search || r.name.includes(search) || r.team.includes(search) || r.brand.includes(search))

  // 전체 모드: 같은 제품의 팀별 행을 1행으로 병합 (목표·실적 합산, 재고는 제품 공유라 1회만)
  function mergeByProduct(rows: OutboundTargetRow[]): MergedRow[] {
    const map = new Map<string, MergedRow & { _teamSet: Set<string> }>()
    for (const r of rows) {
      let m = map.get(r.name)
      if (!m) {
        m = { ...r, target_by_month: {}, actual_by_month: {}, matched_names: [], stock: 0, _teamSet: new Set(), _byTeam: {} }
        map.set(r.name, m)
      }
      m._teamSet.add(r.team)
      m._byTeam![r.team] = { tgt: r.target_by_month, act: r.actual_by_month }
      m.matched = m.matched || r.matched
      m.stock = Math.max(m.stock, r.stock)  // 공유 재고 → 합산 아닌 1회
      m.matched_names = Array.from(new Set([...m.matched_names, ...r.matched_names]))
      for (const [mo, q] of Object.entries(r.target_by_month)) m.target_by_month[mo] = (m.target_by_month[mo] || 0) + q
      for (const [mo, q] of Object.entries(r.actual_by_month)) m.actual_by_month[mo] = (m.actual_by_month[mo] || 0) + q
    }
    return Array.from(map.values())
      .map(m => ({ ...m, team: Array.from(m._teamSet).sort().join('·'), _teams: Array.from(m._teamSet).sort() }))
      .sort((a, b) => sumVals(b.target_by_month) - sumVals(a.target_by_month))
  }
  // 팀 선택 시 = 팀별 행, 전체 = 제품 병합 행
  const displayRows: MergedRow[] = teamFilter ? targetRows : mergeByProduct(targetRows)

  // 목표월을 과거(달성률)·미래(재고 커버)로 구분
  const pastTM = targetMonths.filter(m => m <= nowMonth)
  const futTM = targetMonths.filter(m => m > nowMonth)
  const isOverridden = (team: string, name: string, m: string) => !!targets?.overrides?.[`${team}|${name}|${m}`]

  // 행별 재고 쇼트 / 필요 보충량 계산
  // 핵심: 미래 충당에 쓸 수 있는 건 '현재 물리 재고'뿐. 과거 초과출고(선출고분)는
  // 이미 나간 물량이라 미래 재고에 더하면 안 됨 → stock만으로 미래 목표를 차감.
  function coverInfo(r: OutboundTargetRow) {
    const shipped = pastTM.reduce((s, m) => s + (r.actual_by_month[m] || 0), 0)
    const pastTarget = pastTM.reduce((s, m) => s + (r.target_by_month[m] || 0), 0)
    const carryover = shipped - pastTarget            // 참고용: +면 목표 초과(선출고), -면 미달 (재고와 무관)
    // 과거 데이터: 현재월 이하 실적의 월평균 (최근 판매 추세)
    const pam = Object.keys(r.actual_by_month).filter(m => m <= nowMonth)
    const avgActual = pam.length ? Math.round(pam.reduce((s, m) => s + (r.actual_by_month[m] || 0), 0) / pam.length) : 0
    // 미래 월 수요 = max(목표, 과거 월평균 실적)  → 목표·추세 둘 다 반영
    const demandOf = (m: string) => Math.max(r.target_by_month[m] || 0, avgActual)
    const req: Record<string, number> = {}            // 월별 필요 추가 입고
    const demandByMonth: Record<string, number> = {}  // 월별 적용 수요
    const balByMonth: Record<string, number> = {}     // 보충 없이 현재고로 버틸 때 월말 잔여
    let avail = r.stock                                // 미래 충당 가용 = 현재 재고
    let bal = r.stock
    let totalReq = 0
    let shortMonth = ''                                // 재고 쇼트(부족 시작) 예상 월
    for (const m of futTM) {
      const d = demandOf(m)
      demandByMonth[m] = d
      const need = Math.max(0, d - avail)
      avail = Math.max(0, avail - d)
      req[m] = need
      totalReq += need
      bal -= d
      balByMonth[m] = bal
      if (bal < 0 && !shortMonth) shortMonth = m       // 처음으로 음수 = 쇼트 시점
    }
    return { carryover, req, totalReq, startAvail: r.stock, shortMonth, balByMonth, avgActual, demandByMonth }
  }

  // 누적 달성률은 '현재까지 도래한 목표월(pastTM)' 기준으로만 계산 (미래 목표는 분모 제외)
  const pastActual = (r: OutboundTargetRow) => pastTM.reduce((s, m) => s + (r.actual_by_month[m] || 0), 0)
  const pastTargetSum = (r: OutboundTargetRow) => pastTM.reduce((s, m) => s + (r.target_by_month[m] || 0), 0)

  // 팀별 집계 (전체 행 기준, 필터 무관) — 월별 실적/목표/필요입고 + 누적
  const teamAgg: Record<string, {
    act: Record<string, number>; tgt: Record<string, number>; req: Record<string, number>
    pastAct: number; pastTgt: number; totalReq: number
  }> = {}
  for (const team of (targets?.teams || [])) {
    const rs = (targets?.rows || []).filter(r => r.team === team)
    const act: Record<string, number> = {}, tgt: Record<string, number> = {}, req: Record<string, number> = {}
    let totalReq = 0
    for (const r of rs) {
      // 실적은 전체 월(전월·3개월전·전년동월 포함) 합산, 목표는 목표월만
      for (const m of Object.keys(r.actual_by_month)) act[m] = (act[m] || 0) + (r.actual_by_month[m] || 0)
      for (const m of targetMonths) tgt[m] = (tgt[m] || 0) + (r.target_by_month[m] || 0)
      const ci = coverInfo(r)
      for (const m of futTM) req[m] = (req[m] || 0) + (ci.req[m] || 0)
      totalReq += ci.totalReq
    }
    teamAgg[team] = {
      act, tgt, req, totalReq,
      pastAct: pastTM.reduce((s, m) => s + (act[m] || 0), 0),
      pastTgt: pastTM.reduce((s, m) => s + (tgt[m] || 0), 0),
    }
  }

  const teamSummaryColumns = [
    {
      title: '팀', dataIndex: 'team', key: 'team', width: 110, fixed: 'left' as const,
      render: (t: string) => <Tag color="blue" style={{ fontSize: '0.74rem' }}>{targets?.team_labels?.[t] || t}</Tag>,
    },
    ...targetMonths.map(m => {
      const isFuture = m > nowMonth
      return {
        title: <span style={{ fontSize: '0.74rem', color: m === nowMonth ? '#2563eb' : isFuture ? '#7c3aed' : '#374151' }}>
          {m.slice(5)}월{isFuture && <><br /><span style={{ fontSize: '0.6rem' }}>필요입고</span></>}</span>,
        key: m, width: 92, align: 'center' as const,
        onCell: isFuture ? () => ({ style: { background: '#faf5ff' } }) : undefined,
        render: (_: unknown, row: { team: string }) => {
          const a = teamAgg[row.team]
          if (!a) return null
          if (isFuture) {
            const need = a.req[m] || 0
            return <span style={{ fontWeight: 700, fontSize: '0.8rem', color: need > 0 ? '#dc2626' : '#10b981' }}>{need > 0 ? `+${need.toLocaleString()}` : '충당'}</span>
          }
          const t = a.tgt[m] || 0, ac = a.act[m] || 0
          const pct = t > 0 ? Math.round((ac / t) * 100) : null
          if (!t) return <span style={{ color: '#e5e7eb' }}>·</span>
          return (
            <div>
              <div style={{ fontSize: '0.66rem', color: '#9ca3af' }}>{ac.toLocaleString()}/{t.toLocaleString()}</div>
              <div style={{ fontWeight: 800, fontSize: '0.82rem', color: achColor(pct) }}>{pct == null ? '—' : `${pct}%`}</div>
            </div>
          )
        },
      }
    }),
    {
      title: <Tooltip title="현재까지 도래한 목표월 누적 실적 ÷ 누적 목표"><span style={{ fontSize: '0.74rem' }}>종합 달성률</span></Tooltip>,
      key: 'overall', width: 110, align: 'center' as const, fixed: 'right' as const,
      render: (_: unknown, row: { team: string }) => {
        const a = teamAgg[row.team]
        const pct = a && a.pastTgt > 0 ? Math.round((a.pastAct / a.pastTgt) * 100) : null
        return (
          <div>
            <div style={{ fontSize: '0.66rem', color: '#9ca3af' }}>{(a?.pastAct || 0).toLocaleString()}/{(a?.pastTgt || 0).toLocaleString()}</div>
            <div style={{ fontWeight: 800, color: achColor(pct) }}>{pct == null ? '—' : `${pct}%`}</div>
          </div>
        )
      },
    },
    {
      title: <Tooltip title={`전월(${prevMonth}) 대비 이번달(${nowMonth}) 팀 출고 증감`}><span style={{ fontSize: '0.74rem' }}>전월대비</span></Tooltip>,
      key: 'mom', width: 90, align: 'center' as const, fixed: 'right' as const,
      render: (_: unknown, row: { team: string }) => {
        const a = teamAgg[row.team]
        return growthTag(a?.act[nowMonth] || 0, a?.act[prevMonth] || 0, prevMonth, nowMonth)
      },
    },
    {
      title: <Tooltip title={`3개월 전(${mon3ago}) 대비 이번달(${nowMonth}) 팀 출고 증감 (3개월 추세)`}><span style={{ fontSize: '0.74rem' }}>3개월추세</span></Tooltip>,
      key: 'mom3', width: 90, align: 'center' as const, fixed: 'right' as const,
      render: (_: unknown, row: { team: string }) => {
        const a = teamAgg[row.team]
        return growthTag(a?.act[nowMonth] || 0, a?.act[mon3ago] || 0, mon3ago, nowMonth)
      },
    },
    {
      title: <Tooltip title={`전년 동월(${yoyMonth}) 대비 이번달(${nowMonth}) 팀 출고 증감`}><span style={{ fontSize: '0.74rem' }}>전년동월</span></Tooltip>,
      key: 'yoy', width: 90, align: 'center' as const, fixed: 'right' as const,
      render: (_: unknown, row: { team: string }) => {
        const a = teamAgg[row.team]
        return growthTag(a?.act[nowMonth] || 0, a?.act[yoyMonth] || 0, yoyMonth, nowMonth)
      },
    },
    {
      title: <Tooltip title="미래 목표 전체를 맞추기 위한 팀 합계 추가 입고량"><span style={{ fontSize: '0.74rem' }}>추가입고 필요</span></Tooltip>,
      key: 'req', width: 110, align: 'center' as const, fixed: 'right' as const,
      render: (_: unknown, row: { team: string }) => {
        const tot = teamAgg[row.team]?.totalReq || 0
        return <span style={{ fontWeight: 800, color: tot > 0 ? '#dc2626' : '#10b981' }}>{tot > 0 ? `+${tot.toLocaleString()}` : '재고 충당'}</span>
      },
    },
  ]

  const targetMonthColumns = targetMonths.map(m => {
    const isFuture = m > nowMonth
    return {
      title: (
        <span style={{ fontSize: '0.74rem', color: m === nowMonth ? '#2563eb' : isFuture ? '#7c3aed' : '#374151', fontWeight: m === nowMonth ? 700 : 500 }}>
          {m === nowMonth ? `${m.slice(5)}월(현재)` : `${m.slice(5)}월`}{isFuture && <><br /><span style={{ fontSize: '0.6rem', fontWeight: 400 }}>필요입고</span></>}
        </span>
      ),
      key: m, width: 96, align: 'center' as const,
      onCell: (record: OutboundTargetRow) => isFuture
        ? { style: { background: '#faf5ff' } }
        : {
            style: { cursor: 'pointer' },
            onClick: (e: React.MouseEvent) => { e.stopPropagation(); setShareProduct(record.name); setShareMonth(m) },
          },
      render: (_: unknown, r: OutboundTargetRow) => {
        const target = r.target_by_month[m] || 0
        if (isFuture) {
          // 미래월: 수요(=max(목표, 과거 월평균)) 충당에 필요한 추가 입고량
          const ci = coverInfo(r)
          const d = ci.demandByMonth[m] ?? 0
          if (!d) return <span style={{ color: '#e5e7eb' }}>·</span>
          const need = ci.req[m] ?? 0
          const trendDriven = d > target  // 과거 추세가 목표보다 커서 수요를 끌어올린 경우
          return (
            <Tooltip title={`목표 ${target.toLocaleString()} · 과거 월평균 ${ci.avgActual.toLocaleString()} → 적용 수요 ${d.toLocaleString()}`}>
              <div>
                <div style={{ fontSize: '0.64rem', color: '#9ca3af' }}>목표 {target.toLocaleString()}</div>
                <div style={{ fontSize: '0.64rem', color: trendDriven ? '#7c3aed' : '#6b7280' }}>수요 {d.toLocaleString()}{trendDriven ? '*' : ''}</div>
                <div style={{ fontWeight: 700, fontSize: '0.8rem', color: need > 0 ? '#dc2626' : '#10b981' }}>
                  {need > 0 ? `+${need.toLocaleString()}` : '충당'}
                </div>
              </div>
            </Tooltip>
          )
        }
        // 과거/현재월: 실제 출고 / 목표 · 달성률
        // 전체(병합) 행이면 BD·BE를 나눠서 표시
        const bt = (r as MergedRow)._byTeam
        const teamsHere = (r as MergedRow)._teams || []
        if (bt && teamsHere.length > 1) {
          const lines = teamsHere.map(team => {
            const tt = bt[team]?.tgt[m] || 0, aa = bt[team]?.act[m] || 0
            if (!tt && !aa) return null
            const p = tt > 0 ? Math.round((aa / tt) * 100) : null
            const ovd = isOverridden(team, r.name, m)
            return (
              <Tooltip key={team} title={`${targets?.team_labels?.[team] || team}: ${aa.toLocaleString()} / ${tt.toLocaleString()}${ovd ? ' (수기 수정됨)' : ''}`}>
                <div style={{ fontSize: '0.72rem', lineHeight: 1.3 }}>
                  <span style={{ color: '#9ca3af' }}>{team}</span> <b style={{ color: achColor(p) }}>{p == null ? '—' : `${p}%`}</b>{ovd && <span style={{ color: '#f59e0b' }}> ✎</span>}
                </div>
              </Tooltip>
            )
          }).filter(Boolean)
          if (!lines.length) return <span style={{ color: '#e5e7eb' }}>·</span>
          return <div style={{ background: m === nowMonth ? '#eff6ff' : undefined, borderRadius: 4, padding: '1px 0' }}>{lines}</div>
        }
        const actual = r.actual_by_month[m] || 0
        const pct = target > 0 ? Math.round((actual / target) * 100) : null
        if (!target && !actual) return <span style={{ color: '#e5e7eb' }}>·</span>
        const ovd = isOverridden(r.team, r.name, m)
        return (
          <div style={{ background: m === nowMonth ? '#eff6ff' : undefined, borderRadius: 4, padding: '1px 0' }}>
            <div style={{ fontSize: '0.7rem', color: '#6b7280' }}>{actual.toLocaleString()} / {target ? target.toLocaleString() : '—'}{ovd && <Tooltip title="수기 수정된 목표"><span style={{ color: '#f59e0b' }}> ✎</span></Tooltip>}</div>
            <div style={{ fontWeight: 700, fontSize: '0.8rem', color: achColor(pct) }}>{pct == null ? '—' : `${pct}%`}</div>
          </div>
        )
      },
    }
  })

  const targetColumns = [
    {
      title: '팀', dataIndex: 'team', key: 'team', width: 110, fixed: 'left' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) => a.team.localeCompare(b.team),
      render: (v: string) => v.split('·').map(t => (
        <Tag key={t} color="blue" style={{ fontSize: '0.7rem', marginRight: 2 }}>{targets?.team_labels?.[t] || t}</Tag>
      )),
    },
    {
      title: '브랜드', dataIndex: 'brand', key: 'brand', width: 96, fixed: 'left' as const, ellipsis: true,
      render: (v: string) => <span style={{ fontSize: '0.78rem', color: '#6b7280' }}>{v}</span>,
    },
    {
      title: '공통상품명', dataIndex: 'name', key: 'name', width: 260, fixed: 'left' as const,
      render: (v: string, r: OutboundTargetRow) => (
        <span style={{ fontSize: '0.82rem' }}>
          {v}{' '}
          {r.matched
            ? <Tooltip title={`매칭된 박스히어로 품목: ${r.matched_names.join(', ')}`}><Tag color="green" style={{ fontSize: '0.62rem', padding: '0 3px' }}>매칭</Tag></Tooltip>
            : <Tooltip title="이름이 비슷한 박스히어로 품목을 찾지 못함 → 실제 출고 0으로 표시됩니다"><Tag style={{ fontSize: '0.62rem', padding: '0 3px' }}>미매칭</Tag></Tooltip>}
          <Button size="small" type="link" icon={<EditOutlined />}
            onClick={(e) => { e.stopPropagation(); openEditModal(v) }}
            style={{ padding: '0 4px', height: 'auto', fontSize: '0.7rem' }}>목표수정</Button>
        </span>
      ),
    },
    {
      title: <Tooltip title="현재 박스히어로 재고 (매칭 품목 합, 팀 공유)"><span style={{ fontSize: '0.74rem' }}>기초재고</span></Tooltip>,
      dataIndex: 'stock', key: 'stock', width: 80, align: 'right' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) => a.stock - b.stock,
      render: (v: number) => <span style={{ fontWeight: 700, color: '#10b981' }}>{fmt(v)}</span>,
    },
    ...targetMonthColumns,
    {
      title: <Tooltip title="보충 입고가 없을 때, 현재 재고로 미래 월 수요(=목표·과거 월평균 중 큰 값)를 충당하다 재고가 바닥나는 예상 월"><span style={{ fontSize: '0.74rem' }}>재고 쇼트 예상</span></Tooltip>,
      key: 'short', width: 120, align: 'center' as const, fixed: 'right' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) =>
        (coverInfo(a).shortMonth || '9999') < (coverInfo(b).shortMonth || '9999') ? -1 : 1,
      render: (_: unknown, r: OutboundTargetRow) => {
        if (!futTM.length) return <span style={{ color: '#d1d5db' }}>—</span>
        const { shortMonth, balByMonth, avgActual } = coverInfo(r)
        const tip = `과거 월평균 ${avgActual.toLocaleString()} · 월말 잔여 — ` + futTM.map(m => `${m}: ${(balByMonth[m] ?? 0).toLocaleString()}`).join('  ·  ')
        if (!shortMonth) {
          return <Tooltip title={`월말 잔여 — ${tip}`}><span style={{ fontWeight: 700, color: '#10b981' }}>여유</span></Tooltip>
        }
        return (
          <Tooltip title={`월말 잔여 — ${tip}`}>
            <span style={{ fontWeight: 800, color: '#dc2626' }}>{shortMonth.slice(2)} 쇼트</span>
          </Tooltip>
        )
      },
    },
    {
      title: <Tooltip title="미래 수요(목표·과거 월평균 중 큰 값)를 모두 충당하려면 추가로 필요한 총 입고량 (현재 재고 차감 후)"><span style={{ fontSize: '0.74rem' }}>추가입고 필요</span></Tooltip>,
      key: 'cover', width: 130, align: 'center' as const, fixed: 'right' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) => coverInfo(a).totalReq - coverInfo(b).totalReq,
      render: (_: unknown, r: OutboundTargetRow) => {
        const { carryover, totalReq } = coverInfo(r)
        return (
          <div>
            <div style={{ fontWeight: 800, fontSize: '0.82rem', color: totalReq > 0 ? '#dc2626' : '#10b981' }}>
              {futTM.length === 0 ? '—' : totalReq > 0 ? `+${totalReq.toLocaleString()}` : '재고 충당'}
            </div>
            {carryover > 0
              ? <div style={{ fontSize: '0.64rem', color: '#10b981' }}>선출고 +{carryover.toLocaleString()}</div>
              : carryover < 0 ? <div style={{ fontSize: '0.64rem', color: '#dc2626' }}>미달 {carryover.toLocaleString()}</div> : null}
          </div>
        )
      },
    },
    {
      title: <Tooltip title="현재까지 도래한 목표월 누적 실제출고 ÷ 누적 목표 (미래 목표는 제외)"><span style={{ fontSize: '0.74rem' }}>종합 달성률</span></Tooltip>,
      key: 'total', width: 110, align: 'center' as const, fixed: 'right' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) =>
        pastActual(a) / (pastTargetSum(a) || 1) - pastActual(b) / (pastTargetSum(b) || 1),
      render: (_: unknown, r: OutboundTargetRow) => {
        const tgt = pastTargetSum(r)
        const act = pastActual(r)
        const pct = tgt > 0 ? Math.round((act / tgt) * 100) : null
        return (
          <div>
            <div style={{ fontSize: '0.7rem', color: '#6b7280' }}>{act.toLocaleString()} / {tgt.toLocaleString()}</div>
            <div style={{ fontWeight: 800, color: achColor(pct) }}>{pct == null ? '—' : `${pct}%`}</div>
          </div>
        )
      },
    },
    {
      title: <Tooltip title={`전월(${prevMonth}) 대비 이번달(${nowMonth}) 출고 증감 — 성장/후퇴`}><span style={{ fontSize: '0.74rem' }}>전월대비</span></Tooltip>,
      key: 'mom', width: 90, align: 'center' as const, fixed: 'right' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) => {
        const g = (r: OutboundTargetRow) => { const p = r.actual_by_month[prevMonth] || 0; return p > 0 ? (r.actual_by_month[nowMonth] || 0) / p : 99 }
        return g(a) - g(b)
      },
      render: (_: unknown, r: OutboundTargetRow) => growthCell(r, prevMonth),
    },
    {
      title: <Tooltip title={`3개월 전(${mon3ago}) 대비 이번달(${nowMonth}) 출고 증감 (3개월 추세)`}><span style={{ fontSize: '0.74rem' }}>3개월추세</span></Tooltip>,
      key: 'mom3', width: 90, align: 'center' as const, fixed: 'right' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) => {
        const g = (r: OutboundTargetRow) => { const p = r.actual_by_month[mon3ago] || 0; return p > 0 ? (r.actual_by_month[nowMonth] || 0) / p : 99 }
        return g(a) - g(b)
      },
      render: (_: unknown, r: OutboundTargetRow) => growthCell(r, mon3ago),
    },
    {
      title: <Tooltip title={`전년 동월(${yoyMonth}) 대비 이번달(${nowMonth}) 출고 증감`}><span style={{ fontSize: '0.74rem' }}>전년동월</span></Tooltip>,
      key: 'yoy', width: 90, align: 'center' as const, fixed: 'right' as const,
      sorter: (a: OutboundTargetRow, b: OutboundTargetRow) => {
        const g = (r: OutboundTargetRow) => { const p = r.actual_by_month[yoyMonth] || 0; return p > 0 ? (r.actual_by_month[nowMonth] || 0) / p : 99 }
        return g(a) - g(b)
      },
      render: (_: unknown, r: OutboundTargetRow) => growthCell(r, yoyMonth),
    },
  ]

  const filteredItems = (result?.items || []).filter(r =>
    !search || r.name.includes(search) || r.sku.includes(search))
  const filteredPartners = (result?.partners || []).filter(r =>
    !search || r.partner.includes(search))

  function handleExport() {
    if (!result) return
    const bl = buckets.map(b => bucketLabel(b, result.period))
    let header: string[]
    let rows: (string | number)[][]
    if (groupBy === 'item') {
      const futH = futureMonths.map(m => `${m} 잔여(예측)`)
      header = ['SKU', '상품명', ...bl, '현재고', ...futH, '기간합계', '일평균', '월평균', `${result.forecast_months}개월예상`, '소진예상(개월)', '예상잔여']
      rows = filteredItems.map(r => {
        const out = Math.round(r.monthly_avg)
        const fut = futureMonths.map((_, i) => out <= 0 ? '' : Math.max(0, r.stock - out * (i + 1)))
        return [
          r.sku, r.name, ...buckets.map(b => r.by_bucket[b] || 0), r.stock, ...fut,
          r.total_out, r.daily_avg, r.monthly_avg, r.forecast_total,
          r.deplete_months ?? '', r.remaining_after,
        ]
      })
    } else if (groupBy === 'partner') {
      header = ['거래처', '품목수', ...bl, '기간합계', '일평균', '월평균', `${result.forecast_months}개월예상`]
      rows = filteredPartners.map(r => [
        r.partner, r.sku_count, ...buckets.map(b => r.by_bucket[b] || 0),
        r.total_out, r.daily_avg, r.monthly_avg, r.forecast_total,
      ])
    } else {
      // 목표 대비: 과거월=[출고,목표,달성률%], 미래월=[목표,재고잔여], + 커버
      header = ['팀', '브랜드', '공통상품명', '매칭', '기초재고',
        ...pastTM.flatMap(m => [`${m} 출고`, `${m} 목표`, `${m} 달성률%`]),
        ...futTM.flatMap(m => [`${m} 목표`, `${m} 필요입고`, `${m} 잔여(보충없음)`]),
        '재고쇼트 예상', '추가입고 필요(합)', '선출고/미달', '누적 출고', '누적 목표', '종합 달성률%', '전월대비%', '3개월추세%', '전년동월%']
      const pctChg = (cur: number, base: number) => base > 0 ? Math.round((cur - base) / base * 100) : ''
      rows = displayRows.map(r => {
        const tgt = sumVals(r.target_by_month), act = sumVals(r.actual_by_month)
        const ci = coverInfo(r)
        const cur = r.actual_by_month[nowMonth] || 0
        return [
          r.team, r.brand, r.name, r.matched ? 'O' : 'X', r.stock,
          ...pastTM.flatMap(m => {
            const t = r.target_by_month[m] || 0, a2 = r.actual_by_month[m] || 0
            return [a2, t, t > 0 ? Math.round((a2 / t) * 100) : '']
          }),
          ...futTM.flatMap(m => [r.target_by_month[m] || 0, ci.req[m] ?? '', ci.balByMonth[m] ?? '']),
          ci.shortMonth || '여유', ci.totalReq, ci.carryover,
          act, tgt, tgt > 0 ? Math.round((act / tgt) * 100) : '',
          pctChg(cur, r.actual_by_month[prevMonth] || 0),
          pctChg(cur, r.actual_by_month[mon3ago] || 0),
          pctChg(cur, r.actual_by_month[yoyMonth] || 0),
        ]
      })
    }
    const csv = [header, ...rows].map(r => r.map(v => `"${v}"`).join(',')).join('\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const tag = groupBy === 'item' ? '품목별' : groupBy === 'partner' ? '거래처별' : '목표대비'
    a.download = `출고예측_${tag}_${fromDate}_${toDate}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const periodLabel = { day: '일', week: '주', month: '월' }[period]

  // ── 추세선 차트 시리즈 ────────────────────────────────────────
  const currentRows: (OutboundItemRow | OutboundPartnerRow)[] = groupBy === 'item' ? filteredItems : filteredPartners
  const rowKeyOf = (r: OutboundItemRow | OutboundPartnerRow) =>
    'sku' in r ? r.sku : r.partner
  const selectedRow = selectedKey ? currentRows.find(r => rowKeyOf(r) === selectedKey) || null : null

  const actualSeries = buckets.map(bk => ({
    label: result ? bucketLabel(bk, result.period) : bk,
    value: selectedRow
      ? (selectedRow.by_bucket[bk] || 0)
      : currentRows.reduce((s, r) => s + (r.by_bucket[bk] || 0), 0),
  }))
  const actualSum = actualSeries.reduce((s, p) => s + p.value, 0)
  const avgPerBucket = buckets.length ? actualSum / buckets.length : 0
  let fcCount = forecastMonths
  if (period === 'week') fcCount = Math.round(forecastMonths * 30.44 / 7)
  else if (period === 'day') fcCount = Math.min(92, Math.round(forecastMonths * 30.44))
  const forecastSeries = (result && buckets.length)
    ? futureBuckets(buckets[buckets.length - 1], period, fcCount).map(k => ({
        label: bucketLabel(k, period),
        value: Math.round(avgPerBucket),
      }))
    : []
  const chartTitle = selectedRow
    ? ('name' in selectedRow ? `${selectedRow.name || selectedRow.sku}` : selectedRow.partner)
    : `전체 합계 (${groupBy === 'item' ? `${currentRows.length}개 품목` : `${currentRows.length}개 거래처`})`

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">출고 예측</h1>
        <p className="page-desc">박스히어로 거래처별·품목별 {periodLabel}단위 출고 추세 + 재고 소진 예측</p>
      </div>

      {/* 컨트롤 */}
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginBottom: 16, display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>기준</div>
          <Radio.Group value={groupBy} onChange={e => { setGroupBy(e.target.value); setSelectedKey(null) }} size="small">
            <Radio.Button value="item">품목별</Radio.Button>
            <Radio.Button value="partner">거래처별</Radio.Button>
            <Tooltip title={targetStatus.loaded ? '팀별 목표 대비 달성률' : '먼저 목표 파일을 업로드하세요'}>
              <Radio.Button value="target" disabled={!targetStatus.loaded}>목표 대비</Radio.Button>
            </Tooltip>
          </Radio.Group>
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>집계 단위</div>
          <Radio.Group value={period} onChange={e => setPeriod(e.target.value)} size="small">
            <Radio.Button value="day">일</Radio.Button>
            <Radio.Button value="week">주</Radio.Button>
            <Radio.Button value="month">월</Radio.Button>
          </Radio.Group>
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>시작일</div>
          <DatePicker size="small" value={dayjs(fromDate)} onChange={d => d && setFromDate(d.format('YYYY-MM-DD'))} allowClear={false} />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>종료일</div>
          <DatePicker size="small" value={dayjs(toDate)} onChange={d => d && setToDate(d.format('YYYY-MM-DD'))} allowClear={false} />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>예측 개월</div>
          <InputNumber size="small" min={1} max={24} value={forecastMonths} onChange={v => setForecastMonths(v || 3)} style={{ width: 80 }} />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>
            <Tooltip title="세트로 출고된 수량을 구성 단품으로 풀어서 품목별에 합산합니다 (목표는 단품 기준)">세트→단품</Tooltip>
          </div>
          <Switch size="small" checked={expandSets}
            onChange={v => { setExpandSets(v); if (result && config.api_token) handleSearch(v) }}
            checkedChildren="분해" unCheckedChildren="원본" />
        </div>
        <Button type="primary" icon={<SearchOutlined />} onClick={() => handleSearch()} loading={loading} disabled={!config.api_token}>
          조회
        </Button>
        {result && (
          <Button icon={<DownloadOutlined />} onClick={handleExport} size="small">CSV 다운로드</Button>
        )}
      </div>

      {/* 목표치 + 거래처-팀 매핑 */}
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '12px 20px', marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <AimOutlined style={{ color: '#2563eb' }} />
        <span style={{ fontWeight: 700, fontSize: '0.85rem' }}>판매 목표치</span>
        {targetStatus.loaded ? (
          <span style={{ fontSize: '0.78rem', color: '#6b7280' }}>
            {targetStatus.filename} · {targetStatus.product_count}개 품목 · 팀 {(targetStatus.teams || []).map(t => targetStatus.team_labels?.[t] ? `${targetStatus.team_labels[t]}(${t})` : t).join(', ')} · {targetStatus.months?.[0]}~{targetStatus.months?.slice(-1)[0]}
          </span>
        ) : (
          <span style={{ fontSize: '0.78rem', color: '#9ca3af' }}>목표 파일(판매예상량 xlsx)을 업로드하면 달성률을 볼 수 있습니다</span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Upload accept=".xlsx,.xls" showUploadList={false} beforeUpload={handleTargetUpload}>
            <Button size="small" icon={<UploadOutlined />} loading={uploadingTarget}>
              {targetStatus.loaded ? '목표 교체' : '목표 업로드'}
            </Button>
          </Upload>
          <Button size="small" icon={<ApiOutlined />} onClick={openMapModal} disabled={!targetStatus.loaded}>
            거래처-팀 매핑
          </Button>
          <Button size="small" icon={<AppstoreAddOutlined />} onClick={openSetModal}>
            세트 구성
          </Button>
          {targetStatus.loaded && (
            <Button size="small" danger type="text" onClick={handleTargetDelete}>삭제</Button>
          )}
        </div>
      </div>

      {!config.api_token && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="박스히어로 토큰이 필요합니다. 설정에서 먼저 연동해 주세요." />
      )}
      {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} />}

      {loading && (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#6b7280', fontSize: '0.85rem' }}>
            출고 거래 상세를 수집·집계 중입니다. 기간이 길면 1~2분 소요될 수 있습니다.
          </div>
        </div>
      )}

      {result && !loading && groupBy !== 'target' && (
        <>
          {/* 요약 카드 */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
            {[
              { label: '총 출고량', value: result.summary.grand_total.toLocaleString(), sub: `${result.days_span}일간`, color: '#ef4444' },
              { label: '일평균 출고', value: result.summary.daily_avg.toLocaleString(), sub: `월평균 ${result.summary.monthly_avg.toLocaleString()}`, color: '#374151' },
              { label: `${result.forecast_months}개월 예상 출고`, value: result.summary.forecast_total.toLocaleString(), sub: '현 추세 연장', color: '#2563eb' },
              { label: '품목 / 거래처', value: `${result.summary.item_count} / ${result.summary.partner_count}`, sub: `거래 ${result.summary.tx_count}건`, color: '#374151' },
              { label: '소진 임박 품목', value: result.summary.deplete_soon.toLocaleString(), sub: `${result.forecast_months}개월 내 소진`, color: '#f59e0b' },
            ].map(c => (
              <div key={c.label} style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: '10px 16px', minWidth: 130 }}>
                <div style={{ fontSize: '0.72rem', color: '#6b7280', fontWeight: 600, marginBottom: 4 }}>{c.label}</div>
                <div style={{ fontSize: '1.25rem', fontWeight: 800, color: c.color }}>{c.value}</div>
                <div style={{ fontSize: '0.66rem', color: '#9ca3af', marginTop: 2 }}>{c.sub}</div>
              </div>
            ))}
          </div>

          {/* 추세선 차트 */}
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8, gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: '0.92rem' }}>📉 {periodLabel}단위 출고 추세선</span>
              <span style={{ fontSize: '0.82rem', color: '#374151', fontWeight: 600 }}>{chartTitle}</span>
              {selectedKey && (
                <Button size="small" type="link" onClick={() => setSelectedKey(null)} style={{ padding: 0 }}>
                  전체 합계 보기
                </Button>
              )}
              <span style={{ fontSize: '0.72rem', color: '#9ca3af', marginLeft: 'auto' }}>
                아래 표에서 행을 클릭하면 해당 {groupBy === 'item' ? '품목' : '거래처'} 추세를 봅니다
              </span>
            </div>
            <TrendChart actual={actualSeries} forecast={forecastSeries} unitLabel="개" />
          </div>

          {/* 테이블 */}
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 10 }}>
              <span style={{ fontWeight: 700, fontSize: '0.92rem' }}>
                📈 {groupBy === 'item' ? '품목별' : '거래처별'} {periodLabel}단위 출고 + 예측
              </span>
              {groupBy === 'item' && result.expand_sets && (
                <Tag color="purple" style={{ fontSize: '0.68rem' }}>세트→단품 분해됨</Tag>
              )}
              {groupBy === 'item' && futureColumns.length > 0 && (
                <span style={{ fontSize: '0.72rem', color: '#9ca3af' }}>
                  지난 달 = 실제 출고 · <span style={{ color: '#2563eb' }}>파란색 = 예측 잔여재고</span>(현재고에서 추세대로 차감, 0이면 소진)
                </span>
              )}
              <Input.Search
                size="small"
                placeholder={groupBy === 'item' ? 'SKU 또는 상품명 검색' : '거래처 검색'}
                value={search}
                onChange={e => setSearch(e.target.value)}
                style={{ width: 220, marginLeft: 'auto' }}
              />
            </div>
            {groupBy === 'item' ? (
              <Table
                dataSource={filteredItems}
                columns={itemColumns}
                rowKey="sku"
                size="small"
                scroll={{ x: 'max-content' }}
                pagination={{ pageSize: 50, showSizeChanger: true }}
                onRow={r => ({
                  onClick: () => setSelectedKey(k => k === r.sku ? null : r.sku),
                  style: { cursor: 'pointer', background: selectedKey === r.sku ? '#eff6ff' : undefined },
                })}
              />
            ) : (
              <Table
                dataSource={filteredPartners}
                columns={partnerColumns}
                rowKey="partner"
                size="small"
                scroll={{ x: 'max-content' }}
                pagination={{ pageSize: 50, showSizeChanger: true }}
                onRow={r => ({
                  onClick: () => setSelectedKey(k => k === r.partner ? null : r.partner),
                  style: { cursor: 'pointer', background: selectedKey === r.partner ? '#eff6ff' : undefined },
                })}
              />
            )}
          </div>
        </>
      )}

      {result && !loading && groupBy === 'target' && targets?.enabled && (
        <>
          {/* 팀별 종합 달성률 카드 (현재월 + 누적) */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
            {(targets.teams || []).map(team => {
              const a = teamAgg[team]
              const actNow = a?.act[nowMonth] || 0
              const tgtNow = a?.tgt[nowMonth] || 0
              const pctNow = tgtNow > 0 ? Math.round((actNow / tgtNow) * 100) : null
              const pctCum = a && a.pastTgt > 0 ? Math.round((a.pastAct / a.pastTgt) * 100) : null
              const active = teamFilter === team
              return (
                <div key={team} onClick={() => setTeamFilter(active ? '' : team)}
                  style={{ background: '#fff', borderRadius: 10, border: active ? '2px solid #2563eb' : '1px solid #e5e7eb', padding: '10px 16px', minWidth: 180, cursor: 'pointer' }}>
                  <div style={{ fontSize: '0.74rem', color: '#374151', fontWeight: 700, marginBottom: 4 }}>
                    {targets.team_labels?.[team] || team} {active && <Tag color="blue" style={{ fontSize: '0.6rem' }}>필터중</Tag>}
                  </div>
                  <div style={{ display: 'flex', gap: 16, alignItems: 'baseline' }}>
                    <div>
                      <div style={{ fontSize: '1.4rem', fontWeight: 800, color: achColor(pctNow) }}>{pctNow == null ? '—' : `${pctNow}%`}</div>
                      <div style={{ fontSize: '0.62rem', color: '#9ca3af' }}>{nowMonth.slice(5)}월 ({actNow.toLocaleString()}/{tgtNow.toLocaleString()})</div>
                    </div>
                    <div>
                      <div style={{ fontSize: '1.1rem', fontWeight: 700, color: achColor(pctCum) }}>{pctCum == null ? '—' : `${pctCum}%`}</div>
                      <div style={{ fontSize: '0.62rem', color: '#9ca3af' }}>누적</div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* 팀별 × 월별 달성률 요약 테이블 */}
          {(targets.teams?.length ?? 0) > 0 && (
            <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '14px 20px', marginBottom: 14 }}>
              <div style={{ fontWeight: 700, fontSize: '0.88rem', marginBottom: 8 }}>📊 팀별 목표 대비 달성률 (월별 · 팀 합계)</div>
              <Table
                dataSource={(targets.teams || []).map(t => ({ team: t }))}
                columns={teamSummaryColumns}
                rowKey="team"
                size="small"
                pagination={false}
                scroll={{ x: 'max-content' }}
                onRow={row => ({
                  onClick: () => setTeamFilter(teamFilter === row.team ? '' : row.team),
                  style: { cursor: 'pointer', background: teamFilter === row.team ? '#eff6ff' : undefined },
                })}
              />
            </div>
          )}

          {(targets.unmapped_partners?.length ?? 0) > 0 && (
            <Alert
              type="warning" showIcon style={{ marginBottom: 12 }}
              message={`아직 팀 미지정 거래처 ${targets.unmapped_partners!.length}곳 — 출고가 달성률에 안 잡힙니다`}
              description={
                <span style={{ fontSize: '0.78rem' }}>
                  {targets.unmapped_partners!.slice(0, 8).map(p => `${p.partner}(${p.total_out.toLocaleString()})`).join(', ')}
                  {targets.unmapped_partners!.length > 8 ? ' …' : ''}
                  {' — '}<a onClick={openMapModal}>팀에 매핑하거나 '제외' 처리</a>
                </span>
              }
            />
          )}

          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: '0.92rem' }}>🎯 팀별 목표 대비 달성률 (월간)</span>
              {futTM.length > 0 && (() => {
                const shortCnt = displayRows.filter(r => coverInfo(r).shortMonth).length
                return shortCnt > 0
                  ? <Tag color="red" style={{ fontSize: '0.68rem' }}>⚠ 재고 쇼트 예상 {shortCnt}건</Tag>
                  : <Tag color="green" style={{ fontSize: '0.68rem' }}>재고 여유</Tag>
              })()}
              <Radio.Group size="small" value={teamFilter} onChange={e => setTeamFilter(e.target.value)}>
                <Radio.Button value="">전체</Radio.Button>
                {(targets.teams || []).map(t => (
                  <Radio.Button key={t} value={t}>{targets.team_labels?.[t] || t}</Radio.Button>
                ))}
              </Radio.Group>
              {(targets.set_expanded_qty ?? 0) > 0 && (
                <Tooltip title="세트 출고를 구성 단품 수량으로 분해해 반영했습니다">
                  <Tag color="purple" style={{ fontSize: '0.68rem' }}>세트 분해 {targets.set_expanded_qty!.toLocaleString()}개</Tag>
                </Tooltip>
              )}
              <Input.Search
                size="small" placeholder="상품명 검색"
                value={search} onChange={e => setSearch(e.target.value)}
                style={{ width: 180, marginLeft: 'auto' }}
              />
            </div>
            <div style={{ fontSize: '0.72rem', color: '#9ca3af', marginBottom: 8 }}>
              {teamFilter ? '팀별' : '전체 = 제품 1행(달성 칸은 BD·BE 분리 표시)'} · 지난·이번달 = 출고/목표·달성률 · <span style={{ color: '#7c3aed' }}>보라(미래) = 필요한 추가 입고량</span>(현재고로 충당되면 '충당') · <span style={{ color: '#7c3aed' }}>수요* = 목표·과거 월평균 중 큰 값</span> · <b>행 클릭=전체기간 점유율 · 월 칸 클릭=그 달 점유율</b>
            </div>
            <Table
              dataSource={displayRows}
              columns={targetColumns}
              rowKey={(r) => `${r.team}|${r.name}`}
              size="small"
              scroll={{ x: 'max-content' }}
              pagination={{ pageSize: 50, showSizeChanger: true }}
              onRow={r => ({
                onClick: () => { setShareProduct(r.name); setShareMonth(null) },
                style: { cursor: 'pointer' },
              })}
            />
          </div>
        </>
      )}

      {/* 거래처 → 팀 매핑 모달 */}
      <Modal
        title="거래처 → 팀 매핑"
        open={mapOpen}
        onCancel={() => setMapOpen(false)}
        onOk={saveMap}
        confirmLoading={savingMap}
        okText="저장"
        cancelText="닫기"
        width={620}
      >
        <p style={{ fontSize: '0.8rem', color: '#6b7280', marginTop: 0, marginBottom: 10 }}>
          각 거래처의 팀 버튼을 <b>한 번 클릭</b>해 연결하세요. 매핑된 팀의 출고만 달성률에 반영됩니다.
          목표 추적 대상이 아닌 거래처는 <b>'제외'</b>로 지정하면 달성률·경고에서 완전히 빠집니다.
        </p>

        {/* 툴바: 검색 · 미매핑만 · 추천 · 진행도 */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
          <Input.Search size="small" placeholder="거래처 검색" allowClear
            value={mapSearch} onChange={e => setMapSearch(e.target.value)} style={{ width: 170 }} />
          <Button size="small" type={mapUnmappedOnly ? 'primary' : 'default'} ghost={mapUnmappedOnly}
            onClick={() => setMapUnmappedOnly(v => !v)}>미매핑만</Button>
          <Tooltip title="쿠팡·카카오·토스·홈쇼핑 등 알려진 채널을 자동 채웁니다 (확인 후 저장)">
            <Button size="small" icon={<AimOutlined />} onClick={autoSuggestMap}>추천 자동매핑</Button>
          </Tooltip>
          <span style={{ marginLeft: 'auto', fontSize: '0.76rem', color: '#6b7280' }}>
            매핑 {mapPartners.filter(p => mapDraft[p]).length} / {mapPartners.length}
          </span>
        </div>

        <div style={{ maxHeight: 440, overflow: 'auto' }}>
          <Table
            size="small"
            pagination={false}
            rowKey="partner"
            dataSource={mapPartners
              .filter(p => !mapSearch || p.includes(mapSearch))
              .filter(p => !mapUnmappedOnly || !mapDraft[p])
              .map(p => ({ partner: p }))}
            columns={[
              { title: '거래처', dataIndex: 'partner', key: 'partner', render: (v: string) => <span style={{ fontSize: '0.84rem' }}>{v}</span> },
              {
                title: '팀 (클릭해서 지정)', key: 'team',
                render: (_: unknown, r: { partner: string }) => (
                  <Radio.Group
                    size="small" optionType="button" buttonStyle="solid"
                    value={mapDraft[r.partner] || ''}
                    onChange={e => {
                      const v = e.target.value
                      setMapDraft(d => { const n = { ...d }; if (v) n[r.partner] = v; else delete n[r.partner]; return n })
                    }}
                  >
                    {mapTeams.map(t => <Radio.Button key={t} value={t}>{teamLabel(t)}</Radio.Button>)}
                    <Radio.Button value={EXCLUDE}>제외</Radio.Button>
                    <Radio.Button value="" style={{ color: '#9ca3af' }}>미지정</Radio.Button>
                  </Radio.Group>
                ),
              },
            ]}
          />
        </div>
      </Modal>

      {/* 세트 → 단품 구성(BOM) 모달 */}
      <Modal
        title="세트 → 단품 구성"
        open={setOpen}
        onCancel={() => setSetOpen(false)}
        footer={[
          <Button key="close" onClick={() => setSetOpen(false)}>닫기</Button>,
          <Button key="apply" type="primary" onClick={async () => { setSetOpen(false); if (result) await handleSearch() }}>적용하고 새로고침</Button>,
        ]}
        width={720}
      >
        <p style={{ fontSize: '0.8rem', color: '#6b7280', marginTop: 0 }}>
          세트로 출고된 수량을 구성 단품 출고로 분해해 목표 달성률에 반영합니다. 예: <b>메노포즈 3입세트</b> 출고 1 → <b>메노포즈</b> 출고 3.
          세트/단품은 박스히어로 품목명(또는 SKU) 기준으로 매칭됩니다.
        </p>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12, background: '#f9fafb', padding: 10, borderRadius: 8 }}>
          <Field label="세트명">
            <Input size="small" style={{ width: 160 }} placeholder="예: 메노포즈 3입세트"
              value={bomForm.set_name} onChange={e => setBomForm({ ...bomForm, set_name: e.target.value })} />
          </Field>
          <Field label="세트 SKU(선택)">
            <Input size="small" style={{ width: 110 }} value={bomForm.set_sku}
              onChange={e => setBomForm({ ...bomForm, set_sku: e.target.value })} />
          </Field>
          <Field label="구성 단품명">
            <Input size="small" style={{ width: 150 }} placeholder="예: 메노포즈"
              value={bomForm.component_name} onChange={e => setBomForm({ ...bomForm, component_name: e.target.value })} />
          </Field>
          <Field label="단품 SKU(선택)">
            <Input size="small" style={{ width: 100 }} value={bomForm.component_sku}
              onChange={e => setBomForm({ ...bomForm, component_sku: e.target.value })} />
          </Field>
          <Field label="세트당 수량">
            <InputNumber size="small" min={0.01} step={1} style={{ width: 90 }} value={bomForm.qty_per_set}
              onChange={v => setBomForm({ ...bomForm, qty_per_set: Number(v) || 1 })} />
          </Field>
          <Button size="small" type="primary" onClick={addBom} loading={savingBom}>추가</Button>
        </div>
        <Table
          size="small"
          pagination={false}
          rowKey="id"
          dataSource={setBoms}
          scroll={{ y: 300 }}
          columns={[
            { title: '세트', key: 'set', render: (_: unknown, b: SetBom) => <span style={{ fontSize: '0.8rem' }}>{b.set_name || b.set_sku}</span> },
            { title: '구성 단품', key: 'comp', render: (_: unknown, b: SetBom) => <span style={{ fontSize: '0.8rem' }}>{b.component_name || b.component_sku}</span> },
            { title: '세트당', dataIndex: 'qty_per_set', key: 'qty', width: 70, align: 'right' as const, render: (v: number) => `×${v}` },
            {
              title: '', key: 'del', width: 44,
              render: (_: unknown, b: SetBom) => (
                <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeBom(b.id)} />
              ),
            },
          ]}
        />
      </Modal>

      {/* 거래처별 점유율 모달 (행 클릭=전체기간, 월 셀 클릭=해당 월) */}
      <Modal
        title={`거래처별 출고 점유율 — ${shareProduct || ''} (${shareMonth || '전체 기간'})`}
        open={!!shareProduct}
        onCancel={() => { setShareProduct(null); setShareMonth(null) }}
        footer={[<Button key="close" onClick={() => { setShareProduct(null); setShareMonth(null) }}>닫기</Button>]}
        width={520}
      >
        {(() => {
          const monthMode = !!shareMonth
          const share = (shareProduct && (monthMode
            ? targets?.partner_share_by_month?.[shareProduct]?.[shareMonth!]
            : targets?.partner_share?.[shareProduct])) || []
          const total = share.reduce((s, x) => s + x.qty, 0)
          if (!share.length) return <p style={{ color: '#9ca3af', fontSize: '0.85rem' }}>{monthMode ? `${shareMonth} 출고 데이터가 없습니다.` : '이 제품의 출고 거래처 데이터가 없습니다 (조회 기간 내 출고 없음).'}</p>
          // 팀별 출고 합계 (BD·BE·제외·미매핑)
          const byTeam: Record<string, number> = {}
          share.forEach(s => { const k = s.team || '__UNMAPPED__'; byTeam[k] = (byTeam[k] || 0) + s.qty })
          // 팀별 목표/실적: 월 모드=해당 월, 전체=현재까지 누적(pastTM)
          const tgtRows = (targets?.rows || []).filter(r => r.name === shareProduct)
          const teamTarget: Record<string, number> = {}
          const teamActual: Record<string, number> = {}
          tgtRows.forEach(r => {
            teamTarget[r.team] = monthMode ? (r.target_by_month[shareMonth!] || 0) : pastTM.reduce((s, m) => s + (r.target_by_month[m] || 0), 0)
            teamActual[r.team] = monthMode ? (r.actual_by_month[shareMonth!] || 0) : pastTM.reduce((s, m) => s + (r.actual_by_month[m] || 0), 0)
          })
          const totalTarget = Object.values(teamTarget).reduce((s, v) => s + v, 0)
          const order = [...(targets?.teams || []), EXCLUDE, '__UNMAPPED__']
          const teamChips = order.filter(k => byTeam[k] || teamTarget[k]).map(k => {
            const isReal = k !== EXCLUDE && k !== '__UNMAPPED__'
            const label = k === EXCLUDE ? '제외' : k === '__UNMAPPED__' ? '미매핑' : (targets?.team_labels?.[k] || k)
            const color = k === EXCLUDE ? '#9ca3af' : k === '__UNMAPPED__' ? '#f59e0b' : '#2563eb'
            const bg = k === EXCLUDE ? '#f3f4f6' : k === '__UNMAPPED__' ? '#fff7ed' : '#eff6ff'
            const qty = byTeam[k] || 0; const tgt = teamTarget[k] || 0
            const act = monthMode ? qty : (teamActual[k] || 0)  // 월 모드: 출고=실적
            const pct = total > 0 ? (qty / total * 100).toFixed(1) : '0'
            const achPct = isReal && tgt > 0 ? Math.round(act / tgt * 100) : null
            return (
              <div key={k} style={{ background: bg, border: `1px solid ${color}33`, borderRadius: 8, padding: '6px 12px', minWidth: 132 }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, color }}>{label}</div>
                {isReal ? (
                  <>
                    <div style={{ fontSize: '1.15rem', fontWeight: 800, color: achColor(achPct) }}>
                      {achPct == null ? '—' : `${achPct}%`}
                      <span style={{ fontSize: '0.6rem', color: '#9ca3af', fontWeight: 400 }}> 목표달성</span>
                    </div>
                    <div style={{ fontSize: '0.68rem', color: '#6b7280' }}>{monthMode ? '출고' : '실적'} {act.toLocaleString()} / 목표 {tgt.toLocaleString()}</div>
                    {!monthMode && (
                      <Tooltip title="조회 기간 전체 출고 (목표 시작 전 물량 포함될 수 있음)">
                        <div style={{ fontSize: '0.64rem', color: '#9ca3af' }}>조회기간 출고 {qty.toLocaleString()} ({pct}%)</div>
                      </Tooltip>
                    )}
                    {monthMode && <div style={{ fontSize: '0.64rem', color: '#9ca3af' }}>점유 {pct}%</div>}
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: '1.05rem', fontWeight: 800, color: '#111827' }}>{qty.toLocaleString()}</div>
                    <div style={{ fontSize: '0.64rem', color: '#9ca3af' }}>출고 ({pct}%)</div>
                  </>
                )}
              </div>
            )
          })
          return (
            <>
              <p style={{ fontSize: '0.78rem', color: '#6b7280', marginTop: 0, marginBottom: 8 }}>
                {monthMode ? `${shareMonth} 기준` : '조회 기간 전체'} · 총 출고 {total.toLocaleString()}개{totalTarget > 0 ? ` · 목표 ${totalTarget.toLocaleString()}개` : ''} (세트 분해 반영)
              </p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>{teamChips}</div>
              {share.map(s => {
                const pct = total > 0 ? (s.qty / total) * 100 : 0
                return (
                  <div key={s.partner} style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', marginBottom: 2 }}>
                      <span>{s.partner}{s.team === EXCLUDE
                        ? <Tag style={{ fontSize: '0.6rem', marginLeft: 4 }}>제외</Tag>
                        : s.team
                        ? <Tag color="blue" style={{ fontSize: '0.6rem', marginLeft: 4 }}>{targets?.team_labels?.[s.team] || s.team}</Tag>
                        : <Tag color="orange" style={{ fontSize: '0.6rem', marginLeft: 4 }}>미매핑</Tag>}</span>
                      <span style={{ fontWeight: 700 }}>{pct.toFixed(1)}% · {s.qty.toLocaleString()}</span>
                    </div>
                    <div style={{ background: '#f1f5f9', borderRadius: 4, height: 8 }}>
                      <div style={{ width: `${pct}%`, height: 8, borderRadius: 4, background: s.team === EXCLUDE ? '#cbd5e1' : s.team ? '#2563eb' : '#fb923c' }} />
                    </div>
                  </div>
                )
              })}
            </>
          )
        })()}
      </Modal>

      {/* 목표 수기 수정 모달 (팀 × 전체 월) */}
      <Modal
        title={`목표 수정 — ${editProduct || ''}`}
        open={!!editProduct}
        onCancel={() => setEditProduct(null)}
        onOk={saveEdit}
        confirmLoading={savingEdit}
        okText="저장"
        cancelText="닫기"
        width={Math.min(720, 200 + editTeams.length * 150)}
      >
        <p style={{ fontSize: '0.78rem', color: '#6b7280', marginTop: 0 }}>
          팀별·월별 목표 수량을 수정합니다. 변경한 셀만 저장되며, 비우면 원본(엑셀) 목표로 복귀합니다.
          현재월({dayjs().format('YYYY-MM')})은 파란색으로 표시됩니다.
        </p>
        {editTeams.length === 0 ? (
          <p style={{ color: '#9ca3af', fontSize: '0.82rem' }}>이 제품에 등록된 팀 목표가 없습니다.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: '0.82rem' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '4px 8px', color: '#6b7280' }}>월</th>
                  {editTeams.map(t => (
                    <th key={t} style={{ padding: '4px 8px' }}>
                      <Tag color="blue" style={{ margin: 0 }}>{targets?.team_labels?.[t] || t}</Tag>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(targets?.months || []).map(m => {
                  const isNow = m === dayjs().format('YYYY-MM')
                  return (
                    <tr key={m} style={{ background: isNow ? '#eff6ff' : undefined }}>
                      <td style={{ padding: '3px 8px', fontWeight: isNow ? 700 : 400, color: isNow ? '#2563eb' : '#374151' }}>
                        {m}{isNow ? ' (현재)' : ''}
                      </td>
                      {editTeams.map(t => {
                        const key = `${t}|${m}`
                        return (
                          <td key={t} style={{ padding: '3px 8px' }}>
                            <InputNumber
                              size="small" style={{ width: 130 }} min={0} placeholder="—"
                              value={editVals[key]}
                              onChange={v => setEditVals(s => ({ ...s, [key]: v as number | null }))}
                              formatter={x => `${x}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
                              parser={x => Number((x || '').replace(/,/g, ''))}
                            />
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Modal>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: '0.68rem', color: '#6b7280', marginBottom: 3, fontWeight: 600 }}>{label}</div>
      {children}
    </div>
  )
}

// trendColumns가 품목/거래처 양쪽에서 공유되므로 공통 필드만 참조하는 타입
type OutboundForecastRowLike = {
  total_out: number; daily_avg: number; monthly_avg: number; forecast_total: number
  by_bucket: Record<string, number>
}
