import { useState, useRef } from 'react'
import { Button, Tag, Alert, Spin, message } from 'antd'
import {
  InboxOutlined, FileSearchOutlined, DeleteOutlined,
  FileTextOutlined, FolderOpenOutlined, CopyOutlined,
} from '@ant-design/icons'
import { coupangMailBreakdown, type MailBreakdownResult } from '../services/api'
import { isDocFile, collectFromDataTransfer } from '../lib/fileDrop'

export default function CoupangMailBreakdown() {
  const [files, setFiles] = useState<File[]>([])
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<MailBreakdownResult | null>(null)
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
    if (files.length === 0) { setError('거래명세서를 업로드해주세요.'); return }
    setLoading(true); setResult(null); setError(null)
    try {
      const res = await coupangMailBreakdown(files)
      setResult(res)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      const msg = err?.response?.data?.detail
      if (msg) {
        setError(msg)
      } else if (!err?.response) {
        setError(
          '업로드에 실패했습니다. 엑셀 등에서 파일을 열어둔 상태면 브라우저가 읽지 못합니다 — '
          + '열려 있는 파일을 닫고 다시 시도해주세요. '
          + `(${err?.message || '네트워크 오류'})`,
        )
      } else {
        setError('내역 생성 중 오류가 발생했습니다.')
      }
    } finally {
      setLoading(false)
    }
  }

  async function copyText() {
    if (!result) return
    try {
      // 메일에 붙여넣으면 센터명이 크고 굵게 나오도록 서식(HTML)째로 복사
      const html = buildMailHtml(result)
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([result.text], { type: 'text/plain' }),
        }),
      ])
      message.success('출고 수량 상세를 복사했습니다. (센터명 굵게 · 서식 포함)')
    } catch {
      // 서식 복사 미지원 브라우저 폴백: 일반 텍스트
      try {
        await navigator.clipboard.writeText(result.text)
        message.success('출고 수량 상세를 복사했습니다. (일반 텍스트)')
      } catch {
        message.error('복사에 실패했습니다. 직접 선택해 복사해주세요.')
      }
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
          border: `2px dashed ${dragOver ? '#3b82f6' : '#2a2d3e'}`,
          borderRadius: 12, padding: '36px 24px', textAlign: 'center',
          cursor: 'pointer', transition: 'all 0.15s', marginBottom: 16,
        }}
      >
        <input
          type="file" multiple style={{ display: 'none' }} ref={inputRef}
          accept=".pdf"
          onChange={e => { addFiles(e.target.files); if (inputRef.current) inputRef.current.value = '' }}
        />
        <input
          type="file" multiple style={{ display: 'none' }} ref={dirInputRef}
          {...({ webkitdirectory: '', directory: '' } as any)}
          onChange={e => { addFiles(e.target.files); if (dirInputRef.current) dirInputRef.current.value = '' }}
        />
        <InboxOutlined style={{ fontSize: '2.4rem', color: dragOver ? '#3b82f6' : '#4b5563' }} />
        <div style={{ color: '#e5e7eb', fontSize: '0.95rem', fontWeight: 600, marginTop: 10 }}>
          센터별 거래명세서(PDF)를 끌어다 놓거나 클릭하여 선택
        </div>
        <div style={{ color: '#4b5563', fontSize: '0.8rem', marginTop: 4 }}>
          확정수량·박스(입수 추정)·유통기한을 뽑아 센터별 [출고 수량 상세]를 만들어 드립니다 — 폴더째 드롭 가능
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
        <Button type="primary" icon={<FileSearchOutlined />} size="large" loading={loading}
          disabled={files.length === 0} onClick={handleSubmit}
          style={{ fontWeight: 600, minWidth: 150 }}>
          {loading ? '생성 중...' : '출고 수량 상세 생성'}
        </Button>
        {files.length > 0 && !loading && <Tag color="blue">{files.length}개 파일</Tag>}
        {files.length > 0 && !loading && (
          <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#6b7280' }}
            onClick={() => { setFiles([]); setResult(null) }}>전체 비우기</Button>
        )}
      </div>

      {error && <Alert type="error" message={error} style={{ marginBottom: 16 }} showIcon />}

      {loading && (
        <div style={{ background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 10, padding: 40, textAlign: 'center' }}>
          <Spin size="large" />
          <div style={{ color: '#6b7280', marginTop: 16, fontSize: '0.9rem' }}>거래명세서를 읽어 센터별 내역을 만들고 있습니다...</div>
        </div>
      )}

      {result && !loading && <MailResultView result={result} onCopy={copyText} />}
    </div>
  )
}

