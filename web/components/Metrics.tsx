import type { Metric } from "@/app/research/metrics";

export default function Metrics({ items, cols }: { items: Metric[]; cols?: number }) {
  return (
    <div className="metrics" style={cols ? ({ "--cols": cols } as React.CSSProperties) : undefined}>
      {items.map((m) => (
        <div className="metric" key={m.what}>
          <div className="value">{m.value}{m.unit && <small>{m.unit}</small>}</div>
          {m.delta && <div className="label delta">{m.delta}</div>}
          <div className="what">{m.what}</div>
          <div className="scope" title={m.source}>{m.scope}</div>
        </div>
      ))}
    </div>
  );
}
