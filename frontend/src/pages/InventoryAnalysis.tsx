import { useState, useEffect } from 'react'
import { Button, DatePicker, Table, Tag, Alert, Spin, Input, InputNumber, Tabs, Tooltip, Progress, Select, Modal, message, Collapse, Switch } from 'antd'
import { SearchOutlined, DownloadOutlined, StopOutlined } from '@ant-design/icons'
import type { AppConfig, Location } from '../types'
import { getInventoryAnalysis, getLastInventoryAnalysis, getExcludedPartners, setExcludedPartners } from '../services/api'
import BarList from '../components/BarList'
import ExecReport from '../components/ExecReport'
import type { InventoryAnalysisResult, InventoryItemRow, InventoryPartnerRow } from '../services/api'
import dayjs from 'dayjs'

interface Props { config: AppConfig; locations: Location[] }

const STATUS_META: Record<string, { label: string; color: string }> = {
  ok: { label: '정상', color: 'green' },
  low: { label: '저회전', color: 'orange' },
  slow: { label: '부진', color: 'red' },
  no_stock: { label: '재고없음', color: 'default' },
}

function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '-'
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function downloadCsv(header: string[], rows: (string | number)[][], filename: string) {
  const csv = [header, ...rows].map(r => r.map(v => `"${v ?? ''}"`).join(',')).join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

export default function InventoryAnalysis({ config, locations }: Props) {
  const [fromDate, setFromDate] = useState(dayjs().subtract(90, 'day').format('YYYY-MM-DD'))
  const [locId, setLocId] = useState<number | null>(null)      // BH 위치 필터 (null = 전체)
  const [locInited, setLocInited] = useState(false)

  // 위치 목록 로드 시 기본값 = 호법센터
  useEffect(() => {
    if (locInited || !locations.length) return
    const hobeop = locations.find(l => l.name.includes('호법'))
    if (hobeop) setLocId(hobeop.id)
    setLocInited(true)
  }, [locations, locInited])
  const [toDate, setToDate] = useState(dayjs().format('YYYY-MM-DD'))
  const [slowDays, setSlowDays] = useState(60)
  const [lowTurnover, setLowTurnover] = useState(0.5)
  const [expandSets, setExpandSets] = useState(false)  // 세트→단품 분해 토글
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<InventoryAnalysisResult | null>(null)
  const [restoredAt, setRestoredAt] = useState<string | null>(null)  // 스냅샷 복원 시각 (새 분석 시 해제)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState('items')

  // 앱을 껐다 켜도 마지막 분석 결과 유지 — 서버 스냅샷 복원
  useEffect(() => {
    getLastInventoryAnalysis().then(snap => {
      const r = snap.result
      if (!r) return
      setResult(prev => prev ?? r)
      setRestoredAt(snap.saved_at)
      setFromDate(r.from_date)
      setToDate(r.to_date)
      setSlowDays(r.slow_days)
      setLowTurnover(r.low_turnover)
      if (typeof r.expand_sets === 'boolean') setExpandSets(r.expand_sets)
    }).catch(() => {})
  }, [])

  // 계산 제외 거래처 (수기 지정, 서버 저장)
  const [excluded, setExcluded] = useState<string[]>([])
  const [exclModalOpen, setExclModalOpen] = useState(false)
  const [exclDraft, setExclDraft] = useState<string[]>([])
  const [savingExcl, setSavingExcl] = useState(false)

  useEffect(() => {
    getExcludedPartners().then(d => setExcluded(d.partners || [])).catch(() => {})
  }, [])

  async function handleSearch(expandOverride?: boolean) {
    if (!config.api_token) return
    setLoading(true)
    setError('')
    try {
      const data = await getInventoryAnalysis({
        token: config.api_token,
        from_date: fromDate,
        to_date: toDate,
        location_ids: locId != null ? String(locId) : '',
        slow_days: slowDays,
        low_turnover: lowTurnover,
        expand_sets: expandOverride ?? expandSets,
      })
      setResult(data)
      setRestoredAt(null)
      setExcluded(data.excluded_partners || [])
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  function openExclModal() {
    setExclDraft(excluded)
    setExclModalOpen(true)
  }

  async function saveExcl() {
    setSavingExcl(true)
    try {
      const d = await setExcludedPartners(exclDraft)
      setExcluded(d.partners || [])
      setExclModalOpen(false)
      message.success('제외 거래처가 저장되었습니다. 다시 분석하면 반영됩니다.')
      if (result) handleSearch()
    } catch {
      message.error('저장 실패')
    } finally {
      setSavingExcl(false)
    }
  }

  // 거래처 탭에서 한 번에 제외 (저장 + 재분석)
  async function quickExclude(partner: string) {
    if (excluded.includes(partner)) return
    const next = [...excluded, partner]
    try {
      const d = await setExcludedPartners(next)
      setExcluded(d.partners || [])
      message.success(`'${partner}' 제외 후 다시 분석합니다`)
      handleSearch()
    } catch {
      message.error('저장 실패')
    }
  }

  // 제외 대상 후보: 현재 결과의 거래처 + 이미 제외된 것 (결과 없으면 제외분만)
  const partnerCandidates = Array.from(new Set([
    ...(result?.partners || []).map(p => p.partner),
    ...excluded,
  ])).sort()

  const itemsAll = result?.items || []
  const filteredItems = itemsAll.filter(r =>
    !search || r.name.includes(search) || r.bh_skus.some(s => s.includes(search))
  )
  const slowItems = filteredItems.filter(r => r.status === 'slow' || r.status === 'low')
  const partnersAll = (result?.partners || []).filter(r => !search || r.partner.includes(search))

  // 현재고·소비기한: 품목별 로트(유통기한별 재고)를 평탄화
  interface LotRow {
    rowKey: string; name: string; code: string; expiration_date: string
    total: number; available: number; unavail: number; dday: number | null
  }
  const today = dayjs().startOf('day')
  const lotRows: LotRow[] = filteredItems.flatMap(r =>
    (r.lots || []).map((l, i) => ({
      rowKey: `${r.key}_${i}`,
      name: r.name,
      code: l.code,
      expiration_date: l.expiration_date,
      total: l.total,
      available: l.available,
      unavail: l.unavail,
      dday: l.expiration_date ? dayjs(l.expiration_date).startOf('day').diff(today, 'day') : null,
    }))
  ).sort((a, b) => (a.expiration_date || '9999').localeCompare(b.expiration_date || '9999'))

  function ddayTag(d: number | null) {
    if (d === null) return <span style={{ color: '#9ca3af' }}>-</span>
    if (d < 0) return <Tag color="magenta">기한만료 {-d}일</Tag>
    if (d <= 90) return <Tag color="red">D-{d}</Tag>
    if (d <= 180) return <Tag color="orange">D-{d}</Tag>
    return <span style={{ color: '#6b7280' }}>D-{d}</span>
  }

  function exportItems(rows: InventoryItemRow[], label: string) {
    downloadCsv(
      ['상품명', 'SKU', 'BH재고', 'OB총재고', 'OB가용', 'OB가용외', '기간입고', '기초재고(역산)', '평균재고', '기간출고', '일평균', '월평균', '회전율(월·평균재고기준)', '소진(개월·현재고기준)', '마지막출고', '무출고일수', '상태', '주요거래처'],
      rows.map(r => [
        r.name, r.bh_skus.join(' / '), r.bh_stock, r.ob_total ?? '', r.ob_available ?? '', r.ob_unusable ?? '',
        r.in_qty ?? '', r.begin_stock ?? '', r.avg_stock ?? '',
        r.out_qty, r.daily_avg, r.monthly_avg, r.turnover ?? '', r.deplete_months ?? '',
        r.last_out ?? '', r.days_since_out ?? '', STATUS_META[r.status]?.label || r.status,
        r.partners.slice(0, 3).map(p => `${p.partner}(${p.qty})`).join(' | '),
      ]),
      `재고분석_${label}_${fromDate}_${toDate}.csv`,
    )
  }

  function exportLots() {
    downloadCsv(
      ['상품명', 'OB코드', '소비기한', 'D-day', '총재고', '가용', '가용외'],
      lotRows.map(r => [r.name, r.code, r.expiration_date, r.dday ?? '', r.total, r.available, r.unavail]),
      `현재고_소비기한_${dayjs().format('YYYY-MM-DD')}.csv`,
    )
  }

  function exportPartners() {
    downloadCsv(
      ['거래처', '기간출고', '점유율(%)', '일평균', '월평균', '취급품목수', '배분재고', '회전율(월)'],
      partnersAll.map(r => [r.partner, r.out_qty, r.share, r.daily_avg, r.monthly_avg, r.item_count, r.alloc_stock, r.turnover ?? '']),
      `재고분석_거래처별_${fromDate}_${toDate}.csv`,
    )
  }

  const itemColumns = [
    {
      title: '상품명', dataIndex: 'name', key: 'name', width: 240, ellipsis: true, fixed: 'left' as const,
      render: (v: string, r: InventoryItemRow) => (
        <Tooltip title={r.bh_skus.join(', ')}>
          <span style={{ fontSize: '0.82rem' }}>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: '상태', dataIndex: 'status', key: 'status', width: 80,
      filters: Object.entries(STATUS_META).map(([k, m]) => ({ text: m.label, value: k })),
      onFilter: (v: unknown, r: InventoryItemRow) => r.status === v,
      render: (v: string) => <Tag color={STATUS_META[v]?.color}>{STATUS_META[v]?.label || v}</Tag>,
    },
    {
      title: 'BH 재고', dataIndex: 'bh_stock', key: 'bh_stock', width: 90, align: 'right' as const,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => a.bh_stock - b.bh_stock,
      render: (v: number) => <span style={{ fontWeight: 600 }}>{num(v)}</span>,
    },
    {
      title: 'OB 총재고', dataIndex: 'ob_total', key: 'ob_total', width: 90, align: 'right' as const,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => (a.ob_total ?? -1) - (b.ob_total ?? -1),
      render: (v: number | null) => num(v),
    },
    {
      title: 'OB 가용', dataIndex: 'ob_available', key: 'ob_available', width: 85, align: 'right' as const,
      render: (v: number | null) => num(v),
    },
    {
      title: '기간 출고', dataIndex: 'out_qty', key: 'out_qty', width: 90, align: 'right' as const,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => a.out_qty - b.out_qty,
      defaultSortOrder: 'descend' as const,
      render: (v: number) => <span style={{ color: '#ef4444', fontWeight: 600 }}>{num(v)}</span>,
    },
    {
      title: '월평균 출고', dataIndex: 'monthly_avg', key: 'monthly_avg', width: 95, align: 'right' as const,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => a.monthly_avg - b.monthly_avg,
      render: (v: number) => num(v, 1),
    },
    {
      title: <Tooltip title={<>기간 평균재고 = (기초재고 + 현재고) ÷ 2<br />기초재고 = 현재고 + 기간출고 − 기간입고 (거래 이력 역산)<br />회전율의 분모로 사용 — 상단 'ⓘ 지표 계산 방식' 참고</>}>평균재고 ⓘ</Tooltip>,
      dataIndex: 'avg_stock', key: 'avg_stock', width: 90, align: 'right' as const,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => (a.avg_stock ?? -1) - (b.avg_stock ?? -1),
      render: (v: number | null | undefined, r: InventoryItemRow) => v == null ? '-' : (
        <Tooltip title={r.begin_stock != null ? `기초 ${num(r.begin_stock)} → 현재 ${num(r.bh_stock)} (기간입고 ${num(r.in_qty ?? 0)})` : undefined}>
          <span>{num(v, 1)}</span>
        </Tooltip>
      ),
    },
    {
      title: <Tooltip title={<>월평균 출고 ÷ 기간 평균재고 (한 달에 재고가 몇 번 도는지)<br />조회 시점 재고가 아닌 평균재고 기준이라 입고 타이밍 왜곡이 없습니다</>}>회전율(월) ⓘ</Tooltip>,
      dataIndex: 'turnover', key: 'turnover', width: 95, align: 'right' as const,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => (a.turnover ?? -1) - (b.turnover ?? -1),
      render: (v: number | null) => v === null ? '-' : (
        <span style={{ fontWeight: 600, color: v < lowTurnover ? '#f59e0b' : '#10b981' }}>{v.toFixed(2)}</span>
      ),
    },
    {
      title: <Tooltip title={<>현재고 ÷ 월평균 출고<br />'지금 재고가 언제 떨어지나'는 미래 전망이므로 현재고 기준</>}>소진(개월) ⓘ</Tooltip>,
      dataIndex: 'deplete_months', key: 'deplete_months', width: 95, align: 'right' as const,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => (a.deplete_months ?? 9999) - (b.deplete_months ?? 9999),
      render: (v: number | null) => v === null ? '-' : (
        <span style={{ color: v > 6 ? '#ef4444' : '#374151' }}>{v.toFixed(1)}</span>
      ),
    },
    {
      title: '마지막 출고', dataIndex: 'last_out', key: 'last_out', width: 105,
      sorter: (a: InventoryItemRow, b: InventoryItemRow) => (a.last_out || '').localeCompare(b.last_out || ''),
      render: (v: string | null, r: InventoryItemRow) => v ? (
        <span style={{ fontSize: '0.78rem' }}>
          {v}
          {r.days_since_out !== null && r.days_since_out >= slowDays && (
            <Tag color="red" style={{ marginLeft: 4, fontSize: '0.65rem', padding: '0 3px' }}>{r.days_since_out}일</Tag>
          )}
        </span>
      ) : <span style={{ color: '#9ca3af', fontSize: '0.78rem' }}>기간 내 없음</span>,
    },
    {
      title: '주요 거래처', key: 'top_partners', width: 220, ellipsis: true,
      render: (_: unknown, r: InventoryItemRow) => (
        <span style={{ fontSize: '0.75rem', color: '#6b7280' }}>
          {r.partners.slice(0, 2).map(p => `${p.partner} ${p.share}%`).join(' · ')}
        </span>
      ),
    },
  ]

  const partnerColumns = [
    {
      title: '거래처', dataIndex: 'partner', key: 'partner', width: 220, ellipsis: true,
      render: (v: string) => <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{v}</span>,
    },
    {
      title: '기간 출고', dataIndex: 'out_qty', key: 'out_qty', width: 100, align: 'right' as const,
      sorter: (a: InventoryPartnerRow, b: InventoryPartnerRow) => a.out_qty - b.out_qty,
      defaultSortOrder: 'descend' as const,
      render: (v: number) => <span style={{ color: '#ef4444', fontWeight: 700 }}>{num(v)}</span>,
    },
    {
      title: '점유율', dataIndex: 'share', key: 'share', width: 140,
      sorter: (a: InventoryPartnerRow, b: InventoryPartnerRow) => a.share - b.share,
      render: (v: number) => <Progress percent={v} size="small" format={p => `${p}%`} strokeColor="#10b981" />,
    },
    {
      title: '월평균 출고', dataIndex: 'monthly_avg', key: 'monthly_avg', width: 100, align: 'right' as const,
      sorter: (a: InventoryPartnerRow, b: InventoryPartnerRow) => a.monthly_avg - b.monthly_avg,
      render: (v: number) => num(v, 1),
    },
    {
      title: '취급 품목', dataIndex: 'item_count', key: 'item_count', width: 85, align: 'right' as const,
      sorter: (a: InventoryPartnerRow, b: InventoryPartnerRow) => a.item_count - b.item_count,
      render: (v: number) => num(v),
    },
    {
      title: <Tooltip title="품목별 '기간 평균재고'를 이 거래처의 출고 점유율만큼 배분한 수량">배분 재고 ⓘ</Tooltip>,
      dataIndex: 'alloc_stock', key: 'alloc_stock', width: 95, align: 'right' as const,
      sorter: (a: InventoryPartnerRow, b: InventoryPartnerRow) => a.alloc_stock - b.alloc_stock,
      render: (v: number) => num(v),
    },
    {
      title: <Tooltip title="거래처 월평균 출고 ÷ 배분 재고">회전율(월) ⓘ</Tooltip>,
      dataIndex: 'turnover', key: 'turnover', width: 95, align: 'right' as const,
      sorter: (a: InventoryPartnerRow, b: InventoryPartnerRow) => (a.turnover ?? -1) - (b.turnover ?? -1),
      render: (v: number | null) => v === null ? '-' : (
        <span style={{ fontWeight: 600, color: v < lowTurnover ? '#f59e0b' : '#10b981' }}>{v.toFixed(2)}</span>
      ),
    },
    {
      title: '계산', key: 'exclude', width: 80, align: 'center' as const, fixed: 'right' as const,
      render: (_: unknown, r: InventoryPartnerRow) => (
        <Tooltip title={`'${r.partner}'을(를) 모든 계산에서 제외하고 다시 분석합니다`}>
          <Button size="small" danger type="text" icon={<StopOutlined />}
            onClick={() => quickExclude(r.partner)}>제외</Button>
        </Tooltip>
      ),
    },
  ]

  const lotColumns = [
    {
      title: '상품명', dataIndex: 'name', key: 'name', width: 280, ellipsis: true,
      render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
    },
    {
      title: 'OB 코드', dataIndex: 'code', key: 'code', width: 110,
      render: (v: string) => <span style={{ fontSize: '0.75rem', fontFamily: 'monospace' }}>{v}</span>,
    },
    {
      title: '소비기한', dataIndex: 'expiration_date', key: 'expiration_date', width: 110,
      sorter: (a: LotRow, b: LotRow) => (a.expiration_date || '9999').localeCompare(b.expiration_date || '9999'),
      defaultSortOrder: 'ascend' as const,
      render: (v: string) => v || <span style={{ color: '#9ca3af' }}>미입력</span>,
    },
    {
      title: '잔여기간', dataIndex: 'dday', key: 'dday', width: 120,
      sorter: (a: LotRow, b: LotRow) => (a.dday ?? 99999) - (b.dday ?? 99999),
      render: (v: number | null) => ddayTag(v),
    },
    {
      title: '총재고', dataIndex: 'total', key: 'total', width: 90, align: 'right' as const,
      sorter: (a: LotRow, b: LotRow) => a.total - b.total,
      render: (v: number) => <span style={{ fontWeight: 600 }}>{num(v)}</span>,
    },
    {
      title: '가용', dataIndex: 'available', key: 'available', width: 90, align: 'right' as const,
      render: (v: number) => num(v),
    },
    {
      title: '가용외', dataIndex: 'unavail', key: 'unavail', width: 90, align: 'right' as const,
      render: (v: number) => v ? <span style={{ color: '#f59e0b' }}>{num(v)}</span> : '-',
    },
  ]

  const expandItemRow = (r: InventoryItemRow) => (
    <div style={{ padding: '4px 8px' }}>
      <div style={{ fontSize: '0.78rem', fontWeight: 700, marginBottom: 6 }}>거래처별 출고 ({r.partners.length}곳)</div>
      <Table
        dataSource={r.partners}
        rowKey="partner"
        size="small"
        pagination={false}
        columns={[
          { title: '거래처', dataIndex: 'partner', key: 'partner', width: 260 },
          { title: '출고량', dataIndex: 'qty', key: 'qty', width: 100, align: 'right' as const, render: (v: number) => num(v) },
          {
            title: '점유율', dataIndex: 'share', key: 'share', width: 180,
            render: (v: number) => <Progress percent={v} size="small" format={p => `${p}%`} />,
          },
        ]}
      />
    </div>
  )

  const expandPartnerRow = (r: InventoryPartnerRow) => (
    <div style={{ padding: '4px 8px' }}>
      <div style={{ fontSize: '0.78rem', fontWeight: 700, marginBottom: 6 }}>품목별 출고 ({r.items.length}개)</div>
      <Table
        dataSource={r.items}
        rowKey="key"
        size="small"
        pagination={r.items.length > 20 ? { pageSize: 20 } : false}
        columns={[
          { title: '상품명', dataIndex: 'name', key: 'name', width: 300, ellipsis: true },
          { title: '출고량', dataIndex: 'qty', key: 'qty', width: 100, align: 'right' as const, render: (v: number) => num(v) },
          { title: '현재고', dataIndex: 'stock', key: 'stock', width: 100, align: 'right' as const, render: (v: number) => num(v) },
        ]}
      />
    </div>
  )

  // ── 대시보드 집계 (검색 필터와 무관한 전체 스냅샷) ──────────────
  const statusDist = ['ok', 'low', 'slow', 'no_stock'].map(st => ({
    status: st,
    label: STATUS_META[st].label,
    count: itemsAll.filter(r => r.status === st).length,
  })).filter(d => d.count > 0)
  const statusColors: Record<string, string> = { ok: '#10b981', low: '#f59e0b', slow: '#ef4444', no_stock: '#9ca3af' }
  const statusTotal = statusDist.reduce((s2, d) => s2 + d.count, 0)

  const topItems = itemsAll.slice(0, 10).map(r => ({
    label: r.name, value: r.out_qty,
    sub: r.turnover !== null ? `${r.turnover.toFixed(1)}회` : undefined,
  }))
  const topPartners = (result?.partners || []).slice(0, 10).map(r => ({
    label: r.partner, value: r.out_qty, sub: `${r.share}%`,
  }))

  const allLots = itemsAll.flatMap(r => r.lots || [])
  const expiryBuckets = [
    { label: '기한 만료', color: '#eb2f96', qty: 0, lots: 0, test: (d: number) => d < 0 },
    { label: '90일 이내', color: '#ef4444', qty: 0, lots: 0, test: (d: number) => d >= 0 && d <= 90 },
    { label: '180일 이내', color: '#f59e0b', qty: 0, lots: 0, test: (d: number) => d > 90 && d <= 180 },
    { label: '180일 초과', color: '#10b981', qty: 0, lots: 0, test: (d: number) => d > 180 },
  ]
  const todayD = dayjs().startOf('day')
  for (const l of allLots) {
    if (!l.expiration_date) continue
    const d = dayjs(l.expiration_date).startOf('day').diff(todayD, 'day')
    const b = expiryBuckets.find(x => x.test(d))
    if (b) { b.qty += l.total; b.lots += 1 }
  }

  const s = result?.summary
  const summaryCards = s ? [
    { label: '품목 수', value: num(s.item_count), color: '#374151' },
    { label: 'BH 총재고', value: num(s.total_bh_stock), color: '#2563eb' },
    ...(s.total_avg_stock != null ? [
      { label: '기간 평균재고', value: num(s.total_avg_stock), color: '#0891b2' },
    ] : []),
    ...(result?.ob_enabled ? [
      { label: 'OB 총재고', value: num(s.total_ob_total), color: '#7c3aed' },
      { label: 'OB 가용재고', value: num(s.total_ob_available), color: '#7c3aed' },
    ] : []),
    { label: '기간 총출고', value: num(s.total_out), color: '#ef4444' },
    { label: '전체 회전율(월)', value: s.overall_turnover !== null ? s.overall_turnover.toFixed(2) : '-', color: '#10b981' },
    { label: '부진재고', value: num(s.slow_count), color: '#dc2626' },
    { label: '저회전', value: num(s.low_count), color: '#f59e0b' },
    { label: '거래처 수', value: num(s.partner_count), color: '#374151' },
  ] : []

  // ── 경영진 요약 보고서 자동 생성 (분석 결과 기준) ─────────────────
  function buildExecAutoText(): string {
    if (!result || !s) return ''
    const locName = locId != null ? (locations.find(l => l.id === locId)?.name || '선택 위치') : '전체 위치'
    const L: string[] = []
    L.push(`■ 재고 분석 요약 (${dayjs().format('YYYY-MM-DD')} 작성 · ${locName} · 조회기간 ${result.from_date} ~ ${result.to_date}, ${result.days_span}일)`)
    L.push(`• 관리 품목 ${num(s.item_count)}개 · BH 총재고 ${num(s.total_bh_stock)}개${result.ob_enabled ? ` · OB 가용재고 ${num(s.total_ob_available)}개` : ''}`)
    L.push(`• 기간 총 출고 ${num(s.total_out)}개 (거래 ${num(s.tx_count)}건) · 전체 회전율 ${s.overall_turnover !== null ? s.overall_turnover.toFixed(2) : '-'}회/월${result.avg_basis === 'average' ? ' (기간 평균재고 기준)' : ''}`)
    L.push('')
    L.push('■ 재고 건전성')
    const riskCnt = s.slow_count + s.low_count
    L.push(`• 부진재고 ${num(s.slow_count)}개 · 저회전 ${num(s.low_count)}개${s.item_count > 0 ? ` (전체 품목의 ${Math.round(riskCnt * 100 / s.item_count)}%)` : ''} — 처분·프로모션 검토 대상`)
    if (result.ob_enabled && allLots.length > 0) {
      const expired = expiryBuckets[0], d90 = expiryBuckets[1]
      if (expired.qty > 0 || d90.qty > 0) {
        L.push(`• 소비기한 리스크: 기한 만료 ${expired.qty.toLocaleString()}개(${expired.lots}로트) · 90일 이내 ${d90.qty.toLocaleString()}개(${d90.lots}로트)`)
      } else {
        L.push('• 소비기한 임박(90일 이내) 재고 없음')
      }
    }
    L.push('')
    L.push('■ 주요 출고')
    if (itemsAll.length > 0) L.push(`• 품목 TOP3: ${itemsAll.slice(0, 3).map(r => `${r.name} ${num(r.out_qty)}개`).join(' · ')}`)
    if ((result.partners || []).length > 0) L.push(`• 거래처 TOP3: ${result.partners.slice(0, 3).map(r => `${r.partner} ${r.share}%`).join(' · ')}`)
    L.push('')
    L.push('■ 종합 의견')
    L.push('• (필요 시 \'수정\'을 눌러 코멘트를 남기세요)')
    return L.join('\n')
  }
  const execAutoText = buildExecAutoText()

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">재고 분석</h1>
        <p className="page-desc">박스히어로·아워박스 기준 재고수량 / 회전율(기간 평균재고 기준) / 부진재고 + 거래처별 출고·회전율</p>
      </div>

      {/* 지표 계산 방식 상세 설명 */}
      <Collapse ghost size="small"
        style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', marginBottom: 16 }}
        items={[{
          key: 'metric-help',
          label: <span style={{ fontWeight: 700, fontSize: '0.85rem' }}>ℹ️ 지표 계산 방식 자세히 보기 — 회전율 · 평균재고 · 소진개월 · 부진재고</span>,
          children: (
            <div style={{ fontSize: '0.8rem', color: '#374151', lineHeight: 1.9 }}>
              <p style={{ margin: '0 0 10px' }}>
                <b>회전율(월) = 월평균 출고 ÷ 기간 평균재고</b><br />
                한 달 동안 재고가 몇 번 팔려 나가는지를 뜻합니다. 분모로 <b>조회 시점의 현재고가 아니라
                조회 기간의 평균재고</b>를 씁니다. 현재고는 입출고에 따라 계속 변하기 때문에, 현재고로 나누면
                <b> 입고 직후에 분석할 땐 회전율이 실제보다 낮게</b>(멀쩡한 상품이 저회전으로 오분류),
                <b> 재고가 바닥일 땐 실제보다 높게</b>(부진 상품이 정상으로 보임) 왜곡됩니다.
                예: 월 100개씩 꾸준히 나가는 상품도 300개 입고 다음날 분석하면 0.33회, 재고 50개일 때 분석하면 2.0회로 널뛰기합니다.
              </p>
              <p style={{ margin: '0 0 10px' }}>
                <b>평균재고 = (기초재고 + 현재고) ÷ 2</b><br />
                기초재고(조회 시작일 시점 재고)는 박스히어로 거래 이력으로 역산합니다:
                <b> 기초재고 = 현재고 + 기간출고 − 기간입고</b>. 창고 간 이동·재고조정 거래는 반영되지 않는
                추정치이며, 역산 결과가 음수면 0으로 처리합니다. 입고 거래 조회에 실패한 경우에만
                예전 방식(현재고 기준)으로 폴백하고 화면에 경고를 띄웁니다.
              </p>
              <p style={{ margin: '0 0 10px' }}>
                <b>소진(개월) = 현재고 ÷ 월평균 출고</b><br />
                "지금 있는 재고가 언제 떨어지나"라는 <b>미래 전망</b>이므로, 여기는 평균이 아닌
                <b> 현재고 기준이 맞습니다</b>. 회전율과 분모가 달라서 1/회전율과 정확히 일치하지 않을 수 있습니다.
              </p>
              <p style={{ margin: '0 0 10px' }}>
                <b>부진재고 / 저회전 분류</b><br />
                · <Tag color="red" style={{ marginRight: 0 }}>부진</Tag> 재고가 있는데 최근 N일(기본 60일) 동안 출고가 없음 — 재고량과 무관하게 <b>출고 이력 기준</b><br />
                · <Tag color="orange" style={{ marginRight: 0 }}>저회전</Tag> 재고가 있고 회전율이 기준값(기본 0.5회/월) 미만 — 팔리긴 하는데 재고 대비 느림
              </p>
              <p style={{ margin: '0 0 10px' }}>
                <b>거래처별 회전율 = 거래처 월평균 출고 ÷ 배분재고</b><br />
                배분재고는 품목별 <b>평균재고</b>를 그 거래처의 출고 점유율만큼 나눠 가진 값입니다.
                (예: A상품 평균재고 100개 중 쿠팡 출고 점유율이 60%면 쿠팡 배분재고 60개)
              </p>
              <p style={{ margin: 0 }}>
                <b>세트→단품 토글</b><br />
                기본(원본)은 세트 SKU를 세트 그대로 집계합니다. 이 경우 단품 재고는 있는데 주로 세트로
                팔리는 상품은 출고가 적게 잡혀 <b>부진/저회전으로 오분류</b>될 수 있습니다.
                토글을 켜면 세트 출고·입고를 <b>구성 단품 수량으로 환산</b>해서 단품의 출고량·회전율·부진
                판정에 합산합니다 (세트 구성은 출고 예측 페이지의 '세트 구성' 등록 기준).
                단, 세트 SKU 자체의 재고가 박스히어로에 남아 있으면 그 세트는 출고 0으로 잡혀 부진으로
                보일 수 있으니 세트 재고 운영 방식에 맞춰 해석하세요.
              </p>
            </div>
          ),
        }]} />

      {/* 경영진 요약 보고서 (자동 생성 + 수기 수정 가능) */}
      <ExecReport pageKey="inventory" autoText={execAutoText} />

      {/* 컨트롤 */}
      <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px 20px', marginBottom: 16, display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>위치 (BH 재고·출고 기준)</div>
          <Select
            size="small" allowClear showSearch optionFilterProp="label"
            placeholder="전체 위치"
            value={locId}
            onChange={v => setLocId(v ?? null)}
            options={locations.map(l => ({ label: l.name, value: l.id }))}
            style={{ width: 160 }}
          />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>출고 집계 시작일</div>
          <DatePicker size="small" value={dayjs(fromDate)} onChange={d => d && setFromDate(d.format('YYYY-MM-DD'))} allowClear={false} />
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600 }}>종료일</div>
          <DatePicker size="small" value={dayjs(toDate)} onChange={d => d && setToDate(d.format('YYYY-MM-DD'))} allowClear={false} />
        </div>
        <div>
          <Tooltip title={`재고가 있는데 마지막 출고 후 ${slowDays}일 넘게 출고가 없으면 '부진재고'로 분류합니다.`}>
            <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600, cursor: 'help' }}>부진 기준 (무출고 일수) ⓘ</div>
          </Tooltip>
          <InputNumber size="small" min={1} max={365} value={slowDays} onChange={v => v && setSlowDays(v)} style={{ width: 70 }} suffix="일" />
        </div>
        <div>
          <Tooltip title={
            <>
              회전율(월) = 월평균 출고 ÷ 기간 평균재고. 한 달 동안 재고가 몇 번 팔려 나가는지를 뜻합니다.<br />
              이 값이 {lowTurnover} 미만이면 '저회전'으로 분류합니다.<br />
              예: 0.5회 = 한 달에 재고의 절반만 소진 → 다 팔리는 데 약 {lowTurnover > 0 ? (1 / lowTurnover).toFixed(1) : '-'}개월 이상 걸리는 상품
            </>
          }>
            <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600, cursor: 'help' }}>저회전 기준 (월 회전율 미만) ⓘ</div>
          </Tooltip>
          <InputNumber size="small" min={0} max={10} step={0.1} value={lowTurnover} onChange={v => v !== null && setLowTurnover(v)} style={{ width: 70 }} suffix="회" />
        </div>
        <div>
          <Tooltip title={
            <>
              세트로 출고·입고된 수량을 구성 단품 수량으로 풀어서 단품 기준으로 집계합니다.<br />
              (세트 구성은 출고 예측 페이지의 '세트 구성' 등록 기준)<br />
              단품 재고는 있는데 주로 세트로 팔리는 상품이 부진/저회전으로 오분류되는 것을 막아줍니다.
            </>
          }>
            <div style={{ fontSize: '0.72rem', color: '#6b7280', marginBottom: 4, fontWeight: 600, cursor: 'help' }}>세트→단품 ⓘ</div>
          </Tooltip>
          <Switch size="small" checked={expandSets}
            onChange={v => { setExpandSets(v); if (result && config.api_token) handleSearch(v) }}
            checkedChildren="분해" unCheckedChildren="원본" />
        </div>
        <Button type="primary" icon={<SearchOutlined />} onClick={() => handleSearch()} loading={loading} disabled={!config.api_token}>
          분석
        </Button>
        <Tooltip title="특정 거래처의 출고를 회전율·부진 판정 등 모든 계산에서 제외합니다. (예: 내부이동·폐기·샘플 등)">
          <Button icon={<StopOutlined />} onClick={openExclModal}>
            계산 제외 거래처{excluded.length > 0 ? ` (${excluded.length})` : ''}
          </Button>
        </Tooltip>
        {!config.api_token && <span style={{ fontSize: '0.78rem', color: '#ef4444' }}>설정에서 박스히어로 토큰을 먼저 연결하세요</span>}
      </div>

      {error && <Alert type="error" message={error} style={{ marginBottom: 12 }} />}
      {restoredAt && result && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message={<span style={{ fontSize: '0.82rem' }}>💾 마지막 분석 결과입니다 (저장: {restoredAt}) — 최신 데이터가 필요하면 다시 분석하세요.</span>} />
      )}
      {result?.expand_sets && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message={
            <span style={{ fontSize: '0.82rem' }}>
              📦 세트→단품 분해 적용됨
              {(result.set_expanded_out ?? 0) > 0
                ? ` — 세트 출고 ${num(result.set_expanded_out)}개를 구성 단품 수량으로 환산해 집계했습니다.`
                : ' — 기간 내 분해된 세트 출고가 없습니다 (세트 구성 등록 여부를 확인하세요).'}
            </span>
          } />
      )}
      {result && (result.excluded_partners?.length ?? 0) > 0 && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message={
            <span style={{ fontSize: '0.82rem' }}>
              계산에서 제외된 거래처 {result.excluded_partners.length}곳: {result.excluded_partners.join(', ')}
              {result.excluded_out_qty > 0 && ` — 제외된 출고 ${num(result.excluded_out_qty)}개`}
              {'  '}<a onClick={openExclModal}>수정</a>
            </span>
          }
        />
      )}
      {result && !result.ob_enabled && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="아워박스 API가 설정되지 않아 OB 재고 없이 박스히어로 기준으로만 분석했습니다." />
      )}
      {result?.errors?.map((e, i) => <Alert key={i} type="warning" message={e} style={{ marginBottom: 12 }} />)}

      {loading && (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#6b7280', fontSize: '0.85rem' }}>
            재고·출고 이력을 수집 중... 기간에 따라 1~2분 소요될 수 있습니다.
          </div>
        </div>
      )}

      {result && !loading && (
        <>
          {/* 요약 카드 */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
            {summaryCards.map(c => (
              <div key={c.label} style={{ background: '#fff', borderRadius: 10, border: '1px solid #e5e7eb', padding: '10px 16px', minWidth: 110 }}>
                <div style={{ fontSize: '0.72rem', color: '#6b7280', fontWeight: 600, marginBottom: 4 }}>{c.label}</div>
                <div style={{ fontSize: '1.2rem', fontWeight: 800, color: c.color }}>{c.value}</div>
              </div>
            ))}
          </div>

          {/* 용어 설명 (쉬운 말) */}
          <div style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 12, padding: '12px 18px', marginBottom: 14 }}>
            <div style={{ fontWeight: 700, fontSize: '0.82rem', marginBottom: 8, color: '#166534' }}>
              📖 이 숫자들이 뜻하는 것 <span style={{ fontWeight: 400, color: '#6b7280', fontSize: '0.72rem' }}>— 최근 {result.days_span}일간 출고 {num(result.summary.tx_count)}건을 분석했습니다</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '6px 24px', fontSize: '0.78rem', color: '#374151', lineHeight: 1.6 }}>
              <div><b>회전율(월)</b> — 한 달 동안 재고가 몇 번 다 팔리는지. 클수록 잘 나가는 상품입니다. <span style={{ color: '#6b7280' }}>(월평균 출고 ÷ 평균재고 기준. 기간 중 입고가 많았던 품목은 소진예상의 역수와 다를 수 있음)</span></div>
              <div><b>소진(개월)</b> — 지금 파는 속도라면 남은 재고가 몇 달이면 바닥나는지. <span style={{ color: '#6b7280' }}>(너무 길면 재고가 과하게 쌓인 것)</span></div>
              <div><b>부진재고</b> — 재고는 쌓여 있는데 최근 <b>{slowDays}일</b> 동안 한 개도 안 나간 상품. <span style={{ color: '#6b7280' }}>(처분·프로모션 검토 대상)</span></div>
              <div><b>저회전</b> — 팔리긴 하지만 속도가 느린 상품. 회전율이 <b>{lowTurnover}</b> 미만이면 표시됩니다. <span style={{ color: '#6b7280' }}>(재고 소진까지 {lowTurnover > 0 ? (1 / lowTurnover).toFixed(1) : '-'}개월 이상)</span></div>
            </div>
          </div>

          {/* 차트 대시보드 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: 12, marginBottom: 14 }}>
            {/* 재고 상태 분포 */}
            <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '14px 18px' }}>
              <div style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 10 }}>재고 상태 분포 <span style={{ color: '#9ca3af', fontWeight: 400, fontSize: '0.72rem' }}>({statusTotal}개 품목)</span></div>
              <div style={{ display: 'flex', height: 18, borderRadius: 6, overflow: 'hidden', gap: 2 }}>
                {statusDist.map(d => (
                  <Tooltip key={d.status} title={`${d.label} ${d.count}개 (${Math.round(d.count * 100 / statusTotal)}%)`}>
                    <div style={{ width: `${(d.count / statusTotal) * 100}%`, background: statusColors[d.status], minWidth: 6 }} />
                  </Tooltip>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 14, marginTop: 10, flexWrap: 'wrap' }}>
                {statusDist.map(d => (
                  <span key={d.status} style={{ fontSize: '0.76rem', color: '#374151', display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 9, height: 9, borderRadius: 2, background: statusColors[d.status], flexShrink: 0 }} />
                    {d.label} <b>{d.count}</b>
                    <span style={{ color: '#9ca3af' }}>({Math.round(d.count * 100 / statusTotal)}%)</span>
                  </span>
                ))}
              </div>
              {/* 소비기한 임박 재고 (OB 로트 기준) */}
              {result.ob_enabled && allLots.length > 0 && (
                <>
                  <div style={{ fontWeight: 700, fontSize: '0.85rem', margin: '16px 0 8px' }}>소비기한별 재고 <span style={{ color: '#9ca3af', fontWeight: 400, fontSize: '0.72rem' }}>(OB 로트 기준)</span></div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                    {expiryBuckets.map(b => (
                      <div key={b.label} style={{ borderLeft: `3px solid ${b.color}`, background: '#f9fafb', borderRadius: 6, padding: '7px 10px' }}>
                        <div style={{ fontSize: '0.68rem', color: '#6b7280', fontWeight: 600 }}>{b.label}</div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 800, color: '#111827' }}>{b.qty.toLocaleString()}</div>
                        <div style={{ fontSize: '0.65rem', color: '#9ca3af' }}>{b.lots}개 로트</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* 품목 TOP 10 출고 */}
            <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '14px 18px' }}>
              <div style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 10 }}>품목 TOP 10 출고 <span style={{ color: '#9ca3af', fontWeight: 400, fontSize: '0.72rem' }}>(값 옆 = 회전율)</span></div>
              <BarList rows={topItems} color="#10b981" unit="개" />
            </div>

            {/* 거래처 TOP 10 출고 */}
            <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '14px 18px' }}>
              <div style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 10 }}>거래처 TOP 10 출고 <span style={{ color: '#9ca3af', fontWeight: 400, fontSize: '0.72rem' }}>(값 옆 = 점유율)</span></div>
              <BarList rows={topPartners} color="#2563eb" unit="개" />
            </div>
          </div>

          {/* 탭 + 테이블 */}
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '12px 20px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Tabs
                activeKey={tab}
                onChange={setTab}
                style={{ flex: 1 }}
                items={[
                  { key: 'items', label: `품목별 (${filteredItems.length})` },
                  { key: 'partners', label: `거래처별 (${partnersAll.length})` },
                  { key: 'slow', label: `부진·저회전 (${slowItems.length})` },
                  ...(result?.ob_enabled ? [{ key: 'lots', label: `현재고·소비기한 (${lotRows.length})` }] : []),
                ]}
              />
              <Input.Search
                size="small"
                placeholder={tab === 'partners' ? '거래처 검색' : 'SKU 또는 상품명 검색'}
                value={search}
                onChange={e => setSearch(e.target.value)}
                style={{ width: 200 }}
              />
              <Button
                icon={<DownloadOutlined />} size="small"
                onClick={() => {
                  if (tab === 'partners') exportPartners()
                  else if (tab === 'lots') exportLots()
                  else exportItems(tab === 'slow' ? slowItems : filteredItems, tab === 'slow' ? '부진저회전' : '품목별')
                }}
              >
                CSV
              </Button>
            </div>

            {(tab === 'items' || tab === 'slow') && (
              <Table
                dataSource={tab === 'slow' ? slowItems : filteredItems}
                columns={itemColumns}
                rowKey="key"
                size="small"
                scroll={{ x: 'max-content' }}
                pagination={{ pageSize: 50, showSizeChanger: true }}
                expandable={{ expandedRowRender: expandItemRow, rowExpandable: r => r.partners.length > 0 }}
              />
            )}
            {tab === 'lots' && (
              <Table
                dataSource={lotRows}
                columns={lotColumns}
                rowKey="rowKey"
                size="small"
                scroll={{ x: 'max-content' }}
                pagination={{ pageSize: 50, showSizeChanger: true }}
              />
            )}
            {tab === 'partners' && (
              <Table
                dataSource={partnersAll}
                columns={partnerColumns}
                rowKey="partner"
                size="small"
                scroll={{ x: 'max-content' }}
                pagination={{ pageSize: 50, showSizeChanger: true }}
                expandable={{ expandedRowRender: expandPartnerRow, rowExpandable: r => r.items.length > 0 }}
              />
            )}
          </div>
        </>
      )}

      {/* 계산 제외 거래처 관리 모달 */}
      <Modal
        title="계산 제외 거래처"
        open={exclModalOpen}
        onCancel={() => setExclModalOpen(false)}
        onOk={saveExcl}
        confirmLoading={savingExcl}
        okText="저장 후 재분석"
        cancelText="닫기"
        width={560}
      >
        <p style={{ fontSize: '0.82rem', color: '#6b7280', marginTop: 0 }}>
          여기서 고른 거래처의 출고는 <b>회전율·부진 판정·거래처별 집계 등 모든 계산에서 제외</b>됩니다.
          (예: 내부이동·폐기·샘플·마케팅 무상 등 실제 판매가 아닌 거래처)
          설정은 서버에 저장되어 다음에도 유지됩니다.
        </p>
        <Select
          mode="tags"
          allowClear
          showSearch
          placeholder="제외할 거래처를 선택하거나 직접 입력하세요"
          value={exclDraft}
          onChange={setExclDraft}
          options={partnerCandidates.map(p => ({ label: p, value: p }))}
          style={{ width: '100%' }}
          optionFilterProp="label"
        />
        <div style={{ marginTop: 10, fontSize: '0.76rem', color: '#9ca3af' }}>
          {result
            ? `현재 분석 결과의 거래처 ${result.partners.length}곳 중에서 선택할 수 있습니다.`
            : '먼저 한 번 분석하면 거래처 목록에서 고를 수 있습니다. (직접 입력도 가능)'}
          {' '}목록에 없는 거래처는 이름을 직접 입력해 추가할 수 있습니다.
        </div>
      </Modal>
    </div>
  )
}
