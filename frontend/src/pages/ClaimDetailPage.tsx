import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveEvents } from '../api/events';
import type { ClaimDetail } from '../api/types';
import { EvidencePanel, FreshnessBadge } from '../components/EvidencePanel';
import { fmtDate, fmtScore, titleCase } from '../components/format';
import { SupportSummary } from '../components/Support';
import { Badge, Button, Empty, ErrorPanel, Field, Input, Loading, PageHeader, Section, StatusBadge } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

export function ClaimDetailPage() {
  const { id = '' } = useParams();
  const toast = useToast();
  const state = useAsync<ClaimDetail>(() => api.claims.get(id), [id]);
  const [reason, setReason] = useState('');
  const [showRetract, setShowRetract] = useState(false);
  const [busy, setBusy] = useState(false);
  useLiveEvents(['claim.revised', 'claim.stale', 'claim.retracted', 'conflict.opened', 'conflict.resolved', 'document.revised', 'document.retracted'], (e) => {
    if (e.ref_id === id || !e.kind.startsWith('claim')) void state.reload();
  });

  const d = state.data;
  const c = d?.claim;

  async function retract(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.claims.retract(id, reason.trim());
      toast.push('Claim retracted', 'ok');
      setShowRetract(false);
      await state.reload();
    } catch (err) {
      toast.error(err, 'Could not retract');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, ...(c?.goal_id ? [{ label: 'Goal', to: `/app/goals/${c.goal_id}` }] : []), { label: 'Claim' }]} />
      {state.loading && !d ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {d && c ? (
        <>
          <PageHeader
            title={<span className="row" style={{ gap: 8 }}>Claim <StatusBadge status={c.status} /> <Badge tone="outline">{titleCase(c.kind)}</Badge> <Badge tone="outline">v{c.version}</Badge></span>}
            meta={<span className="mono">{c.claim_id}</span>}
            actions={c.status !== 'retracted' ? <Button size="sm" variant="danger" onClick={() => setShowRetract((s) => !s)}>Retract</Button> : null}
          >
            <p style={{ fontSize: 'var(--fs-lg)', marginTop: 8 }}>{c.text}</p>
          </PageHeader>
          {showRetract ? (
            <form className="card form" style={{ marginBottom: 16 }} onSubmit={(e) => void retract(e)}>
              <Field label="Reason for retraction" required hint="Creator or unit lead. Dependent discoveries are marked accordingly.">
                {(fid) => <Input id={fid} required value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />}
              </Field>
              <div className="form-actions">
                <Button onClick={() => setShowRetract(false)}>Cancel</Button>
                <Button type="submit" variant="danger" busy={busy}>Retract claim</Button>
              </div>
            </form>
          ) : null}
          <div className="split split--wide">
            <div>
              <Section title="Evidence and lineage">
                <EvidencePanel evidence={d.evidence} conflicts={d.conflicts} revisions={d.revisions} dependencies={d.dependencies} freshness={d.freshness} />
              </Section>
              <Section title="Derivations" meta={`${d.derivations.length}`}>
                {d.derivations.length ? (
                  <ul className="list list--tight">
                    {d.derivations.map((dv, i) => (
                      <li key={dv.derivation_id ?? i} className="small">
                        <strong>{titleCase(dv.operator ?? 'derivation')}</strong>
                        {dv.model ? <span className="muted"> · model {dv.model}{dv.tier ? ` (${dv.tier})` : ''}</span> : null}
                        {dv.contributors?.length ? <span className="muted"> · contributors {dv.contributors.map((x) => (typeof x === 'string' ? x : x.name)).join(', ')}</span> : null}
                        <span className="xs muted"> · {fmtDate(dv.at ?? dv.created_at)}</span>
                        {dv.inputs?.length ? <div className="xs muted">{dv.inputs.length} input{dv.inputs.length === 1 ? '' : 's'}</div> : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>No derivations recorded.</Empty>
                )}
              </Section>
              <Section title="History" meta={`${d.history.length} version${d.history.length === 1 ? '' : 's'}`}>
                {d.history.length ? (
                  <ul className="list list--tight">
                    {d.history.map((h) => (
                      <li key={`${h.claim_id}-${h.version}`} className="small">
                        <div className="row" style={{ gap: 6 }}>
                          <Badge tone="outline">v{h.version}</Badge>
                          <StatusBadge status={h.status} />
                          <span className="xs muted">{fmtDate(h.updated_at)}</span>
                          {h.claim_id !== c.claim_id ? <Link to={`/app/claims/${h.claim_id}`} className="xs">open</Link> : null}
                        </div>
                        <div>{h.text}</div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>No earlier versions.</Empty>
                )}
              </Section>
            </div>
            <aside className="stack side-panel">
              <div className="card">
                <div className="card__title">Support</div>
                <SupportSummary support={c.support} />
              </div>
              <div className="card">
                <div className="card__title">Freshness</div>
                <FreshnessBadge freshness_at={d.freshness?.freshness_at} stale={d.freshness?.stale} ageDays={d.freshness?.age_days} />
                <dl className="kv" style={{ marginTop: 8 }}>
                  <dt>Valid</dt>
                  <dd>{fmtDate(c.valid_from, false)} → {fmtDate(c.valid_to, false)}</dd>
                  <dt>Confidence</dt>
                  <dd>{fmtScore(c.confidence)}</dd>
                  <dt>Visibility</dt>
                  <dd>{c.visibility}{c.scope_unit_id ? <> · <Link to={`/app/unit/${c.scope_unit_id}`}>scope unit</Link></> : null}</dd>
                  <dt>Created by</dt>
                  <dd>{c.created_by?.name} <span className="xs muted">({c.created_by?.type})</span></dd>
                  <dt>Goal</dt>
                  <dd>{c.goal_id ? <Link to={`/app/goals/${c.goal_id}`}>{c.goal_id}</Link> : '—'}</dd>
                  <dt>Question</dt>
                  <dd>{c.question_id ? <Link to={`/app/questions/${c.question_id}`}>{c.question_id}</Link> : '—'}</dd>
                  <dt>Supersedes</dt>
                  <dd>{c.supersedes_claim_id ? <Link to={`/app/claims/${c.supersedes_claim_id}`}>{c.supersedes_claim_id}</Link> : '—'}</dd>
                  <dt>Superseded by</dt>
                  <dd>{c.superseded_by ? <Link to={`/app/claims/${c.superseded_by}`}>{c.superseded_by}</Link> : '—'}</dd>
                  <dt>Created</dt>
                  <dd>{fmtDate(c.created_at)}</dd>
                </dl>
              </div>
            </aside>
          </div>
        </>
      ) : null}
    </div>
  );
}
