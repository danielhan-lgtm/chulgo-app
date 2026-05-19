import { useState, useRef } from 'react'
import { Button, Tabs, Tag, Alert, Spin } from 'antd'
import {
  FilePdfOutlined, FileImageOutlined, FileExcelOutlined,
  FilePptOutlined, SearchOutlined, DeleteOutlined, CheckCircleOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import { reviewCoupangDocs } from '../services/api'

interface FileSlot {
  key: keyof CoupangFiles
  label: string
  accept: string
  icon: React.ReactNode
  hint: string
}

interface CoupangFiles {
  거래명세서: File | null
  부착리스트: File | null
  적재리스트: File | null
  밀크런등록내역: File | null
  출고내역: File | null
}

const COUPANG_SLOTS: FileSlot[] = [
  { key: '거래명세서',  label: '거래명세서',     accept: '.pdf',              icon: <FilePdfOutlined />,   hint: 'PDF' },
  { key: '부착리스트',  label: '부착리스트',     accept: '.pdf',              icon: <FilePdfOutlined />,   hint: 'PDF' },
  { key: '적재리스트',  label: '적재리스트',     accept: '.ppt,.pptx',        icon: <FilePptOutlined />,   hint: 'PPT / PPTX' },
  { key: '밀크런등록내역', label: '밀크런 등록내역', accept: '.jpg,.jpeg,.png',  icon: <FileImageOutlined />, hint: 'JPG / PNG' },
  { key: '출고내역',    label: '출고내역',       accept: '.xlsx,.xls',        icon: <FileExcelOutlined />, hint: 'Excel' },
]

export default function DocReview() {
  const [files, setFiles] = useState<CoupangFiles>({
    거래명세서: null, 부착리스트: null, 적재리스트: null, 밀크런등록내역: null, 출고내역: null,
  })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [uploadedFiles, setUploadedFiles] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({})

  function handleFileChange(key: keyof CoupangFiles, e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null
    setFiles(prev => ({ ...prev, [key]: f }))
    setResult(null)
    setError(null)
  }

  function removeFile(key: keyof CoupangFiles) {
    setFiles(prev => ({ ...prev, [key]: null }))
    if (inputRefs.current[key]) inputRefs.current[key]!.value = ''
  }

  async function handleSubmit() {
    const hasAny = Object.values(files).some(Boolean)
    if (!hasAny) { setError('최소 하나의 파일을 업로드해주세요.'); return }
    setLoading(true)
    setResult(null)
    setError(null)
    try {
      const res = await reviewCoupangDocs(files)
      setResult(res.result)
      setUploadedFiles(res.uploaded_files || [])
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || '서류 검토 중 오류가 발생했습니다.')
    } finally {
      setLoading(false)
    }
  }

  const fileCount = Object.values(files).filter(Boolean).length

  return (
    <div style={{ padding: '28px 32px', maxWidth: 900, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ color: '#fff', fontWeight: 700, fontSize: '1.3rem', margin: 0 }}>서류 검토</h2>
        <p style={{ color: '#6b7280', marginTop: 4, fontSize: '0.85rem' }}>
          여러 서류를 업로드하면 AI가 교차 검토하여 불일치를 찾아드립니다.
        </p>
      </div>

      <Tabs
        defaultActiveKey="coupang"
        items={[
          {
            key: 'coupang',
            label: '쿠팡',
            children: (
              <div>
                {/* File upload grid */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
                  {COUPANG_SLOTS.map(slot => (
                    <div
                      key={slot.key}
                      style={{
                        background: '#12151f',
                        border: `1px solid ${files[slot.key] ? '#10b981' : '#2a2d3e'}`,
                        borderRadius: 10,
                        padding: '14px 16px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 12,
                        cursor: 'pointer',
                        transition: 'border-color 0.2s',
                      }}
                      onClick={() => inputRefs.current[slot.key]?.click()}
                    >
                      <input
                        type="file"
                        accept={slot.accept}
                        style={{ display: 'none' }}
                        ref={el => { inputRefs.current[slot.key] = el }}
                        onChange={e => handleFileChange(slot.key, e)}
                      />
                      <div style={{
                        width: 38, height: 38, borderRadius: 8, flexShrink: 0,
                        background: files[slot.key] ? '#065f46' : '#1a1d27',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        color: files[slot.key] ? '#10b981' : '#6b7280',
                        fontSize: '1.1rem',
                      }}>
                        {slot.icon}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ color: '#e5e7eb', fontSize: '0.85rem', fontWeight: 600 }}>{slot.label}</div>
                        {files[slot.key] ? (
                          <div style={{ color: '#10b981', fontSize: '0.75rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {files[slot.key]!.name}
                          </div>
                        ) : (
                          <div style={{ color: '#4b5563', fontSize: '0.75rem' }}>{slot.hint} 클릭하여 선택</div>
                        )}
                      </div>
                      {files[slot.key] && (
                        <Button
                          type="text"
                          size="small"
                          icon={<DeleteOutlined />}
                          style={{ color: '#6b7280', flexShrink: 0 }}
                          onClick={e => { e.stopPropagation(); removeFile(slot.key) }}
                        />
                      )}
                    </div>
                  ))}
                </div>

                {/* Submit */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
                  <Button
                    type="primary"
                    icon={<SearchOutlined />}
                    size="large"
                    loading={loading}
                    disabled={fileCount === 0}
                    onClick={handleSubmit}
                    style={{ fontWeight: 600, minWidth: 140 }}
                  >
                    {loading ? '검토 중...' : '서류 검토 시작'}
                  </Button>
                  {fileCount > 0 && !loading && (
                    <Tag color="green" icon={<CheckCircleOutlined />}>
                      {fileCount}개 파일 준비됨
                    </Tag>
                  )}
                </div>

                {/* Error */}
                {error && (
                  <Alert type="error" message={error} style={{ marginBottom: 16 }} showIcon />
                )}

                {/* Loading */}
                {loading && (
                  <div style={{
                    background: '#12151f', border: '1px solid #2a2d3e', borderRadius: 10,
                    padding: 40, textAlign: 'center',
                  }}>
                    <Spin size="large" />
                    <div style={{ color: '#6b7280', marginTop: 16, fontSize: '0.9rem' }}>
                      AI가 서류를 교차 검토하고 있습니다...
                    </div>
                    <div style={{ color: '#4b5563', marginTop: 6, fontSize: '0.8rem' }}>
                      파일 수에 따라 30초~1분 소요될 수 있습니다.
                    </div>
                  </div>
                )}

                {/* Result */}
                {result && !loading && (
                  <div style={{
                    background: '#12151f',
                    border: '1px solid #2a2d3e',
                    borderRadius: 10,
                    padding: '20px 24px',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
                      <div style={{ color: '#10b981', fontWeight: 700, fontSize: '0.9rem' }}>검토 완료</div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {uploadedFiles.map(f => (
                          <Tag key={f} color="blue" style={{ fontSize: '0.72rem' }}>{f}</Tag>
                        ))}
                      </div>
                    </div>
                    <div style={{ color: '#d1d5db', lineHeight: 1.8, fontSize: '0.88rem' }}>
                      <ReactMarkdown
                        components={{
                          h1: ({ children }) => <h1 style={{ color: '#fff', fontSize: '1.1rem', marginTop: 20 }}>{children}</h1>,
                          h2: ({ children }) => <h2 style={{ color: '#e5e7eb', fontSize: '1rem', marginTop: 16 }}>{children}</h2>,
                          h3: ({ children }) => <h3 style={{ color: '#d1d5db', fontSize: '0.9rem', marginTop: 12 }}>{children}</h3>,
                          strong: ({ children }) => <strong style={{ color: '#fff' }}>{children}</strong>,
                          table: ({ children }) => (
                            <table style={{ borderCollapse: 'collapse', width: '100%', marginTop: 10, marginBottom: 10 }}>
                              {children}
                            </table>
                          ),
                          th: ({ children }) => (
                            <th style={{ background: '#1e2130', color: '#9ca3af', padding: '6px 12px', border: '1px solid #2a2d3e', textAlign: 'left', fontSize: '0.8rem' }}>
                              {children}
                            </th>
                          ),
                          td: ({ children }) => (
                            <td style={{ padding: '6px 12px', border: '1px solid #2a2d3e', fontSize: '0.82rem' }}>
                              {children}
                            </td>
                          ),
                          hr: () => <hr style={{ border: 'none', borderTop: '1px solid #2a2d3e', margin: '16px 0' }} />,
                          p: ({ children }) => <p style={{ marginBottom: 8 }}>{children}</p>,
                          li: ({ children }) => <li style={{ marginBottom: 4 }}>{children}</li>,
                        }}
                      >
                        {result}
                      </ReactMarkdown>
                    </div>
                  </div>
                )}
              </div>
            ),
          },
          {
            key: 'kurly',
            label: '마켓컬리',
            children: (
              <div style={{
                background: '#12151f', border: '1px dashed #2a2d3e', borderRadius: 10,
                padding: 48, textAlign: 'center',
              }}>
                <div style={{ fontSize: '2rem', marginBottom: 12 }}>🚧</div>
                <div style={{ color: '#6b7280', fontSize: '0.9rem' }}>마켓컬리 서류 검토는 준비 중입니다.</div>
              </div>
            ),
          },
        ]}
        style={{ color: '#e5e7eb' }}
      />
    </div>
  )
}
