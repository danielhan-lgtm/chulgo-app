import { useState, useRef } from 'react'
import { Button, Tabs, Tag, Alert, Spin, Collapse } from 'antd'
import {
  InboxOutlined, SearchOutlined, DeleteOutlined, CheckCircleOutlined,
  WarningOutlined, CloseCircleOutlined, FileTextOutlined, FolderOpenOutlined,
} from '@ant-design/icons'
import { reviewCoupangDocs } from '../services/api'
import { isDocFile, collectFromDataTransfer } from '../lib/fileDrop'
import KurlyReview from './KurlyReview'
import CoupangMailBreakdown from './CoupangMailBreakdown'

type Status = 'ok' | 'warn' | 'error'

interface Check { label: string; status: Status; detail: string }
interface Item {
  sku: string; name: string
  req: number; load: number; stmt: number; box: number; status: Status
}
interface Group {
  center: string
  milkrun: string | null
  date: string | null
  status: Status
  present: string[]
  missing: string[]
  checks: Check[]
  items: Item[]
  totals: { req: number; load: number; stmt: number; box_req: number; box_load: number }
}
interface ReviewResult {
  groups: Group[]
  summary: { total: number; ok: number; warn: number; error: number }
  files: { name: string; kind: string }[]
  parse_errors: string[]
}

const STATUS_COLOR: Record<Status, string> = { ok: '#10b981', warn: '#f59e0b', error: '#ef4444' }
const STATUS_BG: Record<Status, string> = { ok: '#065f46', warn: '#78350f', error: '#7f1d1d' }
const STATUS_LABEL: Record<Status, string> = { ok: '이상 없음', warn: '확인 필요', error: '불일치' }

function StatusIcon({ s }: { s: Status }) {
  if (s === 'ok') return <CheckCircleOutlined style={{ color: STATUS_COLOR.ok }} />
  if (s === 'warn') return <WarningOutlined style={{ color: STATUS_COLOR.warn }} />
  return <CloseCircleOutlined style={{ color: STATUS_COLOR.error }} />
}

