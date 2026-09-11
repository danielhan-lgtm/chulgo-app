import { Tooltip } from 'antd'

// 가로 막대 순위 차트 (단일 색조, 값 직접 라벨) — 재고분석·출고예측 대시보드 공용
export default function BarList({ rows, color, unit }: {
  rows: { label: string; value: number; sub?: string }[]
  color: string
  unit?: string
}) {
  const max = Math.max(...rows.map(r => r.value), 1)
  if (!rows.length) return <div style={{ fontSize: '0.78rem', color: '#9ca3af', padding: '12px 0' }}>표시할 데이터가 없습니다</div>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {rows.map(r => (
        <Tooltip key={r.label} title={`${r.label} — ${r.value.toLocaleString()}${unit || ''}${r.sub ? ` (${r.sub})` : ''}`}>
          <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr 76px', gap: 8, alignItems: 'center', cursor: 'default' }}>
            <span style={{ fontSize: '0.75rem', color: '#374151', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.label}</span>
            <div style={{ background: '#f3f4f6', borderRadius: 4, height: 14, overflow: 'hidden' }}>
              <div style={{ width: `${(r.value / max) * 100}%`, height: '100%', background: color, borderRadius: 4, minWidth: 2 }} />
            </div>
            <span style={{ fontSize: '0.75rem', color: '#111827', fontWeight: 600, textAlign: 'right' }}>
              {r.value.toLocaleString()}{unit || ''}{r.sub && <span style={{ color: '#9ca3af', fontWeight: 400 }}> {r.sub}</span>}
            </span>
          </div>
        </Tooltip>
      ))}
    </div>
  )
}
