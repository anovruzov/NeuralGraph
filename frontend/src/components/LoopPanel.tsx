import { useState } from 'react';
import { api } from '../api/client';
import type { Loop, LoopAction } from '../api/types';
import { agoSeconds, fmtDate, fmtNum, fmtUsd, titleCase } from './format';
import { loopIsActive } from './Lists';
import { Badge, Button, Empty } from './ui';
import { useToast } from './Toast';

/** Discovery loop panel: real state, explanation, Active indicator only when active_indicator.active, heartbeat age, budget remaining. */
export function LoopPanel({ goalId, loop, onChange, canManage = true }: { goalId: string; loop: Loop | null | undefined; onChange: (loop: Loop) => void; canManage?: boolean }) {
  const toast = useToast();
  const [busy, setBusy] = useState<LoopAction | null>(null);

  async function act(action: LoopAction) {
    setBusy(action);
    try {
      const res = await api.goals.loopAction(goalId, action);
      onChange(res.loop);
      toast.push(`Loop: ${titleCase(action)} requested`, 'ok');
    } catch (err) {
      toast.error(err, `Could not ${action} the loop`);
    } finally {
      setBusy(null);
    }
  }

  if (!loop) {
    return (
      <div className="stack stack--sm">
        <Empty>No discovery loop yet. Activating the goal starts one.</Empty>
        {canManage ? (
          <div className="row">
            <Button variant="primary" size="sm" busy={busy === 'start'} onClick={() => void act('start')}>Start</Button>
          </div>
        ) : null}
      </div>
    );
  }
  const active = loopIsActive(loop);
  const hb = loop.active_indicator?.heartbeat_age_seconds;
  const remaining = loop.budget_remaining;
  const desiredMismatch = loop.desired === 'active' && !active && loop.state !== 'waiting';
  return (
    <div className="stack">
      <div className="loop-state">
        {active ? (
          <Badge tone="ok" live>Active</Badge>
        ) : (
          <Badge tone={loop.state === 'failed' || loop.state === 'blocked' || loop.state === 'budget_exhausted' ? 'danger' : loop.state === 'paused' || loop.state === 'stopped' ? 'warn' : 'info'}>{titleCase(loop.state)}</Badge>
        )}
        <span className="xs muted">desired: {titleCase(loop.desired)}</span>
        {desiredMismatch ? <Badge tone="warn" title="What people asked for differs from what the worker observes">desired ≠ observed</Badge> : null}
      </div>
      {loop.explanation ? <div className="loop-state__explanation">{loop.explanation}</div> : null}
      <dl className="kv">
        <dt>Worker heartbeat</dt>
        <dd>
          {hb === null || hb === undefined ? 'no heartbeat' : agoSeconds(hb)}
          {loop.active_indicator?.worker_id ? <span className="xs muted"> · {loop.active_indicator.worker_id}</span> : null}
        </dd>
        <dt>Last run</dt>
        <dd>{fmtDate(loop.last_run_at)}</dd>
        <dt>Next check</dt>
        <dd>{fmtDate(loop.next_check_at)}</dd>
        <dt>Runs</dt>
        <dd>{fmtNum(loop.run_count)}</dd>
        <dt>Asked / found</dt>
        <dd>{fmtNum(loop.stats?.questions_asked)} questions · {fmtNum(loop.stats?.discoveries)} discoveries</dd>
        <dt>Spent</dt>
        <dd>{fmtNum(loop.stats?.tokens)} tokens · {fmtUsd(loop.stats?.usd)}</dd>
        <dt>Budget remaining</dt>
        <dd>
          {remaining ? (
            <>
              {fmtNum(remaining.tokens)} tokens · {fmtUsd(remaining.usd)} · {fmtNum(remaining.questions)} questions
            </>
          ) : '—'}
        </dd>
      </dl>
      {canManage ? (
        <div className="row">
          {loop.state === 'paused' || loop.desired === 'paused' ? (
            <Button size="sm" busy={busy === 'resume'} onClick={() => void act('resume')}>Resume</Button>
          ) : (
            <Button size="sm" busy={busy === 'pause'} onClick={() => void act('pause')} disabled={loop.desired === 'stopped'}>Pause</Button>
          )}
          <Button size="sm" busy={busy === 'run_now'} onClick={() => void act('run_now')} title="Enqueue an immediate tick">Run now</Button>
          {loop.desired === 'stopped' || loop.state === 'stopped' || loop.state === 'completed' ? (
            <Button size="sm" variant="primary" busy={busy === 'start'} onClick={() => void act('start')}>Start</Button>
          ) : (
            <Button size="sm" variant="danger" busy={busy === 'stop'} onClick={() => void act('stop')}>Stop</Button>
          )}
        </div>
      ) : null}
    </div>
  );
}
