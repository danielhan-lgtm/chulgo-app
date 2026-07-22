import { useState, useRef } from 'react'
import { Button, Tag, Alert, Spin, Collapse } from 'antd'
import {
  InboxOutlined, SearchOutlined, DeleteOutlined, CheckCircleOutlined,
  WarningOutlined, CloseCircleOutlined, FileTextOutlined, FolderOpenOutlined,
} from '@ant-design/icons'
import { reviewKurlyDocs } from '../services/api'
import { isDocFile, collectFromDataTransfer } from '../lib/fileDrop'

type Status = 'ok' | 'warn' | 'error'

interface Check { label: string; status: Status; detail: string }
interface KItem {
  code: string; name: string
  reqBox: number; stmtBox: number; labelCount: number
  reqTotal: number; stmtTotal: number; labelTotal: number
  stmtPerBox: number; labelPerBox: number
  status: Status; note: string
}
interface KGroup {
  orderCode: string; center: string; date: string; supplier: string
  status: Status
  present: string[]; missing: string[]
  checks: Check[]; items: KItem[]
  totals: { reqBox: number; stmtBox: number; labelCount: number; reqTotal: number; stmtTotal: number; labelTotal: number }
  hasReq: boolean
}
interface KResult {
  groups: KGroup[]
  summary: { total: number; ok: number; warn: number; error: number }
  files: { name: string; kind: string }[]
  parse_errors: string[]
}

const C: Record<Status, string> = { ok: '#10b981', warn: '#f59e0b', error: '#ef4444' }
const BG: Record<Status, string> = { ok: '#065f46', warn: '#78350f', error: '#7f1d1d' }
const LABEL: Record<Status, string> = { ok: '이상 없음', warn: '확인 필요', error: '불일치' }

function StatusIcon({ s }: { s: Status }) {
  if (s === 'ok') return <CheckCircleOutlined style={{ color: C.ok }} />
  if (s === 'warn') return <WarningOutlined style={{ color: C.warn }} />
  return <CloseCircleOutlined style={{ color: C.error }} />
}

