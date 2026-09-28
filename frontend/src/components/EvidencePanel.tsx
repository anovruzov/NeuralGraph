// Evidence panel reused everywhere a claim is shown: evidence refs (disclosed excerpts), freshness, known source
// dependencies (roots), unresolved disagreements, revision history. Renders only what the API returned.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { ClaimDependencies, ClaimFreshness, Conflict, EvidenceRef, EvidenceRawResponse, Revision } from '../api/types';
import { ago, fmtDate, shortId, titleCase, truncate } from './format';
import { Badge, Button, Empty, StatusBadge } from './ui';

export function FreshnessBadge({ freshness_at, stale, ageDays }: { freshness_at: string | null | undefined; stale?: boolean; ageDays?: number | null }) {
  if (!freshness_at) return <Badge tone="outline">freshness unknown</Badge>;
  const label = ageDays !== undefined && ageDays !== null ? `${Math.round(ageDays)}d old` : ago(freshness_at);
  return (
    <Badge tone={stale ? 'warn' : 'ok'} title={`Freshest evidence observed ${fmtDate(freshness_at)}`}>
      {stale ? 'stale · ' : 'fresh · '}
      {label}
    </Badge>
  );
}

export function EvidenceRefItem({ ref: r, defaultOpen }: { ref: EvidenceRef; defaultOpen?: boolean }) {
  const [raw, setRaw] = useState<EvidenceRawResponse | null>(null);
  const [rawError, setRawError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(!!defaultOpen);
  const rootKnown = Boolean(r.root_known);

  async function loadRaw() {
    setBusy(true);
    setRawError(null);
    try {
      setRaw(await api.evidence.raw(r.ref_id));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setRawError('Not authorized: raw text is visible to the holder owner or through a raw grant.');
      else setRawError(err instanceof Error ? err.message : 'Could not fetch raw evidence');
    } finally {
      setBusy(false);
    }
  }

  return (
    <li>
      <div className="item">
        <div className="item__body">
          <div className="row" style={{ gap: 6 }}>
            <span className="item__title">{r.title || 'Untitled source'}</span>
            <StatusBadge status={r.status} />
            <Badge tone="outline">{r.kind || 'document'}</Badge>
            <Badge tone="outline" title="What the holder's export policy disclosed">{titleCase(r.disclosure_level || 'excerpt')}</Badge>
          </div>
          <div className="item__meta">
            <span>holder: {r.holder_name || r.holder_id}</span>
            <span>observed {fmtDate(r.observed_at, false)}</span>
            <span>v{r.version}</span>
            {rootKnown ? (
              <span title={r.source_root_id ?? ''}>root {shortId(r.source_root_id, 8)}{r.copies_of_same_root > 1 ? ` · ${r.copies_of_same_root} copies` : ''}</span>
            ) : (
              <span className="badge badge--outline">root unknown</span>
            )}
          </div>
          {r.disclosed_excerpt ? (
            <blockquote className="small" style={{ margin: '6px 0 0', paddingLeft: 10, borderLeft: '2px solid var(--line-strong)', color: 'var(--text-2)' }}>
              {open ? r.disclosed_excerpt : truncate(r.disclosed_excerpt, 240)}
              {r.disclosed_excerpt.length > 240 ? (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen((o) => !o)} style={{ marginLeft: 6 }}>
                  {open ? 'less' : 'more'}
                </button>
              ) : null}
            </blockquote>
          ) : (
            <div className="xs muted" style={{ marginTop: 4 }}>No excerpt disclosed by the holder's export policy.</div>
          )}
          {raw ? (
            <pre style={{ marginTop: 8 }}>{raw.text}</pre>
          ) : null}
          {rawError ? <div className="notice notice--warn xs" style={{ marginTop: 6 }}>{rawError}</div> : null}
        </div>
        <div className="item__actions">
          {!raw ? (
            <Button size="sm" variant="ghost" busy={busy} onClick={() => void loadRaw()} title="Fetch the full text from the holder (owner or raw grant only)">
              Raw text
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export function RevisionList({ revisions }: { revisions: Revision[] }) {
  if (!revisions.length) return <Empty>No revisions recorded.</Empty>;
  return (
    <ul className="list list--tight">
      {revisions.map((rv, i) => {
        const actor = typeof rv.actor === 'string' ? rv.actor : rv.actor?.name ?? rv.actor_id ?? 'system';
        return (
          <li key={rv.revision_id ?? i}>
            <div className="row" style={{ gap: 6 }}>
              <span className="small">
                {rv.version !== undefined ? <strong>v{rv.version}</strong> : null} {titleCase(rv.action ?? 'revision')}
              </span>
              <span className="xs muted">{actor} · {fmtDate(rv.at ?? rv.created_at)}</span>
            </div>
            {rv.reason ? <div className="xs muted">{rv.reason}</div> : null}
          </li>
        );
      })}
    </ul>
  );
}

export function ConflictItems({ conflicts, showClaims = true }: { conflicts: Conflict[]; showClaims?: boolean }) {
  if (!conflicts.length) return <Empty>No open disagreements.</Empty>;
  return (
    <ul className="list">
      {conflicts.map((c) => (
        <li key={c.conflict_id}>
          <div className="row" style={{ gap: 6 }}>
            <StatusBadge status={c.status} />
            <span className="small">{c.summary || 'Two claims disagree.'}</span>
          </div>
          {showClaims ? (
            <div className="xs" style={{ marginTop: 4, display: 'grid', gap: 2 }}>
              <span>
                A: <Link to={`/app/claims/${c.claim_a?.claim_id}`}>{truncate(c.claim_a?.text, 120)}</Link> <StatusBadge status={c.claim_a?.status} />
              </span>
              <span>
                B: <Link to={`/app/claims/${c.claim_b?.claim_id}`}>{truncate(c.claim_b?.text, 120)}</Link> <StatusBadge status={c.claim_b?.status} />
              </span>
            </div>
          ) : null}
          <div className="xs muted" style={{ marginTop: 2 }}>
            opened {fmtDate(c.created_at)}
            {c.question_id ? (
              <>
                {' · '}
                <Link to={`/app/questions/${c.question_id}`}>investigation question</Link>
              </>
            ) : null}
            {c.resolved_at ? ` · resolved ${fmtDate(c.resolved_at)}` : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

export interface EvidencePanelProps {
  evidence: EvidenceRef[];
  conflicts?: Conflict[];
  revisions?: Revision[];
  dependencies?: ClaimDependencies | null;
  freshness?: ClaimFreshness | { freshness_at: string | null } | null;
  title?: string;
}

export function EvidencePanel({ evidence, conflicts = [], revisions = [], dependencies, freshness, title = 'Evidence' }: EvidencePanelProps) {
  const fr = freshness as ClaimFreshness | null | undefined;
  return (
    <div className="stack">
      <div className="row row--between">
        <h3>{title}</h3>
        {freshness ? <FreshnessBadge freshness_at={freshness.freshness_at} stale={fr?.stale} ageDays={fr?.age_days} /> : null}
      </div>
      {evidence.length ? (
        <ul className="list">
          {evidence.map((r) => (
            <EvidenceRefItem key={r.ref_id} ref={r} />
          ))}
        </ul>
      ) : (
        <Empty>No evidence references are visible to you for this item.</Empty>
      )}
      {dependencies ? (
        <div>
          <h4>Known source dependencies</h4>
          {dependencies.roots.length ? (
            <ul className="list list--tight">
              {dependencies.roots.map((d) => (
                <li key={d.source_root_id} className="small">
                  <span className="mono" title={d.source_root_id}>{shortId(d.source_root_id, 14)}</span> · {d.ref_count} reference{d.ref_count === 1 ? '' : 's'} · {d.holders.length} holder{d.holders.length === 1 ? '' : 's'}
                </li>
              ))}
            </ul>
          ) : (
            <div className="xs muted">No known roots.</div>
          )}
          {dependencies.unknown.length ? (
            <div className="xs muted" style={{ marginTop: 4 }}>
              {dependencies.unknown.length} reference{dependencies.unknown.length === 1 ? '' : 's'} with unknown root (not counted as independent).
            </div>
          ) : null}
        </div>
      ) : null}
      {conflicts.length ? (
        <div>
          <h4>Unresolved disagreements</h4>
          <ConflictItems conflicts={conflicts} />
        </div>
      ) : null}
      {revisions.length ? (
        <div>
          <h4>Revision history</h4>
          <RevisionList revisions={revisions} />
        </div>
      ) : null}
    </div>
  );
}
