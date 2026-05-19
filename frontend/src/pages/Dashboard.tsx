import { useState, useEffect } from 'react'
import { Row, Col, Button, Select, Tag, Empty, Spin } from 'antd'
import { ClearOutlined, ReloadOutlined } from '@ant-design/icons'
import type { AppConfig, LogEntry } from '../types'
import { getLogs, clearLogs } from '../services/api'

interface Props {
  config: AppConfig
}

export default function Dashboard({ config }: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [counter, setCounter] = useState({ total: 0, success: 0, error: 0 })
  const [warnCount, setWarnCount] = useState(0)
  const [filter, setFilter] = useState<string>('전체')
  const [loading, setLoading] = useState(false)
  const [selectedPayload, setSelectedPayload] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    fetchLogs()
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [])

  async function fetchLogs() {
    try {
      const data = await getLogs()
      setLogs(data.logs || [])
      setCounter(data.counter || { total: 0, success: 0, error: 0 })
      setWarnCount(data.warn_count || 0)
    } catch {
      // ignore
    }
  }

  async function handleClearLogs() {
    setLoading(true)
    await clearLogs()
    setLogs([])
    setCounter({ total: 0, success: 0, error: 0 })
    setWarnCount(0)
    setLoading(false)
  }

  const bhOk = Boolean(config.api_token)
  const slackOk = Boolean(config.slack_token)
  const gmailOk = Boolean(config.gmail_token)

  const filteredLogs = logs.filter(l => {
    if (filter === '전체') return true
    if (filter === '성공') return l.level === 'success'
    if (filter === '오류') return l.level === 'error'
    if (filter === '경고') return l.level === 'warning'
    return true
  })

  const levelCfg: Record<string, { icon: string; cls: string; color: string }> = {
    success: { icon: '✅', cls: 'log-success', color: '#065f46' },
    error:   { icon: '❌', cls: 'log-error',   color: '#991b1b' },
    warning: { icon: '⚠️', cls: 'log-warning', color: '#92400e' },
    info:    { icon: 'ℹ️', cls: 'log-info',    color: '#1e40af' },
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">대시보드</h1>
        <p className="page-desc">파이프라인 상태 · 전송 이력 · 오류 로그</p>
      </div>

      {/* Pipeline */}
      <div className="pipe-wrap">
        {[
          { icon: '💬', label: 'Slack', ok: slackOk, sub: slackOk ? '채널 연결됨' : '미연결' },
          { icon: '📧', label: 'Gmail', ok: gmailOk, sub: gmailOk ? '연결됨' : '미연결' },
          { icon: '📂', label: '마스터 파일', ok: true, sub: '대기 중' },
          { icon: '⚙️', label: '변환 엔진', ok: true, sub: '대기 중' },
          { icon: '📦', label: '박스히어로', ok: bhOk, sub: bhOk ? '연결됨' : '미연결' },
        ].map((node, i, arr) => (
          <div key={node.label} style={{ display: 'flex', alignItems: 'center', flex: i < arr.length - 1 ? undefined : 1 }}>
            <div className="pipe-node" style={{ flex: 1 }}>
              <div style={{ fontSize: '1.4rem', marginBottom: 4 }}>{node.icon}</div>
              <div style={{ fontWeight: 700, fontSize: '0.82rem', color: '#111827' }}>
                <span className={`pipe-dot ${node.ok ? 'on' : 'off'}`} />
                {node.label}
              </div>
              <div style={{ fontSize: '0.7rem', color: '#9ca3af', marginTop: 1 }}>{node.sub}</div>
            </div>
            {i < arr.length - 1 && <span className="pipe-arrow">→</span>}
          </div>
        ))}
      </div>

      {/* Metric Cards */}
      <Row gutter={[12, 12]} style={{ marginBottom: 20 }}>
        {[
          { label: '오늘 총 처리', val: counter.total, icon: '📋', bg: '#eff6ff', badge: 'badge-gray' },
          { label: '전송 성공', val: counter.success, icon: '✅', bg: '#ecfdf5', badge: 'badge-green' },
          { label: '전송 실패', val: counter.error, icon: '❌', bg: '#fef2f2', badge: 'badge-red' },
          { label: '경고', val: warnCount, icon: '⚠️', bg: '#fffbeb', badge: 'badge-yellow' },
        ].map(m => (
          <Col key={m.label} span={6}>
            <div className="metric-card">
              <div className="metric-icon" style={{ background: m.bg }}>{m.icon}</div>
              <div style={{ fontSize: '0.78rem', fontWeight: 600, color: '#6b7280', marginBottom: 6 }}>{m.label}</div>
              <div style={{ fontSize: '2rem', fontWeight: 800, color: '#111827', lineHeight: 1, letterSpacing: '-1px' }}>{m.val}</div>
              <div style={{ marginTop: 6 }}>
                <span style={{ fontSize: '0.72rem', fontWeight: 600, borderRadius: 20, padding: '2px 8px', background: '#f3f4f6', color: '#4b5563' }}>
                  오늘 {m.val}건
                </span>
              </div>
            </div>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]}>
        {/* Logs */}
        <Col span={15}>
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
              <span style={{ fontWeight: 700, fontSize: '0.92rem', color: '#111827', flex: 1 }}>📋 전송 이력 / 오류 로그</span>
              <Select
                value={filter}
                onChange={setFilter}
                size="small"
                style={{ width: 100 }}
                options={['전체', '성공', '오류', '경고'].map(v => ({ value: v, label: v }))}
              />
              <Button size="small" icon={<ReloadOutlined />} onClick={fetchLogs} />
              <Button size="small" icon={<ClearOutlined />} onClick={handleClearLogs} loading={loading}>초기화</Button>
            </div>
            {filteredLogs.length === 0 ? (
              <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: '0.85rem' }}>
                전송 이력이 없습니다.
              </div>
            ) : (
              <div style={{ maxHeight: 400, overflowY: 'auto' }}>
                {filteredLogs.map((log, i) => {
                  const cfg = levelCfg[log.level] || levelCfg.info
                  return (
                    <div
                      key={i}
                      className={`log-item ${cfg.cls}`}
                      style={{ cursor: log.payload ? 'pointer' : 'default' }}
                      onClick={() => log.payload && setSelectedPayload(log.payload as Record<string, unknown>)}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <span style={{ fontWeight: 700, fontSize: '0.82rem', color: cfg.color }}>
                          {cfg.icon} {log.message}
                        </span>
                        <span style={{ fontSize: '0.7rem', color: '#9ca3af', whiteSpace: 'nowrap', marginLeft: 8 }}>{log.ts}</span>
                      </div>
                      {log.detail && <div style={{ fontSize: '0.74rem', color: '#6b7280', marginTop: 3 }}>{log.detail}</div>}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </Col>

        {/* Payload Inspector */}
        <Col span={9}>
          <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: '16px', boxShadow: '0 1px 3px rgba(0,0,0,0.04)', height: '100%' }}>
            <div style={{ fontWeight: 700, fontSize: '0.92rem', color: '#111827', marginBottom: 12 }}>🔍 Payload Inspector</div>
            {selectedPayload ? (
              <>
                <Button size="small" onClick={() => setSelectedPayload(null)} style={{ marginBottom: 8 }}>닫기</Button>
                <pre style={{ background: '#f9fafb', borderRadius: 8, padding: 12, fontSize: '0.74rem', overflow: 'auto', maxHeight: 360 }}>
                  {JSON.stringify(selectedPayload, null, 2)}
                </pre>
              </>
            ) : (
              <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: '0.85rem' }}>
                로그 항목을 클릭하면 payload를 확인할 수 있습니다
              </div>
            )}
          </div>
        </Col>
      </Row>
    </div>
  )
}
