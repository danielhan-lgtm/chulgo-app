import { useState, useEffect, useCallback, useRef } from 'react'
import { Button, Input, DatePicker, Select, message, Spin, Popconfirm, Progress } from 'antd'
import dayjs, { Dayjs } from 'dayjs'
import type { ReceivingRecord, ReceivingItem, ProductMapping, BoxheroItem, AppConfig } from '../types'
import {
  getReceivings, approveReceiving, cancelReceiving, ignoreReceiving,
  syncReceiving, getSyncStatus, getMappings, saveMapping, autoMap,
  deleteMapping, getBoxheroItemsForReceiving, getOurboxProductsForReceiving,
} from '../services/api'

interface Props { config: AppConfig }

const { RangePicker } = DatePicker

type TabKey = 'receivings' | 'mapping'
type StatusFilter = '' | 'pending' | 'approved' | 'ignored'

export default function Receiving({ config }: Props) {
  const [tab, setTab] = useState<TabKey>('receivings')

  // ── 입고 목록 ──────────────────────────────────────────────────────────────
  const [records, setRecords] = useState<ReceivingRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('pending')
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selectedSnos, setSelectedSnos] = useState<Set<string>>(new Set())
  const [syncStatus, setSyncStatus] = useState({ status: 'idle', lastSyncTime: null as string | null, lastSyncError: null as string | null })
  const [syncing, setSyncing] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ visible: boolean; pct: number; label: string }>({ visible: false, pct: 0, label: '' })

  // ── 매핑 탭 ───────────────────────────────────────────────────────────────
  const [mappings, setMappings] = useState<ProductMapping[]>([])
  const [ourboxProds, setOurboxProds] = useState<any[]>([])
  const [bhItems, setBhItems] = useState<BoxheroItem[]>([])
  const [mappingLoading, setMappingLoading] = useState(false)
  const [searchOurbox, setSearchOurbox] = useState('')
  const [searchBh, setSearchBh] = useState('')
  const [selOurbox, setSelOurbox] = useState<any | null>(null)
  const [selBh, setSelBh] = useState<BoxheroItem | null>(null)
  const [savingMap, setSavingMap] = useState(false)
  const [autoMapping, setAutoMapping] = useState(false)
  const [manualMode, setManualMode] = useState(false)
  const [manualCd, setManualCd] = useState('')
  const [manualNm, setManualNm] = useState('')

  // ── 로드 ──────────────────────────────────────────────────────────────────
  const loadRecords = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getReceivings()
      setRecords(data)
    } catch (e: any) {
      message.error('입고 목록 로드 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadSyncStatus = useCallback(async () => {
    try { setSyncStatus(await getSyncStatus()) } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    loadRecords()
    loadSyncStatus()
    const t = setInterval(loadSyncStatus, 30000)
    return () => clearInterval(t)
  }, [loadRecords, loadSyncStatus])

  // ── 필터 ──────────────────────────────────────────────────────────────────
  const filtered = records.filter(r => {
    if (statusFilter && r.status !== statusFilter) return false
    if (dateRange) {
      const d = dayjs(r.put_req_dt?.slice(0, 10))
      if (!d.isValid()) return false
      if (d.isBefore(dateRange[0], 'day') || d.isAfter(dateRange[1], 'day')) return false
    }
    if (search) {
      const q = search.toLowerCase()
      return r.put_sno.includes(q) ||
        (r.vendor_nm || '').toLowerCase().includes(q) ||
        (r.items || []).some((i: any) => (i.sale_prod_nm || '').toLowerCase().includes(q))
    }
    return true
  })

  const countPending  = records.filter(r => r.status === 'pending').length
  const countApproved = records.filter(r => r.status === 'approved').length
  const countIgnored  = records.filter(r => r.status === 'ignored').length

  // ── 동기화 ────────────────────────────────────────────────────────────────
  async function handleSync() {
    setSyncing(true)
    try {
      const res = await syncReceiving()
      if (res.already) {
        message.info(res.message)
      }
      // 완료까지 2초 간격 폴링 (아워박스 로그인 포함 15초~1분 소요, 최대 3분 대기)
      for (let i = 0; i < 90; i++) {
        await new Promise(r => setTimeout(r, 2000))
        const s = await getSyncStatus()
        setSyncStatus(s)
        if (s.status !== 'syncing') {
          if (s.status === 'error') {
            message.error('동기화 실패: ' + (s.lastSyncError || '알 수 없는 오류'), 8)
          } else {
            const n = (s as any).lastNewCount
            message.success(n != null ? `✅ 동기화 완료 — 신규 입고 ${n}건` : '✅ 동기화 완료')
            await loadRecords()
          }
          break
        }
      }
    } catch (e: any) {
      message.error('동기화 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSyncing(false)
    }
  }

  // ── 승인/무시/취소 ────────────────────────────────────────────────────────
  async function handleApprove(sno: string) {
    try {
      const res = await approveReceiving(sno)
      message.success(`✅ 등록 완료! TX#${res.boxhero_tx_id}`)
      if (res.unmapped_count > 0) message.warning(`⚠ 매핑 없는 상품 ${res.unmapped_count}개 제외됨`)
      await loadRecords()
    } catch (e: any) { message.error('승인 실패: ' + (e.response?.data?.detail || e.message)) }
  }

  async function handleCancel(sno: string) {
    try {
      await cancelReceiving(sno)
      message.success('취소 완료. 대기중으로 초기화됐습니다.')
      await loadRecords()
    } catch (e: any) { message.error('취소 실패: ' + (e.response?.data?.detail || e.message)) }
  }

  async function handleIgnore(sno: string) {
    try {
      await ignoreReceiving(sno)
      message.success('무시됨')
      await loadRecords()
    } catch (e: any) { message.error('무시 실패: ' + (e.response?.data?.detail || e.message)) }
  }

  // ── 일괄 처리 ─────────────────────────────────────────────────────────────
  function toggleSelectAll(checked: boolean) {
    const pendingSnos = filtered.filter(r => r.status === 'pending').map(r => r.put_sno)
    setSelectedSnos(checked ? new Set(pendingSnos) : new Set())
  }

  function toggleSelect(sno: string) {
    setSelectedSnos(prev => {
      const next = new Set(prev)
      next.has(sno) ? next.delete(sno) : next.add(sno)
      return next
    })
  }

  async function bulkApprove() {
    const snos = [...selectedSnos]
    let done = 0, ok = 0, fail = 0
    setBulkProgress({ visible: true, pct: 0, label: '' })
    for (const sno of snos) {
      setBulkProgress({ visible: true, pct: Math.round((done / snos.length) * 100), label: `처리 중: ${sno} (${done + 1}/${snos.length})` })
      try { await approveReceiving(sno); ok++ } catch { fail++ }
      done++
    }
    setBulkProgress({ visible: true, pct: 100, label: `완료: 성공 ${ok}건 / 실패 ${fail}건` })
    message.success(`일괄 승인: 성공 ${ok}건 / 실패 ${fail}건`)
    setSelectedSnos(new Set())
    setTimeout(() => { setBulkProgress({ visible: false, pct: 0, label: '' }); loadRecords() }, 2000)
  }

  async function bulkIgnore() {
    const snos = [...selectedSnos]
    let done = 0
    setBulkProgress({ visible: true, pct: 0, label: '' })
    for (const sno of snos) {
      setBulkProgress({ visible: true, pct: Math.round((done / snos.length) * 100), label: `처리 중: ${sno}` })
      try { await ignoreReceiving(sno) } catch { /* continue */ }
      done++
    }
    setBulkProgress({ visible: true, pct: 100, label: `${snos.length}건 무시됨` })
    message.info(`🚫 ${snos.length}건 무시됨`)
    setSelectedSnos(new Set())
    setTimeout(() => { setBulkProgress({ visible: false, pct: 0, label: '' }); loadRecords() }, 1000)
  }

  // ── 매핑 탭 로드 ──────────────────────────────────────────────────────────
  async function loadMappingPage() {
    setMappingLoading(true)
    try {
      const [maps, items] = await Promise.all([getMappings(), getBoxheroItemsForReceiving()])
      setMappings(maps)
      setBhItems(items)
    } catch (e: any) {
      message.error('매핑 로드 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setMappingLoading(false)
    }
    // 아워박스 상품은 스크래핑 기반이라 느릴 수 있어 별도 로드 (실패해도 매핑/박스히어로는 표시)
    try {
      setOurboxProds(await getOurboxProductsForReceiving())
    } catch (e: any) {
      message.warning('아워박스 상품 로드 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  function switchTab(key: TabKey) {
    setTab(key)
    if (key === 'mapping' && (bhItems.length === 0 || ourboxProds.length === 0)) loadMappingPage()
  }

  async function handleSaveMapping() {
    if (!selOurbox || !selBh) return
    setSavingMap(true)
    try {
      await saveMapping({ ourbox_prod_cd: selOurbox.prod_cd, ourbox_prod_nm: selOurbox.sale_prod_nm || '', boxhero_item_id: selBh.id, boxhero_item_nm: selBh.name, boxhero_sku: selBh.sku || '' })
      message.success('✅ 매핑 저장됨')
      setSelOurbox(null); setSelBh(null)
      setMappings(await getMappings())
    } catch (e: any) { message.error('저장 실패: ' + (e.response?.data?.detail || e.message)) }
    finally { setSavingMap(false) }
  }

  async function handleAutoMap() {
    setAutoMapping(true)
    try {
      const res = await autoMap()
      message.success(`✅ 자동 매핑 완료! ${res.added}개 연결 / ${res.skipped}개 미매칭`)
      setMappings(await getMappings())
    } catch (e: any) { message.error('자동 매핑 실패: ' + (e.response?.data?.detail || e.message)) }
    finally { setAutoMapping(false) }
  }

  async function handleDeleteMapping(prodCd: string) {
    try {
      await deleteMapping(prodCd)
      message.success('삭제됨')
      setMappings(prev => prev.filter(m => m.ourbox_prod_cd !== prodCd))
    } catch (e: any) { message.error('삭제 실패') }
  }

  function applyManualInput() {
    if (!manualCd.trim()) { message.warning('상품코드를 입력하세요'); return }
    setSelOurbox({ prod_cd: manualCd.trim(), sale_prod_nm: manualNm.trim() || manualCd.trim() })
    setManualMode(false); setManualCd(''); setManualNm('')
  }

  // ── 렌더 헬퍼 ─────────────────────────────────────────────────────────────
  const syncDotColor = syncStatus.status === 'syncing' ? '#f59e0b' : syncStatus.status === 'error' ? '#ef4444' : '#27ae60'

  const pendingFilteredCount = filtered.filter(r => r.status === 'pending').length
  const allPendingSelected = pendingFilteredCount > 0 && [...selectedSnos].every(s => filtered.find(r => r.put_sno === s))
  const somePendingSelected = selectedSnos.size > 0

  const filteredOurbox = ourboxProds.filter(p =>
    !searchOurbox || (p.sale_prod_nm || '').toLowerCase().includes(searchOurbox.toLowerCase()) || (p.prod_cd || '').includes(searchOurbox)
  ).slice(0, 100)

  const filteredBh = bhItems.filter(i =>
    !searchBh || (i.name || '').toLowerCase().includes(searchBh.toLowerCase()) || (i.sku || '').includes(searchBh)
  ).slice(0, 100)

  // 이미 매핑된 상품 집합 (양쪽 목록에 "연결됨" 표시용)
  const mappedOurboxCds = new Set(mappings.map(m => m.ourbox_prod_cd))
  const mappedBhIds = new Set(mappings.map(m => m.boxhero_item_id))

  // ─── UI ────────────────────────────────────────────────────────────────────
  return (
    <div>
      {/* 헤더 */}
      <div style={{ background: '#2c3e50', padding: '14px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 0 }}>
        <h1 style={{ color: '#fff', fontSize: 18, fontWeight: 700 }}>📦 입고 정산기</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13 }}>
          <span style={{ color: '#bdc3c7', fontSize: 12 }}>
            {syncStatus.lastSyncError
              ? <span style={{ color: '#e74c3c' }}>오류: {syncStatus.lastSyncError}</span>
              : syncStatus.lastSyncTime ? `마지막: ${syncStatus.lastSyncTime}` : '마지막 동기화: -'
            }
          </span>
          <span style={{ padding: '4px 10px', borderRadius: 12, fontSize: 12, color: '#fff', background: syncDotColor, fontWeight: 600 }}>
            {syncStatus.status === 'syncing' ? '동기화중...' : syncStatus.status === 'error' ? '오류' : '대기중'}
          </span>
          <Button
            size="small"
            type="primary"
            loading={syncing || syncStatus.status === 'syncing'}
            onClick={handleSync}
            style={{ fontSize: 12 }}
          >
            🔄 지금 동기화
          </Button>
        </div>
      </div>

      {/* 탭 */}
      <div style={{ background: '#34495e', display: 'flex', padding: '0 24px' }}>
        {([['receivings', '📋 입고 목록'], ['mapping', '🔗 상품 매핑']] as [TabKey, string][]).map(([key, label]) => (
          <div
            key={key}
            onClick={() => switchTab(key)}
            style={{
              color: tab === key ? '#fff' : '#bdc3c7',
              padding: '12px 18px', cursor: 'pointer', fontSize: 14,
              borderBottom: tab === key ? '3px solid #3498db' : '3px solid transparent',
              transition: 'all 0.2s',
            }}
          >
            {label}
          </div>
        ))}
      </div>

      {/* 컨텐츠 */}
      <div style={{ padding: '20px 24px', background: '#f0f2f5', minHeight: 'calc(100vh - 180px)' }}>

        {/* ── 입고 목록 탭 ─────────────────────────────────────────────── */}
        {tab === 'receivings' && (
          <div>
            {/* 통계 카드 */}
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
              {[
                { key: 'pending', label: '대기중', count: countPending, color: '#f39c12' },
                { key: 'approved', label: '승인완료', count: countApproved, color: '#27ae60' },
                { key: 'ignored', label: '무시됨', count: countIgnored, color: '#95a5a6' },
                { key: '', label: '전체', count: filtered.length, color: '#2c3e50', right: true },
              ].map(s => (
                <div
                  key={s.key}
                  onClick={() => setStatusFilter(s.key as StatusFilter)}
                  style={{
                    background: '#fff', borderRadius: 8, padding: '12px 18px',
                    boxShadow: statusFilter === s.key ? `0 0 0 2px #3498db` : '0 1px 4px rgba(0,0,0,0.1)',
                    textAlign: 'center', minWidth: 100, cursor: 'pointer',
                    marginLeft: s.right ? 'auto' : 0,
                    transition: 'box-shadow 0.15s, transform 0.15s',
                  }}
                >
                  <div style={{ fontSize: 24, fontWeight: 700, color: s.color }}>{s.count}</div>
                  <div style={{ fontSize: 11, color: '#7f8c8d', marginTop: 2 }}>{s.label}</div>
                </div>
              ))}
            </div>

            {/* 필터 바 */}
            <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
              <select
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value as StatusFilter)}
                style={{ padding: '7px 12px', border: '1px solid #ddd', borderRadius: 6, fontSize: 13 }}
              >
                <option value="">전체 상태</option>
                <option value="pending">대기중</option>
                <option value="approved">승인완료</option>
                <option value="ignored">무시됨</option>
              </select>
              <RangePicker
                size="small"
                value={dateRange}
                onChange={v => setDateRange(v as [Dayjs, Dayjs] | null)}
                placeholder={['시작일', '종료일']}
                style={{ fontSize: 13 }}
              />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="입고번호 or 상품명 검색"
                style={{ padding: '7px 12px', border: '1px solid #ddd', borderRadius: 6, fontSize: 13, width: 200 }}
              />
            </div>

            {/* 일괄처리 바 */}
            {somePendingSelected && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
                background: '#eaf4ff', border: '1px solid #aed6f1', borderRadius: 8,
                marginBottom: 12, flexWrap: 'wrap',
              }}>
                <span style={{ fontWeight: 700, color: '#2980b9', fontSize: 14 }}>{selectedSnos.size}개 선택됨</span>
                <Popconfirm title={`${selectedSnos.size}건을 일괄 승인하시겠습니까?`} onConfirm={bulkApprove} okText="승인" cancelText="취소">
                  <button style={btnStyle('#27ae60')}>✅ 일괄 승인</button>
                </Popconfirm>
                <Popconfirm title={`${selectedSnos.size}건을 일괄 무시하시겠습니까?`} onConfirm={bulkIgnore} okText="무시" cancelText="취소">
                  <button style={btnStyle('#e74c3c')}>🚫 일괄 무시</button>
                </Popconfirm>
                <button style={btnStyle('#95a5a6')} onClick={() => setSelectedSnos(new Set())}>✕ 선택 해제</button>
                {bulkProgress.visible && (
                  <div style={{ width: '100%', marginTop: 4 }}>
                    <Progress percent={bulkProgress.pct} size="small" />
                    <div style={{ fontSize: 11, color: '#7f8c8d', marginTop: 2 }}>{bulkProgress.label}</div>
                  </div>
                )}
              </div>
            )}

            {/* 전체 선택 */}
            {filtered.some(r => r.status === 'pending') && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#555', marginBottom: 8, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={allPendingSelected && somePendingSelected}
                  onChange={e => toggleSelectAll(e.target.checked)}
                  style={{ width: 16, height: 16, cursor: 'pointer', accentColor: '#3498db' }}
                />
                <label style={{ cursor: 'pointer' }}>대기중 전체 선택</label>
              </div>
            )}

            {/* 목록 */}
            {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

            {!loading && filtered.length === 0 && (
              <div style={{ textAlign: 'center', padding: 60, color: '#95a5a6' }}>
                <div style={{ fontSize: 48, marginBottom: 12 }}>📭</div>
                <div>입고 내역이 없습니다.</div>
              </div>
            )}

            {!loading && filtered.map(r => <RecordCard
              key={r.put_sno}
              record={r}
              expanded={expanded.has(r.put_sno)}
              selected={selectedSnos.has(r.put_sno)}
              onToggle={() => setExpanded(prev => { const n = new Set(prev); n.has(r.put_sno) ? n.delete(r.put_sno) : n.add(r.put_sno); return n })}
              onSelect={() => toggleSelect(r.put_sno)}
              onApprove={() => handleApprove(r.put_sno)}
              onCancel={() => handleCancel(r.put_sno)}
              onIgnore={() => handleIgnore(r.put_sno)}
            />)}
          </div>
        )}

        {/* ── 상품 매핑 탭 ─────────────────────────────────────────────── */}
        {tab === 'mapping' && (
          <div>
            {mappingLoading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

            {!mappingLoading && (
              <>
                {/* 2열 레이아웃 */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 16 }}>
                  {/* 아워박스 */}
                  <div style={panelStyle}>
                    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
                      <h3 style={panelTitleStyle}>아워박스 상품 <span style={{ fontSize: 11, color: '#7f8c8d', fontWeight: 400 }}>({ourboxProds.length}개)</span></h3>
                      <button
                        style={{ ...btnStyle('#95a5a6'), marginLeft: 'auto', fontSize: 11 }}
                        onClick={() => setManualMode(v => !v)}
                      >✏️ 직접 입력</button>
                    </div>
                    {manualMode && (
                      <div style={{ padding: 8, background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 6, marginBottom: 8 }}>
                        <div style={{ fontSize: 12, color: '#7f8c8d', marginBottom: 6 }}>목록에 없는 상품을 직접 입력</div>
                        <input value={manualCd} onChange={e => setManualCd(e.target.value)} placeholder="상품코드 (prod_cd)" style={miniInputStyle} />
                        <input value={manualNm} onChange={e => setManualNm(e.target.value)} placeholder="상품명" style={{ ...miniInputStyle, marginTop: 4 }} />
                        <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                          <button style={btnStyle('#3498db')} onClick={applyManualInput}>이 상품으로 선택</button>
                          <button style={btnStyle('#95a5a6')} onClick={() => setManualMode(false)}>닫기</button>
                        </div>
                      </div>
                    )}
                    <input
                      value={searchOurbox} onChange={e => setSearchOurbox(e.target.value)}
                      placeholder="상품명 검색..."
                      style={{ ...miniInputStyle, marginBottom: 10 }}
                    />
                    <div style={itemListStyle}>
                      {filteredOurbox.length === 0
                        ? <div style={{ padding: 12, textAlign: 'center', color: '#7f8c8d' }}>검색 결과 없음</div>
                        : filteredOurbox.map(p => {
                          const linked = mappedOurboxCds.has(p.prod_cd)
                          return (
                          <div
                            key={p.prod_cd}
                            onClick={() => setSelOurbox((prev: typeof p | null) => prev?.prod_cd === p.prod_cd ? null : p)}
                            style={{ ...itemRowStyle, background: selOurbox?.prod_cd === p.prod_cd ? '#d6eaf8' : linked ? '#f6fff8' : undefined }}
                          >
                            <span>{linked && <span style={linkedBadgeStyle}>✅ 연결됨</span>}{p.sale_prod_nm || ''}</span>
                            <span style={{ color: '#7f8c8d', fontSize: 11, marginLeft: 8 }}>{p.prod_cd}</span>
                          </div>
                          )
                        })
                      }
                    </div>
                  </div>

                  {/* 박스히어로 */}
                  <div style={panelStyle}>
                    <h3 style={{ ...panelTitleStyle, marginBottom: 12 }}>박스히어로 상품 <span style={{ fontSize: 11, color: '#7f8c8d', fontWeight: 400 }}>({bhItems.length}개)</span></h3>
                    <input
                      value={searchBh} onChange={e => setSearchBh(e.target.value)}
                      placeholder="상품명 또는 SKU 검색..."
                      style={{ ...miniInputStyle, marginBottom: 10 }}
                    />
                    <div style={itemListStyle}>
                      {filteredBh.length === 0
                        ? <div style={{ padding: 12, textAlign: 'center', color: '#7f8c8d' }}>검색 결과 없음</div>
                        : filteredBh.map(i => {
                          const linked = mappedBhIds.has(i.id)
                          return (
                          <div
                            key={i.id}
                            onClick={() => setSelBh(prev => prev?.id === i.id ? null : i)}
                            style={{ ...itemRowStyle, background: selBh?.id === i.id ? '#d6eaf8' : linked ? '#f6fff8' : undefined }}
                          >
                            <span>{linked && <span style={linkedBadgeStyle}>✅ 연결됨</span>}{i.name}</span>
                            <span style={{ color: '#7f8c8d', fontSize: 11, marginLeft: 8 }}>{i.sku || ''}</span>
                          </div>
                          )
                        })
                      }
                    </div>
                  </div>
                </div>

                {/* 매핑 확인 패널 */}
                {selOurbox && selBh && (
                  <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.1)', padding: 16, marginBottom: 16 }}>
                    <h3 style={{ fontSize: 15, marginBottom: 12 }}>🔗 매핑 연결</h3>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 10, background: '#f8f9fa', borderRadius: 6, fontSize: 13, marginBottom: 12 }}>
                      <span>📦 {selOurbox.sale_prod_nm}<br /><small style={{ color: '#7f8c8d' }}>{selOurbox.prod_cd}</small></span>
                      <span style={{ color: '#3498db', fontSize: 18 }}>→</span>
                      <span>🗃 {selBh.name}<br /><small style={{ color: '#7f8c8d' }}>SKU: {selBh.sku || '-'}</small></span>
                    </div>
                    <button style={btnStyle('#27ae60')} onClick={handleSaveMapping} disabled={savingMap}>✅ 매핑 저장</button>
                    <button style={{ ...btnStyle('#95a5a6'), marginLeft: 8 }} onClick={() => { setSelOurbox(null); setSelBh(null) }}>취소</button>
                  </div>
                )}

                {/* 저장된 매핑 목록 */}
                <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.1)' }}>
                  <div style={{ padding: '14px 18px', borderBottom: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{ fontWeight: 700, fontSize: 15, color: '#2c3e50' }}>📌 저장된 매핑 목록 <span style={{ fontSize: 12, color: '#7f8c8d', fontWeight: 400 }}>({mappings.length}개)</span></span>
                    <Popconfirm
                      title="바코드 기반 자동 매핑을 실행합니다. 기존 매핑은 유지됩니다."
                      onConfirm={handleAutoMap}
                      okText="실행" cancelText="취소"
                    >
                      <button style={{ ...btnStyle('#3498db'), marginLeft: 'auto' }} disabled={autoMapping}>
                        {autoMapping ? '⏳ 매핑 중...' : '⚡ 바코드 자동 매핑'}
                      </button>
                    </Popconfirm>
                  </div>
                  {mappings.length === 0
                    ? <div style={{ padding: 20, textAlign: 'center', color: '#7f8c8d' }}>매핑 없음. 위에서 상품을 선택해 연결하세요.</div>
                    : mappings.map(m => (
                      <div key={m.ourbox_prod_cd} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderBottom: '1px solid #f0f0f0', fontSize: 13 }}>
                        <span style={{ flex: 1 }}>{m.ourbox_prod_nm || m.ourbox_prod_cd}</span>
                        <span style={{ color: '#3498db', margin: '0 8px' }}>→</span>
                        <span style={{ flex: 1 }}>{m.boxhero_item_nm} <small style={{ color: '#7f8c8d' }}>(SKU: {m.boxhero_sku || '-'})</small></span>
                        <Popconfirm title="매핑을 삭제하시겠습니까?" onConfirm={() => handleDeleteMapping(m.ourbox_prod_cd)} okText="삭제" cancelText="취소">
                          <button style={btnStyle('#e74c3c')}>삭제</button>
                        </Popconfirm>
                      </div>
                    ))
                  }
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── 레코드 카드 컴포넌트 ──────────────────────────────────────────────────────

