// Compact list renderers for goals, questions, discoveries and claims (used by workspaces and detail pages).
import { Link } from 'react-router-dom';
import type { Claim, Discovery, Goal, Loop, Question } from '../api/types';
import { ago, fmtDate, fmtPct, titleCase, truncate } from './format';
import { SupportSummary } from './Support';
import { Badge, Empty, Meter, StatusBadge } from './ui';

export function loopIsActive(loop: Loop | null | undefined): boolean {
  return Boolean(loop && loop.active_indicator && loop.active_indicator.active);
}

export function LoopBadge({ loop }: { loop: Loop | null | undefined }) {
  if (!loop) return <Badge tone="outline">no loop</Badge>;
  const active = loopIsActive(loop);
  return (
    <Badge tone={active ? 'ok' : loop.state === 'failed' || loop.state === 'blocked' || loop.state === 'budget_exhausted' ? 'danger' : loop.state === 'paused' ? 'warn' : 'info'} live={active} title={loop.explanation}>
      {active ? 'Active' : titleCase(loop.state)}
    </Badge>
  );
}

export function ProgressDisplay({ progress, compact }: { progress: Goal['progress'] | null | undefined; compact?: boolean }) {
  if (!progress || !progress.known) {
    return (
      <span className="row" style={{ gap: 6 }}>
        <Badge tone="outline">Unknown</Badge>
        {!compact && progress?.note ? <span className="xs muted">{progress.note}</span> : null}
      </span>
    );
  }
  const v = progress.value ?? 0;
  return (
    <span className="row" style={{ gap: 8, minWidth: 120 }}>
      <span style={{ flex: 1, minWidth: 60 }}>
        <Meter value={v} />
      </span>
      <span className="small nowrap">{fmtPct(v)}</span>
      {!compact ? <span className="xs muted">{progress.method ? `${progress.method}` : ''}{progress.computed_at ? ` · ${ago(progress.computed_at)}` : ''}</span> : null}
    </span>
  );
}

export function GoalList({ goals, empty = 'No goals.', showOwner = true }: { goals: Goal[]; empty?: string; showOwner?: boolean }) {
  if (!goals.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="list">
      {goals.map((g) => (
        <li key={g.goal_id}>
          <div className="item">
            <div className="item__body">
              <div className="row" style={{ gap: 6 }}>
                <Link to={`/app/goals/${g.goal_id}`} className="item__title">{g.title}</Link>
                <StatusBadge status={g.status} />
                <LoopBadge loop={g.loop} />
                {g.is_demo ? <Badge tone="outline">demo</Badge> : null}
              </div>
              <div className="item__meta">
                {showOwner ? <span>{g.owner_type === 'unit' ? 'unit' : 'owner'}: {g.owner_name}</span> : null}
                {g.scope_unit_name ? <span>scope: {g.scope_unit_name}</span> : null}
                {g.deadline ? <span>due {fmtDate(g.deadline, false)}</span> : null}
                {g.counts ? <span>{g.counts.open_questions} open q · {g.counts.discoveries} discoveries</span> : null}
                <span>priority {g.priority}</span>
              </div>
            </div>
            <div style={{ width: 160 }}>
              <ProgressDisplay progress={g.progress} compact />
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function QuestionList({ questions, empty = 'No questions.' }: { questions: Question[]; empty?: string }) {
  if (!questions.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="list">
      {questions.map((q) => (
        <li key={q.question_id}>
          <div className="item">
            <div className="item__body">
              <div className="row" style={{ gap: 6 }}>
                <Link to={`/app/questions/${q.question_id}`} className="item__title">{truncate(q.text, 160)}</Link>
                <StatusBadge status={q.status} />
                {q.needs_my_input ? <Badge tone="accent">needs my input</Badge> : null}
              </div>
              <div className="item__meta">
                <span>{titleCase(q.kind)}</span>
                {q.goal_title ? <span>goal: {q.goal_title}</span> : null}
                {q.scope_unit_name ? <span>scope: {q.scope_unit_name}</span> : null}
                <span>priority {typeof q.priority === 'number' ? q.priority.toFixed(2) : q.priority} (heuristic)</span>
                <span>{q.routes?.length ?? 0} route{(q.routes?.length ?? 0) === 1 ? '' : 's'}</span>
                <span>{ago(q.created_at)}</span>
              </div>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function DiscoveryList({ discoveries, empty = 'No discoveries.' }: { discoveries: Discovery[]; empty?: string }) {
  if (!discoveries.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="list">
      {discoveries.map((d) => (
        <li key={d.discovery_id}>
          <div className="item">
            <div className="item__body">
              <div className="row" style={{ gap: 6 }}>
                <Link to={`/app/discoveries/${d.discovery_id}`} className="item__title">{d.title}</Link>
                <StatusBadge status={d.status} />
                <Badge tone="outline">{titleCase(d.kind)}</Badge>
                <Badge tone="outline">{titleCase(d.level)}</Badge>
              </div>
              <div className="small" style={{ color: 'var(--text-2)', marginTop: 2 }}>{truncate(d.summary, 220)}</div>
              <div className="item__meta">
                {d.scope_unit_name ? <span>{d.scope_unit_name}</span> : null}
                {d.goal_title ? <span>goal: {d.goal_title}</span> : null}
                <span>{d.claim_ids?.length ?? d.claims?.length ?? 0} claims</span>
                <SupportSummary support={d.support} compact />
                <span>{ago(d.updated_at || d.created_at)}</span>
              </div>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function ClaimList({ claims, empty = 'No claims.', onSelect }: { claims: Claim[]; empty?: string; onSelect?: (c: Claim) => void }) {
  if (!claims.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="list">
      {claims.map((c) => (
        <li key={c.claim_id}>
          <div className="item">
            <div className="item__body">
              <div className="row" style={{ gap: 6 }}>
                <StatusBadge status={c.status} />
                <Badge tone="outline">{titleCase(c.kind)}</Badge>
                {c.version > 1 ? <Badge tone="outline">v{c.version}</Badge> : null}
              </div>
              <div className="small" style={{ marginTop: 3 }}>
                {onSelect ? (
                  <button type="button" className="btn btn--ghost" style={{ padding: 0, whiteSpace: 'normal', textAlign: 'left', fontWeight: 400, fontSize: 'inherit' }} onClick={() => onSelect(c)}>
                    {c.text}
                  </button>
                ) : (
                  <Link to={`/app/claims/${c.claim_id}`} style={{ color: 'inherit' }}>{c.text}</Link>
                )}
              </div>
              <div className="item__meta">
                <SupportSummary support={c.support} compact />
                {c.confidence !== null && c.confidence !== undefined ? <span>confidence {Number(c.confidence).toFixed(2)}</span> : null}
                <span>fresh {ago(c.freshness_at)}</span>
                <Link to={`/app/claims/${c.claim_id}`} className="xs">detail</Link>
              </div>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
