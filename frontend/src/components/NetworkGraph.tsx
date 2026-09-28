// d3-force network view with geometric glyphs per node type, zoom/pan, click-to-select, and a readable list view.
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type SimulationLinkDatum, type SimulationNodeDatum } from 'd3-force';
import { useEffect, useMemo, useRef, useState, type WheelEvent as ReactWheelEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { Link } from 'react-router-dom';
import type { NetworkEdge, NetworkNode } from '../api/types';
import { artifactPath, titleCase } from './format';
import { GLYPH_COLORS, NodeShape } from '../shell/Glyph';
import { Badge, Empty, Table } from './ui';

interface SimNode extends SimulationNodeDatum {
  id: string;
  node: NetworkNode;
}
interface SimLink extends SimulationLinkDatum<SimNode> {
  kind: string;
}

const RADIUS: Record<string, number> = { unit: 11, user: 7, holder: 7, goal: 8, question: 6, claim: 5, discovery: 9, evidence: 5 };

function layout(nodes: NetworkNode[], edges: NetworkEdge[], width: number, height: number): { nodes: SimNode[]; links: SimLink[] } {
  const simNodes: SimNode[] = nodes.map((n, i) => ({ id: n.id, node: n, x: width / 2 + Math.cos(i) * 40, y: height / 2 + Math.sin(i) * 40 }));
  const index = new Map(simNodes.map((n) => [n.id, n]));
  const links: SimLink[] = edges
    .filter((e) => index.has(e.source) && index.has(e.target))
    .map((e) => ({ source: e.source, target: e.target, kind: e.kind }));
  const sim = forceSimulation<SimNode>(simNodes)
    .force('link', forceLink<SimNode, SimLink>(links).id((d) => d.id).distance((l) => (l.kind === 'parent' ? 70 : l.kind === 'member' || l.kind === 'leads' ? 45 : 55)).strength(0.6))
    .force('charge', forceManyBody<SimNode>().strength(nodes.length > 200 ? -60 : -140))
    .force('center', forceCenter(width / 2, height / 2))
    .force('collide', forceCollide<SimNode>().radius((d) => (RADIUS[d.node.type] ?? 7) + 6))
    .stop();
  const iterations = Math.min(300, Math.max(120, Math.ceil(Math.log(sim.alphaMin()) / Math.log(1 - sim.alphaDecay()))));
  for (let i = 0; i < iterations; i++) sim.tick();
  return { nodes: simNodes, links };
}

export interface NetworkGraphProps {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  selectedId?: string | null;
  onSelect?: (node: NetworkNode | null) => void;
  height?: number;
  showLegend?: boolean;
}

export function NetworkGraph({ nodes, edges, selectedId, onSelect, height = 640, showLegend = true }: NetworkGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 900, h: height });
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) setSize({ w: Math.max(200, r.width), h: Math.max(200, r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const graph = useMemo(() => layout(nodes, edges, size.w, size.h), [nodes, edges, size.w, size.h]);
  useEffect(() => setTransform({ x: 0, y: 0, k: 1 }), [nodes, edges]);

  const neighbors = useMemo(() => {
    const set = new Set<string>();
    if (!selectedId) return set;
    for (const l of graph.links) {
      const s = typeof l.source === 'object' ? l.source.id : String(l.source);
      const t = typeof l.target === 'object' ? l.target.id : String(l.target);
      if (s === selectedId) set.add(t);
      if (t === selectedId) set.add(s);
    }
    return set;
  }, [graph.links, selectedId]);

  function onWheel(e: ReactWheelEvent<SVGSVGElement>) {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const factor = Math.exp(-e.deltaY * 0.0015);
    setTransform((t) => {
      const k = Math.min(6, Math.max(0.2, t.k * factor));
      const ratio = k / t.k;
      return { k, x: px - (px - t.x) * ratio, y: py - (py - t.y) * ratio };
    });
  }
  function onPointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    drag.current = { x: e.clientX, y: e.clientY, tx: transform.x, ty: transform.y, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    if (d.moved) setTransform((t) => ({ ...t, x: d.tx + dx, y: d.ty + dy }));
  }
  function onPointerUp(e: ReactPointerEvent<SVGSVGElement>) {
    const d = drag.current;
    drag.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // no capture was taken (pointerdown happened on a node)
    }
    if (d && !d.moved && onSelect && e.target === e.currentTarget) onSelect(null);
  }

  if (!nodes.length) return <Empty>No nodes are visible in this view.</Empty>;
  const showLabels = transform.k >= 0.8 || nodes.length <= 60;
  return (
    <div className="network__canvas" ref={containerRef} style={{ height }}>
      <svg role="img" aria-label={`Network graph with ${nodes.length} nodes and ${edges.length} edges`} onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}>
        <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
          {graph.links.map((l, i) => {
            const s = l.source as SimNode;
            const t = l.target as SimNode;
            const dim = selectedId && s.id !== selectedId && t.id !== selectedId;
            return <line key={i} className={`edge edge--${l.kind}`} x1={s.x} y1={s.y} x2={t.x} y2={t.y} opacity={dim ? 0.25 : 0.9} />;
          })}
          {graph.nodes.map((n) => {
            const type = n.node.type;
            const color = GLYPH_COLORS[type] ?? '#6b665d';
            const r = RADIUS[type] ?? 7;
            const selected = n.id === selectedId;
            const dim = selectedId && !selected && !neighbors.has(n.id);
            return (
              <g
                key={n.id}
                transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                opacity={dim ? 0.3 : 1}
                style={{ cursor: 'pointer' }}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect?.(n.node);
                }}
                onPointerDown={(e) => e.stopPropagation()}
                role="button"
                tabIndex={0}
                aria-label={`${titleCase(type)}: ${n.node.label}`}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') onSelect?.(n.node);
                }}
              >
                <NodeShape type={type} r={r} fill={type === 'discovery' || type === 'evidence' ? '#fff' : color} stroke={color} className={selected ? 'node--selected' : undefined} />
                {showLabels ? (
                  <text className="node-label" x={r + 3} y={3}>
                    {n.node.label.length > 28 ? `${n.node.label.slice(0, 27)}…` : n.node.label}
                  </text>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>
      {showLegend ? (
        <div className="network__legend" aria-hidden>
          {Object.entries(GLYPH_COLORS)
            .filter(([k]) => k !== 'conflict')
            .map(([k, c]) => (
              <span key={k}>
                <svg width="12" height="12" viewBox="-7 -7 14 14">
                  <NodeShape type={k} r={5} fill={k === 'discovery' || k === 'evidence' ? '#fff' : c} stroke={c} />
                </svg>
                {k}
              </span>
            ))}
        </div>
      ) : null}
      <div style={{ position: 'absolute', right: 8, top: 8, display: 'flex', gap: 4 }}>
        <button type="button" className="btn btn--sm" aria-label="Zoom in" onClick={() => setTransform((t) => ({ ...t, k: Math.min(6, t.k * 1.25) }))}>+</button>
        <button type="button" className="btn btn--sm" aria-label="Zoom out" onClick={() => setTransform((t) => ({ ...t, k: Math.max(0.2, t.k / 1.25) }))}>−</button>
        <button type="button" className="btn btn--sm" aria-label="Reset view" onClick={() => setTransform({ x: 0, y: 0, k: 1 })}>Reset</button>
      </div>
    </div>
  );
}

/** Connections of one node, readable. */
export function NodeConnections({ node, nodes, edges }: { node: NetworkNode; nodes: NetworkNode[]; edges: NetworkEdge[] }) {
  const index = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const rows = edges
    .filter((e) => e.source === node.id || e.target === node.id)
    .map((e) => {
      const outgoing = e.source === node.id;
      const other = index.get(outgoing ? e.target : e.source);
      return { edge: e, other, outgoing };
    })
    .filter((r) => r.other);
  const path = artifactPath(node.type, node.id);
  return (
    <div className="stack stack--sm">
      <div className="row" style={{ gap: 6 }}>
        <Badge tone="accent">{titleCase(node.type)}</Badge>
        {node.level ? <Badge tone="outline">{titleCase(node.level)}</Badge> : null}
        {node.status ? <Badge tone="outline">{titleCase(node.status)}</Badge> : null}
      </div>
      <h3>{node.label}</h3>
      {path ? <Link to={path}>Open {node.type} page →</Link> : null}
      {node.meta && Object.keys(node.meta).length ? (
        <dl className="kv">
          {Object.entries(node.meta).slice(0, 8).map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt>{titleCase(k)}</dt>
              <dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <h4>Connections ({rows.length})</h4>
      {rows.length ? (
        <ul className="list list--tight">
          {rows.map((r, i) => {
            const p = r.other ? artifactPath(r.other.type, r.other.id) : null;
            return (
              <li key={i} className="small">
                <span className="xs muted">{r.outgoing ? '→' : '←'} {r.edge.kind}</span>{' '}
                {p ? <Link to={p}>{r.other!.label}</Link> : r.other!.label} <span className="xs muted">({r.other!.type})</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="xs muted">No connections in this view.</div>
      )}
    </div>
  );
}

/** Readable list alternative to the canvas. */
export function NetworkList({ nodes, edges, onSelect }: { nodes: NetworkNode[]; edges: NetworkEdge[]; onSelect?: (n: NetworkNode) => void }) {
  const degree = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of edges) {
      m.set(e.source, (m.get(e.source) ?? 0) + 1);
      m.set(e.target, (m.get(e.target) ?? 0) + 1);
    }
    return m;
  }, [edges]);
  const sorted = [...nodes].sort((a, b) => a.type.localeCompare(b.type) || a.label.localeCompare(b.label));
  return (
    <Table
      columns={[
        { key: 'type', header: 'Type', render: (n) => <Badge tone="outline">{titleCase(n.type)}</Badge> },
        {
          key: 'label',
          header: 'Node',
          render: (n) => {
            const p = artifactPath(n.type, n.id);
            return p ? <Link to={p}>{n.label}</Link> : n.label;
          },
        },
        { key: 'level', header: 'Level', render: (n) => (n.level ? titleCase(n.level) : '—') },
        { key: 'status', header: 'Status', render: (n) => (n.status ? titleCase(n.status) : '—') },
        { key: 'deg', header: 'Connections', num: true, render: (n) => degree.get(n.id) ?? 0 },
      ]}
      rows={sorted}
      rowKey={(n) => n.id}
      onRowClick={onSelect}
      empty="No nodes are visible in this view."
      caption="Network nodes"
    />
  );
}
