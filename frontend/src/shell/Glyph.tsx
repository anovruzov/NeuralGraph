// Geometric glyphs used for node types in navigation and the network view.
// unit: hexagon, user: circle, holder: square, goal: diamond, question: triangle, claim: small circle, discovery: hexagon outline, evidence: square outline.
import type { CSSProperties } from 'react';

export type GlyphType = 'unit' | 'user' | 'holder' | 'goal' | 'question' | 'claim' | 'discovery' | 'evidence' | 'home' | 'memory' | 'chat' | 'network' | 'admin' | 'executive' | 'conflict' | string;

export const GLYPH_COLORS: Record<string, string> = {
  unit: '#3b5f6e',
  user: '#6b665d',
  holder: '#8a7a4f',
  goal: '#2f7a4f',
  question: '#3d5e8f',
  claim: '#7a5c8f',
  discovery: '#b4642a',
  evidence: '#8a7a4f',
  conflict: '#a33a32',
};

export function hexagonPoints(cx: number, cy: number, r: number): string {
  const pts: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i - Math.PI / 2;
    pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`);
  }
  return pts.join(' ');
}

/** SVG shape for a node type, centered at (0,0) with radius r. Used by the network canvas. */
export function NodeShape({ type, r, fill, stroke, className }: { type: GlyphType; r: number; fill: string; stroke: string; className?: string }) {
  const common = { fill, stroke, strokeWidth: 1.2, className };
  switch (type) {
    case 'unit':
      return <polygon points={hexagonPoints(0, 0, r)} {...common} />;
    case 'discovery':
      return <polygon points={hexagonPoints(0, 0, r)} {...common} fill="#fff" strokeWidth={2} />;
    case 'holder':
      return <rect x={-r * 0.85} y={-r * 0.85} width={r * 1.7} height={r * 1.7} {...common} />;
    case 'evidence':
      return <rect x={-r * 0.8} y={-r * 0.8} width={r * 1.6} height={r * 1.6} {...common} fill="#fff" />;
    case 'goal':
      return <polygon points={`0,${-r * 1.1} ${r * 1.1},0 0,${r * 1.1} ${-r * 1.1},0`} {...common} />;
    case 'question':
      return <polygon points={`0,${-r * 1.1} ${r},${r * 0.8} ${-r},${r * 0.8}`} {...common} />;
    case 'claim':
      return <circle r={r * 0.75} {...common} />;
    case 'user':
    default:
      return <circle r={r} {...common} />;
  }
}

/** Inline 14px glyph for lists and navigation. */
export function Glyph({ type, size = 14, style }: { type: GlyphType; size?: number; style?: CSSProperties }) {
  const color = GLYPH_COLORS[type] ?? 'currentColor';
  const r = size / 2 - 1.5;
  let body: React.ReactNode;
  switch (type) {
    case 'home':
      body = <path d="M2 7.5 8 2.5l6 5V14H9.5v-4h-3v4H2z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />;
      break;
    case 'memory':
      body = (
        <>
          <rect x="2.5" y="2.5" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path d="M5 6h6M5 8.5h6M5 11h4" stroke="currentColor" strokeWidth="1.2" />
        </>
      );
      break;
    case 'chat':
      body = <path d="M2.5 3h11v7.5H6L3 13v-2.5h-.5z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />;
      break;
    case 'network':
      body = (
        <>
          <circle cx="8" cy="3.5" r="1.7" fill="currentColor" />
          <circle cx="3.5" cy="12" r="1.7" fill="currentColor" />
          <circle cx="12.5" cy="12" r="1.7" fill="currentColor" />
          <path d="M8 5.2 3.9 10.5M8 5.2l4.1 5.3M5.2 12h5.6" stroke="currentColor" strokeWidth="1.2" />
        </>
      );
      break;
    case 'admin':
      body = (
        <>
          <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M3.6 12.4 5 11M11 5l1.4-1.4" stroke="currentColor" strokeWidth="1.3" />
        </>
      );
      break;
    case 'executive':
      body = <polygon points={hexagonPoints(8, 8, 6)} fill="none" stroke="currentColor" strokeWidth="1.4" />;
      break;
    case 'conflict':
      body = <path d="M8 2 14 13H2z M8 6v3.5M8 11v.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />;
      break;
    default:
      body = (
        <g transform="translate(8 8)">
          <NodeShape type={type} r={r} fill={color} stroke={color} />
        </g>
      );
  }
  return (
    <svg className="glyph" width={size} height={size} viewBox="0 0 16 16" aria-hidden style={{ color, ...style }}>
      {body}
    </svg>
  );
}

export function BrandMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <polygon points="16,2 28,9 28,23 16,30 4,23 4,9" fill="none" stroke="#3b5f6e" strokeWidth="2.5" />
      <circle cx="16" cy="16" r="4" fill="#3b5f6e" />
    </svg>
  );
}