export default function KurlyReview() {
  const [files, setFiles] = useState<File[]>([])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<KResult | null>(null)
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
    setResult(null); setError(null)
  }

  async function handleSubmit() {
    if (files.length === 0) { setError('서류를 업로드해주세요.'); return }
    setLoading(true); setResult(null); setError(null)
    try {
      const res = await reviewKurlyDocs(files)
      setResult(res)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '서류 검토 중 오류가 발생했습니다.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={async e => { e.preventDefault(); setDragOver(false); addFiles(await collectFromDataTransfer(e.dataTransfer)) }}
        style={{
          background: dragOver ? '#1a2436' : '#12151f',
          border: `2px dashed ${dragOver ? '#a855f7' : '#2a2d3e'}`,
          borderRadius: 12, padding: '36px 24px', textAlign: 'center',
          cursor: 'pointer', transition: 'all 0.15s', marginBottom: 16,
        }}
      >
        <input
          type="file" multiple style={{ display: 'none' }} ref={inputRef}
          accept=".pdf,.pptx,.ppt"
          onChange={e => { addFiles(e.target.files); if (inputRef.current) inputRef.current.value = '' }}
        />
        <input
          type="file" multiple style={{ display: 'none' }} ref={dirInputRef}
          {...({ webkitdirectory: '', directory: '' } as any)}
          onChange={e => { addFiles(e.target.files); if (dirInputRef.current) dirInputRef.current.value = '' }}
        />
        <InboxOutlined style={{ fontSize: '2.4rem', color: dragOver ? '#a855f7' : '#4b5563' }} />
        <div style={{ color: '#e5e7eb', fontSize: '0.95rem', fontWeight: 600, marginTop: 10 }}>
          폴더나 서류를 끌어다 놓거나 클릭하여 선택
        </div>
        <div style={{ color: '#4b5563', fontSize: '0.8rem', marginTop: 4 }}>
          거래명세서(PDF) + 입고 라벨지(PPTX) — 발주코드별로 자동 분류해 교차 검토
        </div>
        <div style={{ marginTop: 14 }}>
          <Button size="small" icon={<FolderOpenOutlined />}
            onClick={e => { e.stopPropagation(); dirInputRef.current?.click() }}
            style={{ background: '#1a1d27', border: '1px solid #2a2d3e', color: '#d1d5db' }}>
            폴더 선택
          </Button>
        </div>
      </div>

      {files.length > 0 && (
        <div style={{ marginBottom: 16, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {files.map((f, i) => (
            <Tag key={f.name + i} closable icon={<FileTextOutlined />}
              onClose={() => setFiles(prev => prev.filter((_, j) => j !== i))}
              style={{ padding: '4px 8px', fontSize: '0.75rem', background: '#1a1d27', color: '#d1d5db', border: '1px solid #2a2d3e' }}>
              {f.name}
            </Tag>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <Button type="primary" icon={<SearchOutlined />} size="large" loading={loading}
          disabled={files.length === 0} onClick={handleSubmit}
          style={{ fontWeight: 600, minWidth: 150, background: '#a855f7' }}>
          {loading ? '검토 중...' : '자동 분류 · 검토'}
        </Button>
        {files.length > 0 && !loading && <Tag color="purple">{files.length}개 파일</Tag>}
        {files.length > 0 && !loading && (
          <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#6b7280' }}
            onClick={() => { setFiles([]); setResult(null) }}>전체 비우기</Button>
        )}
      </div>

      {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} showIcon />}

      {loading && (
        <div style={{ background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 10, padding: 40, textAlign: 'center' }}>
          <Spin size="large" />
          <div style={{ color: '#6b7280', marginTop: 16, fontSize: '0.9rem' }}>거래명세서와 라벨지를 대조하고 있습니다...</div>
        </div>
      )}

      {result && !loading && <KurlyResultView result={result} />}
    </div>
  )
}

function KurlyResultView({ result }: { result: KResult }) {
  const { summary, groups, parse_errors } = result
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap' }}>
        <Pill label="발주" value={summary.total} color="#a855f7" />
        <Pill label="이상 없음" value={summary.ok} color={C.ok} />
        <Pill label="확인 필요" value={summary.warn} color={C.warn} />
        <Pill label="불일치" value={summary.error} color={C.error} />
      </div>

      {parse_errors.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message="일부 파일을 읽지 못했습니다"
          description={<ul style={{ margin: 0, paddingLeft: 18 }}>{parse_errors.map((e, i) => <li key={i}>{e}</li>)}</ul>} />
      )}

      <Collapse
        defaultActiveKey={groups.filter(g => g.status !== 'ok').map(g => g.orderCode)}
        style={{ background: 'transparent', border: 'none' }}
        items={groups.map(g => ({
          key: g.orderCode,
          style: { background: '#12151f', border: `1px solid ${g.status === 'ok' ? '#2a2d3e' : C[g.status]}`, borderRadius: 10, marginBottom: 10 },
          label: (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ background: BG[g.status], color: C[g.status], fontSize: '0.7rem', fontWeight: 700, padding: '2px 8px', borderRadius: 6 }}>{LABEL[g.status]}</span>
              <span style={{ color: '#fff', fontWeight: 700, fontSize: '0.95rem' }}>{g.orderCode}</span>
              {g.center && <span style={{ color: '#9ca3af', fontSize: '0.78rem' }}>{g.center}</span>}
              {g.date && <span style={{ color: '#6b7280', fontSize: '0.78rem' }}>입고 {g.date}</span>}
              <span style={{ color: '#4b5563', fontSize: '0.78rem' }}>· 라벨 {g.totals.labelCount}장 / 박스 {g.totals.stmtBox}</span>
              {g.hasReq && <span style={{ color: '#4b5563', fontSize: '0.74rem' }}>· 요청 {g.totals.reqBox}박스</span>}
              {g.missing.length > 0 && <span style={{ color: C.warn, fontSize: '0.74rem' }}>· 누락 {g.missing.join('·')}</span>}
            </div>
          ),
          children: <KurlyGroupDetail g={g} />,
        }))}
      />
    </div>
  )
}

function Pill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 8, padding: '8px 16px', minWidth: 88 }}>
      <div style={{ color: '#6b7280', fontSize: '0.72rem' }}>{label}</div>
      <div style={{ color, fontSize: '1.3rem', fontWeight: 700 }}>{value}</div>
    </div>
  )
}

