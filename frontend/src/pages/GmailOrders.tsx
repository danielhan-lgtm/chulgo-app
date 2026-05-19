import { useState, useEffect } from 'react'
import { Button, Alert, Spin, Row, Col, Select, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { AppConfig, Location, GmailOrder, Page } from '../types'
import {
  getGmailMessages, downloadGmailAttachment,
  base64ToBlob, convertGeneral, getGeneralColumns, convertNaver,
  sendToBoxhero, downloadBlob
} from '../services/api'

interface Props {
  config: AppConfig
  gmailConnected: boolean
  locations: Location[]
  onNavigate: (p: Page) => void
}

export default function GmailOrders({ config, gmailConnected, locations }: Props) {
  const [orders, setOrders] = useState<GmailOrder[]>([])
  const [debug, setDebug] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [fileBytes, setFileBytes] = useState<Record<string, string>>({})
  const [converting, setConverting] = useState<string | null>(null)
  const [matchData, setMatchData] = useState<Record<string, any>>({})
  const [staged, setStaged] = useState<Record<string, any[]>>({})
  const [locationId, setLocationId] = useState<number | undefined>(config.selected_location_id)
  const [memo, setMemo] = useState('')
  const [sendingKey, setSendingKey] = useState<string | null>(null)

  useEffect(() => {
    if (gmailConnected) fetchMessages()
  }, [gmailConnected])

  async function fetchMessages() {
    setLoading(true)
    try {
      const res = await getGmailMessages()
      setOrders(res.orders || [])
      setDebug(res.debug || '')
      setSelectedIdx(0)
    } catch (e: any) {
      message.error('메일 로드 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setLoading(false)
    }
  }

  async function handleDownloadAttachment(fileKey: string, messageId: string, attachmentId: string) {
    try {
      const res = await downloadGmailAttachment(messageId, attachmentId)
      setFileBytes(prev => ({ ...prev, [fileKey]: res.data }))
    } catch (e: any) {
      message.error('첨부파일 다운로드 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function handleConvert(fileKey: string, b64: string, format: 'general' | 'naver', thresh: number, fname: string) {
    const blob = base64ToBlob(b64)
    setConverting(fileKey)
    try {
      if (format === 'naver') {
        const data = await convertNaver(blob, fname, thresh)
        setMatchData(prev => ({ ...prev, [fileKey]: data }))
      } else {
        const colData = await getGeneralColumns(blob, fname)
        const data = await convertGeneral(blob, fname, colData.name_col, colData.qty_col, thresh)
        setMatchData(prev => ({ ...prev, [fileKey]: data }))
      }
    } catch (e: any) {
      message.error('변환 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setConverting(null)
    }
  }

  async function handleSend(fileKey: string) {
    const s = staged[fileKey]
    if (!config.api_token || !locationId || !s?.length) return
    setSendingKey(fileKey)
    try {
      const res = await sendToBoxhero({
        token: config.api_token,
        location_id: locationId,
        items: s.map((i: any) => ({ sku: i.sku, quantity: i.quantity })),
        memo,
      })
      message.success(`✅ 전송 완료! ID: ${res.tx_id}`)
    } catch (e: any) {
      message.error('전송 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSendingKey(null)
    }
  }

  if (!gmailConnected && !config.gmail_token) {
    return (
      <div>
        <div className="page-header"><h1 className="page-title">Gmail 출고요청</h1></div>
        <Alert
          message="Gmail이 연결되지 않았습니다"
          description="설정 페이지에서 Gmail OAuth를 설정해주세요."
          type="warning"
          showIcon
        />
      </div>
    )
  }

  const order = orders[selectedIdx]

  const total = orders.length
  const withFiles = orders.filter(o => o.files.length > 0).length

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Gmail 출고요청</h1>
        <p className="page-desc">Gmail에서 출고 요청 메일 확인 및 파일 로드</p>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={fetchMessages} loading={loading}>새로고침</Button>
        {debug && <span style={{ fontSize: '0.78rem', color: '#9ca3af' }}>{debug}</span>}
      </div>

      {/* Summary */}
      {orders.length > 0 && (
        <Row gutter={8} style={{ marginBottom: 12 }}>
          {[
            { label: '전체', count: total, bg: '#f9fafb', border: '#e5e7eb', color: '#111827' },
            { label: '첨부 있음', count: withFiles, bg: '#f0fdf4', border: '#bbf7d0', color: '#15803d' },
            { label: '첨부 없음', count: total - withFiles, bg: '#fef2f2', border: '#fecaca', color: '#dc2626' },
          ].map(s => (
            <Col key={s.label} span={4}>
              <div style={{ background: s.bg, border: `1px solid ${s.border}`, borderRadius: 10, padding: '10px 8px', textAlign: 'center' }}>
                <div style={{ fontSize: '1.4rem', fontWeight: 800, color: s.color }}>{s.count}</div>
                <div style={{ fontSize: '0.75rem', color: s.color, fontWeight: 600 }}>{s.label}</div>
              </div>
            </Col>
          ))}
        </Row>
      )}

      {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

      {!loading && orders.length > 0 && (
        <Row gutter={[12, 12]}>
          {/* Mail list */}
          <Col span={8} style={{ maxHeight: 'calc(100vh - 300px)', overflowY: 'auto' }}>
            {orders.map((o, idx) => {
              const isSelected = selectedIdx === idx
              return (
                <div
                  key={o.id}
                  onClick={() => setSelectedIdx(idx)}
                  style={{
                    border: isSelected ? '1.5px solid #10b981' : '1px solid #e5e7eb',
                    background: isSelected ? '#f0fdf4' : '#fff',
                    borderRadius: 10, padding: '10px 13px', marginBottom: 6, cursor: 'pointer',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontSize: '0.68rem', color: '#9ca3af' }}>{o.dt}</span>
                    {o.files.length > 0 && <span style={{ fontSize: '0.7rem', background: '#f0fdf4', color: '#15803d', borderRadius: 4, padding: '1px 6px' }}>📎 {o.files.length}</span>}
                  </div>
                  <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#111827', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{o.subject}</div>
                  <div style={{ fontSize: '0.72rem', color: '#6b7280', marginTop: 2 }}>{o.sender}</div>
                </div>
              )
            })}
          </Col>

          {/* Detail */}
          <Col span={16}>
            {order && (
              <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16 }}>
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: '1rem', fontWeight: 700, color: '#111827', marginBottom: 4 }}>{order.subject}</div>
                  <div style={{ fontSize: '0.78rem', color: '#6b7280' }}>발신: {order.sender}</div>
                  <div style={{ fontSize: '0.78rem', color: '#9ca3af' }}>날짜: {order.dt}</div>
                </div>

                <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#374151', marginBottom: 8 }}>📎 첨부파일</div>
                {order.files.length === 0 ? (
                  <div style={{ color: '#9ca3af', fontSize: '0.82rem' }}>이 메일에 Excel 첨부파일이 없습니다</div>
                ) : order.files.map((f, fi) => {
                  const fileKey = `${order.id}/${f.attachment_id}`
                  const b64 = fileBytes[fileKey]
                  const md = matchData[fileKey]
                  const st = staged[fileKey] || []
                  const isConverting = converting === fileKey
                  const isSending = sendingKey === fileKey

                  return (
                    <div key={fi} style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8, padding: 12, marginBottom: 8 }}>
                      <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#374151', marginBottom: 8 }}>
                        📊 {f.name}
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <Button size="small" onClick={() => handleDownloadAttachment(fileKey, f.message_id, f.attachment_id)}>
                          👁 불러오기
                        </Button>
                        {b64 && (
                          <>
                            <Button size="small" onClick={() => downloadBlob(base64ToBlob(b64), f.name)}>
                              ⬇️ 원본 다운로드
                            </Button>
                            <Button size="small" type="primary" loading={isConverting} onClick={() => handleConvert(fileKey, b64, 'general', 70, f.name)}>
                              📄 일반 변환
                            </Button>
                            <Button size="small" loading={isConverting} onClick={() => handleConvert(fileKey, b64, 'naver', 65, f.name)}>
                              🛒 네이버 변환
                            </Button>
                          </>
                        )}
                      </div>

                      {md && (
                        <div style={{ marginTop: 10 }}>
                          <div style={{ fontSize: '0.78rem', fontWeight: 700, marginBottom: 6 }}>
                            변환 결과: ✅ {md.results?.length || 0}건 / ❌ {md.unmatched?.length || 0}건
                          </div>
                          {(() => {
                            const curStaged = st.length > 0 ? st : (md.results || []).filter((r: any) => r.sku).map((r: any) => ({ sku: r.sku, quantity: r.quantity, price: r.price || 0 }))
                            if (st.length === 0 && curStaged.length > 0) {
                              setStaged(prev => ({ ...prev, [fileKey]: curStaged }))
                            }
                            return (
                              <div style={{ fontSize: '0.78rem', color: '#6b7280' }}>
                                스테이징: {curStaged.length}종 · {curStaged.reduce((s: number, i: any) => s + i.quantity, 0)}개
                              </div>
                            )
                          })()}
                          {st.length > 0 && config.api_token && (
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 8 }}>
                              <Select
                                placeholder="위치"
                                value={locationId}
                                onChange={setLocationId}
                                size="small"
                                style={{ width: 130 }}
                                options={locations.map(l => ({ value: l.id, label: l.name }))}
                              />
                              <input
                                value={memo}
                                onChange={e => setMemo(e.target.value)}
                                placeholder="메모"
                                style={{ flex: 1, border: '1px solid #e5e7eb', borderRadius: 6, padding: '3px 8px', fontSize: '0.78rem' }}
                              />
                              <Button size="small" type="primary" loading={isSending} onClick={() => handleSend(fileKey)}>
                                🚀 전송
                              </Button>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </Col>
        </Row>
      )}

      {!loading && orders.length === 0 && gmailConnected && (
        <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 32, textAlign: 'center', color: '#9ca3af' }}>
          출고 관련 메일이 없습니다
        </div>
      )}
    </div>
  )
}
