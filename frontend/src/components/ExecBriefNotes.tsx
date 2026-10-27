import { useEffect, useState } from 'react'
import { Button, Input, Tag, message } from 'antd'
import { SaveOutlined } from '@ant-design/icons'
import { saveExecBriefNotes } from '../services/api'
import type { ExecBrief } from '../services/api'

interface Props {
  brief: ExecBrief | null
  onSaved: () => void   // 저장 뒤 보고문 다시 집계
}

const CH_KEYS = ['coupang', 'oliveyoung', 'kurly', 'naver']
const EMOJI: Record<string, string> = { up: '📈', down: '📉', flat: '➡️' }

/** 경영진보고(짧은 양식)의 사용자 입력 — 제목·한줄정리·채널별 의견. 비워 두면 자동 문구가 들어간다. */
export default function ExecBriefNotes({ brief, onSaved }: Props) {
  const [title, setTitle] = useState('')
  const [headline, setHeadline] = useState('')
  const [ops, setOps] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!brief || dirty) return
    setTitle(brief.notes?.title || '')
    setHeadline(brief.notes?.headline || '')
    setOps({ ...(brief.notes?.opinions || {}) })
  }, [brief])  // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    setSaving(true)
    try {
      await saveExecBriefNotes({ title, headline, opinions: ops })
      setDirty(false)
      message.success('경영진보고 의견 저장')
      onSaved()
    } catch { message.error('저장 실패') } finally { setSaving(false) }
  }

  const chans = brief?.channels || []
  const byKey = Object.fromEntries(chans.map(c => [c.key, c]))
  const autoHeadline = brief ? brief.text.split('\n').filter(l => l.startsWith('• ') && brief.text.indexOf(l) < brief.text.indexOf('\n[')).join('\n') : ''

  return (
    <div className="panel">
      <h3>■ 경영진보고 의견 <span className="sub">비워 두면 집계 결과로 자동 문구를 넣습니다 · 저장하면 다음 집계부터 반영</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {brief?.notes?.updated_at && <span className="sub">저장 {brief.notes.updated_at}</span>}
          <Button size="small" type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={save}>저장</Button>
        </span>
      </h3>
      <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: '6px 10px', alignItems: 'start', fontSize: '0.82rem' }}>
        <div style={{ paddingTop: 4 }}>보고 제목</div>
        <Input size="small" value={title} placeholder="DJ&A(수입제품) 발주·납품 현황" onChange={e => { setTitle(e.target.value); setDirty(true) }} />

        <div style={{ paddingTop: 4 }}>한줄정리<div className="sub" style={{ fontSize: '0.7rem' }}>줄마다 한 항목</div></div>
        <Input.TextArea size="small" value={headline} autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder={autoHeadline.replace(/• /g, '') || '쿠팡·컬리 — 추석 전 입고분 소진 구간, 발주 감소 📉'}
          onChange={e => { setHeadline(e.target.value); setDirty(true) }} />

        {CH_KEYS.map(k => {
          const c = byKey[k]
          return (
            <div key={k} style={{ display: 'contents' }}>
              <div style={{ paddingTop: 4 }}>
                {c?.head || k} 의견
                {c && !c.error && <Tag style={{ marginLeft: 4, fontSize: '0.66rem' }}>{EMOJI[c.direction]} {c.metric} {c.direction === 'up' ? '증가' : c.direction === 'down' ? '감소' : '유지'}</Tag>}
              </div>
              <Input size="small" value={ops[k] || ''} placeholder={c?.auto_opinion || '자동 문구'}
                onChange={e => { setOps(o => ({ ...o, [k]: e.target.value })); setDirty(true) }} />
            </div>
          )
        })}
      </div>
      <div className="hint" style={{ marginTop: 6 }}>
        이달 누계는 1일~기준일, 비교는 지난달 같은 날짜까지입니다. 발주는 발주 캘린더 기준(쿠팡·컬리 납기일, 올리브영 발주일), 납품은 입고등록·입고확정 기준이며 금액은 공급가입니다.
        '납기 미납'은 아직 납기가 오지 않은 가장 가까운 발주(자투리 잔량 제외), 주요 품목은 발주 수량(네이버는 사은품을 뺀 실출고) 기준으로 이달 상위 3개와 끊긴 품목, 재고 일수는 채널 판매 추이의 재고 ÷ 일평균 판매입니다.
        한줄정리 자동 방향은 이달 발주 누계가 지난달 같은 기간 대비 ±10%를 넘는지로 정하고(네이버는 실출고), 재고 7일분 이하 품목이 있으면 '증가'로 봅니다.
      </div>
    </div>
  )
}
