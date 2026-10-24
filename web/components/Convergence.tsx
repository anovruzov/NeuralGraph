/* Weak, independent signals converging into one discovery. Static figure with a slow reveal under normal motion. */
const SIG = [
  { y: 50, label: "ticket volume, one region" },
  { y: 110, label: "two renewals slipped" },
  { y: 170, label: "latency p99 drift" },
  { y: 230, label: "a rollback last week" },
  { y: 290, label: "unrelated · noise" },
];
export default function Convergence() {
  return (
    <svg viewBox="0 0 760 340" role="img" aria-label="Independent weak signals converging into a single discovery" className="conv-svg">
      <style>{`
        .conv-svg text { font-family: var(--mono); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; fill: var(--fg-3); }
        .conv-svg .path { stroke-dasharray: 1; stroke-dashoffset: 1; }
        @media (prefers-reduced-motion: no-preference) {
          .conv-svg .path { animation: convdraw 4s ease-out forwards; }
          .conv-svg .path:nth-of-type(2) { animation-delay: .4s } .conv-svg .path:nth-of-type(3) { animation-delay: .8s } .conv-svg .path:nth-of-type(4) { animation-delay: 1.2s }
          .conv-svg .disc { opacity: 0; animation: convin 1.2s ease-out 2.6s forwards; }
          @keyframes convdraw { to { stroke-dashoffset: 0; } }
          @keyframes convin { to { opacity: 1; } }
        }
        @media (prefers-reduced-motion: reduce) { .conv-svg .path { stroke-dashoffset: 0; } }
      `}</style>
      {SIG.map((s, i) => {
        const noise = i === 4;
        return (
          <g key={s.y}>
            <circle cx={40} cy={s.y} r={3} fill={noise ? "var(--fg-3)" : "var(--signal)"} />
            <text x={54} y={s.y + 4}>{s.label}</text>
            {!noise ? (
              <path className="path" pathLength={1} d={`M 300 ${s.y} C 460 ${s.y}, 500 170, 600 170`} fill="none" stroke="var(--signal-dim)" strokeWidth={1} />
            ) : (
              <path d={`M 300 ${s.y} L 420 ${s.y + 14}`} fill="none" stroke="var(--line-2)" strokeWidth={1} strokeDasharray="2 4" />
            )}
          </g>
        );
      })}
      <g className="disc">
        <rect x={600} y={150} width={40} height={40} fill="var(--bg-1)" stroke="var(--signal)" />
        <rect x={612} y={162} width={16} height={16} fill="var(--signal)" />
        <text x={660} y={166} style={{ fill: "var(--fg)" }}>one discovery</text>
        <text x={660} y={182}>none sufficient alone</text>
      </g>
    </svg>
  );
}