function escapeHtml(s: string) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 메일 붙여넣기용 HTML: 센터명은 크고 굵게, 품목은 일반 텍스트. */
function buildMailHtml(result: MailBreakdownResult): string {
  const parts: string[] = []
  parts.push('<div style="font-family:inherit;color:#000;">')
  parts.push('<p style="margin:0 0 12px 0;"><b>[출고 수량 상세]</b></p>')
  for (const g of result.groups) {
    parts.push(
      `<p style="margin:14px 0 4px 0;font-size:1.15em;"><b>쿠팡 ${escapeHtml(g.center)} 센터</b></p>`,
    )
    for (const it of g.items) {
      const expire = it.expire ? ` / 유통기한 ${it.expire} 이후` : ''
      parts.push(
        `<p style="margin:0 0 2px 0;">${escapeHtml(it.name)}: ${it.qty.toLocaleString()}개 (${it.box}박스)${escapeHtml(expire)}</p>`,
      )
    }
  }
  parts.push('</div>')
  return parts.join('')
}

function MailResultView({ result, onCopy }: { result: MailBreakdownResult; onCopy: () => void }) {
  const { summary, groups, parse_errors, files } = result
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap' }}>
        <Pill label="센터" value={summary.centers} color="#3b82f6" />
        <Pill label="총 수량" value={summary.totalQty} color="#10b981" suffix="개" />
        <Pill label="총 박스" value={summary.totalBox} color="#f59e0b" suffix="박스" />
      </div>

      {parse_errors.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message="일부 파일을 읽지 못했습니다"
          description={<ul style={{ margin: 0, paddingLeft: 18 }}>{parse_errors.map((e, i) => <li key={i}>{e}</li>)}</ul>} />
      )}

      {/* 복사용 미리보기 (메일에 붙여넣는 모양 그대로) */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <span style={{ color: '#9ca3af', fontSize: '0.85rem', fontWeight: 600 }}>
            메일에 붙여넣을 [출고 수량 상세] — 센터명 굵게 · 서식 포함 복사
          </span>
          <Button type="primary" size="small" icon={<CopyOutlined />} onClick={onCopy}>복사</Button>
        </div>
        <div style={{
          background: '#0d1017', border: '1px solid #2a2d3e', borderRadius: 10,
          padding: '16px 18px', maxHeight: 420, overflowY: 'auto',
          color: '#e5e7eb', fontSize: '0.85rem', lineHeight: 1.8,
        }}>
          <div style={{ fontWeight: 700, marginBottom: 12 }}>[출고 수량 상세]</div>
          {groups.map(g => (
            <div key={g.center} style={{ marginBottom: 14 }}>
              <div style={{ fontWeight: 800, fontSize: '1.05rem', color: '#fff', marginBottom: 4 }}>
                쿠팡 {g.center} 센터
              </div>
              {g.items.map((it, i) => (
                <div key={i} style={{ color: '#d1d5db' }}>
                  {it.name}: {it.qty.toLocaleString()}개 ({it.box}박스){it.expire ? ` / 유통기한 ${it.expire} 이후` : ''}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>

      {/* 센터별 카드 */}
      <div style={{ display: 'grid', gap: 10 }}>
        {groups.map(g => (
          <div key={g.center} style={{ background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 10, padding: '14px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
              <span style={{ color: '#fff', fontWeight: 700, fontSize: '0.95rem' }}>쿠팡 {g.center} 센터</span>
              <span style={{ color: '#4b5563', fontSize: '0.78rem' }}>
                {g.totalQty.toLocaleString()}개 / {g.totalBox}박스{g.date ? ` · 입고 ${g.date}` : ''}
              </span>
            </div>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.8rem' }}>
              <tbody>
                {g.items.map((it, i) => (
                  <tr key={i}>
                    <td style={{ padding: '4px 8px', color: '#d1d5db' }}>{it.name}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', color: '#e5e7eb', whiteSpace: 'nowrap' }}>{it.qty.toLocaleString()}개</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', color: '#9ca3af', whiteSpace: 'nowrap' }}>{it.box}박스</td>
                    <td style={{ padding: '4px 8px', color: it.expire ? '#6b7280' : '#ef4444', whiteSpace: 'nowrap' }}>
                      {it.expire ? `유통기한 ${it.expire} 이후` : '유통기한 미확인'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      {files.length > 0 && (
        <div style={{ marginTop: 16, color: '#4b5563', fontSize: '0.75rem' }}>
          읽은 파일: {files.map(f => `${f.name} → ${f.kind}`).join(' · ')}
        </div>
      )}
    </div>
  )
}

function Pill({ label, value, color, suffix }: { label: string; value: number; color: string; suffix?: string }) {
  return (
    <div style={{ background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 8, padding: '8px 16px', minWidth: 88 }}>
      <div style={{ color: '#6b7280', fontSize: '0.72rem' }}>{label}</div>
      <div style={{ color, fontSize: '1.3rem', fontWeight: 700 }}>{value.toLocaleString()}<span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{suffix || ''}</span></div>
    </div>
  )
}
