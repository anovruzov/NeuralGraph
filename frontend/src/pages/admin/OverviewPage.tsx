import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { useLiveRefresh } from '../../api/events';
import type { AdminOverview, Job } from '../../api/types';
import { ago, agoSeconds, fmtDuration, fmtNum, fmtUsd, titleCase } from '../../components/format';
import { LoopBadge } from '../../components/Lists';
import { Badge, Button, Empty, ErrorPanel, Loading, Meter, Section, Stat, StatusBadge, Table } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { useToast } from '../../components/Toast';

export function WorkersTable({ workers }: { workers: AdminOverview['workers'] }) {
  return (
    <Table
      rows={workers}
      rowKey={(w) => w.worker_id}
      empty="No worker has reported a heartbeat. Start `mycelic worker` or enable the in-process worker."
      caption="Workers"
      columns={[
        { key: 'id', header: 'Worker', render: (w) => <span className="mono">{w.worker_id}</span> },
        { key: 'role', header: 'Role', render: (w) => w.role },
        { key: 'host', header: 'Host', render: (w) => w.hostname },
        {
          key: 'hb',
          header: 'Heartbeat',
          render: (w) => (
            <Badge tone={w.heartbeat_age_seconds === null ? 'neutral' : w.heartbeat_age_seconds < 60 ? 'ok' : w.heartbeat_age_seconds < 300 ? 'warn' : 'danger'}>
              {agoSeconds(w.heartbeat_age_seconds)}
            </Badge>
          ),
        },
        { key: 'busy', header: 'State', render: (w) => (w.busy ? <Badge tone="info">busy</Badge> : <Badge tone="outline">idle</Badge>) },
        { key: 'stats', header: 'Stats', render: (w) => <span className="xs mono">{Object.entries(w.stats ?? {}).map(([k, v]) => `${k}=${String(v)}`).join(' ') || '—'}</span> },
      ]}
    />
  );
}

