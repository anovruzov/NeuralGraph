/* The recursive loop as a static figure. Four stations on a ring; the ring itself is the point. */
const STEPS = [
  { k: "Discover", a: -90 },
  { k: "Verify", a: 0 },
  { k: "Distribute", a: 90 },
  { k: "Learn", a: 180 },
];
const C = 260, R = 170;
const pt = (deg: number, r = R) => ({ x: C + r * Math.cos((deg * Math.PI) / 180), y: C + r * Math.sin((deg * Math.PI) / 180) });

export default function Loop() {
  return (
    <svg viewBox="0 0 520 520" role="img" aria-label="The recursive loop: discover, verify, distribute, learn, and back to discover" className="loop-svg">
      <style>{`
        .loop-svg text { font-family: var(--mono); font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; fill: var(--fg); }
        .loop-svg .sub { fill: var(--fg-3); font-size: 9.5px; letter-spacing: 0.1em; }
        @media (prefers-reduced-motion: no-preference) {
          .loop-svg .flow { stroke-dasharray: 2 10; animation: loopflow 6s linear infinite; }
          @keyframes loopflow { to { stroke-dashoffset: -120; } }
        }
      `}</style>
      <circle cx={C} cy={C} r={R} fill="none" stroke="var(--line-2)" strokeWidth={1} />
      <circle className="flow" cx={C} cy={C} r={R} fill="none" stroke="var(--signal)" strokeWidth={1} strokeLinecap="round" opacity={0.7} />
      {STEPS.map((s, i) => {
        const p = pt(s.a);
        const arrow = pt(s.a + 45);
        const tangent = (s.a + 45 + 90) * (Math.PI / 180);
        return (
          <g key={s.k}>
            <circle cx={p.x} cy={p.y} r={26} fill="var(--bg)" stroke="var(--fg-2)" strokeWidth={1} />
            <text x={p.x} y={p.y + 4} textAnchor="middle" className="sub">{String(i + 1).padStart(2, "0")}</text>
            <text x={p.x + (s.a === 0 ? 40 : s.a === 180 ? -40 : 0)} y={p.y + (s.a === -90 ? -40 : s.a === 90 ? 50 : 4)} textAnchor={s.a === 0 ? "start" : s.a === 180 ? "end" : "middle"}>{s.k}</text>
            <polygon points={`${arrow.x},${arrow.y} ${arrow.x - 7 * Math.cos(tangent) + 4 * Math.sin(tangent)},${arrow.y - 7 * Math.sin(tangent) - 4 * Math.cos(tangent)} ${arrow.x - 7 * Math.cos(tangent) - 4 * Math.sin(tangent)},${arrow.y - 7 * Math.sin(tangent) + 4 * Math.cos(tangent)}`} fill="var(--signal)" />
          </g>
        );
      })}
      <text x={C} y={C - 10} textAnchor="middle" className="sub">question</text>
      <text x={C} y={C + 10} textAnchor="middle">↺ again</text>
    </svg>
  );
}
