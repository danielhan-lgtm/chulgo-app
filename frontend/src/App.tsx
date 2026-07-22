import { useState, useEffect } from 'react'
import { ConfigProvider, message } from 'antd'
import Sidebar from './components/Sidebar'
import Dashboard from './pages/Dashboard'
import GeneralConverter from './pages/GeneralConverter'
import NaverConverter from './pages/NaverConverter'
import SlackOrders from './pages/SlackOrders'
import GmailOrders from './pages/GmailOrders'
import OBOrders from './pages/OBOrders'
import Settings from './pages/Settings'
import Receiving from './pages/Receiving'
import DocReview from './pages/DocReview'
import InvoicePage from './pages/InvoicePage'
import ReconcilePage from './pages/ReconcilePage'
import ChannelPage from './pages/ChannelPage'
import MappingPage from './pages/MappingPage'
import OrderPlannerPage from './pages/OrderPlannerPage'
import DisposalReport from './pages/DisposalReport'
import ExpiryDonationReport from './pages/ExpiryDonationReport'
import CoupangLoadList from './pages/CoupangLoadList'
import CoupangGrowthLoad from './pages/CoupangGrowthLoad'
import KurlyLabel from './pages/KurlyLabel'
import OutboundForecast from './pages/OutboundForecast'
import type { Page, AppConfig, Location } from './types'
import { getConfig, getMasterStatus, getLocations, slackConnect, getGmailStatus } from './services/api'

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [config, setConfig] = useState<AppConfig>({})
  const [locations, setLocations] = useState<Location[]>([])
  const [channels, setChannels] = useState<Record<string, string>>({})
  const [masterLoaded, setMasterLoaded] = useState(false)
  const [gmailConnected, setGmailConnected] = useState(false)

  // 미매핑 패널 → 매핑 페이지 이동 이벤트 수신
  useEffect(() => {
    const handler = () => setPage('mapping')
    window.addEventListener('navigate-mapping', handler)
    return () => window.removeEventListener('navigate-mapping', handler)
  }, [])

  useEffect(() => {
    loadAppConfig()
    // Check for Gmail OAuth callback
    const params = new URLSearchParams(window.location.search)
    if (params.get('gmail_connected') === '1') {
      message.success('Gmail 연결 완료!')
      setGmailConnected(true)
      window.history.replaceState({}, '', '/')
    } else if (params.get('gmail_error')) {
      message.error('Gmail 연결 실패: ' + params.get('gmail_error'))
      window.history.replaceState({}, '', '/')
    }
  }, [])

  async function loadAppConfig() {
    try {
      const data = await getConfig()
      const cfg: AppConfig = data.config || {}
      setConfig(cfg)
      setMasterLoaded(data.master_loaded || data.has_master_default)

      // 저장된 토큰으로 자동 재연동
      if (cfg.api_token) {
        try {
          const locData = await getLocations(cfg.api_token)
          setLocations(locData.locations || [])
        } catch { /* 토큰 만료 등 무시 */ }
      }

      if (cfg.slack_token) {
        try {
          const slackData = await slackConnect(cfg.slack_token)
          setChannels(slackData.channels || {})
        } catch { /* 무시 */ }
      }

      // Gmail 연결 상태 확인
      try {
        const gmailData = await getGmailStatus()
        if (gmailData.connected) setGmailConnected(true)
      } catch { /* 무시 */ }

    } catch {
      // 백엔드 아직 미시작
    }
    try {
      const ms = await getMasterStatus()
      setMasterLoaded(ms.loaded)
    } catch { /* ignore */ }
  }

  const bhConnected = Boolean(config.api_token)
  const slackConnected = Boolean(config.slack_token)

  function handleConfigChange(updates: Partial<AppConfig>) {
    setConfig(prev => ({ ...prev, ...updates }))
  }

  function handleLocationsChange(locs: Location[]) {
    setLocations(locs)
  }

  function handleChannelsChange(chs: Record<string, string>) {
    setChannels(chs)
  }

  const pageProps = {
    config,
    locations,
    channels,
    onConfigChange: handleConfigChange,
    onLocationsChange: handleLocationsChange,
    onChannelsChange: handleChannelsChange,
    onMasterLoaded: () => setMasterLoaded(true),
    onGmailConnected: () => setGmailConnected(true),
    onNavigate: setPage,
  }

  function renderPage() {
    switch (page) {
      case 'dashboard': return <Dashboard config={config} />
      case 'general': return <GeneralConverter config={config} locations={locations} onNavigate={setPage} />
      case 'naver': return <NaverConverter config={config} locations={locations} onNavigate={setPage} />
      case 'slack': return <SlackOrders config={config} channels={channels} locations={locations} onNavigate={setPage} />
      case 'gmail': return <GmailOrders config={config} gmailConnected={gmailConnected} locations={locations} onNavigate={setPage} />
      case 'oborders': return <OBOrders />
      case 'receiving': return <Receiving config={config} />
      case 'docreview': return <DocReview />
      case 'invoice': return <InvoicePage />
      case 'reconcile': return <ReconcilePage config={config} />
      case 'channel': return <ChannelPage config={config} />
      case 'outbound': return <OutboundForecast config={config} />
      case 'mapping': return <MappingPage config={config} />
      case 'orderplan': return <OrderPlannerPage />
      case 'disposal': return <DisposalReport />
      case 'expiry': return <ExpiryDonationReport />
      case 'coupangload': return <CoupangLoadList />
      case 'coupanggrowthload': return <CoupangGrowthLoad />
      case 'kurlylabel': return <KurlyLabel />
      case 'settings': return (
        <Settings
          config={config}
          locations={locations}
          channels={channels}
          onConfigChange={handleConfigChange}
          onLocationsChange={handleLocationsChange}
          onChannelsChange={handleChannelsChange}
          onMasterLoaded={() => setMasterLoaded(true)}
          onGmailConnected={() => setGmailConnected(true)}
        />
      )
    }
  }

  return (
    <ConfigProvider theme={{
      token: {
        colorPrimary: '#10b981',
        borderRadius: 8,
        fontFamily: "Inter, 'Malgun Gothic', sans-serif",
      },
    }}>
      <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
        <Sidebar
          page={page}
          onPageChange={setPage}
          bhConnected={bhConnected}
          slackConnected={slackConnected}
          gmailConnected={gmailConnected}
          masterLoaded={masterLoaded}
        />
        <div className="page-content">
          {renderPage()}
        </div>
      </div>
    </ConfigProvider>
  )
}
