"use client";
import { useEffect, useRef, useState } from "react";

/*
  One restrained animation of the discovery loop. Nine stages, each a state of the same diagram.
  Under reduced motion nothing moves on its own: the viewer steps through the stages with the buttons.
*/

const STAGES = [
  { k: "Local sources", t: "Raw data stays where it is produced. Each participant reasons over its own records." },
  { k: "Weak signals", t: "Each source notices something that is insignificant on its own." },
  { k: "Independent support", t: "Three separate sources point the same way. Independence is what makes them count." },
  { k: "Hypothesis", t: "Mycelic forms a candidate explanation with its evidence and lineage attached." },
  { k: "Targeted verification", t: "The question goes back to the sources that can answer it. Nothing is assumed." },
  { k: "Verified discovery", t: "A source that was silent confirms. The hypothesis becomes a discovery with full lineage." },
  { k: "Propagation", t: "The discovery travels to the people and systems that need it, with its evidence." },
  { k: "State change", t: "The network now knows something it did not. Its topology and priorities shift." },
  { k: "Next investigation", t: "What the organization learned changes what Mycelic looks for next." },
] as const;

const PERIOD = 2800;

type P = { x: number; y: number };
const SRC: P[] = [ { x: 120, y: 92 }, { x: 120, y: 204 }, { x: 120, y: 316 }, { x: 120, y: 428 } ];
const HYP: P = { x: 470, y: 260 };
const DISC: P = { x: 660, y: 260 };
const ORG: P[] = [ { x: 860, y: 110 }, { x: 860, y: 260 }, { x: 860, y: 410 } ];

