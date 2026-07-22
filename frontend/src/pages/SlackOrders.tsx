import { useState, useEffect } from 'react'
import { Button, Select, message, Spin, Alert, Row, Col, Upload, Switch } from 'antd'
import { ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import type { AppConfig, Location, SlackOrder, Page } from '../types'
import {
  getSlackMessages, downloadSlackFile, toggleSlackReaction, joinSlackChannel,
  base64ToBlob, convertGeneral, getGeneralColumns, convertNaver, sendToBoxhero, downloadBlob, updateConfig
} from '../services/api'

interface Props {
  config: AppConfig
  channels: Record<string, string>
  locations: Location[]
  onNavigate: (p: Page) => void
}

function reactionToStatus(reactions: { name: string; count: number }[]) {
  const STATUS_MAP: Record<string, [string, string, string]> = {
    // 완료
    white_check_mark: ['완료', '#10b981', '#d1fae5'],
    heavy_check_mark: ['완료', '#10b981', '#d1fae5'],
    ballot_box_with_check: ['완료', '#10b981', '#d1fae5'],
    check: ['완료', '#10b981', '#d1fae5'],
    check_mark: ['완료', '#10b981', '#d1fae5'],
    '100': ['완료', '#10b981', '#d1fae5'],
    done: ['완료', '#10b981', '#d1fae5'],
    ok: ['완료', '#10b981', '#d1fae5'],
    ok_hand: ['완료', '#10b981', '#d1fae5'],
    완료: ['완료', '#10b981', '#d1fae5'],
    완: ['완료', '#10b981', '#d1fae5'],
    success: ['완료', '#10b981', '#d1fae5'],
    // 진행중
    hourglass_flowing_sand: ['진행중', '#f59e0b', '#fef3c7'],
    hourglass: ['진행중', '#f59e0b', '#fef3c7'],
    arrows_counterclockwise: ['진행중', '#f59e0b', '#fef3c7'],
    loading: ['진행중', '#f59e0b', '#fef3c7'],
    spinner: ['진행중', '#f59e0b', '#fef3c7'],
    진행중: ['진행중', '#f59e0b', '#fef3c7'],
    처리중: ['진행중', '#f59e0b', '#fef3c7'],
    // 반려
    x: ['반려', '#ef4444', '#fee2e2'],
    negative_squared_cross_mark: ['반려', '#ef4444', '#fee2e2'],
    반려: ['반려', '#ef4444', '#fee2e2'],
    취소: ['반려', '#ef4444', '#fee2e2'],
    no_entry: ['반려', '#ef4444', '#fee2e2'],
  }
  const priority = ['완료', '반려', '진행중']
  const found: Record<string, [string, string, number]> = {}
  for (const r of reactions) {
    const key = r.name.toLowerCase()
    if (STATUS_MAP[key]) {
      const [label, color, bg] = STATUS_MAP[key]
      found[label] = [color, bg, r.count]
    }
  }
  for (const p of priority) {
    if (found[p]) return { label: p, color: found[p][0], bg: found[p][1], count: found[p][2] }
  }
  return null
}

function statusLabelOf(o: SlackOrder): string {
  return reactionToStatus(o.reactions)?.label || '미처리'
}

// 슬랙 mrkdwn → 읽기 좋은 텍스트 (멘션/링크/이모지 코드 정리)
const EMOJI_MAP: Record<string, string> = {
  package: '📦', warning: '⚠️', white_check_mark: '✅', x: '❌',
  hourglass_flowing_sand: '⏳', calendar: '📅', date: '📅', pushpin: '📌',
  memo: '📝', truck: '🚚', bulb: '💡', point_right: '👉', mag: '🔍',
  bell: '🔔', tada: '🎉', fire: '🔥', star: '⭐',
}

function decodeSlackLine(line: string): string {
  return line
    .replace(/<@([A-Z0-9]+)>/g, '@$1')
    .replace(/<#[A-Z0-9]+\|([^>]+)>/g, '#$1')
    .replace(/<(https?:[^|>]+)\|([^>]+)>/g, '$2')
    .replace(/<(https?:[^>]+)>/g, '$1')
    .replace(/:([a-z0-9_+-]+):/g, (m, name) => EMOJI_MAP[name] || m)
}

function renderInline(line: string) {
  const parts = line.split(/(\*[^*\n]+\*|`[^`\n]+`)/g)
  return parts.map((p, i) => {
    if (p.length > 2 && p.startsWith('*') && p.endsWith('*'))
      return <strong key={i}>{p.slice(1, -1)}</strong>
    if (p.length > 2 && p.startsWith('`') && p.endsWith('`'))
      return <code key={i} style={{ background: '#eef2f7', padding: '0 4px', borderRadius: 4, fontSize: '0.78rem' }}>{p.slice(1, -1)}</code>
    return p
  })
}

function getSummary(parsed: Record<string, string>) {
  const clean = (s: string) => s.replace(/<[^>]+>/g, '').replace(/:\w+:/g, '').replace(/\n{2,}/g, '\n').trim()
  return {
    목적: (clean(parsed['목적'] || '')).split('\n')[0].slice(0, 30),
    일정: (() => {
      const s = clean(parsed['일정'] || '')
      const dates = s.match(/\d{1,2}[./]\d{1,2}|\d{4}[./\-]\d{1,2}[./\-]\d{1,2}/g)
      return dates ? dates.join('  ') : s.slice(0, 20)
    })(),
    품목: clean(parsed['품목'] || '').split('\n').filter(Boolean).slice(0, 3).join(', '),
    담당자: (() => { const m = clean(parsed['담당자'] || '').match(/[가-힣]{2,4}/); return m ? m[0] : '' })(),
  }
}

export default function SlackOrders({ config, channels, locations }: Props) {
  const [selectedChannel, setSelectedChannel] = useState<string>('')
  const [orders, setOrders] = useState<SlackOrder[]>([])
  const [debug, setDebug] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [fileBytes, setFileBytes] = useState<Record<string, string>>({})  // key → base64
  const [converting, setConverting] = useState<string | null>(null)
  const [matchData, setMatchData] = useState<Record<string, any>>({})  // fileKey → match result
  const [staged, setStaged] = useState<Record<string, any[]>>({})
  const [sendingKey, setSendingKey] = useState<string | null>(null)
  const [locationId, setLocationId] = useState<number | undefined>(config.selected_location_id)
  const [memo, setMemo] = useState('')
  const [statusFilter, setStatusFilter] = useState<string | null>(null)  // null = 전체
  const [notifyOn, setNotifyOn] = useState<boolean>(!!config.slack_outbound_notify)
  const [notifyChannel, setNotifyChannel] = useState<string>(config.slack_outbound_channel || '물류_출고')
  const [notifyTime, setNotifyTime] = useState<string>(config.bh_notify_time || '18:00')

  const channelNames = Object.keys(channels)
  const channelId = selectedChannel ? channels[selectedChannel] : ''
  const order = orders[selectedIdx]

  useEffect(() => {
    if (channelNames.length > 0 && !selectedChannel) {
      const def = channelNames.find(n => n === '물류_출고') || channelNames[0]
      setSelectedChannel(def)
    }
  }, [channels])

  useEffect(() => {
    if (selectedChannel && channels[selectedChannel]) {
      fetchMessages(channels[selectedChannel])
    }
  }, [selectedChannel])

  async function fetchMessages(chId: string) {
    if (!config.slack_token || !chId) return
    setLoading(true)
    try {
      const res = await getSlackMessages(config.slack_token, chId)
      setOrders(res.orders || [])
      setDebug(res.debug || '')
      setSelectedIdx(0)
    } catch (e: any) {
      message.error('메시지 로드 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setLoading(false)
    }
  }

  async function handleChannelChange(name: string) {
    setSelectedChannel(name)
    setOrders([])
    setStatusFilter(null)
    await fetchMessages(channels[name])
  }

  async function handleRefresh() {
    if (channelId) await fetchMessages(channelId)
  }

  async function saveNotify(on: boolean, ch: string, t: string) {
    setNotifyOn(on); setNotifyChannel(ch); setNotifyTime(t)
    try {
      await updateConfig({ slack_outbound_notify: on, slack_outbound_channel: ch, bh_notify_time: t })
      message.success(on ? `매일 ${t} #${ch}에 출고 요약 ON` : '출고 요약 OFF')
    } catch {
      message.error('설정 저장 실패')
    }
  }

  async function handleJoinChannel() {
    try {
      await joinSlackChannel(config.slack_token!, channelId)
      message.success('채널 참여 완료')
      await fetchMessages(channelId)
    } catch (e: any) {
      const detail = e.response?.data?.detail || ''
      if (detail === 'private_channel') {
        message.warning('Private 채널입니다. Slack에서 직접 봇을 초대해주세요.')
      } else {
        message.error('채널 참여 실패: ' + detail)
      }
    }
  }

  async function handleReaction(emoji: string) {
    if (!order || !config.slack_token) return
    try {
      await toggleSlackReaction(config.slack_token, channelId, order.ts, emoji)
      await handleRefresh()
    } catch (e: any) {
      message.error('리액션 오류: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function handleDownloadFile(fileKey: string, url: string) {
    try {
      const res = await downloadSlackFile(url, config.slack_token!)
      setFileBytes(prev => ({ ...prev, [fileKey]: res.data }))
    } catch (e: any) {
      message.error('파일 다운로드 실패: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function handleConvert(fileKey: string, b64: string, format: 'general' | 'naver', thresh: number) {
    const blob = base64ToBlob(b64)
    const filename = fileKey.split('/').pop() || 'file.xlsx'
    setConverting(fileKey)
    try {
      if (format === 'naver') {
        const data = await convertNaver(blob, filename, thresh)
        setMatchData(prev => ({ ...prev, [fileKey]: { ...data, format: 'naver' } }))
      } else {
        const colData = await getGeneralColumns(blob, filename)
        const data = await convertGeneral(blob, filename, colData.name_col, colData.qty_col, thresh)
        setMatchData(prev => ({ ...prev, [fileKey]: { ...data, format: 'general' } }))
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

  if (!config.slack_token) {
    return (
      <div>
        <div className="page-header"><h1 className="page-title">Slack 출고요청</h1></div>
        <Alert message="Slack이 연결되지 않았습니다. 설정 페이지에서 Bot Token을 입력해주세요." type="warning" showIcon />
      </div>
    )
  }

  const stats = {
    total: orders.length,
    done: orders.filter(o => statusLabelOf(o) === '완료').length,
    wip: orders.filter(o => statusLabelOf(o) === '진행중').length,
    reject: orders.filter(o => statusLabelOf(o) === '반려').length,
    todo: orders.filter(o => statusLabelOf(o) === '미처리').length,
  }

  // 필터링된 목록 (원본 인덱스 보존)
  const visibleOrders = orders
    .map((o, idx) => ({ o, idx }))
    .filter(({ o }) => !statusFilter || statusLabelOf(o) === statusFilter)

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Slack 출고요청</h1>
        <p className="page-desc">Slack 채널에서 출고 요청 확인 및 파일 로드</p>
      </div>

      {/* Channel selector */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
        <Select
          placeholder="채널 선택"
          value={selectedChannel || undefined}
          onChange={handleChannelChange}
          style={{ width: 200 }}
          options={channelNames.map(n => ({ value: n, label: `#${n}` }))}
        />
        <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading}>새로고침</Button>
        {debug && <span style={{ fontSize: '0.78rem', color: '#9ca3af' }}>{debug}</span>}
      </div>

      {/* BH 출고 → 슬랙 봇 알림 설정 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, padding: '10px 14px', background: notifyOn ? '#ecfdf5' : '#f9fafb', border: `1px solid ${notifyOn ? '#10b981' : '#e5e7eb'}`, borderRadius: 10, flexWrap: 'wrap' }}>
        <RobotOutlined style={{ color: notifyOn ? '#10b981' : '#9ca3af' }} />
        <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>박스히어로 출고 일일 요약 봇</span>
        <Switch checked={notifyOn} onChange={on => saveNotify(on, notifyChannel, notifyTime)} checkedChildren="ON" unCheckedChildren="OFF" />
        <span style={{ fontSize: '0.78rem', color: '#6b7280', display: 'flex', alignItems: 'center', gap: 6 }}>
          매일
          <Select
            size="small"
            value={notifyTime}
            onChange={t => saveNotify(notifyOn, notifyChannel, t)}
            style={{ width: 90 }}
            options={Array.from({ length: 48 }, (_, i) => { const v = `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 ? '30' : '00'}`; return { value: v, label: v } })}
          />
          <Select
            size="small"
            value={notifyChannel}
            onChange={ch => saveNotify(notifyOn, ch, notifyTime)}
            style={{ width: 170 }}
            options={channelNames.map(n => ({ value: n, label: `#${n}` }))}
            showSearch
          />
        </span>
        <span style={{ fontSize: '0.72rem', color: '#9ca3af' }}>· 그날 BH 처리된 출고를 거래처별로 취합해 1회 (출고일자 표기)</span>
      </div>

      {/* Status bar */}
      {orders.length > 0 && (
        <Row gutter={8} style={{ marginBottom: 12 }}>
          {[
            { label: '전체', filter: null, count: stats.total, bg: '#f9fafb', border: '#e5e7eb', color: '#111827' },
            { label: '미처리', filter: '미처리', count: stats.todo, bg: '#fef9c3', border: '#fde047', color: '#92400e' },
            { label: '진행중', filter: '진행중', count: stats.wip, bg: '#eff6ff', border: '#bfdbfe', color: '#1d4ed8' },
            { label: '완료', filter: '완료', count: stats.done, bg: '#f0fdf4', border: '#bbf7d0', color: '#15803d' },
            { label: '반려', filter: '반려', count: stats.reject, bg: '#fef2f2', border: '#fecaca', color: '#dc2626' },
          ].map(s => {
            const isActive = statusFilter === s.filter
            return (
              <Col key={s.label} span={4}>
                <div
                  onClick={() => setStatusFilter(s.filter)}
                  style={{
                    background: s.bg,
                    border: `${isActive ? 2 : 1}px solid ${isActive ? s.color : s.border}`,
                    borderRadius: 10, padding: '10px 8px', textAlign: 'center', cursor: 'pointer',
                    boxShadow: isActive ? `0 0 0 2px ${s.bg}, 0 2px 6px rgba(0,0,0,0.08)` : 'none',
                    transition: 'all 0.12s',
                  }}
                >
                  <div style={{ fontSize: '1.4rem', fontWeight: 800, color: s.color }}>{s.count}</div>
                  <div style={{ fontSize: '0.75rem', color: s.color, fontWeight: 600 }}>{s.label}</div>
                </div>
              </Col>
            )
          })}
        </Row>
      )}

      {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

      {debug === 'not_in_channel' && (
        <Alert
          message={`봇이 #${selectedChannel} 채널에 접근할 수 없습니다`}
          description="채널에 봇을 초대하거나 아래 버튼으로 참여를 시도하세요."
          type="error"
          action={<Button size="small" onClick={handleJoinChannel}>채널 참여 시도</Button>}
          style={{ marginBottom: 12 }}
        />
      )}

      {!loading && orders.length > 0 && (
        <Row gutter={[12, 12]}>
          {/* Order list */}
          <Col span={8} style={{ maxHeight: 'calc(100vh - 260px)', overflowY: 'auto' }}>
            {visibleOrders.length === 0 && (
              <div style={{ color: '#9ca3af', fontSize: '0.82rem', textAlign: 'center', padding: 20 }}>
                해당 상태의 메시지가 없습니다
              </div>
            )}
            {visibleOrders.map(({ o, idx }) => {
              const summ = getSummary(o.parsed)
              const status = reactionToStatus(o.reactions)
              const isSelected = selectedIdx === idx
              return (
                <div
                  key={o.ts}
                  onClick={() => setSelectedIdx(idx)}
                  style={{
                    border: isSelected ? '1.5px solid #10b981' : '1px solid #e5e7eb',
                    background: isSelected ? '#f0fdf4' : '#fff',
                    borderRadius: 10, padding: '10px 13px', marginBottom: 6, cursor: 'pointer',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontSize: '0.68rem', color: '#9ca3af' }}>{o.dt} {o.files.length > 0 ? '📎' : ''}</span>
                    {status ? (
                      <span style={{ background: status.bg, color: status.color, borderRadius: 4, padding: '1px 8px', fontSize: '0.7rem', fontWeight: 700 }}>● {status.label}</span>
                    ) : o.reactions.length > 0 ? (
                      <span style={{ background: '#fef3c7', color: '#92400e', borderRadius: 4, padding: '1px 8px', fontSize: '0.7rem' }}>
                        :{o.reactions.map(r => r.name).join(': :')}: 미인식
                      </span>
                    ) : (
                      <span style={{ background: '#f3f4f6', color: '#9ca3af', borderRadius: 4, padding: '1px 8px', fontSize: '0.7rem' }}>미처리</span>
                    )}
                  </div>
                  <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#111827', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
                    {summ.목적 || o.title}
                  </div>
                  {summ.일정 && <div style={{ fontSize: '0.7rem', color: '#6b7280', marginTop: 3 }}>📅 {summ.일정}</div>}
                </div>
              )
            })}
          </Col>

          {/* Detail */}
          <Col span={16}>
            {order && (
              <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 16 }}>
                {/* Status + summary */}
                {(() => {
                  const status = reactionToStatus(order.reactions)
                  const summ = getSummary(order.parsed)
                  return (
                    <>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                        {status ? (
                          <span style={{ background: status.bg, color: status.color, borderRadius: 6, padding: '4px 12px', fontSize: '0.82rem', fontWeight: 800 }}>● {status.label}</span>
                        ) : (
                          <span style={{ background: '#f3f4f6', color: '#6b7280', borderRadius: 6, padding: '4px 12px', fontSize: '0.82rem' }}>⏳ 미처리</span>
                        )}
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
                        {summ.일정 && <span style={{ background: '#eff6ff', color: '#1d4ed8', borderRadius: 6, padding: '3px 10px', fontSize: '0.78rem', fontWeight: 700 }}>📅 {summ.일정}</span>}
                        {summ.목적 && <span style={{ background: '#f0fdf4', color: '#15803d', borderRadius: 6, padding: '3px 10px', fontSize: '0.78rem', fontWeight: 700 }}>🎯 {summ.목적}</span>}
                        {summ.담당자 && <span style={{ background: '#faf5ff', color: '#7e22ce', borderRadius: 6, padding: '3px 10px', fontSize: '0.78rem', fontWeight: 700 }}>👤 {summ.담당자}</span>}
                      </div>
                      {summ.품목 && (
                        <div style={{ background: '#f9fafb', borderRadius: 8, padding: '8px 12px', fontSize: '0.82rem', color: '#374151', marginBottom: 12 }}>
                          <strong>품목:</strong> {summ.품목}
                        </div>
                      )}
                    </>
                  )
                })()}

                {/* Message detail (raw text) */}
                {order.raw && (
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#374151', marginBottom: 6 }}>📄 메시지 세부내역</div>
                    <div style={{
                      background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8,
                      padding: '10px 14px', fontSize: '0.82rem', color: '#1f2937', lineHeight: 1.7,
                      wordBreak: 'break-word', maxHeight: 360, overflowY: 'auto',
                    }}>
                      {order.raw.split('\n').map((ln, i) => (
                        <div key={i}>{ln ? renderInline(decodeSlackLine(ln)) : ' '}</div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Reaction buttons */}
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#374151', marginBottom: 6 }}>🏷 처리 상태 변경</div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {[
                      { emoji: 'hourglass_flowing_sand', label: '⏳ 처리중' },
                      { emoji: 'white_check_mark', label: '✅ 완료' },
                      { emoji: 'x', label: '❌ 반려' },
                    ].map(btn => {
                      const active = order.reactions.some(r => r.name === btn.emoji)
                      return (
                        <Button
                          key={btn.emoji}
                          size="small"
                          type={active ? 'primary' : 'default'}
                          onClick={() => handleReaction(btn.emoji)}
                        >
                          {btn.label}
                        </Button>
                      )
                    })}
                  </div>
                </div>

                {/* Files */}
                <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#374151', marginBottom: 8 }}>📎 첨부파일</div>
                {order.files.length === 0 ? (
                  <div style={{ color: '#9ca3af', fontSize: '0.82rem' }}>첨부파일 없음</div>
                ) : order.files.map((f, fi) => {
                  const fileKey = `${order.ts}/${fi}/${f.name}`
                  const b64 = fileBytes[fileKey]
                  const md = matchData[fileKey]
                  const st = staged[fileKey] || []
                  const isConverting = converting === fileKey
                  const isSending = sendingKey === fileKey

                  return (
                    <div key={fi} style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8, padding: 12, marginBottom: 8 }}>
                      <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#374151', marginBottom: 8 }}>
                        📊 {f.name} <span style={{ fontSize: '0.72rem', color: '#9ca3af', fontWeight: 400 }}>{Math.round(f.size / 1024)} KB</span>
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <Button size="small" onClick={() => handleDownloadFile(fileKey, f.url)}>
                          👁 파일 불러오기
                        </Button>
                        {b64 && (
                          <>
                            <Button size="small" onClick={() => {
                              const blob = base64ToBlob(b64)
                              downloadBlob(blob, f.name)
                            }}>⬇️ 원본 다운로드</Button>
                            <Button size="small" type="primary" loading={isConverting} onClick={() => handleConvert(fileKey, b64, 'general', 70)}>
                              📄 일반 변환
                            </Button>
                            <Button size="small" loading={isConverting} onClick={() => handleConvert(fileKey, b64, 'naver', 65)}>
                              🛒 네이버 변환
                            </Button>
                          </>
                        )}
                      </div>

                      {/* Match results */}
                      {md && (
                        <div style={{ marginTop: 10 }}>
                          <div style={{ fontSize: '0.78rem', fontWeight: 700, marginBottom: 6 }}>
                            변환 결과: ✅ {md.results?.length || 0}건 / ❌ {md.unmatched?.length || 0}건
                          </div>
                          {(() => {
                            const allRows = [
                              ...(md.results || []).map((r: any) => ({ ...r, ok: true })),
                              ...(md.unmatched || []).map((u: any) => ({ original_name: u.original_name, quantity: u.quantity, matched_name: '(건너뜀)', ok: false, sku: '', price: 0 })),
                            ]
                            const curStaged = st.length > 0 ? st : allRows.filter((r: any) => r.ok && r.sku).map((r: any) => ({ sku: r.sku, quantity: r.quantity, price: r.price || 0 }))
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

      {!loading && orders.length === 0 && channelId && debug !== 'not_in_channel' && (
        <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 32, textAlign: 'center', color: '#9ca3af' }}>
          출고 요청 메시지가 없습니다
        </div>
      )}
    </div>
  )
}