function KurlyGroupDetail({ g }: { g: KGroup }) {
  const td: React.CSSProperties = { padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: 'center' }
  const hr = g.hasReq
  const triple = (req: number, stmt: number, label: number) => (hr ? `${req} / ${stmt} / ${label}` : `${stmt} / ${label}`)
  const boxHdr = hr ? '박스(요청/명세/라벨)' : '박스(명세/라벨)'
  const totHdr = hr ? '총수량(요청/명세/라벨)' : '총수량(명세/라벨)'
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 18px', marginBottom: 16 }}>
        {g.checks.map((c, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem' }}>
            <StatusIcon s={c.status} />
            <span style={{ color: '#9ca3af' }}>{c.label}</span>
            <span style={{ color: c.status === 'ok' ? '#d1d5db' : C[c.status], fontWeight: 600 }}>{c.detail}</span>
          </div>
        ))}
      </div>

      {g.items.length > 0 && (
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ background: '#1e2130' }}>
              {['상품코드', '상품명', boxHdr, totHdr, '입수(명세/라벨)', '비고', ''].map(h => (
                <th key={h} style={{ color: '#9ca3af', padding: '6px 10px', border: '1px solid #2a2d3e', textAlign: h === '상품명' || h === '비고' ? 'left' : 'center', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {g.items.map((it, idx) => {
              const boxOk = it.stmtBox === it.labelCount && (!hr || it.reqBox === it.stmtBox)
              const totOk = it.stmtTotal === it.labelTotal && (!hr || it.reqTotal === it.stmtTotal)
              return (
                <tr key={it.code || `req-${idx}`} style={{ background: it.status === 'ok' ? 'transparent' : BG[it.status] + '33' }}>
                  <td style={{ ...td, color: '#9ca3af' }}>{it.code || '-'}</td>
                  <td style={{ ...td, textAlign: 'left', color: '#d1d5db' }}>{it.name}</td>
                  <td style={{ ...td, color: boxOk ? '#d1d5db' : C.error, fontWeight: boxOk ? 400 : 700 }}>{triple(it.reqBox, it.stmtBox, it.labelCount)}</td>
                  <td style={{ ...td, color: totOk ? '#d1d5db' : C.error, fontWeight: totOk ? 400 : 700 }}>{triple(it.reqTotal, it.stmtTotal, it.labelTotal)}</td>
                  <td style={{ ...td, color: it.stmtPerBox === it.labelPerBox ? '#9ca3af' : C.warn }}>{it.stmtPerBox} / {it.labelPerBox}</td>
                  <td style={{ ...td, textAlign: 'left', color: it.note ? C[it.status] : '#4b5563', fontSize: '0.74rem' }}>{it.note || '-'}</td>
                  <td style={td}><StatusIcon s={it.status} /></td>
                </tr>
              )
            })}
            <tr style={{ background: '#1a1d27', fontWeight: 700 }}>
              <td colSpan={2} style={{ ...td, textAlign: 'right', color: '#9ca3af' }}>합계</td>
              <td style={{ ...td, color: g.totals.labelCount === g.totals.stmtBox ? '#d1d5db' : C.error }}>{triple(g.totals.reqBox, g.totals.stmtBox, g.totals.labelCount)}</td>
              <td style={{ ...td, color: '#d1d5db' }}>{triple(g.totals.reqTotal, g.totals.stmtTotal, g.totals.labelTotal)}</td>
              <td colSpan={3} style={{ border: '1px solid #2a2d3e' }} />
            </tr>
          </tbody>
        </table>
      )}
    </div>
  )
}
