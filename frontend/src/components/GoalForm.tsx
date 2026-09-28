import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { ActorRef, Goal, GoalCreateBody, SuccessCriterion } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Button, Checkbox, Field, Input, Select, Textarea } from './ui';
import { useAsync } from './useAsync';
import { useOrg } from './useOrg';
import { useToast } from './Toast';

const PERMITTED_ACTIONS = ['ask_questions', 'route_to_holders', 'verify', 'commit_claims', 'create_followups', 'escalate', 'record_outcomes'];

function toDateInput(iso: string | null | undefined): string {
  if (!iso) return '';
  return iso.slice(0, 10);
}

export interface GoalFormProps {
  existing?: Goal;
  defaultScopeUnitId?: string;
  defaultParentGoalId?: string;
  onDone: (goal: Goal) => void;
  onCancel?: () => void;
}

/** Create (POST /goals) or edit (PATCH /goals/{id}) a goal with every field from API.md. */
export function GoalForm({ existing, defaultScopeUnitId, defaultParentGoalId, onDone, onCancel }: GoalFormProps) {
  const me = useSession();
  const toast = useToast();
  const { units } = useOrg();
  const otherGoals = useAsync(() => api.goals.list({ limit: 200 }), []);
  const [title, setTitle] = useState(existing?.title ?? '');
  const [objective, setObjective] = useState(existing?.objective ?? '');
  const [owner, setOwner] = useState(existing ? `${existing.owner_type}:${existing.owner_id}` : `user:${me.user.user_id}`);
  const [scopeUnit, setScopeUnit] = useState(existing?.scope_unit_id ?? defaultScopeUnitId ?? '');
  const [parentGoal, setParentGoal] = useState(existing?.parent_goal_id ?? defaultParentGoalId ?? '');
  const [criteria, setCriteria] = useState<SuccessCriterion[]>(existing?.success_criteria?.length ? existing.success_criteria : [{ metric: '', target: '', direction: 'down' }]);
  const [baseline, setBaseline] = useState(existing?.baseline ?? '');
  const [measurement, setMeasurement] = useState(existing?.measurement_source ?? '');
  const [deadline, setDeadline] = useState(toDateInput(existing?.deadline));
  const [priority, setPriority] = useState(String(existing?.priority ?? 3));
  const [permitted, setPermitted] = useState<string[]>(existing?.permitted_actions ?? PERMITTED_ACTIONS);
  const [budget, setBudget] = useState({
    tokens: String(existing?.budget?.tokens ?? ''),
    usd: String(existing?.budget?.usd ?? ''),
    questions: String(existing?.budget?.questions ?? ''),
    followup_depth: String(existing?.budget?.followup_depth ?? ''),
  });
  const [dependencies, setDependencies] = useState<string[]>(existing?.dependencies ?? []);
  const [assignees, setAssignees] = useState<ActorRef[]>(existing?.assignees ?? []);
  const [assigneeInput, setAssigneeInput] = useState('');
  const [activate, setActivate] = useState(true);
  const [busy, setBusy] = useState(false);

  const leadUnits = me.principal.memberships.filter((m) => m.role !== 'org_admin');
  const ownerUnits = leadUnits.filter((m, i) => leadUnits.findIndex((x) => x.unit_id === m.unit_id) === i);

  function updateCriterion(i: number, patch: Partial<SuccessCriterion>) {
    setCriteria((c) => c.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }

  function addAssignee() {
    const raw = assigneeInput.trim();
    if (!raw) return;
    const [type, id] = raw.includes(':') ? (raw.split(':', 2) as [string, string]) : [raw.startsWith('unit_') ? 'unit' : 'user', raw];
    const unit = units.find((u) => u.unit_id === id);
    setAssignees((a) => [...a, { type, id, name: unit?.name ?? id }]);
    setAssigneeInput('');
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const [owner_type, owner_id] = owner.split(':') as ['user' | 'unit', string];
      const body: GoalCreateBody = {
        title: title.trim(),
        objective: objective.trim(),
        owner_type,
        owner_id,
        success_criteria: criteria.filter((c) => c.metric.trim()).map((c) => ({ metric: c.metric.trim(), target: c.target, direction: c.direction })),
        priority: Number(priority),
        permitted_actions: permitted,
        dependencies,
        assignees,
      };
      if (scopeUnit) body.scope_unit_id = scopeUnit;
      if (parentGoal) body.parent_goal_id = parentGoal;
      if (baseline.trim()) body.baseline = baseline.trim();
      if (measurement.trim()) body.measurement_source = measurement.trim();
      if (deadline) body.deadline = new Date(`${deadline}T00:00:00Z`).toISOString();
      const b: Record<string, number> = {};
      for (const [k, v] of Object.entries(budget)) if (v !== '') b[k] = Number(v);
      if (Object.keys(b).length) body.budget = b;
      let goal: Goal;
      if (existing) {
        const res = await api.goals.update(existing.goal_id, body);
        goal = res.goal;
        toast.push('Goal updated', 'ok');
      } else {
        body.activate = activate;
        const res = await api.goals.create(body);
        goal = res.goal;
        toast.push(activate ? 'Goal created and loop started' : 'Goal created', 'ok');
      }
      onDone(goal);
    } catch (err) {
      toast.error(err, 'Could not save the goal');
    } finally {
      setBusy(false);
    }
  }

  const goalOptions = (otherGoals.data?.items ?? []).filter((g) => g.goal_id !== existing?.goal_id);
  return (
    <form className="form" onSubmit={(e) => void submit(e)} aria-label={existing ? 'Edit goal' : 'Create goal'}>
      <Field label="Title" required>{(id) => <Input id={id} required value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />}</Field>
      <Field label="Objective" required hint="What outcome is wanted, in one or two sentences. The loop reads this.">
        {(id) => <Textarea id={id} required rows={3} value={objective} onChange={(e) => setObjective(e.target.value)} />}
      </Field>
      <div className="form-row">
        <Field label="Owner">
          {(id) => (
            <Select id={id} value={owner} onChange={(e) => setOwner(e.target.value)} disabled={Boolean(existing)}>
              <option value={`user:${me.user.user_id}`}>Me ({me.user.name})</option>
              {ownerUnits.map((m) => (
                <option key={m.unit_id} value={`unit:${m.unit_id}`}>{m.unit_name} (unit)</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Scope unit" hint="Which unit's holders may be asked">
          {(id) => (
            <Select id={id} value={scopeUnit} onChange={(e) => setScopeUnit(e.target.value)}>
              <option value="">No scope (private)</option>
              {units.filter((u) => !u.archived_at).map((u) => (
                <option key={u.unit_id} value={u.unit_id}>{u.name} ({u.type})</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Parent goal">
          {(id) => (
            <Select id={id} value={parentGoal} onChange={(e) => setParentGoal(e.target.value)}>
              <option value="">None</option>
              {goalOptions.map((g) => (
                <option key={g.goal_id} value={g.goal_id}>{g.title}</option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <fieldset>
        <legend>Success criteria</legend>
        <div className="stack stack--sm">
          {criteria.map((c, i) => (
            <div key={i} className="row" style={{ alignItems: 'flex-end' }}>
              <Field label="Metric" className="" >{(id) => <Input id={id} value={c.metric} onChange={(e) => updateCriterion(i, { metric: e.target.value })} placeholder="resolution_time_hours" />}</Field>
              <Field label="Target">{(id) => <Input id={id} value={String(c.target ?? '')} onChange={(e) => updateCriterion(i, { target: e.target.value })} placeholder="24" />}</Field>
              <Field label="Direction">
                {(id) => (
                  <Select id={id} value={c.direction ?? 'down'} onChange={(e) => updateCriterion(i, { direction: e.target.value })}>
                    <option value="down">decrease</option>
                    <option value="up">increase</option>
                    <option value="reach">reach</option>
                  </Select>
                )}
              </Field>
              <Button size="sm" variant="ghost" aria-label="Remove criterion" onClick={() => setCriteria((cs) => cs.filter((_, j) => j !== i))} disabled={criteria.length === 1}>×</Button>
            </div>
          ))}
          <div>
            <Button size="sm" onClick={() => setCriteria((cs) => [...cs, { metric: '', target: '', direction: 'down' }])}>Add criterion</Button>
          </div>
        </div>
      </fieldset>
      <div className="form-row">
        <Field label="Baseline">{(id) => <Input id={id} value={baseline} onChange={(e) => setBaseline(e.target.value)} placeholder="e.g. 52h median" />}</Field>
        <Field label="Measurement source">{(id) => <Input id={id} value={measurement} onChange={(e) => setMeasurement(e.target.value)} placeholder="ticketing export" />}</Field>
        <Field label="Deadline">{(id) => <Input id={id} type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />}</Field>
        <Field label="Priority" hint="1 (low) – 5 (high)">
          {(id) => <Input id={id} type="number" min={1} max={5} value={priority} onChange={(e) => setPriority(e.target.value)} />}
        </Field>
      </div>
      <fieldset>
        <legend>Budget (blank = organization default)</legend>
        <div className="form-row">
          <Field label="Tokens">{(id) => <Input id={id} type="number" min={0} value={budget.tokens} onChange={(e) => setBudget({ ...budget, tokens: e.target.value })} />}</Field>
          <Field label="USD">{(id) => <Input id={id} type="number" min={0} step="0.01" value={budget.usd} onChange={(e) => setBudget({ ...budget, usd: e.target.value })} />}</Field>
          <Field label="Questions">{(id) => <Input id={id} type="number" min={0} value={budget.questions} onChange={(e) => setBudget({ ...budget, questions: e.target.value })} />}</Field>
          <Field label="Follow-up depth">{(id) => <Input id={id} type="number" min={0} max={10} value={budget.followup_depth} onChange={(e) => setBudget({ ...budget, followup_depth: e.target.value })} />}</Field>
        </div>
      </fieldset>
      <fieldset>
        <legend>Permitted actions for the loop</legend>
        <div className="row">
          {PERMITTED_ACTIONS.map((a) => (
            <Checkbox key={a} label={a.replace(/_/g, ' ')} checked={permitted.includes(a)} onChange={(e) => setPermitted((p) => (e.target.checked ? [...p, a] : p.filter((x) => x !== a)))} />
          ))}
        </div>
      </fieldset>
      <div className="form-row">
        <Field label="Dependencies" hint="Goals this one waits on (Ctrl/Cmd-click for several)">
          {(id) => (
            <Select id={id} multiple size={4} value={dependencies} onChange={(e) => setDependencies([...e.target.selectedOptions].map((o) => o.value))}>
              {goalOptions.map((g) => (
                <option key={g.goal_id} value={g.goal_id}>{g.title}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Assignees" hint="usr_… or unit id; Enter to add">
          {(id) => (
            <div className="stack stack--sm">
              <div className="row">
                <Input id={id} value={assigneeInput} onChange={(e) => setAssigneeInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addAssignee(); } }} list="goalform-units" placeholder="usr_… / unit_…" />
                <datalist id="goalform-units">
                  <option value={me.user.user_id}>{me.user.name}</option>
                  {units.map((u) => (
                    <option key={u.unit_id} value={u.unit_id}>{u.name}</option>
                  ))}
                </datalist>
                <Button size="sm" onClick={addAssignee}>Add</Button>
              </div>
              <div className="row" style={{ gap: 4 }}>
                {assignees.map((a, i) => (
                  <span key={`${a.id}-${i}`} className="chip" onClick={() => setAssignees((as) => as.filter((_, j) => j !== i))} title="Remove">
                    <span className="chip__type">{a.type}</span> {a.name || a.id} ×
                  </span>
                ))}
              </div>
            </div>
          )}
        </Field>
      </div>
      <div className="form-actions" style={{ justifyContent: existing ? 'flex-end' : 'space-between' }}>
        {!existing ? <Checkbox label="Activate now and start the discovery loop" checked={activate} onChange={(e) => setActivate(e.target.checked)} /> : null}
        <div className="row">
          {onCancel ? <Button onClick={onCancel}>Cancel</Button> : null}
          <Button type="submit" variant="primary" busy={busy}>{existing ? 'Save changes' : 'Create goal'}</Button>
        </div>
      </div>
    </form>
  );
}
