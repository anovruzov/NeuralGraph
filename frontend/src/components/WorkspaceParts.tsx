// Sections shared by unit workspaces (team / department / subsidiary / region) and the executive workspace.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { ChildSummary, Comparison, Conflict, ConflictOutcome, Dependency, Goal, Progress, Synthesis, SynthesisItem, UnitMember } from '../api/types';
import { ago, fmtDate, titleCase, truncate } from './format';
import { ProgressDisplay } from './Lists';
import { Badge, Button, Empty, Field, Input, Select, StatusBadge, Table } from './ui';
import { useToast } from './Toast';

export function SynthesisList({ items, empty = 'None identified.' }: { items: SynthesisItem[] | undefined; empty?: string }) {
  if (!items?.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="list list--tight">
      {items.map((it, i) => (
        <li key={i}>
          <div className="small">{it.text}</div>
          <div className="row" style={{ gap: 4, marginTop: 3 }}>
            {it.unit_names?.map((u) => (
              <Badge key={u} tone="outline">{u}</Badge>
            ))}
            {it.discovery_ids?.map((d) => (
              <Link key={d} to={`/app/discoveries/${d}`} className="chip"><span className="chip__type">disc</span><span className="chip__label">{d.slice(0, 14)}</span></Link>
            ))}
            {it.claim_ids?.map((c) => (
              <Link key={c} to={`/app/claims/${c}`} className="chip"><span className="chip__type">claim</span><span className="chip__label">{c.slice(0, 14)}</span></Link>
            ))}
            {it.conflict_ids?.map((c) => (
              <span key={c} className="chip" title={c}><span className="chip__type">conflict</span><span className="chip__label">{c.slice(0, 14)}</span></span>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function SynthesisSummary({ synthesis }: { synthesis: Synthesis | null | undefined }) {
  if (!synthesis || synthesis.method === 'none') {
    return <div className="notice">No synthesis has been computed yet{synthesis?.computed_at ? '' : ' (no model call has run for this unit)'}. Lists below come directly from the coordination database.</div>;
  }
  return (
    <div className="card">
      <div className="row row--between">
        <div className="card__title" style={{ marginBottom: 0 }}>Cross-unit synthesis</div>
        <span className="xs muted">method: {synthesis.method} · computed {ago(synthesis.computed_at)}</span>
      </div>
      <p style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{synthesis.summary || <span className="muted">No summary text.</span>}</p>
    </div>
  );
}

export function ChildrenTable({ children }: { children: ChildSummary[] }) {
  return (
    <Table
      rows={children}
      rowKey={(c) => c.unit.unit_id}
      empty="This unit has no child units."
      caption="Child units"
      columns={[
        { key: 'name', header: 'Unit', render: (c) => <Link to={`/app/unit/${c.unit.unit_id}`}>{c.unit.name}</Link> },
        { key: 'type', header: 'Type', render: (c) => titleCase(c.unit.type) },
        { key: 'members', header: 'Members', num: true, render: (c) => c.unit.member_count },
        { key: 'goals', header: 'Goals', num: true, render: (c) => c.goals },
        { key: 'disc', header: 'Discoveries', num: true, render: (c) => c.discoveries },
        { key: 'oq', header: 'Open questions', num: true, render: (c) => c.open_questions },
      ]}
    />
  );
}

export function ComparisonsTable({ comparisons }: { comparisons: Comparison[] }) {
  return (
    <Table
      rows={comparisons}
      rowKey={(c) => c.unit_id}
      empty="No units to compare."
      caption="Comparison across units"
      columns={[
        { key: 'name', header: 'Unit', render: (c) => <Link to={`/app/unit/${c.unit_id}`}>{c.name}</Link> },
        { key: 'disc', header: 'Discoveries', num: true, render: (c) => c.discoveries },
        { key: 'sup', header: 'Supported claims', num: true, render: (c) => c.supported_claims },
        { key: 'conf', header: 'Open conflicts', num: true, render: (c) => (c.open_conflicts ? <Badge tone="danger">{c.open_conflicts}</Badge> : 0) },
        { key: 'stale', header: 'Stale claims', num: true, render: (c) => (c.stale_claims ? <Badge tone="warn">{c.stale_claims}</Badge> : 0) },
      ]}
    />
  );
}

export function DependenciesList({ dependencies, goals }: { dependencies: Dependency[]; goals: Goal[] }) {
  const title = (id: string) => goals.find((g) => g.goal_id === id)?.title;
  if (!dependencies.length) return <Empty>No goal dependencies.</Empty>;
  return (
    <ul className="list list--tight">
      {dependencies.map((d, i) => (
        <li key={`${d.goal_id}-${d.depends_on}-${i}`} className="small row" style={{ gap: 6 }}>
          <Link to={`/app/goals/${d.goal_id}`}>{d.goal_title ?? title(d.goal_id) ?? d.goal_id}</Link>
          <span className="muted">depends on</span>
          <Link to={`/app/goals/${d.depends_on}`}>{d.depends_on_title ?? title(d.depends_on) ?? d.depends_on}</Link>
          <StatusBadge status={d.status} />
        </li>
      ))}
    </ul>
  );
}

export function ProgressList({ progress, goals }: { progress: { goal_id: string; progress: Progress }[]; goals: Goal[] }) {
  const rows = progress.length ? progress : goals.map((g) => ({ goal_id: g.goal_id, progress: g.progress }));
  if (!rows.length) return <Empty>No goals to report on.</Empty>;
  return (
    <ul className="list list--tight">
      {rows.map((p) => {
        const g = goals.find((x) => x.goal_id === p.goal_id);
        return (
          <li key={p.goal_id} className="row" style={{ justifyContent: 'space-between' }}>
            <Link to={`/app/goals/${p.goal_id}`} className="small" style={{ flex: 1, minWidth: 160 }}>{g?.title ?? p.goal_id}</Link>
            <div style={{ width: 220 }}>
              <ProgressDisplay progress={p.progress} compact />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Goals grouped by assignee (task ownership). */
export function TaskOwnership({ goals }: { goals: Goal[] }) {
  const groups = new Map<string, { name: string; goals: Goal[] }>();
  for (const g of goals) {
    const targets = g.assignees.length ? g.assignees : [{ type: g.owner_type, id: g.owner_id, name: g.owner_name }];
    for (const a of targets) {
      const key = `${a.type}:${a.id}`;
      const grp = groups.get(key) ?? { name: `${a.name || a.id} (${a.type})`, goals: [] };
      grp.goals.push(g);
      groups.set(key, grp);
    }
  }
  if (!groups.size) return <Empty>No goals assigned.</Empty>;
  return (
    <div className="grid grid--auto">
      {[...groups.values()].map((grp) => (
        <div key={grp.name} className="card">
          <div className="card__title">{grp.name}</div>
          <ul className="list list--tight">
            {grp.goals.map((g) => (
              <li key={g.goal_id} className="small row" style={{ justifyContent: 'space-between' }}>
                <Link to={`/app/goals/${g.goal_id}`}>{truncate(g.title, 60)}</Link>
                <StatusBadge status={g.status} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function MembersList({ members }: { members: UnitMember[] }) {
  return (
    <Table
      rows={members}
      rowKey={(m) => `${m.user_id}-${m.role}`}
      empty="No members."
      caption="Unit members"
      columns={[
        { key: 'name', header: 'Name', render: (m) => m.user_name },
        { key: 'email', header: 'Email', render: (m) => m.email },
        { key: 'role', header: 'Role', render: (m) => titleCase(m.role) },
      ]}
    />
  );
}

/** Conflicts with resolve / investigate actions (leads of the scope). */
export function ConflictsSection({ conflicts, canResolve, onChanged }: { conflicts: Conflict[]; canResolve: boolean; onChanged: () => void }) {
  const toast = useToast();
  const [openId, setOpenId] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ConflictOutcome>('unresolved');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  async function resolve(c: Conflict) {
    setBusy(c.conflict_id);
    try {
      await api.conflicts.resolve(c.conflict_id, { outcome, note: note.trim() });
      toast.push('Conflict resolved', 'ok');
      setOpenId(null);
      setNote('');
      onChanged();
    } catch (err) {
      toast.error(err, 'Could not resolve');
    } finally {
      setBusy(null);
    }
  }
  async function investigate(c: Conflict) {
    setBusy(c.conflict_id);
    try {
      const res = await api.conflicts.investigate(c.conflict_id);
      toast.push(`Contradiction question created: ${truncate(res.question.text, 60)}`, 'ok');
      onChanged();
    } catch (err) {
      toast.error(err, 'Could not start an investigation');
    } finally {
      setBusy(null);
    }
  }

  if (!conflicts.length) return <Empty>No contradictions between claims in this scope.</Empty>;
  return (
    <ul className="list">
      {conflicts.map((c) => (
        <li key={c.conflict_id}>
          <div className="row" style={{ gap: 6 }}>
            <StatusBadge status={c.status} />
            <span className="small">{c.summary || 'Two claims disagree.'}</span>
            <span className="xs muted">{fmtDate(c.created_at)}</span>
          </div>
          <div className="grid grid--2" style={{ marginTop: 6, gap: 8 }}>
            {[c.claim_a, c.claim_b].map((cl, i) => (
              <div key={cl?.claim_id ?? i} className="card card--muted" style={{ padding: '8px 10px' }}>
                <div className="row" style={{ gap: 4 }}>
                  <Badge tone="outline">{i === 0 ? 'A' : 'B'}</Badge>
                  <StatusBadge status={cl?.status} />
                </div>
                <div className="small" style={{ marginTop: 4 }}>
                  <Link to={`/app/claims/${cl?.claim_id}`} style={{ color: 'inherit' }}>{cl?.text}</Link>
                </div>
              </div>
            ))}
          </div>
          {c.resolution ? <div className="xs" style={{ marginTop: 4 }}>Resolution: {typeof c.resolution === 'string' ? c.resolution : JSON.stringify(c.resolution)}</div> : null}
          {c.question_id ? (
            <div className="xs" style={{ marginTop: 4 }}>
              <Link to={`/app/questions/${c.question_id}`}>Investigation question →</Link>
            </div>
          ) : null}
          {c.status !== 'resolved' ? (
            <div className="row" style={{ marginTop: 6 }}>
              {!c.question_id ? <Button size="sm" busy={busy === c.conflict_id} onClick={() => void investigate(c)}>Investigate</Button> : null}
              {canResolve ? <Button size="sm" onClick={() => setOpenId(openId === c.conflict_id ? null : c.conflict_id)}>Resolve…</Button> : null}
            </div>
          ) : null}
          {openId === c.conflict_id ? (
            <form className="form form--inline" style={{ marginTop: 8 }} onSubmit={(e) => { e.preventDefault(); void resolve(c); }}>
              <Field label="Outcome">
                {(fid) => (
                  <Select id={fid} value={outcome} onChange={(e) => setOutcome(e.target.value as ConflictOutcome)}>
                    <option value="a_wins">A is current</option>
                    <option value="b_wins">B is current</option>
                    <option value="both_valid_scoped">Both valid in their scope</option>
                    <option value="both_retracted">Retract both</option>
                    <option value="unresolved">Leave unresolved</option>
                  </Select>
                )}
              </Field>
              <Field label="Note" required>{(fid) => <Input id={fid} required value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
              <Button type="submit" variant="primary" busy={busy === c.conflict_id}>Record resolution</Button>
            </form>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