export default function DocReview() {
  const [files, setFiles] = useState<File[]>([])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ReviewResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const dirInputRef = useRef<HTMLInputElement | null>(null)

  function addFiles(list: FileList | File[] | null) {
    if (!list) return
    const incoming = Array.from(list).filter(f => isDocFile(f.name))
    if (incoming.length === 0) return
    setFiles(prev => {
      const seen = new Set(prev.map(f => f.name + f.size))
      return [...prev, ...incoming.filter(f => !seen.has(f.name + f.size))]
    })
    setResult(null)
    setError(null)
  }

  function removeFile(idx: number) {
    setFiles(prev => prev.filter((_, i) => i !== idx))
  }

  async function handleSubmit() {
    if (files.length === 0) { setError('서류를 업로드해주세요.'); return }
    setLoading(true); setResult(null); setError(null)
    try {
      const res = await reviewCoupangDocs(files)
      setResult(res)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      const msg = err?.response?.data?.detail
      if (msg) {
        setError(msg)
      } else if (!err?.response) {
        // 서버 응답 자체가 없음 = 업로드 중 파일 읽기 실패(다른 프로그램이 잠금) 또는 서버 연결 실패
        setError(
          '업로드에 실패했습니다. 엑셀 등에서 파일을 열어둔 상태면 브라우저가 읽지 못합니다 — '
          + '열려 있는 파일(특히 출고요청 엑셀)을 닫고 다시 시도해주세요. '
          + `(${err?.message || '네트워크 오류'})`,
        )
      } else {
        setError('서류 검토 중 오류가 발생했습니다.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: '28px 32px', maxWidth: 980, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ color: '#fff', fontWeight: 700, fontSize: '1.3rem', margin: 0 }}>서류 검토</h2>
        <p style={{ color: '#6b7280', marginTop: 4, fontSize: '0.85rem' }}>
          센터 구분 없이 서류를 한꺼번에 올리면 자동으로 센터별로 분류해 교차 검토합니다. (API 미사용 · 무료)
        </p>
      </div>

      <Tabs
        defaultActiveKey="coupang"
        items={[
          {
            key: 'coupang',
            label: '쿠팡 (밀크런)',
            children: (
              <div>
                {/* 드롭존 */}
                <div
                  onClick={() => inputRef.current?.click()}
                  onDragOver={e => { e.preventDefault(); setDragOver(true) }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={async e => {
                    e.preventDefault(); setDragOver(false)
                    const fs = await collectFromDataTransfer(e.dataTransfer)
                    addFiles(fs)
                  }}
                  style={{
                    background: dragOver ? '#1a2436' : '#12151f',
                    border: `2px dashed ${dragOver ? '#3b82f6' : '#2a2d3e'}`,
                    borderRadius: 12, padding: '36px 24px', textAlign: 'center',
                    cursor: 'pointer', transition: 'all 0.15s', marginBottom: 16,
                  }}
                >
                  <input
                    type="file" multiple style={{ display: 'none' }} ref={inputRef}
                    accept=".pdf,.pptx,.ppt,.xlsx,.xls"
                    onChange={e => { addFiles(e.target.files); if (inputRef.current) inputRef.current.value = '' }}
                  />
                  {/* 폴더 통째로 선택 (하위 폴더까지 재귀) */}
                  <input
                    type="file" multiple style={{ display: 'none' }} ref={dirInputRef}
                    {...({ webkitdirectory: '', directory: '' } as any)}
                    onChange={e => { addFiles(e.target.files); if (dirInputRef.current) dirInputRef.current.value = '' }}
                  />
                  <InboxOutlined style={{ fontSize: '2.4rem', color: dragOver ? '#3b82f6' : '#4b5563' }} />
                  <div style={{ color: '#e5e7eb', fontSize: '0.95rem', fontWeight: 600, marginTop: 10 }}>
                    폴더나 여러 서류를 여기에 끌어다 놓거나 클릭하여 선택
                  </div>
                  <div style={{ color: '#4b5563', fontSize: '0.8rem', marginTop: 4 }}>
                    거래명세서·부착리스트(PDF), 적재리스트(PPTX), 출고요청·밀크런 접수내역(Excel) — 폴더째 드롭하면 하위 파일까지 자동 수집
                  </div>
                  <div style={{ marginTop: 14 }}>
                    <Button
                      size="small"
                      icon={<FolderOpenOutlined />}
                      onClick={e => { e.stopPropagation(); dirInputRef.current?.click() }}
                      style={{ background: '#1a1d27', border: '1px solid #2a2d3e', color: '#d1d5db' }}
                    >
                      폴더 선택
                    </Button>
                  </div>
                </div>

                {/* 업로드 목록 */}
                {files.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                      {files.map((f, i) => (
                        <Tag
                          key={f.name + i} closable onClose={() => removeFile(i)}
                          icon={<FileTextOutlined />}
                          style={{ padding: '4px 8px', fontSize: '0.75rem', background: '#1a1d27', color: '#d1d5db', border: '1px solid #2a2d3e' }}
                        >
                          {f.name}
                        </Tag>
                      ))}
                    </div>
                  </div>
                )}

                {/* 실행 */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
                  <Button
                    type="primary" icon={<SearchOutlined />} size="large"
                    loading={loading} disabled={files.length === 0} onClick={handleSubmit}
                    style={{ fontWeight: 600, minWidth: 150 }}
                  >
                    {loading ? '검토 중...' : '자동 분류 · 검토'}
                  </Button>
                  {files.length > 0 && !loading && (
                    <Tag color="blue">{files.length}개 파일</Tag>
                  )}
                  {files.length > 0 && !loading && (
                    <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#6b7280' }}
                      onClick={() => { setFiles([]); setResult(null) }}>전체 비우기</Button>
                  )}
                </div>

                {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} showIcon />}

                {loading && (
                  <div style={{ background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 10, padding: 40, textAlign: 'center' }}>
                    <Spin size="large" />
                    <div style={{ color: '#6b7280', marginTop: 16, fontSize: '0.9rem' }}>서류를 분류하고 교차 검토하고 있습니다...</div>
                  </div>
                )}

                {result && !loading && <ResultView result={result} />}
              </div>
            ),
          },
          {
            key: 'kurly',
            label: '마켓컬리',
            children: <KurlyReview />,
          },
          {
            key: 'coupang-mail',
            label: '쿠팡 출고요청 (메일용)',
            children: <CoupangMailBreakdown />,
          },
        ]}
        style={{ color: '#e5e7eb' }}
      />
    </div>
  )
}

function ResultView({ result }: { result: ReviewResult }) {
  const { summary, groups, parse_errors } = result
  return (
    <div>
      {/* 요약 */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap' }}>
        <SummaryPill label="센터" value={summary.total} color="#3b82f6" />
        <SummaryPill label="이상 없음" value={summary.ok} color={STATUS_COLOR.ok} />
        <SummaryPill label="확인 필요" value={summary.warn} color={STATUS_COLOR.warn} />
        <SummaryPill label="불일치" value={summary.error} color={STATUS_COLOR.error} />
      </div>

      {parse_errors.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message="일부 파일을 읽지 못했습니다"
          description={<ul style={{ margin: 0, paddingLeft: 18 }}>{parse_errors.map((e, i) => <li key={i}>{e}</li>)}</ul>} />
      )}

      <Collapse
        defaultActiveKey={groups.filter(g => g.status !== 'ok').map(g => g.center)}
        style={{ background: 'transparent', border: 'none' }}
        items={groups.map(g => ({
          key: g.center,
          style: { background: '#12151f', border: `1px solid ${g.status === 'ok' ? '#2a2d3e' : STATUS_COLOR[g.status]}`, borderRadius: 10, marginBottom: 10 },
          label: (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{
                background: STATUS_BG[g.status], color: STATUS_COLOR[g.status],
                fontSize: '0.7rem', fontWeight: 700, padding: '2px 8px', borderRadius: 6,
              }}>{STATUS_LABEL[g.status]}</span>
              <span style={{ color: '#fff', fontWeight: 700, fontSize: '0.95rem' }}>{g.center}</span>
              {g.milkrun && <span style={{ color: '#6b7280', fontSize: '0.78rem' }}>밀크런 {g.milkrun}</span>}
              <span style={{ color: '#4b5563', fontSize: '0.78rem' }}>
                · {g.totals.load.toLocaleString()}ea / {g.totals.box_load}box
              </span>
              {g.missing.length > 0 && (
                <span style={{ color: STATUS_COLOR.warn, fontSize: '0.74rem' }}>· 누락 {g.missing.join('·')}</span>
              )}
            </div>
          ),
          children: <GroupDetail g={g} />,
        }))}
      />
    </div>
  )
}

function SummaryPill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 8, padding: '8px 16px', minWidth: 88 }}>
      <div style={{ color: '#6b7280', fontSize: '0.72rem' }}>{label}</div>
      <div style={{ color, fontSize: '1.3rem', fontWeight: 700 }}>{value}</div>
    </div>
  )
}

