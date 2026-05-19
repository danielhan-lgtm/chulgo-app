import { useState } from 'react'
import { Button, Upload, message, Tooltip } from 'antd'
import {
  DashboardOutlined, FileExcelOutlined, ShoppingOutlined,
  MessageOutlined, MailOutlined, SettingOutlined, InboxOutlined,
  ImportOutlined, AuditOutlined, FileTextOutlined,
} from '@ant-design/icons'
import type { Page } from '../types'
import { uploadMaster } from '../services/api'

interface Props {
  page: Page
  onPageChange: (p: Page) => void
  bhConnected: boolean
  slackConnected: boolean
  gmailConnected: boolean
  masterLoaded: boolean
}

const NAV_ITEMS: { key: Page; icon: React.ReactNode; label: string }[] = [
  { key: 'dashboard', icon: <DashboardOutlined />, label: '대시보드' },
  { key: 'general', icon: <FileExcelOutlined />, label: '일반 형식' },
  { key: 'naver', icon: <ShoppingOutlined />, label: '네이버 형식' },
  { key: 'slack', icon: <MessageOutlined />, label: 'Slack 출고요청' },
  { key: 'gmail', icon: <MailOutlined />, label: 'Gmail 출고요청' },
  { key: 'receiving', icon: <ImportOutlined />, label: '입고 정산기' },
  { key: 'docreview', icon: <AuditOutlined />, label: '서류 검토' },
  { key: 'invoice', icon: <FileTextOutlined />, label: '거래명세서' },
  { key: 'settings', icon: <SettingOutlined />, label: '설정' },
]

export default function Sidebar({ page, onPageChange, bhConnected, slackConnected, gmailConnected, masterLoaded }: Props) {
  const [uploading, setUploading] = useState(false)

  async function handleMasterUpload(file: File) {
    setUploading(true)
    try {
      const res = await uploadMaster(file)
      message.success(`마스터 파일 로드됨: ${res.rows}개 항목`)
    } catch {
      message.error('마스터 파일 업로드 실패')
    } finally {
      setUploading(false)
    }
    return false
  }

  return (
    <div style={{
      width: 220, minWidth: 220, background: '#0f1117',
      display: 'flex', flexDirection: 'column', height: '100vh',
      borderRight: '1px solid #1e2130', overflow: 'hidden',
    }}>
      {/* Logo */}
      <div style={{ padding: '20px 16px 16px', borderBottom: '1px solid #2e2f45' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            background: '#e84c4c', borderRadius: 8, width: 34, height: 34,
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.1rem', flexShrink: 0,
          }}>📦</div>
          <div>
            <div style={{ color: '#fff', fontWeight: 800, fontSize: '0.95rem', letterSpacing: '-0.3px', lineHeight: 1.2 }}>출고 라몬</div>
            <div style={{ color: '#6b6e8a', fontSize: '0.68rem' }}>박스히어로 출고 변환기</div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <div style={{ padding: '12px 8px', flex: 1, overflow: 'auto' }}>
        <div style={{ color: '#6b6e8a', fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6, paddingLeft: 8 }}>메뉴</div>
        {NAV_ITEMS.map(item => (
          <button
            key={item.key}
            onClick={() => onPageChange(item.key)}
            style={{
              display: 'flex', alignItems: 'center', gap: 10, width: '100%',
              padding: '9px 12px', borderRadius: 8, border: 'none', cursor: 'pointer',
              background: page === item.key ? '#1a1d27' : 'transparent',
              color: page === item.key ? '#10b981' : '#9ca3af',
              fontSize: '0.85rem', fontWeight: page === item.key ? 600 : 400,
              marginBottom: 2, transition: 'all 0.15s', textAlign: 'left',
            }}
          >
            <span style={{ fontSize: '0.9rem' }}>{item.icon}</span>
            {item.label}
          </button>
        ))}

        <div style={{ borderTop: '1px solid #2e2f45', margin: '12px 0' }} />

        {/* Status indicators */}
        <div style={{ color: '#6b6e8a', fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8, paddingLeft: 8 }}>연동 상태</div>
        {[
          { label: '박스히어로', ok: bhConnected },
          { label: 'Slack', ok: slackConnected },
          { label: 'Gmail', ok: gmailConnected },
          { label: '마스터 파일', ok: masterLoaded },
        ].map(({ label, ok }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 12px', fontSize: '0.78rem', color: ok ? '#10b981' : '#6b7280' }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
              background: ok ? '#10b981' : '#4b5563',
              boxShadow: ok ? '0 0 0 2px rgba(16,185,129,0.2)' : 'none',
            }} />
            {label}
          </div>
        ))}

        <div style={{ borderTop: '1px solid #2e2f45', margin: '12px 0' }} />

        {/* Master file upload */}
        <div style={{ padding: '0 8px' }}>
          <div style={{ color: '#6b6e8a', fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>마스터 파일</div>
          <Upload
            accept=".xlsx,.xls"
            showUploadList={false}
            beforeUpload={handleMasterUpload}
          >
            <Button
              loading={uploading}
              size="small"
              icon={<InboxOutlined />}
              style={{ width: '100%', fontSize: '0.78rem', background: '#1a1d27', border: '1px solid #2a2d3e', color: '#d1d5db' }}
            >
              {masterLoaded ? '파일 교체' : '파일 업로드'}
            </Button>
          </Upload>
        </div>
      </div>

      {/* Footer */}
      <div style={{ padding: '12px 16px', borderTop: '1px solid #2e2f45' }}>
        <div style={{ color: '#4b5563', fontSize: '0.7rem' }}>⚠️ 토큰은 비밀번호입니다</div>
      </div>
    </div>
  )
}
