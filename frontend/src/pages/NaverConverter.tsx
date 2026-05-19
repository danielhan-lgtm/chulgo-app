import { useState } from 'react'
import { Button, Upload, Slider, Select, message, Row, Col, Alert, Spin } from 'antd'
import { SendOutlined, DownloadOutlined, DeleteOutlined } from '@ant-design/icons'
import type { AppConfig, Location, StagedItem, Page } from '../types'
import { convertNaver, sendToBoxhero, downloadBlob } from '../services/api'

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
  method?: string
  sku: string
  price: number
  ok: boolean
}

export default function NaverConverter({ config, locations }: Props) {
  const [fileObj, setFileObj] = useState<File | null>(null)
  const [filename, setFilename] = useState('')
  const [threshold, setThreshold] = useState(65)
  const [converting, setConverting] = useState(false)
  const [preview, setPreview] = useState<Array<{ 상품명: string; 수량: number }>>([])
  const [matchRows, setMatchRows] = useState<MatchRow[]>([])
  const [masterNames, setMasterNames] = useState<string[]>([])
  const [masterByName, setMasterByName] = useState<Record<string, { sku: string; price: number }>>({})
  const [staged, setStaged] = useState<StagedItem[]>([])
  const [sending, setSending] = useState(false)
  const [locationId, setLocationId] = useState<number | undefined>(config.selected_location_id)
  const [memo, setMemo] = useState('')

  async function handleFileSelect(file: File) {
    setFileObj(file)
    setFilename(file.name)
    setMatchRows([])
    setStaged([])
    setPreview([])
    return false
  }

  async function handleConvert() {
    if (!fileObj) return
    setConverting(true)
    try {
      const data = await convertNaver(fileObj, filename, threshold)
      setMasterNames(data.master_names || [])
      setMasterByName(data.master_by_name || {})
      setPreview(data.preview || [])
      const rows: MatchRow[] = [
        ...data.results.map((r: any, i: number) => ({
          key: `ok-${i}`,
          original_name: r.original_name,
          quantity: r.quantity,
          matched_name: r.matched_name,
          method: r.method,
          sku: r.sku,
          price: r.price,
          ok: true,
        })),
        ...data.unmatched.map((u: any, i: number) => ({
          key: `fail-${i}`,
          original_name: u.original_name,
          quantity: u.quantity,
          matched_name: '(건너뜀)',
          method: u.method,
          sku: '',
          price: 0,
          ok: false,
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
      if (map[info.sku]) {
        map[info.sku].quantity += r.quantity
      } else {
        map[info.sku] = { sku: info.sku, quantity: r.quantity, price: info.price }
      }
    }
    setStaged(Object.values(map).filter(s => s.sku && s.quantity > 0))
  }

  function handleMappingChange(rowKey: string, newName: string) {
    const updated = matchRows.map(r => r.key === rowKey ? { ...r, matched_name: newName, ok: newName !== '(건너뜀)' } : r)
    setMatchRows(updated)
    buildStaged(updated, masterByName)
  }

  function handleStagedQtyChange(sku: string, qty: number) {
    setStaged(prev => prev.map(s => s.sku === sku ? { ...s, quantity: qty } : s))
  }

  function handleRemoveStaged(sku: string) {
    setStaged(prev => prev.filter(s => s.sku !== sku))
  }

  function handleDownload() {
    if (staged.length === 0) return
    const rows = staged.map(s => `${s.sku}\t${s.quantity}\t${s.price}`).join('\n')
    const blob = new Blob(['\uFEFF' + 'SKU\t수량\t단가\n' + rows], { type: 'text/tab-separated-values;charset=utf-8' })
    downloadBlob(blob, '네이버_변경양식_출력.tsv')
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
    } catch (e: any) {
      message.error('전송 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSending(false)
    }
  }

  const hasResults = matchRows.length > 0
  const totalSku = staged.length
  const totalQty = staged.reduce((s, r) => s + r.quantity, 0)
  const okCount = matchRows.filter(r => r.ok).length
  const failCount = matchRows.filter(r => !r.ok).length

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">네이버 형식</h1>
        <p className="page-desc">네이버 출고 파일 → 박스히어로 변경양식 변환</p>
      </div>

      {/* Flow bar */}
      <div className="flow-bar">
        {[
          { n: '1', label: '📁 출고 파일', done: !!fileObj },
          { n: '2', label: '🔄 변환', done: hasResults },
          { n: '3', label: '📋 스테이징', done: staged.length > 0 },
          { n: '4', label: '⬇️ 전송', done: false },
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

      <Row gutter={[16, 16]}>
        {/* Upload */}
        <Col span={10}>
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #10b981', paddingBottom: 8 }}>📁 출고 파일</div>
            <Upload.Dragger accept=".xlsx,.xls" showUploadList={false} beforeUpload={handleFileSelect} style={{ background: '#fafafa', borderColor: '#d1d5db', marginBottom: 12 }}>
              <p style={{ fontSize: '1.5rem', margin: '8px 0' }}>🛒</p>
              <p style={{ fontSize: '0.82rem', color: '#6b7280' }}>네이버 출고 파일 (양식+BoxHero 시트)</p>
            </Upload.Dragger>
            {filename && <div style={{ marginBottom: 12, fontSize: '0.8rem', color: '#059669' }}>✅ {filename}</div>}
            {fileObj && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: '0.78rem', color: '#6b7280' }}>임계값: {threshold}%</span>
                <div style={{ flex: 1 }}>
                  <Slider min={40} max={100} value={threshold} onChange={setThreshold} />
                </div>
                <Button type="primary" onClick={handleConvert} loading={converting}>🔄 변환</Button>
              </div>
            )}
          </div>
        </Col>

        {/* Preview */}
        <Col span={14}>
          <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, minHeight: 120 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, borderBottom: '2px solid #10b981', paddingBottom: 8 }}>
              🔄 변환 결과 미리보기
            </div>
            {converting ? <div style={{ textAlign: 'center', padding: 20 }}><Spin /></div> : preview.length > 0 ? (
              <div style={{ maxHeight: 180, overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                  <thead>
                    <tr style={{ background: '#f9fafb' }}>
                      <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }}>상품명</th>
                      <th style={{ padding: '6px 10px', textAlign: 'center', fontWeight: 600, color: '#6b7280', width: 60 }}>수량</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.slice(0, 10).map((row, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #f3f4f6' }}>
                        <td style={{ padding: '5px 10px', color: '#374151' }}>{row['상품명']}</td>
                        <td style={{ padding: '5px 10px', textAlign: 'center', color: '#374151' }}>{row['수량']}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ color: '#9ca3af', fontSize: '0.82rem' }}>파일 업로드 후 변환하면 미리보기가 표시됩니다</div>
            )}
          </div>
        </Col>
      </Row>

      {/* Mapping table */}
      {hasResults && (
        <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16, marginTop: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
            <span style={{ flex: 1, fontWeight: 700 }}>
              🔧 원본 상품 → 마스터 매핑 &nbsp;
              <span style={{ fontSize: '0.82rem', color: '#6b7280' }}>✅ {okCount}건 / ❌ {failCount}건</span>
            </span>
            <Button size="small" onClick={() => { setMatchRows([]); setStaged([]) }}>다시 변환</Button>
          </div>
          <div style={{ maxHeight: 300, overflowY: 'auto', marginBottom: 12 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
              <thead>
                <tr style={{ background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                  <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }}>원본 상품명</th>
                  <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600, color: '#6b7280', width: 70 }}>수량</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }}>마스터 매핑</th>
                  <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600, color: '#6b7280', width: 60 }}>상태</th>
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
                    <td style={{ padding: '7px 12px', textAlign: 'center' }}>
                      {row.ok ? '✅' : '❌'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Staging */}
          {staged.length > 0 && (
            <>
              <div style={{ borderTop: '1px solid #f3f4f6', paddingTop: 12, marginTop: 4, fontWeight: 700, marginBottom: 8 }}>📋 스테이징 그리드</div>
              <div style={{ maxHeight: 220, overflowY: 'auto', marginBottom: 12 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                  <thead>
                    <tr style={{ background: '#f9fafb' }}>
                      <th style={{ padding: '7px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7280' }}>SKU</th>
                      <th style={{ padding: '7px 12px', textAlign: 'center', fontWeight: 600, color: '#6b7280', width: 90 }}>수량</th>
                      <th style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 600, color: '#6b7280', width: 90 }}>단가</th>
                      <th style={{ width: 36 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {staged.map(s => (
                      <tr key={s.sku} style={{ borderBottom: '1px solid #f3f4f6' }}>
                        <td style={{ padding: '6px 12px' }}>{s.sku}</td>
                        <td style={{ padding: '6px 12px', textAlign: 'center' }}>
                          <input type="number" value={s.quantity} min={1}
                            onChange={e => handleStagedQtyChange(s.sku, Number(e.target.value))}
                            style={{ width: 70, textAlign: 'center', border: '1px solid #e5e7eb', borderRadius: 6, padding: '2px 6px', fontSize: '0.82rem' }} />
                        </td>
                        <td style={{ padding: '6px 12px', textAlign: 'right', color: '#6b7280' }}>{s.price.toLocaleString()}</td>
                        <td>
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
                      <div style={{ fontSize: '0.7rem', fontWeight: 600, color: '#065f46', opacity: 0.8 }}>SKU</div>
                    </div>
                    <div style={{ flex: 1, background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10, padding: '8px 12px', textAlign: 'center' }}>
                      <div style={{ fontSize: '1.4rem', fontWeight: 800, color: '#1e40af' }}>{totalQty}</div>
                      <div style={{ fontSize: '0.7rem', fontWeight: 600, color: '#1e40af', opacity: 0.8 }}>총 수량</div>
                    </div>
                  </div>
                </Col>
                <Col span={4}>
                  <Button icon={<DownloadOutlined />} onClick={handleDownload} block>다운로드</Button>
                </Col>
                <Col span={14}>
                  {config.api_token ? (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <Select placeholder="출고 위치" value={locationId} onChange={setLocationId} style={{ width: 150 }} options={locations.map(l => ({ value: l.id, label: l.name }))} size="small" />
                      <input value={memo} onChange={e => setMemo(e.target.value)} placeholder="메모" style={{ flex: 1, border: '1px solid #e5e7eb', borderRadius: 6, padding: '4px 10px', fontSize: '0.82rem' }} />
                      <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={sending}>박스히어로 전송</Button>
                    </div>
                  ) : (
                    <Alert message="설정에서 BoxHero API 토큰을 연결하면 직접 전송할 수 있습니다" type="info" showIcon style={{ fontSize: '0.78rem' }} />
                  )}
                </Col>
              </Row>
            </>
          )}
        </div>
      )}
    </div>
  )
}