function GroupDetail({ g }: { g: Group }) {
  return (
    <div>
      {/* 체크리스트 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 18px', marginBottom: 16 }}>
        {g.checks.map((c, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem' }}>
            <StatusIcon s={c.status} />
            <span style={{ color: '#9ca3af' }}>{c.label}</span>
            <span style={{ color: c.status === 'ok' ? '#d1d5db' : STATUS_COLOR[c.status], fontWeight: 600 }}>{c.detail}</span>
          </div>
        ))}
      </div>

      {/* SKU 표 */}
      {g.items.length > 0 && (
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ background: '#1e2130' }}>
              {['상품번호', '상품명', '요청', '적재', '명세(확정)', 'BOX', ''].map(h => (
                <th key={h} style={{ color: '#9ca3af', padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: h === '상품명' ? 'left' : 'center', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {g.items.map(it => (
              <tr key={it.sku} style={{ background: it.status === 'ok' ? 'transparent' : STATUS_BG[it.status] + '33' }}>
                <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: '#9ca3af' }}>{it.sku}</td>
                <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', color: '#d1d5db' }}>{it.name}</td>
                <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: it.req ? '#d1d5db' : '#4b5563' }}>{it.req || '-'}</td>
                <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: '#d1d5db' }}>{it.load}</td>
                <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: it.load === it.stmt ? '#d1d5db' : STATUS_COLOR.error, fontWeight: it.load === it.stmt ? 400 : 700 }}>{it.stmt}</td>
                <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: '#9ca3af' }}>{it.box}</td>
                <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center' }}><StatusIcon s={it.status} /></td>
              </tr>
            ))}
            <tr style={{ background: '#1a1d27', fontWeight: 700 }}>
              <td colSpan={2} style={{ padding: '6px 10px', border: '1px solid #2a2d3e', color: '#9ca3af', textAlign: 'right' }}>합계</td>
              <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: '#d1d5db' }}>{g.totals.req || '-'}</td>
              <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: '#d1d5db' }}>{g.totals.load}</td>
              <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: '#d1d5db' }}>{g.totals.stmt}</td>
              <td style={{ padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center', color: '#9ca3af' }}>{g.totals.box_load}</td>
              <td style={{ border: '1px solid #2a2d3e' }} />
            </tr>
          </tbody>
        </table>
      )}
    </div>
  )
}
