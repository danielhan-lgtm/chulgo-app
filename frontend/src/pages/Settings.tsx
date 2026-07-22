import { useState, useEffect } from 'react'
import { Button, Input, Select, Alert, message, Tag } from 'antd'
import { CheckCircleOutlined, DisconnectOutlined, LinkOutlined, ExclamationCircleOutlined, FileTextOutlined } from '@ant-design/icons'
import type { AppConfig, Location } from '../types'
import {
  boxheroConnect, slackConnect, getGmailAuthUrl, disconnectGmail, getGmailStatus,
  updateConfig, getLocations,
} from '../services/api'
import api from '../services/api'

interface Props {
  config: AppConfig
  locations: Location[]
  channels: Record<string, string>
  onConfigChange: (updates: Partial<AppConfig>) => void
  onLocationsChange: (locs: Location[]) => void
  onChannelsChange: (chs: Record<string, string>) => void
  onMasterLoaded: () => void
  onGmailConnected: () => void
}

export default function Settings({
  config, locations, channels,
  onConfigChange, onLocationsChange, onChannelsChange, onGmailConnected,
}: Props) {
  const [bhToken, setBhToken] = useState('')
  const [bhLoading, setBhLoading] = useState(false)
  const [slackToken, setSlackToken] = useState('')
  const [slackLoading, setSlackLoading] = useState(false)
  const [selectedChannel, setSelectedChannel] = useState('')
  const [gmailClientId, setGmailClientId] = useState('')
  const [gmailClientSecret, setGmailClientSecret] = useState('')
  const [gmailAuthUrl, setGmailAuthUrl] = useState('')
  const [gmailLoading, setGmailLoading] = useState(false)
  const [gmailConnected, setGmailConnected] = useState(false)
  const [selectedLocation, setSelectedLocation] = useState<number | undefined>(config.selected_location_id)
  const [ourboxId, setOurboxId] = useState(config.ourbox_id || '')
  const [ourboxPw, setOurboxPw] = useState('')
  const [ourboxAccessKey, setOurboxAccessKey] = useState(config.ourbox_access_key || '')
  const [ourboxSecretKey, setOurboxSecretKey] = useState(config.ourbox_secret_key || '')
  const [ourboxSaving, setOurboxSaving] = useState(false)
  const [probeLoading, setProbeLoading] = useState(false)
  const [probeResult, setProbeResult] = useState<Record<string, unknown> | null>(null)
  const [claudeApiKey, setClaudeApiKey] = useState(config.claude_api_key || '')
  const [geminiApiKey, setGeminiApiKey] = useState(config.gemini_api_key || '')
  const [groqApiKey, setGroqApiKey] = useState(config.groq_api_key || '')
  const [claudeSaving, setClaudeSaving] = useState(false)

  useEffect(() => {
    checkGmailStatus()
  }, [])

  async function checkGmailStatus() {
    try {
      const res = await getGmailStatus()
      setGmailConnected(res.connected)
      if (res.connected) onGmailConnected()
    } catch { /* ignore */ }
  }

  async function handleBhConnect() {
    const token = bhToken.trim() || config.api_token
    if (!token) return message.warning('API 토큰을 입력하세요')
    setBhLoading(true)
    try {
      const res = await boxheroConnect(token)
      onConfigChange({ api_token: token })
      onLocationsChange(res.locations)
      message.success(`✅ 박스히어로 연결됨 (${res.locations.length}개 위치)`)
      setBhToken('')
    } catch (e: any) {
      message.error('연결 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setBhLoading(false)
    }
  }

  async function handleBhDisconnect() {
    await updateConfig({ api_token: '' })
    onConfigChange({ api_token: '' })
    onLocationsChange([])
    message.info('박스히어로 연결 해제')
  }

  async function handleLocationChange(locId: number) {
    setSelectedLocation(locId)
    const loc = locations.find(l => l.id === locId)
    await updateConfig({ selected_location_id: locId, selected_location_name: loc?.name })
    onConfigChange({ selected_location_id: locId })
  }

  async function handleSlackConnect() {
    const token = slackToken.trim() || config.slack_token
    if (!token) return message.warning('Bot Token을 입력하세요')
    setSlackLoading(true)
    try {
      const res = await slackConnect(token)
      onConfigChange({ slack_token: token })
      onChannelsChange(res.channels)
      message.success(`✅ Slack 연결됨 (${Object.keys(res.channels).length}개 채널)`)
      setSlackToken('')
    } catch (e: any) {
      message.error('연결 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSlackLoading(false)
    }
  }

  async function handleSlackDisconnect() {
    await updateConfig({ slack_token: '' })
    onConfigChange({ slack_token: '' })
    onChannelsChange({})
    message.info('Slack 연결 해제')
  }

  async function handleGmailAuthUrl() {
    if (!gmailClientId || !gmailClientSecret) return message.warning('Client ID와 Secret을 입력하세요')
    setGmailLoading(true)
    try {
      const res = await getGmailAuthUrl(gmailClientId.trim(), gmailClientSecret.trim())
      setGmailAuthUrl(res.auth_url)
    } catch (e: any) {
      message.error('인증 URL 생성 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setGmailLoading(false)
    }
  }

  async function handleGmailDisconnect() {
    await disconnectGmail()
    setGmailConnected(false)
    setGmailAuthUrl('')
    message.info('Gmail 연결 해제')
  }

  async function handleOurboxProbe() {
    setProbeLoading(true)
    setProbeResult(null)
    try {
      const r = await api.get('/reconcile/probe')
      setProbeResult(r.data)
      if (r.data.login) {
        message.success(`✅ OurBox REST API 로그인 성공! (${r.data.login})`)
      } else {
        message.warning('로그인 실패 - errors 확인')
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error('API 탐색 실패: ' + (err.response?.data?.detail ?? String(e)))
    } finally {
      setProbeLoading(false)
    }
  }

  async function handleOurboxSave() {
    if (!ourboxId.trim()) return message.warning('아워박스 아이디를 입력하세요')
    if (!ourboxPw.trim()) return message.warning('아워박스 비밀번호를 입력하세요')
    setOurboxSaving(true)
    try {
      await updateConfig({ ourbox_id: ourboxId.trim(), ourbox_pw: ourboxPw.trim(), ourbox_access_key: ourboxAccessKey.trim(), ourbox_secret_key: ourboxSecretKey.trim() })
      onConfigChange({ ourbox_id: ourboxId.trim(), ourbox_pw: ourboxPw.trim(), ourbox_access_key: ourboxAccessKey.trim(), ourbox_secret_key: ourboxSecretKey.trim() })
      message.success('아워박스 정보 저장됨')
      setOurboxPw('')
    } catch (e: any) {
      message.error('저장 실패: ' + (e.response?.data?.detail || e.message))
    } finally {
      setOurboxSaving(false)
    }
  }

  const [tplPath, setTplPath] = useState('')
  const [tplStatus, setTplStatus] = useState<{ path: string; exists: boolean } | null>(null)
  const [tplSaving, setTplSaving] = useState(false)

  useEffect(() => {
    api.get('/invoice/template-path').then(r => { setTplStatus(r.data); setTplPath(r.data.path) }).catch(() => {})
  }, [])

  async function saveTemplatePath() {
    if (!tplPath.trim()) return
    setTplSaving(true)
    try {
      await api.post('/invoice/template-path', { path: tplPath.trim() })
      const r = await api.get('/invoice/template-path')
      setTplStatus(r.data)
      message.success('거래명세서 양식 경로 저장됨')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail ?? '저장 실패')
    } finally {
      setTplSaving(false)
    }
  }

  const bhConnected = Boolean(config.api_token)
  const slackConnected = Boolean(config.slack_token)
  const channelNames = Object.keys(channels)

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">설정</h1>
        <p className="page-desc">박스히어로 · Slack · Gmail 연동 설정</p>
      </div>

      <div style={{ maxWidth: 680 }}>

        {/* BoxHero */}
        <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <span style={{ fontSize: '1.2rem' }}>📦</span>
            <span style={{ fontWeight: 700, fontSize: '1rem', color: '#111827' }}>박스히어로 API</span>
            {bhConnected ? (
              <Tag color="success" icon={<CheckCircleOutlined />}>연결됨</Tag>
            ) : (
              <Tag color="default" icon={<ExclamationCircleOutlined />}>미연결</Tag>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <Input.Password
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              value={bhToken}
              onChange={e => setBhToken(e.target.value)}
              style={{ flex: 1 }}
            />
            <Button
              type="primary"
              icon={<LinkOutlined />}
              onClick={handleBhConnect}
              loading={bhLoading}
            >
              연결
            </Button>
            {bhConnected && (
              <Button icon={<DisconnectOutlined />} onClick={handleBhDisconnect} danger>해제</Button>
            )}
          </div>
          {bhConnected && locations.length > 0 && (
            <div>
              <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 6 }}>출고 위치 선택</div>
              <Select
                value={selectedLocation}
                onChange={handleLocationChange}
                style={{ width: '100%' }}
                placeholder="위치 선택"
                options={locations.map(l => ({ value: l.id, label: l.name }))}
              />
            </div>
          )}
          <div style={{ fontSize: '0.75rem', color: '#9ca3af', marginTop: 8 }}>
            박스히어로 설정 &gt; API에서 토큰을 발급받으세요
          </div>
        </div>

        {/* Slack */}
        <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <span style={{ fontSize: '1.2rem' }}>💬</span>
            <span style={{ fontWeight: 700, fontSize: '1rem', color: '#111827' }}>Slack</span>
            {slackConnected ? (
              <Tag color="success" icon={<CheckCircleOutlined />}>연결됨</Tag>
            ) : (
              <Tag color="default" icon={<ExclamationCircleOutlined />}>미연결</Tag>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <Input.Password
              placeholder="xoxb-..."
              value={slackToken}
              onChange={e => setSlackToken(e.target.value)}
              style={{ flex: 1 }}
            />
            <Button type="primary" icon={<LinkOutlined />} onClick={handleSlackConnect} loading={slackLoading}>연결</Button>
            {slackConnected && (
              <Button icon={<DisconnectOutlined />} onClick={handleSlackDisconnect} danger>해제</Button>
            )}
          </div>
          {slackConnected && channelNames.length > 0 && (
            <div>
              <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 6 }}>채널 선택 (출고요청 페이지에서 사용)</div>
              <Select
                value={selectedChannel || undefined}
                onChange={setSelectedChannel}
                style={{ width: '100%' }}
                placeholder="채널 선택"
                options={channelNames.map(n => ({ value: n, label: `#${n}` }))}
                showSearch
              />
            </div>
          )}
          <div style={{ fontSize: '0.75rem', color: '#9ca3af', marginTop: 8 }}>
            api.slack.com에서 Bot Token (xoxb-...)을 발급받으세요
          </div>
        </div>

        {/* Gmail */}
        <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <span style={{ fontSize: '1.2rem' }}>📧</span>
            <span style={{ fontWeight: 700, fontSize: '1rem', color: '#111827' }}>Gmail OAuth</span>
            {gmailConnected ? (
              <Tag color="success" icon={<CheckCircleOutlined />}>연결됨</Tag>
            ) : (
              <Tag color="default" icon={<ExclamationCircleOutlined />}>미연결</Tag>
            )}
          </div>

          {gmailConnected ? (
            <div>
              <Alert message="Gmail이 연결되어 있습니다. 출고 요청 메일을 불러올 수 있습니다." type="success" showIcon style={{ marginBottom: 12 }} />
              <Button icon={<DisconnectOutlined />} onClick={handleGmailDisconnect} danger>Gmail 연결 해제</Button>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                <Input.Password
                  placeholder="Client ID (xxxx.apps.googleusercontent.com)"
                  value={gmailClientId}
                  onChange={e => setGmailClientId(e.target.value)}
                  style={{ flex: 1 }}
                />
              </div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                <Input.Password
                  placeholder="Client Secret (GOCSPX-...)"
                  value={gmailClientSecret}
                  onChange={e => setGmailClientSecret(e.target.value)}
                  style={{ flex: 1 }}
                />
                <Button
                  type="primary"
                  onClick={handleGmailAuthUrl}
                  loading={gmailLoading}
                  icon={<LinkOutlined />}
                >
                  인증 URL 생성
                </Button>
              </div>
              {gmailAuthUrl && (
                <Alert
                  message={
                    <div>
                      <div style={{ fontWeight: 600, marginBottom: 6 }}>Google 인증 필요</div>
                      <div style={{ fontSize: '0.82rem', marginBottom: 8 }}>
                        아래 링크를 클릭해서 Google 계정 인증 후 자동으로 연결됩니다:
                      </div>
                      <Button
                        type="primary"
                        size="small"
                        href={gmailAuthUrl}
                        target="_blank"
                        icon={<LinkOutlined />}
                      >
                        🔑 Google 인증 페이지 열기
                      </Button>
                      <div style={{ fontSize: '0.75rem', color: '#6b7280', marginTop: 8 }}>
                        인증 완료 후 이 페이지로 자동 리다이렉트됩니다
                      </div>
                    </div>
                  }
                  type="info"
                  style={{ marginBottom: 8 }}
                />
              )}
              <div style={{ fontSize: '0.75rem', color: '#9ca3af' }}>
                Google Cloud Console에서 OAuth 2.0 클라이언트 ID를 생성하고<br />
                리다이렉트 URI에 <code>http://localhost:8081/api/gmail/callback</code>을 추가하세요
              </div>
            </>
          )}
        </div>

        {/* OurBox */}
        <div style={{ background: '#fff', borderRadius: 14, border: '1px solid #e5e7eb', padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
            <span style={{ fontSize: '1.2rem' }}>🏭</span>
            <span style={{ fontWeight: 700, fontSize: '1rem', color: '#111827' }}>아워박스 (입고정산기)</span>
            {config.ourbox_id ? (
              <Tag color="success" icon={<CheckCircleOutlined />}>설정됨</Tag>
            ) : (
              <Tag color="default" icon={<ExclamationCircleOutlined />}>미설정</Tag>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <Input
              placeholder="아워박스 아이디"
              value={ourboxId}
              onChange={e => setOurboxId(e.target.value)}
              style={{ flex: 1 }}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <Input.Password
              placeholder="아워박스 비밀번호"
              value={ourboxPw}
              onChange={e => setOurboxPw(e.target.value)}
              style={{ flex: 1 }}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
            <Input.Password
              placeholder="API ACCESS KEY"
              value={ourboxAccessKey}
              onChange={e => setOurboxAccessKey(e.target.value)}
              style={{ flex: 1 }}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
            <Input.Password
              placeholder="API SECRET KEY"
              value={ourboxSecretKey}
              onChange={e => setOurboxSecretKey(e.target.value)}
              style={{ flex: 1 }}
            />
            <Button type="primary" icon={<LinkOutlined />} onClick={handleOurboxSave} loading={ourboxSaving}>저장</Button>
          </div>
          <div style={{ fontSize: '0.73rem', color: '#9ca3af', marginBottom: 4 }}>
            REST API 키: <a href="https://api.ourbox.co.kr/redoc.html" target="_blank" rel="noopener noreferrer">api.ourbox.co.kr</a> 참고.
            없으면 웹 ID/PW로 로그인 시도.
          </div>
          {config.ourbox_id && (
            <div style={{ fontSize: '0.78rem', color: '#6b7280' }}>저장된 아이디: <strong>{config.ourbox_id}</strong> (비밀번호 변경 시 재입력)</div>
          )}
          <div style={{ fontSize: '0.75rem', color: '#9ca3af', marginTop: 6, marginBottom: 10 }}>
            아워박스 로그인 정보 — 입고 정산기 및 재고 대사 동기화 시 사용됩니다
          </div>
          {config.ourbox_id && (
            <div>
              <Button
                size="small"
                icon={<LinkOutlined />}
                onClick={handleOurboxProbe}
                loading={probeLoading}
                style={{ marginBottom: 8 }}
              >
                REST API 탐색 (로그인 테스트)
              </Button>
              {probeResult && (
                <pre style={{ background: '#f9fafb', borderRadius: 8, padding: 10, fontSize: '0.72rem', overflow: 'auto', maxHeight: 200, marginTop: 4 }}>
                  {JSON.stringify(probeResult, null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>

        {/* AI 분석 API Key */}
        <div className="settings-section">
          <div className="settings-section-title">
            🤖 AI 분석 API Key (재고 대사)
          </div>
          <div style={{ fontSize: '0.73rem', color: '#6b7280', marginBottom: 10 }}>
            하나만 입력하면 됩니다. 우선순위: <strong>Gemini (무료)</strong> → Groq (무료) → Claude (유료)
          </div>

          {/* Gemini */}
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#374151', marginBottom: 4 }}>
              🟢 Google Gemini Flash — <span style={{ color: '#10b981', fontWeight: 700 }}>완전 무료</span>
              &nbsp;<a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.72rem' }}>발급하기 →</a>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Input.Password
                placeholder="AIza... (Google AI Studio에서 발급)"
                value={geminiApiKey}
                onChange={e => setGeminiApiKey(e.target.value)}
                style={{ flex: 1 }}
              />
            </div>
          </div>

          {/* Groq */}
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#374151', marginBottom: 4 }}>
              🟡 Groq (LLaMA 3) — <span style={{ color: '#10b981', fontWeight: 700 }}>무료 티어</span>
              &nbsp;<a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.72rem' }}>발급하기 →</a>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Input.Password
                placeholder="gsk_... (Groq Console에서 발급)"
                value={groqApiKey}
                onChange={e => setGroqApiKey(e.target.value)}
                style={{ flex: 1 }}
              />
            </div>
          </div>

          {/* Claude */}
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#374151', marginBottom: 4 }}>
              🔵 Claude (Anthropic) — 유료
              &nbsp;<a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.72rem' }}>발급하기 →</a>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Input.Password
                placeholder="sk-ant-api03-..."
                value={claudeApiKey}
                onChange={e => setClaudeApiKey(e.target.value)}
                style={{ flex: 1 }}
              />
            </div>
          </div>

          <Button
            type="primary"
            loading={claudeSaving}
            style={{ marginTop: 4 }}
            onClick={async () => {
              setClaudeSaving(true)
              await updateConfig({
                gemini_api_key: geminiApiKey.trim(),
                groq_api_key: groqApiKey.trim(),
                claude_api_key: claudeApiKey.trim(),
              })
              onConfigChange({
                gemini_api_key: geminiApiKey.trim(),
                groq_api_key: groqApiKey.trim(),
                claude_api_key: claudeApiKey.trim(),
              })
              setClaudeSaving(false)
            }}
          >
            저장
          </Button>
        </div>

        {/* 거래명세서 양식 경로 */}
        <div className="settings-section">
          <div className="settings-section-title">
            <FileTextOutlined style={{ marginRight: 8 }} />거래명세서 양식 파일
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input
              value={tplPath}
              onChange={e => setTplPath(e.target.value)}
              placeholder="C:\Users\...\거래명세서_양식.xlsx"
              style={{ flex: 1, fontFamily: 'monospace', fontSize: '0.8rem' }}
            />
            <Button loading={tplSaving} onClick={saveTemplatePath} type="primary">저장</Button>
          </div>
          {tplStatus && (
            <div style={{ marginTop: 6, fontSize: '0.78rem' }}>
              {tplStatus.exists
                ? <span style={{ color: '#10b981' }}>✓ 파일 확인됨: {tplStatus.path}</span>
                : <span style={{ color: '#ef4444' }}>✗ 파일 없음: {tplStatus.path}</span>}
            </div>
          )}
          <div style={{ fontSize: '0.75rem', color: '#9ca3af', marginTop: 4 }}>
            거래명세서 생성 시 사용할 엑셀 양식(.xlsx) 파일의 전체 경로를 입력하세요.
          </div>
        </div>

        {/* Info */}
        <Alert
          message="보안 안내"
          description="API 토큰과 인증 정보는 로컬 config.json에 저장됩니다. 타인과 공유하지 마세요."
          type="warning"
          showIcon
        />
      </div>
    </div>
  )
}
