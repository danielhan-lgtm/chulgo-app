interface Point { label: string; value: number }

interface Props {
  actual: Point[]        // 실측 구간 (시간순)
  forecast: Point[]      // 예측 연장 구간 (시간순, 실측 뒤에 붙음)
  height?: number
  unitLabel?: string     // y축/툴팁 단위 표기 (예: '개')
}

/** 의존성 없는 SVG 추세선 차트. 실측=실선(초록), 예측=점선(파랑). */
export default function TrendChart({ actual, forecast, height = 240, unitLabel = '' }: Props) {
  const W = 820
  const H = height
  const padL = 48, padR = 16, padT = 16, padB = 40
  const plotW = W - padL - padR
  const plotH = H - padT - padB

  const all = [...actual, ...forecast]
  if (all.length === 0) {
    return <div style={{ color: '#9ca3af', fontSize: '0.85rem', padding: 20 }}>표시할 데이터가 없습니다.</div>
  }

  const maxV = Math.max(1, ...all.map(p => p.value))
  // y축 눈금 (4단계 round)
  const niceMax = niceCeil(maxV)
  const n = all.length
  const stepX = n > 1 ? plotW / (n - 1) : 0

  const x = (i: number) => padL + stepX * i
  const y = (v: number) => padT + plotH - (v / niceMax) * plotH

  const actualPts = actual.map((p, i) => [x(i), y(p.value)] as const)
  // 예측 선은 마지막 실측점에서 시작해 자연스럽게 이어짐
  const fcStartIdx = actual.length - 1
  const forecastPts = forecast.map((p, k) => [x(fcStartIdx + 1 + k), y(p.value)] as const)
  const forecastLine = fcStartIdx >= 0
    ? [actualPts[fcStartIdx], ...forecastPts]
    : forecastPts

  const toPath = (pts: readonly (readonly [number, number])[]) =>
    pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')

  // x축 라벨: 최대 ~9개만 표기 (밀집 방지)
  const labelEvery = Math.max(1, Math.ceil(n / 9))
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => f * niceMax)

  return (
    <div style={{ width: '100%', overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ minWidth: 520, display: 'block' }}>
        {/* y축 그리드 + 라벨 */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={padL} y1={y(t)} x2={W - padR} y2={y(t)} stroke="#eef0f4" strokeWidth={1} />
            <text x={padL - 8} y={y(t) + 3} textAnchor="end" fontSize={10} fill="#9ca3af">
              {Math.round(t).toLocaleString()}
            </text>
          </g>
        ))}

        {/* 실측/예측 경계 */}
        {forecast.length > 0 && fcStartIdx >= 0 && (
          <>
            <line x1={x(fcStartIdx)} y1={padT} x2={x(fcStartIdx)} y2={padT + plotH}
              stroke="#cbd5e1" strokeWidth={1} strokeDasharray="3 3" />
            <text x={x(fcStartIdx) + 4} y={padT + 10} fontSize={9} fill="#94a3b8">예측 →</text>
          </>
        )}

        {/* 예측 영역(점선) */}
        {forecastLine.length > 1 && (
          <path d={toPath(forecastLine)} fill="none" stroke="#2563eb" strokeWidth={2} strokeDasharray="5 4" />
        )}
        {forecastPts.map((p, i) => (
          <circle key={`f${i}`} cx={p[0]} cy={p[1]} r={2.5} fill="#2563eb" />
        ))}

        {/* 실측(실선) */}
        {actualPts.length > 1 && (
          <path d={toPath(actualPts)} fill="none" stroke="#10b981" strokeWidth={2.5} />
        )}
        {actualPts.map((p, i) => (
          <circle key={`a${i}`} cx={p[0]} cy={p[1]} r={2.8} fill="#10b981">
            <title>{`${actual[i].label}: ${actual[i].value.toLocaleString()}${unitLabel}`}</title>
          </circle>
        ))}

        {/* x축 라벨 */}
        {all.map((p, i) => (
          i % labelEvery === 0 ? (
            <text key={`x${i}`} x={x(i)} y={H - padB + 16} textAnchor="middle" fontSize={9}
              fill={i > fcStartIdx ? '#2563eb' : '#6b7280'}>
              {p.label}
            </text>
          ) : null
        ))}
      </svg>

      {/* 범례 */}
      <div style={{ display: 'flex', gap: 18, justifyContent: 'center', marginTop: 4, fontSize: '0.72rem', color: '#6b7280' }}>
        <span><span style={{ display: 'inline-block', width: 16, height: 2, background: '#10b981', verticalAlign: 'middle', marginRight: 5 }} />실측 출고</span>
        <span><span style={{ display: 'inline-block', width: 16, height: 0, borderTop: '2px dashed #2563eb', verticalAlign: 'middle', marginRight: 5 }} />추세 예측</span>
      </div>
    </div>
  )
}

/** 축 최댓값을 보기 좋은 값으로 올림 (1·2·5·10 계열). */
function niceCeil(v: number): number {
  if (v <= 0) return 1
  const exp = Math.floor(Math.log10(v))
  const base = Math.pow(10, exp)
  const f = v / base
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10
  return nice * base
}