export default function SystemVisual() {
  const [stage, setStage] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [reduced, setReduced] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const visible = useRef(true);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => { setReduced(mq.matches); if (mq.matches) setPlaying(false); };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    if (!ref.current || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((e) => { visible.current = e[0].isIntersecting; }, { threshold: 0.2 });
    io.observe(ref.current);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => { if (visible.current) setStage((s) => (s + 1) % STAGES.length); }, PERIOD);
    return () => window.clearInterval(id);
  }, [playing]);

  const s = stage;
  const at = (k: number) => s >= k;
  const between = (a: number, b: number) => s >= a && s <= b;
  const line = (a: P, b: P, active: boolean, extra: React.SVGProps<SVGLineElement> = {}) => (
    <line className="t" x1={a.x} y1={a.y} x2={b.x} y2={b.y} pathLength={1} strokeDasharray={1}
      strokeDashoffset={active ? 0 : 1} stroke="var(--line-2)" strokeWidth={1} opacity={active ? 1 : 0} {...extra} />
  );

  return (
    <div className="figure sys" ref={ref} aria-label="Animated diagram of the Mycelic discovery loop">
      <div className="sys-stage" role="tablist" aria-label="Stages">
        {STAGES.map((st, i) => (
          <button key={st.k} role="tab" aria-selected={i === s} aria-current={i === s ? "step" : undefined}
            onClick={() => { setStage(i); setPlaying(false); }}>
            <span className="n">{String(i + 1).padStart(2, "0")}</span>{st.k}
          </button>
        ))}
      </div>

      <div className="sys-scroll">
      <svg className="sys-svg" viewBox="0 0 980 520" role="img" aria-labelledby="sys-title sys-desc">
        <title id="sys-title">{`Discovery loop, stage ${s + 1}: ${STAGES[s].k}`}</title>
        <desc id="sys-desc">{STAGES[s].t}</desc>

        {/* column labels */}
        <text x={120} y={40} textAnchor="middle">Local sources</text>
        <text x={HYP.x} y={40} textAnchor="middle">Hypothesis</text>
        <text x={DISC.x} y={40} textAnchor="middle">Discovery</text>
        <text x={ORG[1].x} y={40} textAnchor="middle">Organization</text>

        {/* raw-data boxes: never move */}
        {SRC.map((p, i) => (
          <g key={`raw${i}`} className="t" opacity={at(0) ? 1 : 0}>
            <rect x={p.x - 70} y={p.y - 22} width={44} height={44} fill="var(--bg-2)" stroke="var(--line-2)" />
            {[0, 1, 2].map((r) => (
              <line key={r} x1={p.x - 62} x2={p.x - 34 + (r === 2 ? -8 : 0)} y1={p.y - 10 + r * 10} y2={p.y - 10 + r * 10} stroke="var(--fg-3)" strokeWidth={1} />
            ))}
            <line x1={p.x - 26} y1={p.y} x2={p.x - 9} y2={p.y} stroke="var(--line-2)" />
          </g>
        ))}
        <text x={74} y={486} textAnchor="middle" className="t" style={{ fill: at(0) ? "var(--fg-3)" : "transparent" }}>raw data · local</text>

        {/* weak signal -> hypothesis lines. source 3 (index 3) is the silent one until verification */}
        {SRC.map((p, i) => {
          const supports = i !== 3;
          const active = supports ? at(1) : at(5);
          const strong = supports ? at(2) : at(5);
          return (
            <g key={`sig${i}`}>
              {line(p, HYP, active, { stroke: strong ? "var(--signal-dim)" : "var(--line-2)", strokeWidth: strong ? 1.2 : 1, opacity: active ? (strong ? 1 : 0.6) : 0 })}
            </g>
          );
        })}

        {/* verification query: dashed line from hypothesis back to the silent source and to source 1 */}
        {[SRC[3], SRC[1]].map((p, i) => (
          <line key={`q${i}`} className="t" x1={HYP.x} y1={HYP.y} x2={p.x + 14} y2={p.y} pathLength={1}
            strokeDasharray="0.04 0.03" strokeDashoffset={between(4, 4) ? 0 : 1} stroke="var(--signal)" strokeWidth={1}
            opacity={between(4, 5) ? 1 : 0} />
        ))}
        <text x={300} y={372} className="t" opacity={between(4, 5) ? 1 : 0} style={{ fill: "var(--signal)" }}>question → source</text>

        {/* hypothesis -> discovery */}
        {line(HYP, DISC, at(5), { stroke: "var(--signal-dim)", strokeWidth: 1.2 })}

        {/* propagation: discovery -> org nodes, and back to sources */}
        {ORG.map((p, i) => <g key={`org${i}`}>{line(DISC, p, at(6), { stroke: "var(--signal-dim)" })}</g>)}
        {SRC.map((p, i) => (
          <path key={`back${i}`} className="t" d={`M ${DISC.x} ${DISC.y + 40} C ${DISC.x} ${500}, ${p.x} ${500}, ${p.x} ${p.y + 16}`} fill="none"
            pathLength={1} strokeDasharray={1} strokeDashoffset={at(6) ? 0 : 1} stroke="var(--signal-dim)" strokeWidth={0.8} opacity={at(6) ? 0.55 : 0} />
        ))}

        {/* topology change: new edge between two sources, and between two org nodes */}
        <path className="t" d={`M ${SRC[0].x + 10} ${SRC[0].y + 10} Q 200 ${(SRC[0].y + SRC[2].y) / 2} ${SRC[2].x + 10} ${SRC[2].y - 10}`} fill="none"
          pathLength={1} strokeDasharray={1} strokeDashoffset={at(7) ? 0 : 1} stroke="var(--signal)" strokeWidth={1} opacity={at(7) ? 1 : 0} />
        <text x={212} y={212} className="t" opacity={at(7) ? 1 : 0} style={{ fill: "var(--signal)" }}>new edge</text>

        {/* next investigation: faint dashed query from org node toward an unexplored source */}
        <path className="t" d={`M ${ORG[2].x} ${ORG[2].y} C 760 480, 300 500, ${SRC[3].x + 16} ${SRC[3].y + 6}`} fill="none"
          pathLength={1} strokeDasharray="0.03 0.03" strokeDashoffset={at(8) ? 0 : 1} stroke="var(--fg-2)" strokeWidth={1} opacity={at(8) ? 0.9 : 0} />
        <text x={560} y={496} className="t" opacity={at(8) ? 1 : 0} style={{ fill: "var(--fg-2)" }}>next question</text>

        {/* source nodes */}
        {SRC.map((p, i) => {
          const supports = i !== 3;
          const lit = supports ? at(1) : at(5);
          const changed = at(7);
          return (
            <g key={`src${i}`}>
              <circle className="t" cx={p.x} cy={p.y} r={changed ? 15 : 12} fill="var(--bg-1)" stroke={changed ? "var(--signal)" : lit ? "var(--fg-2)" : "var(--line-2)"} strokeWidth={1} />
              <circle className="t" cx={p.x} cy={p.y} r={3.5} fill={lit ? "var(--signal)" : "var(--fg-3)"} opacity={at(0) ? 1 : 0} />
              {/* weak signal pulse ring */}
              <circle className="t" cx={p.x} cy={p.y} r={between(1, 3) && supports ? 22 : 12} fill="none" stroke="var(--signal)" strokeWidth={0.6} opacity={between(1, 3) && supports ? 0.5 : 0} />
            </g>
          );
        })}
        {["support", "sales", "engineering", "telemetry"].map((n, i) => (
          <text key={n} x={SRC[i].x} y={SRC[i].y + 32} textAnchor="middle">{n}</text>
        ))}

        {/* hypothesis node */}
        <g>
          <circle className="t" cx={HYP.x} cy={HYP.y} r={at(3) ? 20 : 4} fill="var(--bg-1)" stroke={at(3) ? "var(--fg-2)" : "var(--line-2)"} strokeWidth={1}
            strokeDasharray={at(5) ? "0" : "3 3"} opacity={at(2) ? 1 : 0} />
          <circle className="t" cx={HYP.x} cy={HYP.y} r={at(5) ? 7 : 4} fill={at(5) ? "var(--signal)" : "var(--fg-2)"} opacity={at(3) ? 1 : 0} />
          <text x={HYP.x} y={HYP.y + 42} textAnchor="middle" className="t" opacity={at(3) ? 1 : 0}>{at(5) ? "verified" : "candidate · lineage attached"}</text>
        </g>

        {/* discovery node */}
        <g>
          <rect className="t" x={DISC.x - 20} y={DISC.y - 20} width={40} height={40} fill="var(--bg-1)" stroke={at(5) ? "var(--signal)" : "var(--line-2)"} strokeWidth={1} opacity={at(5) ? 1 : 0} />
          <rect className="t" x={DISC.x - 8} y={DISC.y - 8} width={16} height={16} fill="var(--signal)" opacity={at(5) ? 1 : 0} />
          <text x={DISC.x} y={DISC.y + 42} textAnchor="middle" className="t" opacity={at(5) ? 1 : 0}>evidence + lineage</text>
        </g>

        {/* org nodes */}
        {ORG.map((p, i) => (
          <g key={`o${i}`}>
            <circle className="t" cx={p.x} cy={p.y} r={at(7) ? 15 : 12} fill="var(--bg-1)" stroke={at(7) ? "var(--signal)" : at(6) ? "var(--fg-2)" : "var(--line-2)"} strokeWidth={1} opacity={at(0) ? 1 : 0} />
            <circle className="t" cx={p.x} cy={p.y} r={3.5} fill={at(6) ? "var(--signal)" : "var(--fg-3)"} opacity={at(0) ? 1 : 0} />
          </g>
        ))}
        {["people", "agents", "systems"].map((n, i) => (
          <text key={n} x={ORG[i].x} y={ORG[i].y + 32} textAnchor="middle">{n}</text>
        ))}
        <path className="t" d={`M ${ORG[0].x + 24} ${ORG[0].y} L ${ORG[1].x + 24} ${ORG[1].y} L ${ORG[2].x + 24} ${ORG[2].y}`} fill="none" stroke="var(--signal)" strokeWidth={1}
          pathLength={1} strokeDasharray={1} strokeDashoffset={at(7) ? 0 : 1} opacity={at(7) ? 0.7 : 0} />
      </svg>
      </div>

      <div className="sys-caption">
        <div className="label signal" aria-live="polite">{String(s + 1).padStart(2, "0")} / {STAGES.length} · {STAGES[s].k}</div>
        <p>{STAGES[s].t}</p>
        {!reduced && (
          <button className="sys-ctrl" onClick={() => setPlaying((p) => !p)} aria-pressed={!playing}>
            {playing ? "Pause" : "Play"}
          </button>
        )}
        {reduced && (
          <button className="sys-ctrl" onClick={() => setStage((v) => (v + 1) % STAGES.length)}>Next stage</button>
        )}
      </div>
    </div>
  );
}
