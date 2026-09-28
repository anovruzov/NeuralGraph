// A side drawer that opens a claim / discovery / evidence / question by reference (citations, lineage, network).
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Citation, ClaimDetail, DiscoveryDetail, EvidenceDetail, QuestionDetail } from '../api/types';
import { EvidencePanel, EvidenceRefItem, FreshnessBadge } from './EvidencePanel';
import { fmtDate, titleCase } from './format';
import { ClaimList } from './Lists';
import { SupportSummary } from './Support';
import { Badge, Drawer, ErrorPanel, Loading, StatusBadge } from './ui';

export interface ArtifactRef {
  type: string;
  id: string;
  label?: string;
}

interface PanelApi {
  open: (ref: ArtifactRef) => void;
  close: () => void;
}

const Ctx = createContext<PanelApi | null>(null);

export function useArtifactPanel(): PanelApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useArtifactPanel must be used inside ArtifactPanelProvider');
  return ctx;
}

function normalizeType(t: string): string {
  if (t === 'evidence_ref' || t === 'ev' || t === 'evidence') return 'evidence';
  return t;
}

function ArtifactBody({ ref }: { ref: ArtifactRef }) {
  const type = normalizeType(ref.type);
  const [data, setData] = useState<ClaimDetail | DiscoveryDetail | EvidenceDetail | QuestionDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setData(null);
    const load = async () => {
      try {
        let res: ClaimDetail | DiscoveryDetail | EvidenceDetail | QuestionDetail | null = null;
        if (type === 'claim') res = await api.claims.get(ref.id);
        else if (type === 'discovery') res = await api.discoveries.get(ref.id);
        else if (type === 'evidence') res = await api.evidence.get(ref.id);
        else if (type === 'question') res = await api.questions.get(ref.id);
        if (alive) setData(res);
      } catch (err) {
        if (alive) setError(err);
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, [type, ref.id]);

  if (type === 'goal') {
    return (
      <p>
        <Link to={`/app/goals/${ref.id}`}>Open goal {ref.label ?? ref.id} →</Link>
      </p>
    );
  }
  if (type === 'memory') {
    return (
      <div className="stack stack--sm">
        <Badge tone="outline">Private memory</Badge>
        <p>{ref.label ?? ref.id}</p>
        <p className="small muted">Memories live in the owner's holder. <Link to={`/app/memory?q=${encodeURIComponent(ref.label ?? '')}`}>Search my memory →</Link></p>
      </div>
    );
  }
  if (!['claim', 'discovery', 'evidence', 'question'].includes(type)) {
    return <p className="muted">No panel for type "{ref.type}". {ref.label ? <span>{ref.label}</span> : null}</p>;
  }
  if (loading) return <Loading />;
  if (error) return <ErrorPanel error={error} />;
  if (!data) return null;

  if (type === 'claim') {
    const d = data as ClaimDetail;
    return (
      <div className="stack">
        <div className="row" style={{ gap: 6 }}>
          <StatusBadge status={d.claim.status} />
          <Badge tone="outline">{titleCase(d.claim.kind)}</Badge>
          <FreshnessBadge freshness_at={d.freshness?.freshness_at} stale={d.freshness?.stale} ageDays={d.freshness?.age_days} />
        </div>
        <p style={{ fontSize: 'var(--fs-lg)' }}>{d.claim.text}</p>
        <SupportSummary support={d.claim.support} compact />
        <Link to={`/app/claims/${d.claim.claim_id}`}>Open claim page →</Link>
        <EvidencePanel evidence={d.evidence} conflicts={d.conflicts} revisions={d.revisions} dependencies={d.dependencies} />
      </div>
    );
  }
  if (type === 'discovery') {
    const d = data as DiscoveryDetail;
    return (
      <div className="stack">
        <div className="row" style={{ gap: 6 }}>
          <StatusBadge status={d.discovery.status} />
          <Badge tone="outline">{titleCase(d.discovery.kind)}</Badge>
          <Badge tone="outline">{titleCase(d.discovery.level)}</Badge>
        </div>
        <h3>{d.discovery.title}</h3>
        <p>{d.discovery.summary}</p>
        <SupportSummary support={d.discovery.support} compact />
        <Link to={`/app/discoveries/${d.discovery.discovery_id}`}>Open discovery page →</Link>
        <h4>Claims</h4>
        <ClaimList claims={d.claims} />
        <EvidencePanel evidence={d.evidence} conflicts={d.conflicts} />
      </div>
    );
  }
  if (type === 'evidence') {
    const d = data as EvidenceDetail;
    return (
      <div className="stack">
        <ul className="list">
          <EvidenceRefItem ref={d.evidence_ref} defaultOpen />
        </ul>
        <h4>Claims citing this reference</h4>
        <ClaimList claims={d.claims} empty="No visible claims cite this reference." />
      </div>
    );
  }
  const d = data as QuestionDetail;
  return (
    <div className="stack">
      <div className="row" style={{ gap: 6 }}>
        <StatusBadge status={d.question.status} />
        <Badge tone="outline">{titleCase(d.question.kind)}</Badge>
      </div>
      <p style={{ fontSize: 'var(--fs-lg)' }}>{d.question.text}</p>
      <div className="xs muted">asked by {d.question.asker?.name} · {fmtDate(d.question.created_at)}</div>
      <Link to={`/app/questions/${d.question.question_id}`}>Open question page →</Link>
      {d.claims.length ? (
        <>
          <h4>Resulting claims</h4>
          <ClaimList claims={d.claims} />
        </>
      ) : null}
    </div>
  );
}

export function ArtifactPanelProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<ArtifactRef | null>(null);
  const open = useCallback((ref: ArtifactRef) => setCurrent(ref), []);
  const close = useCallback(() => setCurrent(null), []);
  const value = useMemo(() => ({ open, close }), [open, close]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <Drawer open={current !== null} onClose={close} title={current ? `${titleCase(normalizeType(current.type))}${current.label ? `: ${current.label}` : ''}` : ''}>
        {current ? <ArtifactBody ref={current} /> : null}
      </Drawer>
    </Ctx.Provider>
  );
}

/** Citation chip: opens the artifact panel. */
export function CitationChip({ citation }: { citation: Citation | ArtifactRef }) {
  const panel = useArtifactPanel();
  const label = 'label' in citation && citation.label ? citation.label : citation.id;
  return (
    <button type="button" className="chip" onClick={() => panel.open({ type: citation.type, id: citation.id, label })} title={`${citation.type} ${citation.id}`}>
      <span className="chip__type">{normalizeType(citation.type)}</span>
      <span className="chip__label">{label}</span>
    </button>
  );
}
