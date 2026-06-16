import { useState, useEffect, useRef } from 'react'
import {
  Button, Input, Select, Table, Tabs, Tag, Alert, Spin,
  Slider, Popconfirm, message, Tooltip, Row, Col,
} from 'antd'
import {
  CheckOutlined, DeleteOutlined, PlusOutlined,
  ThunderboltOutlined, RobotOutlined, SearchOutlined,
  LinkOutlined, DisconnectOutlined,
} from '@ant-design/icons'
import type { AppConfig } from '../types'
import {
  getNameMappings, getBhItemsForMapping, saveNameMapping,
  confirmNameMapping, deleteNameMapping, autoMatch, aiSuggestMapping,
  getObProducts, createMappingLink, removeMappingLink,
  getObChannels, getBhChannels, getChannelMappings,
  createChannelLink, removeChannelLink, getSuggestMapping,
  getSetBoms, createSetBom, deleteSetBom, type SetBom,
  getMappingAudit, deleteMapping,
} from '../services/api'

interface ChannelMapping { id: number; ob_channel: string; bh_keyword: string; confirmed: number }
interface ObChannel { name: string; mapped: boolean }
interface BhKeyword { keyword: string; count: number; sample: string; mapped: boolean }

interface Props { config: AppConfig }

interface NameMapping {
  id: number
  ob_name: string
  bh_sku: string
  bh_name: string
  match_score: number
  match_method: string
  confirmed: number
  created_at: string
}

interface BhItem { id: number; name: string; sku: string; barcode?: string }

