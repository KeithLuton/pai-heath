export interface Series { name: string; points: { x: string; y: number }[]; tone: 'primary' | 'secondary' }

/**
 * Plain SVG line chart. Draws given points only: no smoothing, no fitted
 * trend, nothing inferred.
 */
export function LineChart({ series, height = 200, marker, yLabel, refLines = [] }: {
  series: Series[]; height?: number; marker?: { x: string; label: string }; yLabel: string;
  refLines?: { y: number; label: string }[];
}) {
  const xs = Array.from(new Set(series.flatMap(s => s.points.map(p => p.x)))).sort();
  const ys = series.flatMap(s => s.points.map(p => p.y)).concat(refLines.map(r => r.y));
  if (!xs.length || !ys.length) return <p className="empty">No readings in this period.</p>;
  const yMin = Math.floor((Math.min(...ys) - 4) / 5) * 5;
  const yMax = Math.ceil((Math.max(...ys) + 4) / 5) * 5;
  const W = 720, H = height, L = 44, R = 12, T = 12, B = 26;
  const px = (x: string) => L + (xs.indexOf(x) / Math.max(1, xs.length - 1)) * (W - L - R);
  const py = (y: number) => T + (1 - (y - yMin) / (yMax - yMin)) * (H - T - B);
  const ticks = [yMin, Math.round((yMin + yMax) / 2), yMax];
  const labelEvery = Math.max(1, Math.ceil(xs.length / 6));
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={yLabel} preserveAspectRatio="none">
        {ticks.map(t => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={py(t)} y2={py(t)} className="chart-grid" />
            <text x={L - 6} y={py(t) + 4} className="chart-tick" textAnchor="end">{t}</text>
          </g>
        ))}
        {refLines.map(r => (
          <g key={r.label}>
            <line x1={L} x2={W - R} y1={py(r.y)} y2={py(r.y)} className="chart-ref" />
            <text x={W - R} y={py(r.y) - 4} className="chart-reflabel" textAnchor="end">{r.label}</text>
          </g>
        ))}
        {marker && xs.includes(marker.x) && (
          <g>
            <line x1={px(marker.x)} x2={px(marker.x)} y1={T} y2={H - B} className="chart-marker" />
            <text x={px(marker.x) + 4} y={T + 10} className="chart-markerlabel">{marker.label}</text>
          </g>
        )}
        {series.map(s => (
          <g key={s.name} className={`chart-series chart-${s.tone}`}>
            <polyline fill="none" points={s.points.map(p => `${px(p.x)},${py(p.y)}`).join(' ')} />
            {s.points.map((p, i) => <circle key={i} cx={px(p.x)} cy={py(p.y)} r={2.2} />)}
          </g>
        ))}
        {xs.map((x, i) => i % labelEvery === 0 && (
          <text key={x} x={px(x)} y={H - 8} className="chart-tick" textAnchor="middle">{x.slice(5)}</text>
        ))}
      </svg>
      <figcaption>
        {series.map(s => <span key={s.name} className={`legend legend-${s.tone}`}>{s.name}</span>)}
      </figcaption>
    </figure>
  );
}