interface CardProps {
  record: ReceivingRecord
  expanded: boolean
  selected: boolean
  onToggle: () => void
  onSelect: () => void
  onApprove: () => void
  onCancel: () => void
  onIgnore: () => void
}

function RecordCard({ record: r, expanded, selected, onToggle, onSelect, onApprove, onCancel, onIgnore }: CardProps) {
  const items = r.items || []
  const mappedCount = items.filter((i: any) => i.boxhero_item_id).length
  const statusIcon = r.status === 'approved' ? '✅' : r.status === 'ignored' ? '🚫' : '⏳'
  const badgeStyle: React.CSSProperties = r.status === 'approved'
    ? { background: '#d4edda', color: '#155724' }
    : r.status === 'ignored'
    ? { background: '#f8d7da', color: '#721c24' }
    : { background: '#ffeeba', color: '#856404' }
  const badgeText = r.status === 'approved' ? '승인완료' : r.status === 'ignored' ? '무시됨' : '대기중'

  return (
    <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.1)', marginBottom: 12, overflow: 'hidden' }}>
      {/* 헤더 */}
      <div
        onClick={onToggle}
        style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', borderBottom: '1px solid #f0f0f0', transition: 'background 0.15s' }}
        onMouseEnter={e => (e.currentTarget.style.background = '#f8f9fa')}
        onMouseLeave={e => (e.currentTarget.style.background = '')}
      >
        {/* 체크박스 */}
        {r.status === 'pending'
          ? <input type="checkbox" checked={selected} onChange={e => { e.stopPropagation(); onSelect() }} onClick={e => e.stopPropagation()} style={{ width: 18, height: 18, cursor: 'pointer', accentColor: '#3498db', flexShrink: 0 }} />
          : <span style={{ width: 18, display: 'inline-block', flexShrink: 0 }} />
        }
        <span style={{ fontSize: 20, minWidth: 28, textAlign: 'center' }}>{statusIcon}</span>
        <span style={{ fontWeight: 700, color: '#2c3e50', fontSize: 15, minWidth: 60 }}>#{r.put_sno}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{r.put_depot_nm} · {r.vendor_nm} · {r.put_type_nm}</div>
          <div style={{ fontSize: 12, color: '#7f8c8d', marginTop: 2 }}>
            입고요청: {r.put_req_dt || '-'} · 완료: {r.put_compt_dtm?.slice(0, 16) || '-'} · 상품 {r.item_cnt}종 · 총 {r.tot_put_qty?.toLocaleString()}개 · 매핑: {mappedCount}/{items.length}
          </div>
        </div>
        <span style={{ fontSize: 11, padding: '3px 8px', borderRadius: 10, fontWeight: 600, ...badgeStyle }}>{badgeText}</span>
        {/* 액션 버튼 */}
        <div onClick={e => e.stopPropagation()} style={{ display: 'flex', gap: 8 }}>
          {r.status === 'pending' && (
            <>
              <Popconfirm title={`#${r.put_sno}를 박스히어로에 등록하시겠습니까?`} onConfirm={onApprove} okText="승인" cancelText="취소">
                <button style={btnStyle('#27ae60')}>✅ 승인</button>
              </Popconfirm>
              <Popconfirm title="무시하시겠습니까?" onConfirm={onIgnore} okText="무시" cancelText="취소">
                <button style={btnStyle('#e74c3c')}>🚫 무시</button>
              </Popconfirm>
            </>
          )}
          {r.status === 'approved' && (
            <>
              <span style={{ fontSize: 12, color: '#27ae60' }}>TX#{r.boxhero_tx_id || '-'}</span>
              <Popconfirm title="박스히어로 등록을 취소하시겠습니까?" onConfirm={onCancel} okText="취소" cancelText="닫기">
                <button style={{ ...btnStyle('#856404'), background: '#fff3cd', border: '1px solid #ffc107' }}>🔄 취소</button>
              </Popconfirm>
            </>
          )}
        </div>
      </div>

      {/* 상세 (펼쳤을 때) */}
      {expanded && (
        <div style={{ padding: '14px 18px', background: '#f8f9fa' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr>{['상품명', '아워박스 상품코드', '입고수량', '박스히어로 매핑'].map(h => (
                <th key={h} style={{ background: '#2c3e50', color: '#fff', padding: '8px 12px', textAlign: 'left' }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {items.map((i: ReceivingItem, idx: number) => (
                <tr key={idx}>
                  <td style={{ padding: '7px 12px', borderBottom: '1px solid #e9ecef' }}>{i.sale_prod_nm}</td>
                  <td style={{ padding: '7px 12px', borderBottom: '1px solid #e9ecef' }}>{i.prod_cd}</td>
                  <td style={{ padding: '7px 12px', borderBottom: '1px solid #e9ecef', textAlign: 'right' }}>{i.put_qty?.toLocaleString()}</td>
                  <td style={{ padding: '7px 12px', borderBottom: '1px solid #e9ecef' }}>
                    {i.boxhero_item_id
                      ? <span style={{ color: '#27ae60', fontWeight: 600 }}>✅ {i.boxhero_item_nm}</span>
                      : <span style={{ color: '#e74c3c' }}>❌ 매핑 없음</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {r.status === 'pending' && (
            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
              <Popconfirm title={`#${r.put_sno}를 박스히어로에 등록하시겠습니까?`} onConfirm={onApprove} okText="승인" cancelText="취소">
                <button style={btnStyle('#27ae60')}>✅ 박스히어로에 입고 등록</button>
              </Popconfirm>
              <Popconfirm title="무시하시겠습니까?" onConfirm={onIgnore} okText="무시" cancelText="취소">
                <button style={btnStyle('#e74c3c')}>🚫 무시</button>
              </Popconfirm>
              {mappedCount < items.length && (
                <span style={{ fontSize: 12, color: '#e74c3c' }}>⚠ {items.length - mappedCount}개 상품 매핑 없음 (매핑된 {mappedCount}개만 등록됨)</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── 스타일 헬퍼 ───────────────────────────────────────────────────────────────

function btnStyle(bg: string): React.CSSProperties {
  return { padding: '4px 10px', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 12, fontFamily: 'inherit', background: bg, color: '#fff' }
}

const panelStyle: React.CSSProperties = { background: '#fff', borderRadius: 10, boxShadow: '0 1px 4px rgba(0,0,0,0.1)', padding: 16 }
const panelTitleStyle: React.CSSProperties = { fontSize: 15, margin: 0, color: '#2c3e50', paddingBottom: 8, borderBottom: '2px solid #3498db', display: 'inline-block' }
const itemListStyle: React.CSSProperties = { maxHeight: 400, overflowY: 'auto', border: '1px solid #e9ecef', borderRadius: 6 }
const itemRowStyle: React.CSSProperties = { padding: '9px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #f0f0f0', cursor: 'pointer', fontSize: 13, transition: 'background 0.1s' }
const miniInputStyle: React.CSSProperties = { width: '100%', padding: '8px 12px', border: '1px solid #ddd', borderRadius: 6, fontSize: 13 }
const linkedBadgeStyle: React.CSSProperties = { fontSize: 10, color: '#27ae60', background: '#d4edda', borderRadius: 4, padding: '1px 5px', marginRight: 6, fontWeight: 600 }
