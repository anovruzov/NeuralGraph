import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveEvents } from '../api/events';
import type { DiscoveryDetail, ReviewAction } from '../api/types';
import { useArtifactPanel } from '../components/ArtifactDrawer';
import { ConflictItems, EvidencePanel, FreshnessBadge, RevisionList } from '../components/EvidencePanel';
import { fmtDate, titleCase } from '../components/format';
import { ClaimList, QuestionList } from '../components/Lists';
import { NetworkGraph } from '../components/NetworkGraph';
import { SupportSummary } from '../components/Support';
import { Badge, Button, Empty, ErrorPanel, Field, Input, Loading, PageHeader, Section, StatusBadge } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

const REVIEW_ACTIONS: { action: ReviewAction; label: string; variant?: 'primary' | 'danger' | 'default' }[] = [
  { action: 'reviewed', label: 'Mark reviewed' },
  { action: 'accepted', label: 'Accept', variant: 'primary' },
  { action: 'dismissed', label: 'Dismiss', variant: 'danger' },
  { action: 'escalated', label: 'Escalate one level up' },
  { action: 'comment', label: 'Comment only' },
];

export function DiscoveryDetailPage() {
  const { id = '' } = useParams();
  const toast = useToast();
  const panel = useArtifactPanel();
  const state = useAsync<DiscoveryDetail>(() => api.discoveries.get(id), [id]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<ReviewAction | null>(null);
  useLiveEvents(['discovery.updated', 'claim.revised', 'claim.stale', 'claim.retracted', 'conflict.opened', 'conflict.resolved', 'question.created'], (e) => {
    if (e.ref_id === id || e.kind !== 'discovery.updated') void state.reload();
  });

  const d = state.data;
  const disc = d?.discovery;

  async function review(action: ReviewAction, e?: FormEvent) {
    e?.preventDefault();
    setBusy(action);
    try {
      const res = await api.discoveries.review(id, note.trim() ? { action, note: note.trim() } : { action });
      toast.push(action === 'escalated' ? `Escalated${res.discovery.escalated_to ? ' (copy created one level up)' : ''}` : `Marked ${action}`, 'ok');
      setNote('');
      await state.reload();
    } catch (err) {
      toast.error(err, `Could not mark ${action}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, ...(disc?.scope_unit_id ? [{ label: disc.scope_unit_name ?? 'Unit', to: `/app/unit/${disc.scope_unit_id}` }] : []), { label: 'Discovery' }]} />
      {state.loading && !d ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {d && disc ? (
        <>
          <PageHeader
            title={<span className="row" style={{ gap: 8 }}>{disc.title} <StatusBadge status={disc.status} /></span>}
            meta={
              <span className="row" style={{ gap: 6 }}>
                <Badge tone="outline">{titleCase(disc.kind)}</Badge>
                <Badge tone="outline">{titleCase(disc.level)} level</Badge>
                <Badge tone="outline">{disc.visibility}</Badge>
                {disc.evidence_health && disc.evidence_health.status !== 'healthy' ? (
                  <Badge tone={disc.evidence_health.status === 'unsupported' ? 'danger' : 'warn'}
                         title={`${disc.evidence_health.inactive_refs} supporting reference(s) changed, withdrawn or deleted at the source`}>
                    evidence {disc.evidence_health.status}
                  </Badge>
                ) : null}
                {disc.is_demo ? <Badge tone="outline">demo</Badge> : null}
                <FreshnessBadge freshness_at={disc.freshness_at} />
                <span>· {disc.scope_unit_name ?? 'private'}{disc.goal_id ? <> · goal <Link to={`/app/goals/${disc.goal_id}`}>{disc.goal_title ?? disc.goal_id}</Link></> : null}{disc.question_id ? <> · from <Link to={`/app/questions/${disc.question_id}`}>question</Link></> : null} · {fmtDate(disc.updated_at)}</span>
                {disc.escalated_to ? <span>· escalated to <Link to={`/app/discoveries/${disc.escalated_to}`}>copy</Link></span> : null}
              </span>
            }
          />
          <div className="split split--wide">
            <div>
              <Section title="Summary">
                <p style={{ whiteSpace: 'pre-wrap' }}>{disc.summary}</p>
                <div className="card card--muted" style={{ marginTop: 8 }}>
                  <div className="card__title">Support</div>
                  <SupportSummary support={disc.support} />
                </div>
              </Section>
              <Section title="Claims" meta={`${d.claims.length}`}>
                <ClaimList claims={d.claims} empty="No claims are visible to you for this discovery." onSelect={(c) => panel.open({ type: 'claim', id: c.claim_id, label: c.text.slice(0, 60) })} />
              </Section>
              <Section title="Evidence" meta={`${d.evidence.length}`}>
                <EvidencePanel evidence={d.evidence} title="Disclosed evidence" />
              </Section>
              <Section title="Conflicts" meta={`${d.conflicts.length}`}>
                <ConflictItems conflicts={d.conflicts} />
              </Section>
              {d.lineage?.nodes?.length ? (
                <Section title="Lineage" meta={`${d.lineage.nodes.length} nodes`}>
                  <NetworkGraph nodes={d.lineage.nodes} edges={d.lineage.edges} height={380} onSelect={(n) => n && panel.open({ type: n.type, id: n.id, label: n.label })} />
                </Section>
              ) : null}
              <Section title="Follow-up questions" meta={`${d.followups.length}`}>
                <QuestionList questions={d.followups} empty="No follow-up questions." />
              </Section>
              <Section title="Revisions">
                <RevisionList revisions={d.revisions} />
              </Section>
            </div>
            <aside className="stack side-panel">
              <div className="card">
                <div className="card__title">Review</div>
                <form className="form" onSubmit={(e) => e.preventDefault()}>
                  <Field label="Note" hint="Optional; recorded with the action.">
                    {(fid) => <Input id={fid} value={note} onChange={(e) => setNote(e.target.value)} />}
                  </Field>
                  <div className="row">
                    {REVIEW_ACTIONS.map((a) => (
                      <Button key={a.action} size="sm" variant={a.variant ?? 'default'} busy={busy === a.action} onClick={() => void review(a.action)}>
                        {a.label}
                      </Button>
                    ))}
                  </div>
                </form>
                <div className="xs muted" style={{ marginTop: 6 }}>Escalation creates a copy one level up when you lead the scope unit or its parent.</div>
              </div>
              <div className="card">
                <div className="card__title">Review history</div>
                {disc.reviews?.length ? (
                  <ul className="list list--tight">
                    {disc.reviews.map((r, i) => (
                      <li key={i} className="small">
                        <StatusBadge status={r.action} /> {r.user_name} <span className="xs muted">{fmtDate(r.at)}</span>
                        {r.note ? <div className="xs">{r.note}</div> : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>Not reviewed yet.</Empty>
                )}
              </div>
            </aside>
          </div>
        </>
      ) : null}
    </div>
  );
}