export function FailedJobsTable({ jobs, onChanged }: { jobs: Job[]; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  async function retry(j: Job) {
    setBusy(j.job_id);
    try {
      await api.admin.retryJob(j.job_id);
      toast.push('Job re-queued', 'ok');
      onChanged();
    } catch (err) {
      toast.error(err, 'Retry failed');
    } finally {
      setBusy(null);
    }
  }
  return (
    <Table
      rows={jobs}
      rowKey={(j) => j.job_id}
      empty="No failed or dead jobs."
      caption="Failed jobs"
      columns={[
        { key: 'kind', header: 'Kind', render: (j) => j.kind },
        { key: 'status', header: 'Status', render: (j) => <StatusBadge status={j.status} /> },
        { key: 'attempts', header: 'Attempts', num: true, render: (j) => `${j.attempts ?? '—'}${j.max_attempts ? `/${j.max_attempts}` : ''}` },
        { key: 'err', header: 'Last error', render: (j) => <span className="xs mono">{(j.last_error ?? j.error ?? '—').toString().slice(0, 160)}</span> },
        { key: 'at', header: 'Updated', render: (j) => ago(j.updated_at ?? j.created_at) },
        { key: 'a', header: '', render: (j) => <Button size="sm" busy={busy === j.job_id} onClick={() => void retry(j)}>Retry</Button> },
      ]}
    />
  );
}

export function AdminOverviewPage() {
  const toast = useToast();
  const state = useAsync<AdminOverview>(() => api.admin.overview(), []);
  const [retryingDead, setRetryingDead] = useState(false);
  useLiveRefresh(['job.progress', 'holder.status', 'loop.state', 'goal.updated'], () => void state.reload(), 1500);
  const d = state.data;

  async function retryDead() {
    setRetryingDead(true);
    try {
      const res = await api.admin.retryDead();
      toast.push(`${res.retried ?? ''} dead job(s) re-queued`, 'ok');
      void state.reload();
    } catch (err) {
      toast.error(err, 'Could not retry dead jobs');
    } finally {
      setRetryingDead(false);
    }
  }

  if (state.loading && !d) return <Loading />;
  if (state.error) return <ErrorPanel error={state.error} retry={() => void state.reload(false)} />;
  if (!d) return null;
  const pending = d.deployment?.migrations?.pending;
  const pendingCount = Array.isArray(pending) ? pending.length : Number(pending ?? 0);
  return (
    <div>
      <div className="grid grid--4" style={{ marginBottom: 20 }}>
        <Stat value={d.workers.length} label="Workers" sub={d.workers.length ? `freshest heartbeat ${agoSeconds(Math.min(...d.workers.map((w) => w.heartbeat_age_seconds ?? Infinity)))}` : 'none reporting'} />
        <Stat value={fmtNum(d.jobs.queued + d.jobs.leased)} label="Jobs in flight" sub={`${d.jobs.queued} queued · ${d.jobs.leased} leased · ${d.jobs.done} done`} />
        <Stat value={<span style={{ color: d.jobs.failed + d.jobs.dead ? 'var(--danger)' : undefined }}>{fmtNum(d.jobs.failed + d.jobs.dead)}</span>} label="Failed / dead" sub={`${d.jobs.failed} failed · ${d.jobs.dead} dead`} />
        <Stat value={fmtUsd(d.usage?.today?.usd)} label="Model spend today" sub={`${fmtNum(d.usage?.today?.calls)} calls · ${fmtNum(d.usage?.today?.tokens)} tokens · month ${fmtUsd(d.usage?.month?.usd)}`} />
      </div>
      <div className="grid grid--3" style={{ marginBottom: 20 }}>
        <div className="card">
          <div className="card__title">Deployment</div>
          <dl className="kv">
            <dt>Version</dt>
            <dd>{d.deployment?.version}</dd>
            <dt>Uptime</dt>
            <dd>{fmtDuration(d.deployment?.uptime_seconds)}</dd>
            <dt>Transport</dt>
            <dd>{typeof d.deployment?.transport === 'string' ? d.deployment.transport : JSON.stringify(d.deployment?.transport)}</dd>
            <dt>Migrations</dt>
            <dd>
              {String(d.deployment?.migrations?.current ?? '—')} / {String(d.deployment?.migrations?.latest ?? '—')}{' '}
              {pendingCount ? <Badge tone="danger">{pendingCount} pending</Badge> : <Badge tone="ok">up to date</Badge>}
            </dd>
          </dl>
          <Link to="/app/admin/deployment" className="xs">Details →</Link>
        </div>
        <div className="card">
          <div className="card__title">Metrics</div>
          <dl className="kv">
            <dt>Queue backlog</dt>
            <dd>{fmtNum(d.metrics?.queue_backlog)}</dd>
            <dt>Failed routes</dt>
            <dd>{fmtNum(d.metrics?.failed_routes)}</dd>
            <dt>Question outcomes</dt>
            <dd className="xs">{Object.entries(d.metrics?.question_outcomes ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}</dd>
            <dt>Evidence freshness</dt>
            <dd>
              {fmtNum(d.metrics?.evidence_freshness?.fresh)} fresh · {fmtNum(d.metrics?.evidence_freshness?.stale)} stale · median {d.metrics?.evidence_freshness?.median_age_days ?? '—'}d
            </dd>
          </dl>
        </div>
        <div className="card">
          <div className="card__title">Usage by tier</div>
          {Object.keys(d.usage?.by_tier ?? {}).length ? (
            <dl className="kv">
              {Object.entries(d.usage.by_tier).map(([tier, u]) => (
                <div key={tier} style={{ display: 'contents' }}>
                  <dt>{titleCase(tier)}</dt>
                  <dd>{fmtNum(u.calls)} calls · {fmtNum(u.tokens)} tokens · {fmtUsd(u.usd)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <div className="xs muted">No model calls recorded.</div>
          )}
        </div>
      </div>
      <Section title="Workers">
        <WorkersTable workers={d.workers} />
      </Section>
      <Section title="Failed jobs" meta={`${d.failed_jobs.length}`} actions={d.jobs.dead ? <Button size="sm" busy={retryingDead} onClick={() => void retryDead()}>Retry all dead</Button> : null}>
        <FailedJobsTable jobs={d.failed_jobs} onChanged={() => void state.reload()} />
      </Section>
      <div className="grid grid--2">
        <Section title="Holders health" meta={`${d.holders.length}`}>
          <Table
            rows={d.holders}
            rowKey={(h) => h.holder_id}
            empty="No holders registered."
            columns={[
              { key: 'name', header: 'Holder', render: (h) => <span>{h.name} <span className="xs muted">({h.owner_name})</span></span> },
              { key: 'status', header: 'Status', render: (h) => <StatusBadge status={h.status} /> },
              { key: 'mode', header: 'Mode', render: (h) => h.mode },
              { key: 'hb', header: 'Heartbeat', render: (h) => ago(h.last_heartbeat_at) },
              { key: 'docs', header: 'Docs', num: true, render: (h) => fmtNum(h.stats?.documents) },
            ]}
          />
        </Section>
        <Section title="Budgets" meta={`${d.budgets.length} goals`}>
          {d.budgets.length ? (
            <ul className="list list--tight">
              {d.budgets.slice(0, 12).map((b) => {
                const ratio = b.budget?.usd ? (b.spent?.usd ?? 0) / b.budget.usd : 0;
                return (
                  <li key={b.goal_id}>
                    <div className="row row--between small">
                      <Link to={`/app/goals/${b.goal_id}`}>{b.title}</Link>
                      <span className="xs muted">{fmtUsd(b.spent?.usd)} / {fmtUsd(b.budget?.usd)}</span>
                    </div>
                    <Meter value={ratio} tone={ratio > 0.9 ? 'danger' : ratio > 0.7 ? 'warn' : undefined} />
                  </li>
                );
              })}
            </ul>
          ) : (
            <Empty>No goal budgets.</Empty>
          )}
          <Link to="/app/admin/budgets" className="xs">All budgets →</Link>
        </Section>
      </div>
      <Section title="Loops" meta={`${d.loops.length}`}>
        <Table
          rows={d.loops}
          rowKey={(l) => l.goal_id}
          empty="No loops."
          columns={[
            { key: 'goal', header: 'Goal', render: (l) => <Link to={`/app/goals/${l.goal_id}`}>{d.budgets.find((b) => b.goal_id === l.goal_id)?.title ?? l.goal_id}</Link> },
            { key: 'state', header: 'State', render: (l) => <LoopBadge loop={l} /> },
            { key: 'desired', header: 'Desired', render: (l) => titleCase(l.desired) },
            { key: 'expl', header: 'Explanation', render: (l) => <span className="xs">{l.explanation}</span> },
            { key: 'hb', header: 'Heartbeat', render: (l) => agoSeconds(l.active_indicator?.heartbeat_age_seconds) },
            { key: 'runs', header: 'Runs', num: true, render: (l) => l.run_count },
            { key: 'next', header: 'Next check', render: (l) => ago(l.next_check_at) },
          ]}
        />
      </Section>
    </div>
  );
}
