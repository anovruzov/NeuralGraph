import { api } from '../../api/client';
import type { AdminOverview } from '../../api/types';
import { fmtDuration, fmtNum } from '../../components/format';
import { Badge, ErrorPanel, Loading, Section, StatusBadge } from '../../components/ui';
import { useAsync } from '../../components/useAsync';

export function DeploymentPage() {
  const overview = useAsync<AdminOverview>(() => api.admin.overview(), []);
  const health = useAsync(() => api.health.healthz(), []);
  const ready = useAsync(() => api.health.readyz().catch((err: unknown) => ({ ok: false, db: false, transport: String(err), worker_heartbeat_age_seconds: null, migrations_pending: true })), []);
  const d = overview.data;
  if (overview.loading && !d) return <Loading />;
  if (overview.error) return <ErrorPanel error={overview.error} retry={() => void overview.reload(false)} />;
  if (!d) return null;
  const dep = d.deployment;
  const pending = dep?.migrations?.pending;
  const pendingList = Array.isArray(pending) ? pending : [];
  const pendingCount = Array.isArray(pending) ? pending.length : Number(pending ?? 0);
  return (
    <div className="grid grid--2">
      <div className="card">
        <div className="card__title">Service</div>
        <dl className="kv">
          <dt>Version</dt>
          <dd>{dep?.version ?? health.data?.version ?? '—'}</dd>
          <dt>Uptime</dt>
          <dd>{fmtDuration(dep?.uptime_seconds)}</dd>
          <dt>Transport</dt>
          <dd>{typeof dep?.transport === 'string' ? dep.transport : <pre className="xs">{JSON.stringify(dep?.transport, null, 2)}</pre>}</dd>
          <dt>Liveness</dt>
          <dd>{health.data ? <StatusBadge status={health.data.ok ? 'ok' : 'error'} /> : health.error ? <StatusBadge status="error" /> : '…'}</dd>
          <dt>Readiness</dt>
          <dd>
            {ready.data ? (
              <span className="row" style={{ gap: 4 }}>
                <StatusBadge status={ready.data.ok ? 'ok' : 'error'} />
                <Badge tone={ready.data.db ? 'ok' : 'danger'}>db</Badge>
                <Badge tone={ready.data.transport ? 'ok' : 'danger'}>transport</Badge>
                <span className="xs muted">worker heartbeat {ready.data.worker_heartbeat_age_seconds ?? '—'}s</span>
              </span>
            ) : '…'}
          </dd>
          <dt>Metrics</dt>
          <dd><a href="/metrics" target="_blank" rel="noreferrer">/metrics</a> (Prometheus text)</dd>
        </dl>
      </div>
      <div className="card">
        <div className="card__title">Migrations</div>
        <dl className="kv">
          <dt>Current</dt>
          <dd>{String(dep?.migrations?.current ?? '—')}</dd>
          <dt>Latest</dt>
          <dd>{String(dep?.migrations?.latest ?? '—')}</dd>
          <dt>Pending</dt>
          <dd>{pendingCount ? <Badge tone="danger">{pendingCount} pending — run `python -m mycelic migrate`</Badge> : <Badge tone="ok">none</Badge>}</dd>
        </dl>
        {pendingList.length ? (
          <ul className="list list--tight" style={{ marginTop: 8 }}>
            {pendingList.map((m) => (
              <li key={m} className="mono xs">{m}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="card" style={{ gridColumn: '1 / -1' }}>
        <div className="card__title">Effective settings</div>
        <div className="xs muted" style={{ marginBottom: 6 }}>Secrets are redacted by the server; provider keys are never returned.</div>
        {Object.keys(dep?.settings ?? {}).length ? (
          <dl className="kv">
            {Object.entries(dep.settings).map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}>
                <dt className="mono">{k}</dt>
                <dd className="mono xs">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <div className="xs muted">No settings reported.</div>
        )}
      </div>
      <Section title="Queue">
        <dl className="kv">
          <dt>Backlog</dt>
          <dd>{fmtNum(d.metrics?.queue_backlog)}</dd>
          <dt>Queued / leased</dt>
          <dd>{fmtNum(d.jobs.queued)} / {fmtNum(d.jobs.leased)}</dd>
          <dt>Failed / dead</dt>
          <dd>{fmtNum(d.jobs.failed)} / {fmtNum(d.jobs.dead)}</dd>
          <dt>Workers</dt>
          <dd>{d.workers.length}</dd>
        </dl>
      </Section>
    </div>
  );
}
