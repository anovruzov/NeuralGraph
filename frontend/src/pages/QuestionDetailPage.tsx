import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveEvents } from '../api/events';
import type { Lineage, QuestionDetail } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { useArtifactPanel } from '../components/ArtifactDrawer';
import { EvidenceRefItem } from '../components/EvidencePanel';
import { fmtDate, fmtNum, fmtScore, fmtUsd, titleCase } from '../components/format';
import { ClaimList, DiscoveryList } from '../components/Lists';
import { NetworkGraph } from '../components/NetworkGraph';
import { RespondForm } from '../components/RespondForm';
import { Badge, Button, Empty, ErrorPanel, Loading, PageHeader, Section, StatusBadge, Table, confirmAction } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

function isLineage(l: unknown): l is Lineage {
  return Boolean(l && typeof l === 'object' && Array.isArray((l as Lineage).nodes) && Array.isArray((l as Lineage).edges));
}

export function QuestionDetailPage() {
  const { id = '' } = useParams();
  const me = useSession();
  const toast = useToast();
  const panel = useArtifactPanel();
  const state = useAsync<QuestionDetail>(() => api.questions.get(id), [id]);
  const [cancelling, setCancelling] = useState(false);
  useLiveEvents(['question.routed', 'question.responded', 'question.resolved', 'claim.committed', 'discovery.created'], (e) => {
    if (e.ref_id === id || (e.payload as { question_id?: string })?.question_id === id || e.kind === 'claim.committed' || e.kind === 'discovery.created') void state.reload();
  });

  const d = state.data;
  const q = d?.question;
  const pb = q?.priority_breakdown;
  const budget = (q?.budget ?? {}) as Record<string, number | undefined>;
  const spent = (q?.budget_spent ?? {}) as Record<string, number | undefined>;
  const canCancel = q && (q.asker?.id === me.user.user_id || me.principal.is_admin) && !['resolved', 'cancelled'].includes(q.status);

  async function cancel() {
    if (!confirmAction('Cancel this question? Pending routes are withdrawn.')) return;
    setCancelling(true);
    try {
      await api.questions.cancel(id);
      toast.push('Question cancelled', 'ok');
      await state.reload();
    } catch (err) {
      toast.error(err, 'Could not cancel');
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, ...(q?.goal_id ? [{ label: 'Goals', to: '/app/goals' }, { label: q.goal_title ?? 'Goal', to: `/app/goals/${q.goal_id}` }] : []), { label: 'Question' }]} />
      {state.loading && !d ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {d && q ? (
        <>
          <PageHeader
            title={<span className="row" style={{ gap: 8 }}><span>Question</span> <StatusBadge status={q.status} /> {q.needs_my_input ? <Badge tone="accent">needs my input</Badge> : null}</span>}
            meta={<span className="mono">{q.question_id}</span>}
            actions={canCancel ? <Button size="sm" variant="danger" busy={cancelling} onClick={() => void cancel()}>Cancel question</Button> : null}
          >
            <p style={{ fontSize: 'var(--fs-lg)', marginTop: 8 }}>{q.text}</p>
          </PageHeader>
          <div className="split">
            <div>
              {q.needs_my_input ? (
                <Section title="Respond from my holder">
                  <div className="card">
                    <RespondForm question={q} holders={me.holders} onDone={() => void state.reload()} />
                  </div>
                </Section>
              ) : null}
              <Section title="Routes" meta={`${q.routes?.length ?? 0}`}>
                <Table
                  rows={q.routes ?? []}
                  rowKey={(r) => r.route_id}
                  empty="Not routed to any holder yet."
                  caption="Question routes"
                  columns={[
                    { key: 'holder', header: 'Holder', render: (r) => r.holder_name || r.holder_id },
                    { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} /> },
                    { key: 'sent', header: 'Sent', render: (r) => fmtDate(r.sent_at) },
                    { key: 'resp', header: 'Responded', render: (r) => fmtDate(r.responded_at) },
                    { key: 'deadline', header: 'Deadline', render: (r) => fmtDate(r.deadline_at) },
                  ]}
                />
              </Section>
              <Section title="Responses" meta={`${d.responses.length}`}>
                {d.responses.length ? (
                  <ul className="list">
                    {d.responses.map((r) => (
                      <li key={r.response_id}>
                        <div className="row" style={{ gap: 6 }}>
                          <strong>{r.holder_name || r.holder_id}</strong>
                          <StatusBadge status={r.status} />
                          {r.confidence !== null && r.confidence !== undefined ? <Badge tone="outline">confidence {fmtScore(r.confidence)}</Badge> : null}
                          <span className="xs muted">received {fmtDate(r.received_at)} · fresh as of {fmtDate(r.freshness_at, false)}</span>
                        </div>
                        <p className="small" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{r.content || <span className="muted">No content (no evidence).</span>}</p>
                        {r.evidence?.length ? (
                          <ul className="list list--tight" style={{ marginLeft: 8, borderLeft: '1px solid var(--line)', paddingLeft: 10 }}>
                            {r.evidence.map((ev) => (
                              <EvidenceRefItem key={ev.ref_id} ref={ev} />
                            ))}
                          </ul>
                        ) : (
                          <div className="xs muted">No disclosed evidence.</div>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>No responses yet.</Empty>
                )}
              </Section>
              <Section title="Resulting claims" meta={`${d.claims.length}`}>
                <ClaimList claims={d.claims} empty="No claims committed from this question." onSelect={(c) => panel.open({ type: 'claim', id: c.claim_id, label: c.text.slice(0, 60) })} />
              </Section>
              <Section title="Discoveries" meta={`${d.discoveries.length}`}>
                <DiscoveryList discoveries={d.discoveries} empty="No discoveries from this question." />
              </Section>
              {isLineage(d.lineage) && d.lineage.nodes.length ? (
                <Section title="Lineage">
                  <NetworkGraph nodes={d.lineage.nodes} edges={d.lineage.edges} height={360} onSelect={(n) => n && panel.open({ type: n.type, id: n.id, label: n.label })} />
                </Section>
              ) : null}
            </div>
            <aside className="stack side-panel">
              <div className="card">
                <div className="card__title">Artifact</div>
                <dl className="kv">
                  <dt>Asker</dt>
                  <dd>{q.asker?.name} <span className="xs muted">({q.asker?.type})</span></dd>
                  <dt>Trigger</dt>
                  <dd>{titleCase(q.trigger) || '—'}</dd>
                  <dt>Kind</dt>
                  <dd>{titleCase(q.kind)}</dd>
                  <dt>Goal</dt>
                  <dd>{q.goal_id ? <Link to={`/app/goals/${q.goal_id}`}>{q.goal_title ?? q.goal_id}</Link> : '—'}</dd>
                  <dt>Scope</dt>
                  <dd>{q.scope_unit_id ? <Link to={`/app/unit/${q.scope_unit_id}`}>{q.scope_unit_name ?? q.scope_unit_id}</Link> : 'private'}</dd>
                  <dt>Valid</dt>
                  <dd>{fmtDate(q.valid_from, false)} → {fmtDate(q.valid_to, false)}</dd>
                  <dt>Uncertainty</dt>
                  <dd>{fmtScore(q.uncertainty)}</dd>
                  <dt>Depth</dt>
                  <dd>{q.depth}{q.parent_question_id ? <> · parent <Link to={`/app/questions/${q.parent_question_id}`}>question</Link></> : null}</dd>
                  <dt>Cooldown</dt>
                  <dd>{fmtDate(q.cooldown_until)}</dd>
                  <dt>Created</dt>
                  <dd>{fmtDate(q.created_at)}</dd>
                  <dt>Resolved</dt>
                  <dd>{fmtDate(q.resolved_at)}</dd>
                </dl>
              </div>
              <div className="card">
                <div className="card__title">Priority {typeof q.priority === 'number' ? q.priority.toFixed(2) : q.priority}</div>
                <div className="xs muted" style={{ marginBottom: 6 }}>Breakdown — heuristic estimates ({pb?.method ?? 'heuristic'}), not measurements.</div>
                {pb ? (
                  <dl className="kv">
                    {(['goal_value', 'uncertainty', 'impact', 'missing_evidence', 'information_gain', 'cost'] as const).map((k) => (
                      <div key={k} style={{ display: 'contents' }}>
                        <dt>{titleCase(k)}</dt>
                        <dd>
                          {fmtScore(pb[k])} <span className="xs muted">× w {fmtScore((pb.weights as Record<string, number | undefined>)?.[k])}</span>
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <div className="xs muted">No breakdown.</div>
                )}
              </div>
              <div className="card">
                <div className="card__title">Candidate domains</div>
                <div className="row" style={{ gap: 4 }}>
                  {q.candidate_domains?.length ? q.candidate_domains.map((dm) => <Badge key={dm} tone="outline">{dm}</Badge>) : <span className="xs muted">Any.</span>}
                </div>
                <div className="card__title" style={{ marginTop: 12 }}>Motivating lineage</div>
                {q.motivating_lineage?.length ? (
                  <div className="row" style={{ gap: 4 }}>
                    {q.motivating_lineage.map((l, i) => (
                      <button key={`${l.type}-${l.id}-${i}`} type="button" className="chip" onClick={() => panel.open({ type: l.type, id: l.id, label: l.label })}>
                        <span className="chip__type">{l.type}</span>
                        <span className="chip__label">{l.label || l.id}</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <span className="xs muted">None recorded.</span>
                )}
              </div>
              <div className="card">
                <div className="card__title">Policy and budget</div>
                <dl className="kv">
                  {Object.entries(q.policy ?? {}).map(([k, v]) => (
                    <div key={k} style={{ display: 'contents' }}>
                      <dt>{titleCase(k)}</dt>
                      <dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
                    </div>
                  ))}
                  <dt>Tokens</dt>
                  <dd>{fmtNum(spent.tokens)} / {fmtNum(budget.tokens)}</dd>
                  <dt>Spend</dt>
                  <dd>{fmtUsd(spent.usd)} / {fmtUsd(budget.usd)}</dd>
                  <dt>Questions</dt>
                  <dd>{fmtNum(spent.questions)} / {fmtNum(budget.questions)}</dd>
                </dl>
                {q.result ? (
                  <>
                    <div className="card__title" style={{ marginTop: 12 }}>Result</div>
                    <pre className="xs">{typeof q.result === 'string' ? q.result : JSON.stringify(q.result, null, 2)}</pre>
                  </>
                ) : null}
              </div>
            </aside>
          </div>
        </>
      ) : null}
    </div>
  );
}
