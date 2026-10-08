import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveEvents } from '../api/events';
import type { ActorRef, Goal, GoalAction, GoalDetail, Loop, Outcome } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { GoalForm } from '../components/GoalForm';
import { fmtBaseline, fmtDate, fmtMeasurementSource, fmtNum, fmtUsd, titleCase } from '../components/format';
import { DiscoveryList, GoalList, LoopBadge, ProgressDisplay, QuestionList } from '../components/Lists';
import { LoopPanel } from '../components/LoopPanel';
import { Badge, Button, Drawer, Empty, ErrorPanel, Field, Input, Loading, Meter, PageHeader, Section, Select, StatusBadge, Textarea, confirmAction } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useOrg } from '../components/useOrg';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

const SIMPLE_ACTIONS: { action: GoalAction; label: string; when: (g: Goal) => boolean; danger?: boolean; confirm?: string }[] = [
  { action: 'activate', label: 'Activate', when: (g) => g.status === 'draft' || g.status === 'completed' },
  { action: 'pause', label: 'Pause', when: (g) => g.status === 'active' },
  { action: 'resume', label: 'Resume', when: (g) => g.status === 'paused' },
  { action: 'complete', label: 'Complete', when: (g) => g.status === 'active' || g.status === 'paused', confirm: 'Mark this goal complete? The loop stops.' },
  { action: 'archive', label: 'Archive', when: (g) => g.status !== 'archived', danger: true, confirm: 'Archive this goal? It disappears from default lists.' },
];

function BudgetBar({ label, spent, total, money }: { label: string; spent: number; total: number; money?: boolean }) {
  const ratio = total > 0 ? spent / total : 0;
  return (
    <div className="stack" style={{ gap: 2 }}>
      <div className="row row--between xs">
        <span className="muted">{label}</span>
        <span>
          {money ? fmtUsd(spent) : fmtNum(spent)} / {money ? fmtUsd(total) : fmtNum(total)}
        </span>
      </div>
      <Meter value={ratio} tone={ratio > 0.9 ? 'danger' : ratio > 0.7 ? 'warn' : undefined} />
    </div>
  );
}

function OutcomeForm({ goal, claimsAvailable, onDone }: { goal: Goal; claimsAvailable: { id: string; text: string }[]; onDone: (o: Outcome, progress: Goal['progress']) => void }) {
  const toast = useToast();
  const [kind, setKind] = useState('measurement');
  const [metric, setMetric] = useState(goal.success_criteria[0]?.metric ?? '');
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState('');
  const [at, setAt] = useState('');
  const [claimIds, setClaimIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const num = Number(value);
      const res = await api.goals.recordOutcome(goal.goal_id, {
        kind,
        value: { metric: metric.trim(), value: Number.isNaN(num) ? value : num, unit: unit.trim() || undefined, at: at ? new Date(at).toISOString() : undefined },
        claim_ids: claimIds.length ? claimIds : undefined,
      });
      toast.push('Outcome recorded', 'ok');
      onDone(res.outcome, res.progress);
      setValue('');
    } catch (err) {
      toast.error(err, 'Could not record the outcome');
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form" onSubmit={(e) => void submit(e)} aria-label="Record outcome">
      <div className="form-row">
        <Field label="Kind">
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="measurement">measurement</option>
              <option value="action">action</option>
              <option value="decision">decision</option>
              <option value="milestone">milestone</option>
            </Select>
          )}
        </Field>
        <Field label="Metric" required>
          {(id) => (
            <Input id={id} required value={metric} onChange={(e) => setMetric(e.target.value)} list="outcome-metrics" />
          )}
        </Field>
        <datalist id="outcome-metrics">
          {goal.success_criteria.map((c) => (
            <option key={c.metric} value={c.metric} />
          ))}
        </datalist>
        <Field label="Value" required>{(id) => <Input id={id} required value={value} onChange={(e) => setValue(e.target.value)} />}</Field>
        <Field label="Unit">{(id) => <Input id={id} value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="hours" />}</Field>
        <Field label="At">{(id) => <Input id={id} type="date" value={at} onChange={(e) => setAt(e.target.value)} />}</Field>
      </div>
      {claimsAvailable.length ? (
        <Field label="Supporting claims" hint="Optional">
          {(id) => (
            <Select id={id} multiple size={3} value={claimIds} onChange={(e) => setClaimIds([...e.target.selectedOptions].map((o) => o.value))}>
              {claimsAvailable.map((c) => (
                <option key={c.id} value={c.id}>{c.text}</option>
              ))}
            </Select>
          )}
        </Field>
      ) : null}
      <div className="form-actions">
        <Button type="submit" variant="primary" busy={busy}>Record outcome</Button>
      </div>
    </form>
  );
}