export default function MappingPage({ config }: Props) {
  const [mappings, setMappings] = useState<NameMapping[]>([])
  const [bhItems, setBhItems] = useState<BhItem[]>([])
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('connect')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'confirmed' | 'unconfirmed'>('all')

  // 연결(듀얼리스트) 탭
  const [obProducts, setObProducts] = useState<{ code: string; name: string; mapped_count: number }[]>([])
  const [obLoading, setObLoading] = useState(false)
  const [obError, setObError] = useState('')
  const [selectedOb, setSelectedOb] = useState<string | null>(null)
  const [obSearch, setObSearch] = useState('')
  const [bhSearch, setBhSearch] = useState('')
  const [linkBusy, setLinkBusy] = useState('')  // 진행 중인 bh_sku
  const [connectThreshold, setConnectThreshold] = useState(80)
  const [autoRunning, setAutoRunning] = useState(false)

  // 채널 매핑 탭
  const [obChannels, setObChannels] = useState<ObChannel[]>([])
  const [bhKeywords, setBhKeywords] = useState<BhKeyword[]>([])
  const [channelMappings, setChannelMappings] = useState<ChannelMapping[]>([])
  const [chLoading, setChLoading] = useState(false)
  const [chError, setChError] = useState('')
  const [selectedObCh, setSelectedObCh] = useState<string | null>(null)
  const [chLinkBusy, setChLinkBusy] = useState('')
  const [chLoaded, setChLoaded] = useState(false)

  // 미매핑 제안
  const [suggestData, setSuggestData] = useState<{
    bh_unmapped: {sku: string; name: string}[]
    ob_unmapped: {prod_cd: string; name: string}[]
    suggestions: {score: number; bh_sku: string; bh_name: string; ob_prod_cd: string; ob_name: string}[]
  } | null>(null)
  const [suggestLoading, setSuggestLoading] = useState(false)
  const [suggestFrom, setSuggestFrom] = useState('')
  const [suggestTo, setSuggestTo] = useState('')

  // 세트 BOM
  const [setBoms, setSetBoms] = useState<SetBom[]>([])
  const [setBomLoading, setSetBomLoading] = useState(false)
  // 세트 제품 (1개)
  const [bomSetSku, setBomSetSku] = useState('')
  const [bomSetName, setBomSetName] = useState('')
  // 구성 단품 목록 (여러 개)
  type CompRow = { component_sku: string; component_name: string; qty_per_set: number; note: string }
  const [bomComps, setBomComps] = useState<CompRow[]>([{ component_sku: '', component_name: '', qty_per_set: 1, note: '' }])
  const [addingBom, setAddingBom] = useState(false)

  const loadSetBoms = async () => {
    setSetBomLoading(true)
    try { setSetBoms(await getSetBoms()) } catch { /* ignore */ } finally { setSetBomLoading(false) }
  }

  // 수동 추가
  const [manualOb, setManualOb] = useState('')
  const [manualBh, setManualBh] = useState<string | null>(null)
  const [addingManual, setAddingManual] = useState(false)

  // 의심 매핑 검사
  type AuditRow = { table: string; score: number; ob_name: string; bh_name: string; ob_code: string; bh_sku: string }
  const [auditRows, setAuditRows] = useState<AuditRow[]>([])
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditDone, setAuditDone] = useState(false)
  async function runAudit() {
    setAuditLoading(true); setAuditDone(false)
    try {
      const d = await getMappingAudit(50)
      setAuditRows(d.suspicious || [])
      setAuditDone(true)
    } catch { message.error('검사 실패') }
    finally { setAuditLoading(false) }
  }
  async function deleteAuditRow(r: AuditRow) {
    try {
      if (r.table === 'product_mapping' && r.ob_code) {
        await deleteMapping(r.ob_code)
      } else {
        await removeMappingLink({ ob_name: r.ob_name, bh_sku: r.bh_sku })
      }
      message.success('매핑 삭제됨')
      setAuditRows(prev => prev.filter(x => !(x.ob_name === r.ob_name && x.bh_sku === r.bh_sku && x.table === r.table)))
      loadAll()
    } catch { message.error('삭제 실패') }
  }

  // 자동 매핑
  const [obInput, setObInput] = useState('')
  const [threshold, setThreshold] = useState(70)
  const [autoResults, setAutoResults] = useState<unknown[]>([])
  const [autoLoading, setAutoLoading] = useState(false)

  // AI 매핑
  const [aiText, setAiText] = useState('')
  const [aiSuggestions, setAiSuggestions] = useState<unknown[]>([])
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')
  const aiRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    loadAll()
    loadObProducts()
  }, [config.api_token])

  async function loadAll() {
    setLoading(true)
    try {
      const [m, bh] = await Promise.all([
        getNameMappings(),
        config.api_token ? getBhItemsForMapping(config.api_token) : Promise.resolve({ items: [] }),
      ])
      setMappings(m)
      setBhItems(bh.items || [])
    } catch { /* ignore */ } finally {
      setLoading(false)
    }
  }

  async function loadObProducts() {
    setObLoading(true); setObError('')
    try {
      const data = await getObProducts()
      setObProducts(data.products || [])
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      setObError(msg)
    } finally {
      setObLoading(false)
    }
  }

  // 선택된 OB 상품명에 연결된 BH SKU 집합
  const linkedSkus = new Set(
    selectedOb ? mappings.filter(m => m.ob_name === selectedOb).map(m => m.bh_sku) : []
  )

  async function toggleLink(bh: BhItem) {
    if (!selectedOb) { message.warning('먼저 왼쪽에서 OurBox 상품을 선택하세요'); return }
    setLinkBusy(bh.sku)
    const isLinked = linkedSkus.has(bh.sku)
    try {
      if (isLinked) {
        await removeMappingLink({ ob_name: selectedOb, bh_sku: bh.sku })
        setMappings(prev => prev.filter(m => !(m.ob_name === selectedOb && m.bh_sku === bh.sku)))
        setObProducts(prev => prev.map(p => p.name === selectedOb ? { ...p, mapped_count: Math.max(0, p.mapped_count - 1) } : p))
      } else {
        await createMappingLink({ ob_name: selectedOb, bh_sku: bh.sku, bh_name: bh.name, confirmed: 1 })
        setMappings(prev => [
          ...prev,
          { id: Date.now(), ob_name: selectedOb, bh_sku: bh.sku, bh_name: bh.name, match_score: 100, match_method: 'manual', confirmed: 1, created_at: '' },
        ])
        setObProducts(prev => prev.map(p => p.name === selectedOb ? { ...p, mapped_count: p.mapped_count + 1 } : p))
      }
    } catch {
      message.error('연결 변경 실패')
    } finally {
      setLinkBusy('')
    }
  }

  async function loadChannels() {
    setChLoading(true); setChError('')
    try {
      const [obc, cm, bhc] = await Promise.all([
        getObChannels(30),
        getChannelMappings(),
        config.api_token ? getBhChannels(config.api_token, 30) : Promise.resolve({ keywords: [] }),
      ])
      setObChannels(obc.channels || [])
      setChannelMappings(cm || [])
      setBhKeywords(bhc.keywords || [])
      setChLoaded(true)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      setChError(msg)
    } finally {
      setChLoading(false)
    }
  }

  const linkedKeywords = new Set(
    selectedObCh ? channelMappings.filter(m => m.ob_channel === selectedObCh).map(m => m.bh_keyword) : []
  )

  async function toggleChannelLink(kw: string) {
    if (!selectedObCh) { message.warning('먼저 왼쪽에서 OurBox 채널을 선택하세요'); return }
    setChLinkBusy(kw)
    const isLinked = linkedKeywords.has(kw)
    try {
      if (isLinked) {
        await removeChannelLink({ ob_channel: selectedObCh, bh_keyword: kw })
        setChannelMappings(prev => prev.filter(m => !(m.ob_channel === selectedObCh && m.bh_keyword === kw)))
      } else {
        await createChannelLink({ ob_channel: selectedObCh, bh_keyword: kw, confirmed: 1 })
        setChannelMappings(prev => [...prev, { id: Date.now(), ob_channel: selectedObCh, bh_keyword: kw, confirmed: 1 }])
      }
    } catch {
      message.error('채널 연결 변경 실패')
    } finally {
      setChLinkBusy('')
    }
  }

  async function handleConnectAutoMatch() {
    if (!config.api_token) { message.warning('BoxHero API 토큰이 필요합니다'); return }
    if (obProducts.length === 0) { message.warning('OurBox 상품 목록이 비어 있습니다'); return }
    setAutoRunning(true)
    try {
      const names = obProducts.map(p => p.name).filter(Boolean)
      const res = await autoMatch(config.api_token, names, connectThreshold)
      await Promise.all([loadAll(), loadObProducts()])
      message.success(`자동 매핑 ${res.matched}/${res.total}건 연결됨 — 이어서 수동으로 보완하세요`)
    } catch {
      message.error('자동 매핑 실패')
    } finally {
      setAutoRunning(false)
    }
  }

  async function handleConfirm(obName: string) {
    await confirmNameMapping(obName)
    setMappings(prev => prev.map(m => m.ob_name === obName ? { ...m, confirmed: 1 } : m))
    message.success('확인 완료')
  }

  async function handleDelete(obName: string) {
    await deleteNameMapping(obName)
    setMappings(prev => prev.filter(m => m.ob_name !== obName))
    message.success('삭제됨')
  }

  async function handleAddManual() {
    if (!manualOb.trim() || !manualBh) return
    setAddingManual(true)
    const bhItem = bhItems.find(b => b.sku === manualBh)
    await saveNameMapping({
      ob_name: manualOb.trim(),
      bh_sku: manualBh,
      bh_name: bhItem?.name || '',
      score: 100,
      method: 'manual',
      confirmed: 1,
    })
    setManualOb(''); setManualBh(null)
    await loadAll()
    message.success('매핑 추가됨')
    setAddingManual(false)
  }

  async function handleAutoMatch() {
    if (!config.api_token) return
    const names = obInput.split('\n').map(s => s.trim()).filter(Boolean)
    if (!names.length) { message.warning('OurBox 상품명을 입력하세요'); return }
    setAutoLoading(true)
    try {
      const res = await autoMatch(config.api_token, names, threshold)
      setAutoResults(res.results || [])
      await loadAll()
      message.success(`${res.matched}/${res.total}개 자동 매핑 완료`)
    } catch { message.error('자동 매핑 실패') } finally {
      setAutoLoading(false)
    }
  }

  async function handleAiSuggest() {
    if (!config.api_token || !config.gemini_api_key) return
    const names = obInput.split('\n').map(s => s.trim()).filter(Boolean)
    if (!names.length) { message.warning('OurBox 상품명을 입력하세요'); return }
    setAiLoading(true); setAiText(''); setAiSuggestions([]); setAiError('')
    let fullText = ''

    await aiSuggestMapping(
      {
        ob_names: names.slice(0, 50),
        bh_items: bhItems.slice(0, 100).map(b => ({ sku: b.sku, name: b.name })),
        gemini_api_key: config.gemini_api_key,
      },
      (text) => { fullText += text; setAiText(fullText) },
      () => {
        setAiLoading(false)
        // JSON 파싱
        try {
          const match = fullText.match(/\[[\s\S]*\]/)
          if (match) {
            const parsed = JSON.parse(match[0])
            setAiSuggestions(parsed)
          }
        } catch { /* ignore */ }
      },
      (err) => { setAiError(err); setAiLoading(false) },
    )
  }

  async function loadSuggest() {
    if (!config.api_token || !suggestFrom || !suggestTo) {
      message.warning('조회 날짜 범위를 입력해주세요')
      return
    }
    setSuggestLoading(true)
    try {
      const data = await getSuggestMapping({
        token: config.api_token,
        from_date: suggestFrom,
        to_date: suggestTo,
      })
      setSuggestData(data)
    } catch {
      message.error('미매핑 조회 실패')
    } finally {
      setSuggestLoading(false)
    }
  }

  async function handleSuggestLink(bh_sku: string, ob_name: string, bh_name: string) {
    try {
      await createMappingLink({ ob_name, bh_sku, bh_name, confirmed: 1 })
      message.success(`연결 완료: ${bh_sku} ↔ ${ob_name}`)
      await loadSuggest()
    } catch {
      message.error('연결 실패')
    }
  }

  async function handleSaveAiSuggestion(s: Record<string, unknown>) {
    await saveNameMapping({
      ob_name: s.ob_name as string,
      bh_sku: s.bh_sku as string,
      bh_name: s.bh_name as string,
      score: (s.confidence as number) || 0,
      method: 'ai',
      confirmed: 1,
    })
    setAiSuggestions(prev => (prev as Record<string, unknown>[]).filter(x => x.ob_name !== s.ob_name))
    await loadAll()
    message.success('AI 제안 매핑 저장됨')
  }

  const filtered = mappings.filter(m => {
    if (filter === 'confirmed' && !m.confirmed) return false
    if (filter === 'unconfirmed' && m.confirmed) return false
    if (search) return m.ob_name.includes(search) || m.bh_name.includes(search) || m.bh_sku.includes(search)
    return true
  })

  const listColumns = [
    {
      title: 'OurBox 이름',
      dataIndex: 'ob_name', key: 'ob_name', ellipsis: true,
      render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
    },
    {
      title: 'BoxHero SKU',
      dataIndex: 'bh_sku', key: 'bh_sku', width: 140,
      render: (v: string) => <code style={{ fontSize: '0.78rem' }}>{v}</code>,
    },
    {
      title: 'BoxHero 이름',
      dataIndex: 'bh_name', key: 'bh_name', ellipsis: true,
      render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
    },
    {
      title: '신뢰도',
      dataIndex: 'match_score', key: 'match_score', width: 80, align: 'right' as const,
      render: (v: number, r: NameMapping) => (
        <Tooltip title={`방식: ${r.match_method}`}>
          <span style={{ color: v >= 90 ? '#10b981' : v >= 70 ? '#f59e0b' : '#ef4444' }}>
            {v > 0 ? `${v.toFixed(0)}%` : '수동'}
          </span>
        </Tooltip>
      ),
    },
    {
      title: '상태',
      dataIndex: 'confirmed', key: 'confirmed', width: 80,
      render: (v: number) => v
        ? <Tag color="success">확인됨</Tag>
        : <Tag color="warning">미확인</Tag>,
    },
    {
      title: '작업',
      key: 'actions', width: 100,
      render: (_: unknown, r: NameMapping) => (
        <div style={{ display: 'flex', gap: 4 }}>
          {!r.confirmed && (
            <Button size="small" type="primary" icon={<CheckOutlined />}
              onClick={() => handleConfirm(r.ob_name)} />
          )}
          <Popconfirm title="삭제하시겠습니까?" onConfirm={() => handleDelete(r.ob_name)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </div>
      ),
    },
  ]

  const hasApiKey = !!config.gemini_api_key || !!config.groq_api_key || !!config.claude_api_key

  const obFiltered = obProducts.filter(p =>
    !obSearch || p.name.toLowerCase().includes(obSearch.toLowerCase()) || p.code.includes(obSearch))
  const bhFiltered = bhItems.filter(b =>
    !bhSearch || (b.name || '').toLowerCase().includes(bhSearch.toLowerCase()) || (b.sku || '').includes(bhSearch))
  // 선택된 OB에 연결된 BoxHero 항목을 맨 위로 정렬
  const bhSorted = selectedOb
    ? [...bhFiltered].sort((a, b) => Number(linkedSkus.has(b.sku)) - Number(linkedSkus.has(a.sku)))
    : bhFiltered
  const firstUnlinkedIdx = selectedOb ? bhSorted.findIndex(b => !linkedSkus.has(b.sku)) : -1

  // 채널: OB 채널별 연결 수
  const chLinkCount = (obName: string) => channelMappings.filter(m => m.ob_channel === obName).length
  const bhKwSorted = selectedObCh
    ? [...bhKeywords].sort((a, b) => Number(linkedKeywords.has(b.keyword)) - Number(linkedKeywords.has(a.keyword)))
    : bhKeywords

  function handleTabChange(key: string) {
    setActiveTab(key)
    if (key === 'channel' && !chLoaded && !chLoading) loadChannels()
    if (key === 'set_bom') loadSetBoms()
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">상품 매핑</h1>
        <p className="page-desc">OurBox ↔ BoxHero 동일 상품 이름 연결 관리</p>
      </div>

      <Tabs activeKey={activeTab} onChange={handleTabChange} items={[
        {
          key: 'connect',
          label: '🔗 연결',
          children: (
            <div>
              <Alert
                type="info"
                showIcon
                message="자동 매핑으로 비슷한 이름을 먼저 연결한 뒤, 왼쪽 OurBox 상품을 선택하고 오른쪽 BoxHero 상품을 클릭해 수동으로 보완하세요. 한 상품에 여러 개 연결(상품명이 여러 SKU로 나뉜 경우), 같은 BoxHero를 여러 OurBox에 연결하는 것도 가능합니다."
                style={{ marginBottom: 12 }}
              />
              {/* 자동 매핑 바 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: '10px 16px', marginBottom: 12 }}>
                <span style={{ fontWeight: 600, fontSize: '0.82rem' }}>
                  <ThunderboltOutlined /> 1단계 · 자동 매핑
                </span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.8rem' }}>
                  <span>최소 유사도</span>
                  <Slider min={50} max={100} value={connectThreshold} onChange={setConnectThreshold} style={{ width: 140 }} />
                  <strong>{connectThreshold}%</strong>
                </div>
                <Button type="primary" size="small" icon={<ThunderboltOutlined />}
                  loading={autoRunning} onClick={handleConnectAutoMatch}
                  disabled={!config.api_token || obProducts.length === 0}>
                  자동 매핑 실행
                </Button>
                <span style={{ fontSize: '0.75rem', color: '#92400e' }}>
                  전체 {obProducts.length}개 상품을 BoxHero와 자동 매칭 → 이어서 수동 보완
                </span>
              </div>
              {obError && (
                <Alert
                  type="warning" showIcon style={{ marginBottom: 12 }}
                  message="OurBox 상품 목록을 불러오지 못했습니다"
                  description={obError}
                  action={<Button size="small" onClick={loadObProducts}>다시 시도</Button>}
                />
              )}
              {selectedOb && (
                <div style={{ marginBottom: 10, padding: '8px 14px', background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 8, fontSize: '0.85rem' }}>
                  선택됨: <strong>{selectedOb}</strong>
                  <span style={{ marginLeft: 10, color: '#059669' }}>연결 {linkedSkus.size}개</span>
                  <span style={{ marginLeft: 10, color: '#6b7280' }}>→ 오른쪽에서 BoxHero 상품을 클릭해 연결/해제</span>
                </div>
              )}
              <Row gutter={12}>
                {/* OurBox */}
                <Col span={12}>
                  <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden' }}>
                    <div style={{ background: '#f9fafb', padding: '8px 12px', borderBottom: '1px solid #e5e7eb', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 700, fontSize: '0.85rem' }}>OurBox 상품 ({obProducts.length})</span>
                      <Button size="small" onClick={loadObProducts} loading={obLoading} style={{ marginLeft: 'auto' }}>새로고침</Button>
                    </div>
                    <div style={{ padding: 8 }}>
                      <Input.Search size="small" placeholder="이름/코드 검색" allowClear
                        value={obSearch} onChange={e => setObSearch(e.target.value)} />
                    </div>
                    <div style={{ maxHeight: 480, overflow: 'auto' }}>
                      {obLoading ? (
                        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
                      ) : obFiltered.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: '0.82rem' }}>상품 없음</div>
                      ) : obFiltered.map(p => (
                        <div key={p.code + p.name}
                          onClick={() => setSelectedOb(p.name)}
                          style={{
                            padding: '8px 12px', cursor: 'pointer', fontSize: '0.82rem',
                            borderBottom: '1px solid #f3f4f6',
                            background: selectedOb === p.name ? '#d1fae5' : '#fff',
                            display: 'flex', alignItems: 'center', gap: 8,
                          }}>
                          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                          {p.mapped_count > 0 && <Tag color="success" style={{ margin: 0 }}>{p.mapped_count}</Tag>}
                        </div>
                      ))}
                    </div>
                  </div>
                </Col>
                {/* BoxHero */}
                <Col span={12}>
                  <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden' }}>
                    <div style={{ background: '#f9fafb', padding: '8px 12px', borderBottom: '1px solid #e5e7eb', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 700, fontSize: '0.85rem' }}>BoxHero 상품 ({bhItems.length})</span>
                      {!config.api_token && <span style={{ fontSize: '0.72rem', color: '#ef4444' }}>토큰 필요</span>}
                    </div>
                    <div style={{ padding: 8 }}>
                      <Input.Search size="small" placeholder="이름/SKU 검색" allowClear
                        value={bhSearch} onChange={e => setBhSearch(e.target.value)} />
                    </div>
                    <div style={{ maxHeight: 480, overflow: 'auto' }}>
                      {bhSorted.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: '0.82rem' }}>상품 없음</div>
                      ) : bhSorted.map((b, idx) => {
                        const linked = linkedSkus.has(b.sku)
                        return (
                          <div key={b.id ?? b.sku}>
                            {/* 연결된 항목 헤더 */}
                            {selectedOb && idx === 0 && linked && (
                              <div style={{ padding: '4px 12px', fontSize: '0.72rem', fontWeight: 700, color: '#2563eb', background: '#eff6ff' }}>
                                ✓ 연결된 항목 ({linkedSkus.size})
                              </div>
                            )}
                            {/* 미연결 구분선 */}
                            {selectedOb && idx === firstUnlinkedIdx && firstUnlinkedIdx > 0 && (
                              <div style={{ padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600, color: '#9ca3af', background: '#f9fafb' }}>
                                나머지 상품
                              </div>
                            )}
                            <div
                              onClick={() => toggleLink(b)}
                              style={{
                                padding: '8px 12px', cursor: selectedOb ? 'pointer' : 'not-allowed',
                                fontSize: '0.82rem', borderBottom: '1px solid #f3f4f6',
                                background: linked ? '#dbeafe' : '#fff', opacity: selectedOb ? 1 : 0.55,
                                display: 'flex', alignItems: 'center', gap: 8,
                              }}>
                              {linked
                                ? <LinkOutlined style={{ color: '#2563eb' }} />
                                : <DisconnectOutlined style={{ color: '#d1d5db' }} />}
                              <div style={{ flex: 1, overflow: 'hidden' }}>
                                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.name}</div>
                                <code style={{ fontSize: '0.72rem', color: '#9ca3af' }}>{b.sku}</code>
                              </div>
                              {linkBusy === b.sku && <Spin size="small" />}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </Col>
              </Row>
            </div>
          ),
        },
        {
          key: 'channel',
          label: '🏬 채널 매핑',
          children: (
            <div>
              <Alert
                type="info"
                showIcon
                message="왼쪽 OurBox 채널을 선택하고, 오른쪽 BoxHero 거래처를 클릭해 연결합니다. 1:N 연결 가능(홈쇼핑_롯데↔홈쇼핑+롯데홈쇼핑 등). 연결 후 재고대사에서 '채널별 구분'을 켜면 채널 단위로 비교됩니다."
                style={{ marginBottom: 12 }}
              />
              {chError && (
                <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                  message="채널 목록을 불러오지 못했습니다" description={chError}
                  action={<Button size="small" onClick={loadChannels}>다시 시도</Button>} />
              )}
              {selectedObCh && (
                <div style={{ marginBottom: 10, padding: '8px 14px', background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 8, fontSize: '0.85rem' }}>
                  선택됨: <strong>{selectedObCh}</strong>
                  <span style={{ marginLeft: 10, color: '#059669' }}>연결 {linkedKeywords.size}개</span>
                  <span style={{ marginLeft: 10, color: '#6b7280' }}>→ 오른쪽 BoxHero 키워드를 클릭해 연결/해제</span>
                </div>
              )}
              <Row gutter={12}>
                {/* OurBox 채널 */}
                <Col span={12}>
                  <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden' }}>
                    <div style={{ background: '#f9fafb', padding: '8px 12px', borderBottom: '1px solid #e5e7eb', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 700, fontSize: '0.85rem' }}>OurBox 채널 ({obChannels.length})</span>
                      <Button size="small" onClick={loadChannels} loading={chLoading} style={{ marginLeft: 'auto' }}>새로고침</Button>
                    </div>
                    <div style={{ maxHeight: 480, overflow: 'auto' }}>
                      {chLoading ? (
                        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
                      ) : obChannels.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: '0.82rem' }}>채널 없음</div>
                      ) : obChannels.map(c => {
                        const cnt = chLinkCount(c.name)
                        return (
                          <div key={c.name} onClick={() => setSelectedObCh(c.name)}
                            style={{
                              padding: '8px 12px', cursor: 'pointer', fontSize: '0.82rem',
                              borderBottom: '1px solid #f3f4f6',
                              background: selectedObCh === c.name ? '#d1fae5' : '#fff',
                              display: 'flex', alignItems: 'center', gap: 8,
                            }}>
                            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                            {cnt > 0 && <Tag color="success" style={{ margin: 0 }}>{cnt}</Tag>}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </Col>
                {/* BoxHero 키워드 */}
                <Col span={12}>
                  <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden' }}>
                    <div style={{ background: '#f9fafb', padding: '8px 12px', borderBottom: '1px solid #e5e7eb', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 700, fontSize: '0.85rem' }}>BoxHero 거래처 ({bhKeywords.length})</span>
                      {!config.api_token && <span style={{ fontSize: '0.72rem', color: '#ef4444' }}>토큰 필요</span>}
                    </div>
                    <div style={{ maxHeight: 480, overflow: 'auto' }}>
                      {bhKwSorted.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: '0.82rem' }}>거래처 없음</div>
                      ) : (() => {
                        const linkedList = bhKwSorted.filter(k => linkedKeywords.has(k.keyword))
                        const unlinkedList = bhKwSorted.filter(k => !linkedKeywords.has(k.keyword))
                        const renderItem = (k: typeof bhKwSorted[0]) => {
                          const linked = linkedKeywords.has(k.keyword)
                          return (
                            <div key={k.keyword} onClick={() => toggleChannelLink(k.keyword)}
                              style={{
                                padding: '8px 12px', cursor: selectedObCh ? 'pointer' : 'not-allowed',
                                fontSize: '0.82rem', borderBottom: '1px solid #f3f4f6',
                                background: linked ? '#dbeafe' : '#fff', opacity: selectedObCh ? 1 : 0.55,
                                display: 'flex', alignItems: 'center', gap: 8,
                              }}>
                              {linked ? <LinkOutlined style={{ color: '#2563eb' }} /> : <DisconnectOutlined style={{ color: '#d1d5db' }} />}
                              <div style={{ flex: 1, overflow: 'hidden' }}>
                                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  <strong>{k.keyword}</strong> <span style={{ color: '#9ca3af', fontSize: '0.72rem' }}>({k.count}건)</span>
                                </div>
                              </div>
                              {chLinkBusy === k.keyword && <Spin size="small" />}
                            </div>
                          )
                        }
                        return (
                          <>
                            {linkedList.length > 0 && (
                              <>
                                <div style={{ padding: '4px 12px', fontSize: '0.72rem', fontWeight: 700, color: '#2563eb', background: '#eff6ff' }}>
                                  ✓ 연결된 거래처 ({linkedList.length})
                                </div>
                                {linkedList.map(renderItem)}
                                <div style={{ padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600, color: '#9ca3af', background: '#f9fafb' }}>
                                  나머지 거래처
                                </div>
                              </>
                            )}
                            {unlinkedList.map(renderItem)}
                          </>
                        )
                      })()}
                    </div>
                  </div>
                </Col>
              </Row>
            </div>
          ),
        },
        {
          key: 'list',
          label: `매핑 목록 (${mappings.length})`,
          children: (
            <div>
              {/* 수동 추가 */}
              <div style={{ background: '#f9fafb', borderRadius: 10, border: '1px solid #e5e7eb', padding: '12px 16px', marginBottom: 14 }}>
                <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 8 }}>
                  <PlusOutlined /> 수동 매핑 추가
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <Input
                    placeholder="OurBox 상품명 입력"
                    value={manualOb}
                    onChange={e => setManualOb(e.target.value)}
                    style={{ flex: 1, minWidth: 200 }}
                    size="small"
                  />
                  <Select
                    placeholder="BoxHero 상품 선택"
                    value={manualBh}
                    onChange={setManualBh}
                    showSearch
                    filterOption={(input, opt) =>
                      (opt?.label as string || '').toLowerCase().includes(input.toLowerCase())
                    }
                    options={bhItems.map(b => ({
                      value: b.sku,
                      label: `${b.name} (${b.sku})`,
                    }))}
                    style={{ minWidth: 280 }}
                    size="small"
                  />
                  <Button type="primary" size="small" icon={<PlusOutlined />}
                    loading={addingManual} onClick={handleAddManual}
                    disabled={!manualOb.trim() || !manualBh}>
                    추가
                  </Button>
                </div>
              </div>

              {/* 필터 + 검색 */}
              <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
                <Select value={filter} onChange={setFilter} size="small" style={{ width: 120 }}
                  options={[
                    { value: 'all', label: '전체' },
                    { value: 'confirmed', label: '확인됨' },
                    { value: 'unconfirmed', label: '미확인' },
                  ]} />
                <Input.Search size="small" placeholder="이름/SKU 검색"
                  value={search} onChange={e => setSearch(e.target.value)} style={{ width: 200 }} />
                <Button size="small" onClick={loadAll} loading={loading}>새로고침</Button>
                <span style={{ fontSize: '0.78rem', color: '#6b7280', marginLeft: 'auto' }}>
                  {filtered.length}개 표시
                </span>
              </div>

              <Table
                dataSource={filtered}
                columns={listColumns}
                rowKey="id"
                size="small"
                loading={loading}
                pagination={{ pageSize: 20, showSizeChanger: true }}
              />
            </div>
          ),
        },
        {
          key: 'audit',
          label: '🩺 의심 매핑 검사',
          children: (
            <div>
              <Alert type="warning" showIcon style={{ marginBottom: 14 }}
                message="이름이 너무 다른 매핑을 찾아냅니다"
                description="OurBox 상품명과 BoxHero 상품명의 유사도가 50% 미만인 매핑을 의심 항목으로 표시합니다. 예: '메노포즈 6개입 → 트윈픽스' 같은 오매핑. 검토 후 잘못된 건 삭제하세요." />
              <Button type="primary" loading={auditLoading} onClick={runAudit} style={{ marginBottom: 14 }}>
                🩺 의심 매핑 검사 실행
              </Button>
              {auditDone && (
                auditRows.length === 0 ? (
                  <Alert type="success" showIcon message="의심 매핑이 없습니다 ✅" description="모든 매핑의 OB·BH 상품명이 충분히 유사합니다." />
                ) : (
                  <Table
                    dataSource={auditRows}
                    rowKey={(r) => `${r.table}-${r.ob_code}-${r.bh_sku}`}
                    size="small"
                    pagination={false}
                    columns={[
                      { title: '유사도', dataIndex: 'score', width: 80, align: 'right' as const,
                        render: (v: number) => <Tag color={v < 30 ? 'error' : 'warning'}>{v}%</Tag> },
                      { title: '구분', dataIndex: 'table', width: 110,
                        render: (v: string) => <Tag>{v === 'product_mapping' ? '코드매핑' : '이름매핑'}</Tag> },
                      { title: 'OurBox 상품', dataIndex: 'ob_name', ellipsis: true,
                        render: (v: string, r: AuditRow) => <span>{v}{r.ob_code ? <code style={{ fontSize: '0.7rem', color: '#9ca3af', marginLeft: 6 }}>{r.ob_code}</code> : ''}</span> },
                      { title: 'BoxHero 상품', dataIndex: 'bh_name', ellipsis: true,
                        render: (v: string, r: AuditRow) => <span>{v}{r.bh_sku ? <code style={{ fontSize: '0.7rem', color: '#9ca3af', marginLeft: 6 }}>{r.bh_sku}</code> : ''}</span> },
                      { title: '조치', width: 90,
                        render: (_: unknown, r: AuditRow) => (
                          <Popconfirm title="이 매핑을 삭제할까요?" description="잘못된 매핑이면 삭제하세요." onConfirm={() => deleteAuditRow(r)} okText="삭제" cancelText="취소">
                            <Button danger size="small">삭제</Button>
                          </Popconfirm>
                        ) },
                    ]}
                  />
                )
              )}
            </div>
          ),
        },
        {
          key: 'unmapped',
          label: '🔍 미매핑',
          children: (
            <div>
              <Alert
                type="info"
                message="매핑이 등록되지 않은 BH/OB 상품을 조회하고, 이름 유사도 기반 연결 제안을 확인합니다."
                style={{ marginBottom: 14 }}
              />
              {/* 조회 날짜 범위 */}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
                <Input
                  placeholder="시작일 (YYYY-MM-DD)"
                  value={suggestFrom}
                  onChange={e => setSuggestFrom(e.target.value)}
                  style={{ width: 160 }}
                  size="small"
                />
                <span>~</span>
                <Input
                  placeholder="종료일 (YYYY-MM-DD)"
                  value={suggestTo}
                  onChange={e => setSuggestTo(e.target.value)}
                  style={{ width: 160 }}
                  size="small"
                />
                <Button
                  type="primary"
                  icon={<SearchOutlined />}
                  onClick={loadSuggest}
                  loading={suggestLoading}
                  size="small"
                >
                  조회
                </Button>
              </div>

              {suggestData && (
                <>
                  {/* 요약 태그 */}
                  <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
                    <Tag color="orange">BH 미매핑 {suggestData.bh_unmapped.length}개</Tag>
                    <Tag color="blue">OB 미매핑 {suggestData.ob_unmapped.length}개</Tag>
                    <Tag color="green">제안 {suggestData.suggestions.length}건</Tag>
                  </div>

                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    {/* BH 미매핑 */}
                    <Col span={12}>
                      <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 6 }}>
                        📦 BoxHero 미매핑 상품
                      </div>
                      <Table
                        dataSource={suggestData.bh_unmapped}
                        rowKey="sku"
                        size="small"
                        pagination={{ pageSize: 10, showSizeChanger: false }}
                        columns={[
                          {
                            title: 'SKU',
                            dataIndex: 'sku', key: 'sku', width: 140,
                            render: (v: string) => <code style={{ fontSize: '0.75rem' }}>{v}</code>,
                          },
                          {
                            title: '이름',
                            dataIndex: 'name', key: 'name', ellipsis: true,
                            render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
                          },
                        ]}
                      />
                    </Col>

                    {/* OB 미매핑 */}
                    <Col span={12}>
                      <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 6 }}>
                        🏭 OurBox 미매핑 상품
                      </div>
                      <Table
                        dataSource={suggestData.ob_unmapped}
                        rowKey="prod_cd"
                        size="small"
                        pagination={{ pageSize: 10, showSizeChanger: false }}
                        columns={[
                          {
                            title: '상품코드',
                            dataIndex: 'prod_cd', key: 'prod_cd', width: 160,
                            render: (v: string) => <code style={{ fontSize: '0.75rem' }}>{v}</code>,
                          },
                          {
                            title: '이름',
                            dataIndex: 'name', key: 'name', ellipsis: true,
                            render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
                          },
                        ]}
                      />
                    </Col>
                  </Row>

                  {/* 제안 목록 */}
                  <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 6 }}>
                    🔗 매핑 제안 (이름 유사도 기반)
                  </div>
                  <Table
                    dataSource={suggestData.suggestions}
                    rowKey={(r) => `${r.bh_sku}-${r.ob_prod_cd}`}
                    size="small"
                    pagination={{ pageSize: 20 }}
                    columns={[
                      {
                        title: '유사도',
                        dataIndex: 'score', key: 'score', width: 70, align: 'right' as const,
                        render: (v: number) => (
                          <span style={{ color: v >= 90 ? '#10b981' : v >= 70 ? '#f59e0b' : '#ef4444', fontWeight: 600 }}>
                            {v}%
                          </span>
                        ),
                      },
                      {
                        title: 'BH SKU',
                        dataIndex: 'bh_sku', key: 'bh_sku', width: 140,
                        render: (v: string) => <code style={{ fontSize: '0.75rem' }}>{v}</code>,
                      },
                      {
                        title: 'BH 이름',
                        dataIndex: 'bh_name', key: 'bh_name', ellipsis: true,
                        render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
                      },
                      {
                        title: 'OB 코드',
                        dataIndex: 'ob_prod_cd', key: 'ob_prod_cd', width: 160,
                        render: (v: string) => <code style={{ fontSize: '0.75rem' }}>{v}</code>,
                      },
                      {
                        title: 'OB 이름',
                        dataIndex: 'ob_name', key: 'ob_name', ellipsis: true,
                        render: (v: string) => <span style={{ fontSize: '0.82rem' }}>{v}</span>,
                      },
                      {
                        title: '',
                        key: 'action', width: 70,
                        render: (_: unknown, r: { bh_sku: string; ob_prod_cd: string; ob_name: string; bh_name: string }) => (
                          <Button
                            size="small"
                            type="primary"
                            icon={<LinkOutlined />}
                            onClick={() => handleSuggestLink(r.bh_sku, r.ob_name, r.bh_name)}
                          >
                            연결
                          </Button>
                        ),
                      },
                    ]}
                  />
                </>
              )}
            </div>
          ),
        },
        {
          key: 'auto',
          label: '⚡ 자동 매핑',
          children: (
            <div>
              <Alert
                type="info"
                message="퍼지 문자열 매칭으로 비슷한 이름을 자동으로 연결합니다. 결과는 목록 탭에서 확인/수정할 수 있습니다."
                style={{ marginBottom: 14 }}
              />
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 6 }}>
                  OurBox 상품명 목록 (한 줄에 하나씩)
                </div>
                <Input.TextArea
                  rows={10}
                  placeholder="프로틴 프레젤 씨솔트&#10;메노포즈&#10;DJ&A 포테이토 웨지..."
                  value={obInput}
                  onChange={e => setObInput(e.target.value)}
                  style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}
                />
              </div>
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: '0.82rem', marginBottom: 4 }}>
                  최소 유사도 임계값: <strong>{threshold}%</strong>
                </div>
                <Slider min={50} max={100} value={threshold} onChange={setThreshold} style={{ maxWidth: 300 }} />
              </div>
              <Button type="primary" icon={<ThunderboltOutlined />}
                onClick={handleAutoMatch} loading={autoLoading}
                disabled={!config.api_token}>
                자동 매핑 실행
              </Button>

              {autoResults.length > 0 && (
                <Table
                  style={{ marginTop: 14 }}
                  dataSource={(autoResults as Record<string, unknown>[]).filter(r => r.bh_sku)}
                  rowKey="ob_name"
                  size="small"
                  pagination={false}
                  columns={[
                    { title: 'OurBox 이름', dataIndex: 'ob_name', key: 'ob_name' },
                    { title: 'BoxHero SKU', dataIndex: 'bh_sku', key: 'bh_sku', width: 140 },
                    { title: 'BoxHero 이름', dataIndex: 'bh_name', key: 'bh_name' },
                    {
                      title: '유사도',
                      dataIndex: 'score', key: 'score', width: 80, align: 'right' as const,
                      render: (v: number) => (
                        <span style={{ color: v >= 90 ? '#10b981' : '#f59e0b' }}>
                          {v.toFixed(0)}%
                        </span>
                      ),
                    },
                    {
                      title: '',
                      dataIndex: 'is_new', key: 'is_new', width: 60,
                      render: (v: boolean) => v ? <Tag color="blue">신규</Tag> : <Tag>기존</Tag>,
                    },
                  ]}
                />
              )}
            </div>
          ),
        },
        {
          key: 'ai',
          label: '🤖 AI 매핑',
          children: (
            <div ref={aiRef}>
              {!hasApiKey && (
                <Alert type="warning" message="Gemini API Key가 필요합니다 (설정 페이지에서 입력)" style={{ marginBottom: 12 }} />
              )}
              <Alert
                type="info"
                message="AI가 이름이 다르게 표기된 동일 상품을 찾아 매핑을 제안합니다. 제안된 항목을 개별 확인 후 저장하세요."
                style={{ marginBottom: 14 }}
              />
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 6 }}>
                  OurBox 상품명 목록 (최대 50개)
                </div>
                <Input.TextArea
                  rows={8}
                  placeholder="이름이 다를 것 같은 OurBox 상품명 입력..."
                  value={obInput}
                  onChange={e => setObInput(e.target.value)}
                  style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}
                />
              </div>
              <Button type="primary" icon={<RobotOutlined />}
                onClick={handleAiSuggest} loading={aiLoading}
                disabled={!hasApiKey || !config.api_token}>
                AI 매핑 제안
              </Button>

              {aiError && <Alert type="error" message={aiError} style={{ marginTop: 10 }} />}

              {aiLoading && !aiSuggestions.length && (
                <div style={{ marginTop: 16, background: '#f9fafb', borderRadius: 8, padding: 16 }}>
                  <Spin size="small" /> <span style={{ marginLeft: 8, fontSize: '0.82rem', color: '#6b7280' }}>AI 분석 중...</span>
                  {aiText && <pre style={{ marginTop: 8, fontSize: '0.75rem', whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{aiText}</pre>}
                </div>
              )}

              {aiSuggestions.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: 8 }}>
                    AI 제안 결과 ({aiSuggestions.length}건) — 개별 확인 후 저장하세요
                  </div>
                  <Table
                    dataSource={aiSuggestions as Record<string, unknown>[]}
                    rowKey="ob_name"
                    size="small"
                    pagination={false}
                    columns={[
                      { title: 'OurBox 이름', dataIndex: 'ob_name', key: 'ob_name', ellipsis: true },
                      { title: 'BoxHero SKU', dataIndex: 'bh_sku', key: 'bh_sku', width: 130 },
                      { title: 'BoxHero 이름', dataIndex: 'bh_name', key: 'bh_name', ellipsis: true },
                      {
                        title: '신뢰도',
                        dataIndex: 'confidence', key: 'confidence', width: 70, align: 'right' as const,
                        render: (v: number) => (
                          <span style={{ color: v >= 90 ? '#10b981' : v >= 70 ? '#f59e0b' : '#ef4444' }}>
                            {v}%
                          </span>
                        ),
                      },
                      {
                        title: '근거',
                        dataIndex: 'reason', key: 'reason', ellipsis: true,
                        render: (v: string) => <span style={{ fontSize: '0.75rem', color: '#6b7280' }}>{v}</span>,
                      },
                      {
                        title: '',
                        key: 'action', width: 70,
                        render: (_: unknown, r: unknown) => (
                          <Button size="small" type="primary" icon={<CheckOutlined />}
                            onClick={() => handleSaveAiSuggestion(r as Record<string, unknown>)}>
                            저장
                          </Button>
                        ),
                      },
                    ]}
                  />
                </div>
              )}
            </div>
          ),
        },
        {
          key: 'set_bom',
          label: '📦 세트 구성',
          children: (() => {
            // 세트별로 그룹화
            const bomGroups: Record<string, { set_name: string; set_sku: string; comps: SetBom[] }> = {}
            for (const b of setBoms) {
              if (!bomGroups[b.set_sku]) bomGroups[b.set_sku] = { set_name: b.set_name, set_sku: b.set_sku, comps: [] }
              bomGroups[b.set_sku].comps.push(b)
            }
            const bhOpts = bhItems.map(b => ({ value: b.sku, label: `${b.name}  [${b.sku}]`, name: b.name }))

            return (
            <div>
              <Alert type="info" style={{ marginBottom: 16 }}
                message="세트 작업 매칭 구성표 (BOM)"
                description={<span>
                  세트 제품 1개를 선택하고, 그 세트에 들어가는 단품들을 모두 추가하세요.<br/>
                  예: <b>홈쇼핑 공통 12개입</b> = <b>메노포즈 단품 × 12개</b> + <b>박스 × 1개</b><br/>
                  등록 후 "전체 수량 매칭"에서 세트 조립·해체를 자동 감지·매칭합니다.
                </span>}
              />

              {/* ── 등록 폼 ── */}
              <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8, padding: 16, marginBottom: 20 }}>
                <div style={{ fontWeight: 700, marginBottom: 14, fontSize: '0.9rem' }}>새 세트 구성 등록</div>

                {bhItems.length === 0 && (
                  <Alert type="warning" style={{ marginBottom: 12 }}
                    message="BoxHero 상품 목록을 먼저 불러오세요 — '연결' 탭에서 상품 불러오기 후 돌아오세요." showIcon />
                )}

                {/* 세트 제품 선택 */}
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#374151', marginBottom: 5 }}>
                    📦 세트 제품 (완성품)
                  </div>
                  <Select showSearch size="small" style={{ width: '50%' }}
                    placeholder="BoxHero 상품 검색..."
                    filterOption={(input, opt) => (opt?.label as string ?? '').toLowerCase().includes(input.toLowerCase())}
                    value={bomSetSku || undefined}
                    onChange={(_val, opt) => {
                      const o = opt as { value: string; name: string }
                      setBomSetSku(o.value); setBomSetName(o.name)
                    }}
                    options={bhOpts}
                  />
                  {bomSetSku && (
                    <span style={{ marginLeft: 8, fontSize: '0.75rem', color: '#6b7280' }}>
                      <code>{bomSetSku}</code> · {bomSetName}
                    </span>
                  )}
                </div>

                {/* 구성 단품 목록 */}
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#374151', marginBottom: 8 }}>
                    🔩 구성 단품 목록 <span style={{ fontWeight: 400, color: '#9ca3af' }}>(여러 개 추가 가능)</span>
                  </div>
                  {bomComps.map((comp, idx) => (
                    <div key={idx} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6,
                      background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, padding: '8px 10px' }}>
                      <span style={{ color: '#9ca3af', fontSize: '0.75rem', width: 20, flexShrink: 0 }}>#{idx + 1}</span>
                      <Select showSearch size="small" style={{ flex: 3 }}
                        placeholder="단품 검색..."
                        filterOption={(input, opt) => (opt?.label as string ?? '').toLowerCase().includes(input.toLowerCase())}
                        value={comp.component_sku || undefined}
                        onChange={(_val, opt) => {
                          const o = opt as { value: string; name: string }
                          setBomComps(prev => prev.map((c, i) => i === idx ? { ...c, component_sku: o.value, component_name: o.name } : c))
                        }}
                        options={bhOpts}
                      />
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                        <span style={{ fontSize: '0.75rem', color: '#6b7280' }}>×</span>
                        <Input size="small" type="number" min={1} style={{ width: 70 }}
                          value={comp.qty_per_set}
                          onChange={e => setBomComps(prev => prev.map((c, i) => i === idx ? { ...c, qty_per_set: Number(e.target.value) } : c))}
                          suffix="개"
                        />
                      </div>
                      <Input size="small" style={{ flex: 2 }} placeholder="메모(선택)"
                        value={comp.note}
                        onChange={e => setBomComps(prev => prev.map((c, i) => i === idx ? { ...c, note: e.target.value } : c))}
                      />
                      {bomComps.length > 1 && (
                        <Button size="small" type="text" danger icon={<DeleteOutlined />}
                          onClick={() => setBomComps(prev => prev.filter((_, i) => i !== idx))} />
                      )}
                    </div>
                  ))}
                  <Button size="small" icon={<PlusOutlined />} style={{ marginTop: 4 }}
                    onClick={() => setBomComps(prev => [...prev, { component_sku: '', component_name: '', qty_per_set: 1, note: '' }])}>
                    단품 추가
                  </Button>
                </div>

                {/* 미리보기 + 저장 */}
                {bomSetSku && bomComps.some(c => c.component_sku) && (
                  <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 6, padding: '8px 12px', marginBottom: 10, fontSize: '0.8rem' }}>
                    <b style={{ color: '#1e3a8a' }}>{bomSetName || bomSetSku}</b>
                    <span style={{ color: '#6b7280' }}> 1개 = </span>
                    {bomComps.filter(c => c.component_sku).map((c, i) => (
                      <span key={i}>
                        {i > 0 && <span style={{ color: '#9ca3af' }}> + </span>}
                        <b>{c.component_name || c.component_sku}</b>
                        <span style={{ color: '#2563eb' }}> ×{c.qty_per_set}</span>
                      </span>
                    ))}
                  </div>
                )}

                <Button type="primary" size="small" loading={addingBom} icon={<PlusOutlined />}
                  disabled={!bomSetSku || bomComps.every(c => !c.component_sku)}
                  onClick={async () => {
                    const validComps = bomComps.filter(c => c.component_sku && c.qty_per_set >= 1)
                    if (!validComps.length) { message.warning('단품을 1개 이상 선택하세요'); return }
                    setAddingBom(true)
                    try {
                      for (const comp of validComps) {
                        await createSetBom({ set_sku: bomSetSku, set_name: bomSetName, ...comp })
                      }
                      message.success(`${bomSetName || bomSetSku} 구성 ${validComps.length}개 등록됨`)
                      setBomSetSku(''); setBomSetName('')
                      setBomComps([{ component_sku: '', component_name: '', qty_per_set: 1, note: '' }])
                      await loadSetBoms()
                    } catch { message.error('등록 실패') }
                    finally { setAddingBom(false) }
                  }}>
                  세트 구성 저장
                </Button>
              </div>

              {/* ── 등록된 BOM 목록 (세트별 그룹) ── */}
              <Spin spinning={setBomLoading}>
                {Object.keys(bomGroups).length === 0 ? (
                  <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: '0.85rem', border: '1px dashed #e5e7eb', borderRadius: 8 }}>
                    등록된 세트 구성 없음 — 위에서 추가하세요
                  </div>
                ) : Object.values(bomGroups).map(grp => (
                  <div key={grp.set_sku} style={{ border: '1px solid #e5e7eb', borderRadius: 8, marginBottom: 12, overflow: 'hidden' }}>
                    {/* 세트 헤더 */}
                    <div style={{ background: '#f0f9ff', padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid #e5e7eb' }}>
                      <Tag color="blue" style={{ fontWeight: 700, fontSize: '0.8rem' }}>세트</Tag>
                      <span style={{ fontWeight: 700, fontSize: '0.9rem' }}>{grp.set_name || grp.set_sku}</span>
                      <code style={{ fontSize: '0.72rem', color: '#9ca3af' }}>{grp.set_sku}</code>
                      <span style={{ marginLeft: 'auto', fontSize: '0.75rem', color: '#6b7280' }}>단품 {grp.comps.length}종</span>
                    </div>
                    {/* 단품 목록 */}
                    {grp.comps.map((comp, ci) => (
                      <div key={comp.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 14px',
                        background: ci % 2 === 0 ? '#fff' : '#fafafa', borderBottom: ci < grp.comps.length - 1 ? '1px solid #f3f4f6' : 'none' }}>
                        <span style={{ color: '#9ca3af', fontSize: '0.75rem', width: 20 }}>└</span>
                        <div style={{ flex: 3 }}>
                          <span style={{ fontWeight: 600 }}>{comp.component_name || comp.component_sku}</span>
                          <code style={{ marginLeft: 6, fontSize: '0.7rem', color: '#9ca3af' }}>{comp.component_sku}</code>
                        </div>
                        <Tag color="geekblue">× {comp.qty_per_set}개</Tag>
                        <span style={{ flex: 1, fontSize: '0.75rem', color: '#9ca3af' }}>{comp.note || ''}</span>
                        <Popconfirm title="이 단품 구성을 삭제할까요?" onConfirm={async () => { await deleteSetBom(comp.id); await loadSetBoms(); message.success('삭제됨') }}>
                          <Button size="small" danger type="text" icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </div>
                    ))}
                  </div>
                ))}
              </Spin>
              <Button size="small" icon={<SearchOutlined />} onClick={loadSetBoms} style={{ marginTop: 8 }}>목록 새로고침</Button>
            </div>
            )
          })(),
        },
      ]} />
    </div>
  )
}
