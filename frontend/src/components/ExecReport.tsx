import { useState, useEffect } from 'react'
import { Button, Tag, Tooltip, Input, Popconfirm, message } from 'antd'
import { EditOutlined, CopyOutlined, ReloadOutlined, SaveOutlined, CloseOutlined, FileTextOutlined } from '@ant-design/icons'
import { getExecReport, saveExecReport, resetExecReport } from '../services/api'

interface Props {
  pageKey: string    // 저장 키 ('outbound' | 'inventory')
  autoText: string   // 현재 분석 결과 기준 자동 생성 본문 ('' = 아직 분석 전)
}

/** 경영진 요약 보고서 카드.
 *
 * - 분석 결과가 바뀔 때마다 autoText가 새로 생성돼 항상 최신 상태를 보여준다.
 * - '수정'으로 본문을 고치면 서버에 저장되고, 이후엔 수정본이 우선 표시된다.
 * - '자동 생성으로 되돌리기'로 수정본을 지우면 다시 자동 생성본을 따라간다.
 */
export default function ExecReport({ pageKey, autoText }: Props) {
  const [savedText, setSavedText] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    getExecReport(pageKey)
      .then(r => { setSavedText(r.text); setSavedAt(r.updated_at) })
      .catch(() => {})
  }, [pageKey])

  const manual = savedText != null
  const displayText = manual ? savedText! : autoText

  async function handleSave() {
    setSaving(true)
    try {
      const r = await saveExecReport(pageKey, draft)
      setSavedText(r.text)
      setSavedAt(r.updated_at)
      setEditing(false)
      message.success('보고서가 저장되었습니다')
    } catch {
      message.error('보고서 저장 실패')
    } finally {
      setSaving(false)
    }
  }

  async function handleReset() {
    try {
      await resetExecReport(pageKey)
      setSavedText(null)
      setSavedAt(null)
      setEditing(false)
      message.success('자동 생성 보고서로 되돌렸습니다')
    } catch {
      message.error('초기화 실패')
    }
  }

  function handleCopy() {
    navigator.clipboard.writeText(displayText)
      .then(() => message.success('보고서가 클립보드에 복사되었습니다'))
      .catch(() => message.error('복사 실패'))
  }

  // 본문 렌더: '■ ' 시작 줄 = 섹션 제목(볼드), '• ' = 불릿, 빈 줄 = 간격
  function renderBody(text: string) {
    return text.split('\n').map((line, i) => {
      if (!line.trim()) return <div key={i} style={{ height: 8 }} />
      if (line.startsWith('■')) {
        return (
          <div key={i} style={{ fontWeight: 800, fontSize: '0.86rem', color: '#111827', margin: '2px 0 3px' }}>
            {line}
          </div>
        )
      }
      return (
        <div key={i} style={{ fontSize: '0.82rem', color: '#374151', lineHeight: 1.65, paddingLeft: line.startsWith('•') || line.startsWith('-') ? 10 : 0 }}>
          {line}
        </div>
      )
    })
  }

  return (
    <div style={{
      background: 'linear-gradient(135deg, #fefce8 0%, #fff 45%)',
      borderRadius: 14, border: '1px solid #fde68a', padding: '14px 20px', marginBottom: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <FileTextOutlined style={{ color: '#d97706' }} />
        <span style={{ fontWeight: 800, fontSize: '0.92rem' }}>경영진 요약 보고서</span>
        {manual
          ? <Tooltip title={`수기 수정본 (저장: ${savedAt || '-'}) — 자동 갱신되지 않습니다`}>
              <Tag color="orange" style={{ fontSize: '0.66rem' }}>✎ 수기 수정됨</Tag>
            </Tooltip>
          : <Tooltip title="분석 결과가 바뀔 때마다 자동으로 다시 작성됩니다">
              <Tag color="green" style={{ fontSize: '0.66rem' }}>자동 생성</Tag>
            </Tooltip>}
        {manual && savedAt && <span style={{ fontSize: '0.7rem', color: '#9ca3af' }}>저장 {savedAt}</span>}

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {!editing && (
            <>
              <Button size="small" icon={<CopyOutlined />} onClick={handleCopy} disabled={!displayText}>복사</Button>
              <Button size="small" icon={<EditOutlined />} disabled={!displayText}
                onClick={() => { setDraft(displayText); setEditing(true); setCollapsed(false) }}>수정</Button>
              {manual && (
                <Popconfirm title="수기 수정본을 지우고 자동 생성 보고서로 되돌릴까요?" okText="되돌리기" cancelText="취소" onConfirm={handleReset}>
                  <Button size="small" icon={<ReloadOutlined />}>자동 생성으로</Button>
                </Popconfirm>
              )}
              <Button size="small" type="text" onClick={() => setCollapsed(c => !c)}>{collapsed ? '펼치기 ▾' : '접기 ▴'}</Button>
            </>
          )}
          {editing && (
            <>
              {autoText && (
                <Tooltip title="편집 중인 내용을 현재 분석 결과 기준 자동 생성본으로 채웁니다">
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => setDraft(autoText)}>자동 생성본 불러오기</Button>
                </Tooltip>
              )}
              <Button size="small" icon={<CloseOutlined />} onClick={() => setEditing(false)}>취소</Button>
              <Button size="small" type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>저장</Button>
            </>
          )}
        </div>
      </div>

      {!collapsed && (
        <div style={{ marginTop: 10 }}>
          {editing ? (
            <Input.TextArea
              value={draft}
              onChange={e => setDraft(e.target.value)}
              autoSize={{ minRows: 8, maxRows: 30 }}
              style={{ fontSize: '0.82rem', lineHeight: 1.65 }}
            />
          ) : displayText ? (
            renderBody(displayText)
          ) : (
            <div style={{ fontSize: '0.8rem', color: '#9ca3af' }}>
              조회/분석을 실행하면 보고서가 자동으로 작성됩니다.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