type Dialog = 'assign' | 'decompose' | 'prioritize' | 'delegate' | 'edit' | null;

export function GoalDetailPage() {
  const { id = '' } = useParams();
  const me = useSession();
  const toast = useToast();
  const { units, unitName } = useOrg();
  const state = useAsync<GoalDetail>(() => api.goals.get(id), [id]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  // dialog form state
  const [assigneeText, setAssigneeText] = useState('');
  const [priority, setPriority] = useState('3');
  const [delegateTo, setDelegateTo] = useState('');
  const [subgoals, setSubgoals] = useState<{ title: string; objective: string; owner: string }[]>([{ title: '', objective: '', owner: '' }]);

  useLiveEvents(['goal.updated', 'loop.state', 'question.created', 'question.routed', 'question.responded', 'question.resolved', 'discovery.created', 'discovery.updated'], (e) => {
    if (e.ref_id === id || (e.payload && (e.payload as { goal_id?: string }).goal_id === id) || e.kind.startsWith('question') || e.kind.startsWith('discovery')) void state.reload();
  });

  const d = state.data;
  const goal = d?.goal;

  async function run(action: GoalAction, extra: Record<string, unknown> = {}, confirmText?: string) {
    if (confirmText && !confirmAction(confirmText)) return;
    setBusyAction(action);
    try {
      await api.goals.action(id, { action, ...extra } as Parameters<typeof api.goals.action>[1]);
      toast.push(`${titleCase(action)} done`, 'ok');
      setDialog(null);
      await state.reload();
    } catch (err) {
      toast.error(err, `Could not ${action}`);
    } finally {
      setBusyAction(null);
    }
  }

  function parseAssignees(text: string): ActorRef[] {
    return text
      .split(/[,\n]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((raw) => {
        const [type, rid] = raw.includes(':') ? (raw.split(':', 2) as [string, string]) : [raw.startsWith('unit_') ? 'unit' : 'user', raw];
        return { type, id: rid, name: type === 'unit' ? unitName(rid) : rid === me.user.user_id ? me.user.name : rid };
      });
  }

  const canManage = Boolean(goal && (goal.owner_type === 'user' ? goal.owner_id === me.user.user_id : true) || goal?.created_by === me.user.user_id || me.principal.is_admin);
  const claimsForOutcome = (d?.discoveries ?? []).flatMap((disc) => (disc.claims ?? []).map((c) => ({ id: c.claim_id, text: c.text })));

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Goals', to: '/app/goals' }, { label: goal?.title ?? '…' }]} />
      {state.loading && !d ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {d && goal ? (
        <>
          <PageHeader
            title={
              <span className="row" style={{ gap: 8 }}>
                {goal.title} <StatusBadge status={goal.status} /> <LoopBadge loop={d.loop ?? goal.loop} /> {goal.is_demo ? <Badge tone="outline">demo</Badge> : null}
              </span>
            }
            meta={
              <>
                Owner {goal.owner_name} ({goal.owner_type}) · scope {goal.scope_unit_name ?? 'private'} · priority {goal.priority} · v{goal.version} · updated {fmtDate(goal.updated_at)}
                {d.parent ? (
                  <>
                    {' '}· parent <Link to={`/app/goals/${d.parent.goal_id}`}>{d.parent.title}</Link>
                  </>
                ) : null}
              </>
            }
            actions={
              <>
                {SIMPLE_ACTIONS.filter((a) => a.when(goal)).map((a) => (
                  <Button key={a.action} size="sm" variant={a.danger ? 'danger' : a.action === 'activate' || a.action === 'resume' ? 'primary' : 'default'} busy={busyAction === a.action} onClick={() => void run(a.action, {}, a.confirm)}>
                    {a.label}
                  </Button>
                ))}
                <Button size="sm" onClick={() => setDialog('assign')}>Assign</Button>
                <Button size="sm" onClick={() => setDialog('decompose')}>Decompose</Button>
                <Button size="sm" onClick={() => { setPriority(String(goal.priority)); setDialog('prioritize'); }}>Prioritize</Button>
                <Button size="sm" onClick={() => setDialog('delegate')}>Delegate</Button>
                <Button size="sm" variant="ghost" onClick={() => setDialog('edit')}>Edit</Button>
              </>
            }
          />
          <div className="split split--wide">
            <div>
              <Section title="Objective">
                <p>{goal.objective}</p>
                <div className="grid grid--2" style={{ marginTop: 8 }}>
                  <div className="card">
                    <div className="card__title">Success criteria</div>
                    {goal.success_criteria.length ? (
                      <ul className="list list--tight">
                        {goal.success_criteria.map((c, i) => (
                          <li key={i} className="small">
                            <strong>{c.metric}</strong> → {String(c.target)} {c.direction ? <span className="muted">({c.direction})</span> : null}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="xs muted">None defined.</div>
                    )}
                    <dl className="kv" style={{ marginTop: 8 }}>
                      <dt>Baseline</dt>
                      <dd>{fmtBaseline(goal.baseline)}</dd>
                      <dt>Measured from</dt>
                      <dd>{fmtMeasurementSource(goal.measurement_source)}</dd>
                      <dt>Deadline</dt>
                      <dd>{fmtDate(goal.deadline, false)}</dd>
                    </dl>
                  </div>
                  <div className="card">
                    <div className="card__title">Progress</div>
                    <ProgressDisplay progress={goal.progress} />
                    {!goal.progress?.known ? <div className="xs muted" style={{ marginTop: 4 }}>Progress is reported as Unknown until an outcome is recorded against a success criterion.</div> : null}
                    <div className="card__title" style={{ marginTop: 12 }}>People</div>
                    <div className="row" style={{ gap: 4 }}>
                      {goal.assignees.length ? goal.assignees.map((a) => <Badge key={`${a.type}-${a.id}`} tone="outline">{a.name || a.id} · {a.type}</Badge>) : <span className="xs muted">No assignees.</span>}
                    </div>
                    <div className="card__title" style={{ marginTop: 12 }}>Dependencies</div>
                    {goal.dependencies.length ? (
                      <ul className="list list--tight">
                        {goal.dependencies.map((dep) => (
                          <li key={dep} className="small">
                            <Link to={`/app/goals/${dep}`}>{dep}</Link>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="xs muted">None.</span>
                    )}
                    <div className="card__title" style={{ marginTop: 12 }}>Permitted actions</div>
                    <div className="row" style={{ gap: 4 }}>
                      {goal.permitted_actions?.length ? goal.permitted_actions.map((a) => <Badge key={a} tone="outline">{a.replace(/_/g, ' ')}</Badge>) : <span className="xs muted">Default.</span>}
                    </div>
                  </div>
                </div>
              </Section>
              <Section title="Questions" meta={`${d.questions.length}`}>
                <QuestionList questions={d.questions} empty="The loop has not asked anything yet." />
              </Section>
              <Section title="Discoveries" meta={`${d.discoveries.length}`}>
                <DiscoveryList discoveries={d.discoveries} empty="No discoveries yet." />
              </Section>
              <Section title="Outcomes" meta={`${d.outcomes.length}`}>
                {d.outcomes.length ? (
                  <ul className="list">
                    {d.outcomes.map((o) => (
                      <li key={o.outcome_id}>
                        <div className="row" style={{ gap: 6 }}>
                          <Badge tone="outline">{o.kind}</Badge>
                          <strong>{o.value?.metric}</strong>
                          <span>= {String(o.value?.value)} {o.value?.unit ?? ''}</span>
                          <span className="xs muted">{fmtDate(o.value?.at ?? o.created_at)}</span>
                        </div>
                        {o.claim_ids?.length ? (
                          <div className="row" style={{ gap: 4, marginTop: 4 }}>
                            {o.claim_ids.map((c) => (
                              <Link key={c} to={`/app/claims/${c}`} className="chip"><span className="chip__type">claim</span>{c.slice(0, 12)}</Link>
                            ))}
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>No outcomes recorded.</Empty>
                )}
                {canManage ? (
                  <div className="card" style={{ marginTop: 12 }}>
                    <div className="card__title">Record outcome</div>
                    <OutcomeForm goal={goal} claimsAvailable={claimsForOutcome} onDone={() => void state.reload()} />
                  </div>
                ) : null}
              </Section>
              {d.subgoals.length ? (
                <Section title="Subgoals" meta={`${d.subgoals.length}`}>
                  <GoalList goals={d.subgoals} />
                </Section>
              ) : null}
            </div>
            <aside className="stack side-panel">
              <div className="card">
                <div className="card__title">Discovery loop</div>
                <LoopPanel goalId={goal.goal_id} loop={d.loop ?? goal.loop} canManage={canManage} onChange={(loop: Loop) => state.setData((prev) => (prev ? { ...prev, loop } : prev))} />
              </div>
              <div className="card">
                <div className="card__title">Budget</div>
                <div className="stack">
                  <BudgetBar label="Tokens" spent={goal.budget_spent?.tokens ?? 0} total={goal.budget?.tokens ?? 0} />
                  <BudgetBar label="Spend" spent={goal.budget_spent?.usd ?? 0} total={goal.budget?.usd ?? 0} money />
                  <BudgetBar label="Questions" spent={goal.budget_spent?.questions ?? 0} total={goal.budget?.questions ?? 0} />
                  <div className="xs muted">Follow-up depth ≤ {goal.budget?.followup_depth ?? '—'} · model usage {fmtNum(d.usage?.calls)} calls · {fmtNum(d.usage?.tokens)} tokens · {fmtUsd(d.usage?.usd)}</div>
                </div>
              </div>
              {goal.counts ? (
                <div className="card">
                  <div className="card__title">Counts</div>
                  <dl className="kv">
                    <dt>Questions</dt>
                    <dd>{goal.counts.questions} ({goal.counts.open_questions} open)</dd>
                    <dt>Discoveries</dt>
                    <dd>{goal.counts.discoveries}</dd>
                    <dt>Claims</dt>
                    <dd>{goal.counts.claims}</dd>
                  </dl>
                </div>
              ) : null}
            </aside>
          </div>

          <Drawer open={dialog === 'assign'} onClose={() => setDialog(null)} title="Assign people or units">
            <form className="form" onSubmit={(e) => { e.preventDefault(); void run('assign', { assignees: parseAssignees(assigneeText) }); }}>
              <Field label="Assignees" hint="One per line: usr_… or unit_… (or type:id). Replaces the current list.">
                {(fid) => <Textarea id={fid} rows={4} value={assigneeText} onChange={(e) => setAssigneeText(e.target.value)} placeholder={goal.assignees.map((a) => `${a.type}:${a.id}`).join('\n') || 'usr_…'} />}
              </Field>
              <div className="form-actions"><Button type="submit" variant="primary" busy={busyAction === 'assign'}>Assign</Button></div>
            </form>
          </Drawer>
          <Drawer open={dialog === 'prioritize'} onClose={() => setDialog(null)} title="Set priority">
            <form className="form" onSubmit={(e) => { e.preventDefault(); void run('prioritize', { priority: Number(priority) }); }}>
              <Field label="Priority (1 low – 5 high)">{(fid) => <Input id={fid} type="number" min={1} max={5} value={priority} onChange={(e) => setPriority(e.target.value)} />}</Field>
              <div className="form-actions"><Button type="submit" variant="primary" busy={busyAction === 'prioritize'}>Save</Button></div>
            </form>
          </Drawer>
          <Drawer open={dialog === 'delegate'} onClose={() => setDialog(null)} title="Delegate ownership">
            <form className="form" onSubmit={(e) => { e.preventDefault(); const [owner_type, owner_id] = delegateTo.split(':'); void run('delegate', { owner_type, owner_id }); }}>
              <p className="small muted">The new owner manages the goal; you keep manage rights through a grant.</p>
              <Field label="New owner" required>
                {(fid) => (
                  <Select id={fid} required value={delegateTo} onChange={(e) => setDelegateTo(e.target.value)}>
                    <option value="">Choose…</option>
                    <optgroup label="Units">
                      {units.filter((u) => !u.archived_at).map((u) => (
                        <option key={u.unit_id} value={`unit:${u.unit_id}`}>{u.name}</option>
                      ))}
                    </optgroup>
                  </Select>
                )}
              </Field>
              <Field label="…or a user id">{(fid) => <Input id={fid} placeholder="usr_…" onChange={(e) => setDelegateTo(e.target.value ? `user:${e.target.value}` : '')} />}</Field>
              <div className="form-actions"><Button type="submit" variant="primary" busy={busyAction === 'delegate'} disabled={!delegateTo}>Delegate</Button></div>
            </form>
          </Drawer>
          <Drawer open={dialog === 'decompose'} onClose={() => setDialog(null)} title="Decompose into subgoals" width={720}>
            <form
              className="form"
              onSubmit={(e) => {
                e.preventDefault();
                const list = subgoals
                  .filter((s) => s.title.trim())
                  .map((s) => {
                    const [owner_type, owner_id] = (s.owner || `user:${me.user.user_id}`).split(':') as [string, string];
                    return { title: s.title.trim(), objective: s.objective.trim(), owner_type, owner_id, scope_unit_id: owner_type === 'unit' ? owner_id : goal.scope_unit_id ?? undefined };
                  });
                void run('decompose', { subgoals: list });
              }}
            >
              {subgoals.map((s, i) => (
                <fieldset key={i}>
                  <legend>Subgoal {i + 1}</legend>
                  <div className="stack stack--sm">
                    <Field label="Title" required={i === 0}>{(fid) => <Input id={fid} value={s.title} onChange={(e) => setSubgoals((ss) => ss.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />}</Field>
                    <Field label="Objective">{(fid) => <Textarea id={fid} rows={2} value={s.objective} onChange={(e) => setSubgoals((ss) => ss.map((x, j) => (j === i ? { ...x, objective: e.target.value } : x)))} />}</Field>
                    <Field label="Owner">
                      {(fid) => (
                        <Select id={fid} value={s.owner} onChange={(e) => setSubgoals((ss) => ss.map((x, j) => (j === i ? { ...x, owner: e.target.value } : x)))}>
                          <option value="">Me</option>
                          {units.filter((u) => !u.archived_at).map((u) => (
                            <option key={u.unit_id} value={`unit:${u.unit_id}`}>{u.name}</option>
                          ))}
                        </Select>
                      )}
                    </Field>
                  </div>
                </fieldset>
              ))}
              <div className="form-actions" style={{ justifyContent: 'space-between' }}>
                <Button size="sm" onClick={() => setSubgoals((ss) => [...ss, { title: '', objective: '', owner: '' }])}>Add subgoal</Button>
                <Button type="submit" variant="primary" busy={busyAction === 'decompose'}>Create subgoals</Button>
              </div>
            </form>
          </Drawer>
          <Drawer open={dialog === 'edit'} onClose={() => setDialog(null)} title="Edit goal" width={760}>
            {dialog === 'edit' ? <GoalForm existing={goal} onCancel={() => setDialog(null)} onDone={() => { setDialog(null); void state.reload(); }} /> : null}
          </Drawer>
        </>
      ) : null}
    </div>
  );
}
