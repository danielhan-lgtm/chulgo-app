import { useState, useRef } from 'react'
import { Button, Upload, Slider, Select, Table, message, Spin, Row, Col, Alert } from 'antd'
import { UploadOutlined, SendOutlined, DownloadOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import type { AppConfig, Location, StagedItem, Page } from '../types'
import { getGeneralColumns, convertGeneral, sendToBoxhero, addLog, base64ToBlob, downloadBlob } from '../services/api'

interface Props {
  config: AppConfig
  locations: Location[]
  onNavigate: (p: Page) => void
}

interface MatchRow {
  key: string
  original_name: string
  quantity: number
  matched_name: string
  score?: number
  sku: string
  price: number
}

export default function GeneralConverter({ config, locations }: Props) {
  const [fileObj, setFileObj] = useState<File | null>(null)
  const [filename, setFilename] = useState('')
  const [columns, setColumns] = useState<string[]>([])
  const [nameCol, setNameCol] = useState('')
  const [qtyCol, setQtyCol] = useState('')
  const [threshold, setThreshold] = useState(70)
  const [converting, setConverting] = useState(false)
  const [matchRows, setMatchRows] = useState<MatchRow[]>([])
  const [masterNames, setMasterNames] = useState<string[]>([])
  const [masterByName, setMasterByName] = useState<Record<string, { sku: string; price: number }>>({})
  const [staged, setStaged] = useState<StagedItem[]>([])
  const [sending, setSending] = useState(false)
  const [locationId, setLocationId] = useState<number | undefined>(config.selected_location_id)
  const [memo, setMemo] = useState('')
  const hasResults = matchRows.length > 0

  async function handleFileSelect(file: File) {
    setFileObj(file)
    setFilename(file.name)
    setMatchRows([])
    setStaged([])
    try {
      const data = await getGeneralColumns(file, file.name)
      setColumns(data.columns)
      setNameCol(data.name_col)
      setQtyCol(data.qty_col)
    } catch (e: any) {
      message.error('파일 읽기 오류: ' + (e.response?.data?.detail || e.message))
    }
    return false
  }

  async function handleConvert() {
    if (!fileObj || !nameCol || !qtyCol) return
    setConverting(true)
    try {
      const data = await convertGeneral(fileObj, filename, nameCol, qtyCol, threshold)
      setMasterNames(data.master_names || [])
      setMasterByName(data.master_by_name || {})
      const rows: MatchRow[] = [
        ...data.results.map((r: any, i: number) => ({
          key: `ok-${i}`,
          original_name: r.original_name,
          quantity: r.quantity,
          matched_name: r.matched_name,
          score: r.score,
          sku: r.sku,
          price: r.price,
        })),
        ...data.unmatched.map((u: any, i: number) => ({
          key: `fail-${i}`,
          original_name: u.original_name,
          quantity: u.quantity,
          matched_name: u.best_candidate || '(건너뜀)',
          score: u.score,
          sku: '',
          price: 0,
        })),
      ]
      setMatchRows(rows)
      buildStaged(rows, data.master_by_name)
    } catch (e: any) {
      message.error('변환 오류: ' + (e.response?.data?.detail || e.message))
    } finally {
      setConverting(false)
    }
  }

  function buildStaged(rows: MatchRow[], byName: Record<string, { sku: string; price: number }>) {
    const map: Record<string, StagedItem> = {}
    for (const r of rows) {
      const info = byName[r.matched_name]
      if (!info || !info.sku) continue
      const key = info.sku
      if (map[key]) {
        map[key].quantity += r.quantity
      } else {
        map[key] = { sku: info.sku, quantity: r.quantity, price: info.price }
      }
    }
    setStaged(Object.values(map).filter(s => s.sku && s.quantity > 0))
  }

  function handleMappingChange(rowKey: string, newName: string) {
    const updated = matchRows.map(r => r.key === rowKey ? { ...r, matched_name: newName } : r)
    setMatchRows(updated)
    buildStaged(updated, masterByName)
  }

  function handleStagedQtyChange(sku: string, qty: number) {
    setStaged(prev => prev.map(s => s.sku === sku ? { ...s, quantity: qty } : s))
  }

  function handleRemoveStaged(sku: string) {
    setStaged(prev => prev.filter(s => s.sku !== sku))
  }

  function handleAddManual() {
    setStaged(prev => [...prev, { sku: 'SKU-', quantity: 1, price: 0 }])
  }

  function handleDownload() {
    if (staged.length === 0) return
    const rows = staged.map(s => `${s.sku}\t${s.quantity}\t${s.price}`).join('\n')
    const header = 'SKU\t수량\t단가\n'
    const blob = new Blob(['\uFEFF' + header + rows], { type: 'text/tab-separated-values;charset=utf-8' })
    downloadBlob(blob, '변경양식_출력.tsv')
  }

  async function handleSend() {
    if (!config.api_token || !locationId || staged.length === 0) return
    setSending(true)
    try {
      const res = await sendToBoxhero({
        token: config.api_token,
        location_id: locationId,
        items: staged.map(s => ({ sku: s.sku, quantity: s.quantity })),
        memo,
      })
      message.success(`✅ 전송 완료! 트랜잭션 ID: ${res.tx_id}`)
      if (res.missing_skus?.length) {
        message.warning(`미등록 SKU ${res.missing_skus.length}건: ${res.missing_skus.join(', ')}`)
      }
    } catch (e: any) {
      message.error('전송 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSending(false)
    }
  }

  const totalSku = staged.length
  const totalQty = staged.reduce((s, r) => s + r.quantity, 0)

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">일반 형식</h1>
        <p className="page-desc">컬리 등 일반 출고 파일 → 박스히어로 변경양식 변환</p>
      </div>

      {/* Flow bar */}
      <div className="flow-bar">
        {[
          { n: '1', label: '📁 출고 파일', done: !!fileObj },
          { n: '2', label: '⚙️ 매칭 옵션', done: columns.length > 0 },
          { n: '3', label: '🔄 변환', done: hasResults },
          { n: '4', label: '📋 스테이징', done: staged.length > 0 },
          { n: '5', label: '⬇️ 전송', done: false },
        ].map((step, i, arr) => (
          <div key={step.n} style={{ display: 'flex', alignItems: 'center', flex: i < arr.length - 1 ? undefined : 1 }}>
            <div className="flow-step">
              <div className={`flow-num${step.done ? '' : ' pending'}`}>{step.n}</div>
              <div className="flow-label">{step.label}</div>
            </div>
            {i < arr.length - 1 && <span className="flow-arrow-sm">›</span>}
          </div>
        ))}
      </div>

      {!hasResults ? (
        <Row gutter={[16, 16]}>
          {/* Step 1: Upload */}
          <Col span={8}>
            <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16 }}>
              <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #10b981', paddingBottom: 8 }}>📁 출고 파일</div>
              <Upload.Dragger
                accept=".xlsx,.xls"
                showUploadList={false}
                beforeUpload={handleFileSelect}
                style={{ background: '#fafafa', borderColor: '#d1d5db' }}
              >
                <p style={{ fontSize: '1.5rem', margin: '8px 0' }}>📊</p>
                <p style={{ fontSize: '0.82rem', color: '#6b7280' }}>Excel 파일을 드래그하거나 클릭</p>
              </Upload.Dragger>
              {filename && <div style={{ marginTop: 8, fontSize: '0.8rem', color: '#059669' }}>✅ {filename}</div>}
            </div>
          </Col>

          {/* Step 2: Options */}
          <Col span={8}>
            <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, height: '100%' }}>
              <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #10b981', paddingBottom: 8 }}>⚙️ 매칭 옵션</div>
              {columns.length > 0 ? (
                <>
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 4 }}>상품명 컬럼</div>
                    <Select value={nameCol} onChange={setNameCol} style={{ width: '100%' }} options={columns.map(c => ({ value: c, label: c }))} size="small" />
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 4 }}>수량 컬럼</div>
                    <Select value={qtyCol} onChange={setQtyCol} style={{ width: '100%' }} options={columns.map(c => ({ value: c, label: c }))} size="small" />
                  </div>
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 4 }}>유사도 임계값: {threshold}%</div>
                    <Slider min={40} max={100} value={threshold} onChange={setThreshold} />
                  </div>
                  <Button type="primary" onClick={handleConvert} loading={converting} block>🔄 변환 시작</Button>
                </>
              ) : (
                <div style={{ color: '#9ca3af', fontSize: '0.82rem' }}>← 파일을 먼저 업로드하세요</div>
              )}
            </div>
          </Col>

          {/* Step 3: Preview */}
          <Col span={8}>
            <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, height: '100%' }}>
              <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #10b981', paddingBottom: 8 }}>🔄 변환 결과</div>
              {converting ? <Spin /> : <div style={{ color: '#9ca3af', fontSize: '0.82rem' }}>변환 후 결과가 표시됩니다</div>}
            </div>
          </Col>
        </Row>
      ) : (
        <>
          {/* Mapping table */}
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
              <span style={{ flex: 1, fontWeight: 700 }}>
                🔧 매핑 확인 &nbsp;
                <span style={{ fontSize: '0.82rem', color: '#6b7280' }}>
                  ✅ {matchRows.filter(r => r.sku).length}건 성공 / ❌ {matchRows.filter(r => !r.sku).length}건 실패
                </span>
              </span>
              <Button size="small" onClick={() => { setMatchRows([]); setStaged([]) }}>🔄 다시 변환</Button>
            </div>
            <div style={{ maxHeight: 320, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                    <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }}>원본 상품명</th>
                    <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600, color: '#6b7280', width: 70 }}>수량</th>
                    <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }}>마스터 매핑</th>
                    <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600, color: '#6b7280', width: 70 }}>유사도</th>
                  </tr>
                </thead>
                <tbody>
                  {matchRows.map(row => (
                    <tr key={row.key} style={{ borderBottom: '1px solid #f3f4f6' }}>
                      <td style={{ padding: '7px 12px', color: '#374151' }}>{row.original_name}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'center', color: '#374151' }}>{row.quantity}</td>
                      <td style={{ padding: '7px 12px' }}>
                        <Select
                          value={row.matched_name}
                          onChange={v => handleMappingChange(row.key, v)}
                          style={{ width: '100%' }}
                          size="small"
                          showSearch
                          options={['(건너뜀)', ...masterNames].map(n => ({ value: n, label: n }))}
                        />
                      </td>
                      <td style={{ padding: '7px 12px', textAlign: 'center', fontSize: '0.75rem', color: row.sku ? '#059669' : '#dc2626' }}>
                        {row.score !== undefined ? `${row.score}%` : ''} {row.sku ? '' : '❌'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Staging grid */}
          {staged.length > 0 && (
            <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
                <span style={{ flex: 1, fontWeight: 700 }}>📋 스테이징 그리드</span>
                <Button size="small" icon={<EditOutlined />} onClick={handleAddManual}>수기 추가</Button>
              </div>
              <div style={{ maxHeight: 260, overflowY: 'auto', marginBottom: 12 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                  <thead>
                    <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                      <th style={{ padding: '7px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }}>SKU</th>
                      <th style={{ padding: '7px 12px', textAlign: 'center', fontWeight: 600, color: '#6b7280', width: 90 }}>수량</th>
                      <th style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 600, color: '#6b7280', width: 90 }}>단가</th>
                      <th style={{ width: 40 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {staged.map(s => (
                      <tr key={s.sku} style={{ borderBottom: '1px solid #f3f4f6' }}>
                        <td style={{ padding: '6px 12px', color: '#374151' }}>{s.sku}</td>
                        <td style={{ padding: '6px 12px', textAlign: 'center' }}>
                          <input
                            type="number"
                            value={s.quantity}
                            min={1}
                            onChange={e => handleStagedQtyChange(s.sku, Number(e.target.value))}
                            style={{ width: 70, textAlign: 'center', border: '1px solid #e5e7eb', borderRadius: 6, padding: '2px 6px', fontSize: '0.82rem' }}
                          />
                        </td>
                        <td style={{ padding: '6px 12px', textAlign: 'right', color: '#6b7280' }}>{s.price.toLocaleString()}</td>
                        <td style={{ padding: '6px 8px' }}>
                          <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => handleRemoveStaged(s.sku)} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <Row gutter={12} align="middle">
                <Col span={6}>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <div style={{ flex: 1, background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 10, padding: '8px 12px', textAlign: 'center' }}>
                      <div style={{ fontSize: '1.4rem', fontWeight: 800, color: '#065f46' }}>{totalSku}</div>
                      <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#065f46', opacity: 0.8 }}>SKU 종류</div>
                    </div>
                    <div style={{ flex: 1, background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10, padding: '8px 12px', textAlign: 'center' }}>
                      <div style={{ fontSize: '1.4rem', fontWeight: 800, color: '#1e40af' }}>{totalQty}</div>
                      <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#1e40af', opacity: 0.8 }}>총 수량</div>
                    </div>
                  </div>
                </Col>
                <Col span={4}>
                  <Button icon={<DownloadOutlined />} onClick={handleDownload} block>엑셀 다운로드</Button>
                </Col>
                <Col span={14}>
                  {config.api_token ? (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <Select
                        placeholder="출고 위치"
                        value={locationId}
                        onChange={setLocationId}
                        style={{ width: 150 }}
                        options={locations.map(l => ({ value: l.id, label: l.name }))}
                        size="small"
                      />
                      <input
                        value={memo}
                        onChange={e => setMemo(e.target.value)}
                        placeholder="메모 (예: 컬리 4월 출고)"
                        style={{ flex: 1, border: '1px solid #e5e7eb', borderRadius: 6, padding: '4px 10px', fontSize: '0.82rem' }}
                      />
                      <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={sending}>
                        박스히어로 전송
                      </Button>
                    </div>
                  ) : (
                    <Alert message="설정 > 박스히어로에서 API 토큰을 연결하면 직접 전송할 수 있습니다" type="info" showIcon style={{ fontSize: '0.78rem' }} />
                  )}
                </Col>
              </Row>
            </div>
          )}
        </>
      )}
    </div>
  )
}
