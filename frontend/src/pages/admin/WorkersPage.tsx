import { useState } from 'react';
import { api } from '../../api/client';
import { useLiveRefresh } from '../../api/events';
import type { Job, Worker } from '../../api/types';
import { ago } from '../../components/format';
import { Button, ErrorPanel, Loading, Section, Select, StatusBadge, Table } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { useToast } from '../../components/Toast';
import { WorkersTable } from './OverviewPage';

const JOB_STATUSES = ['', 'queued', 'leased', 'done', 'failed', 'dead'];

export function WorkersPage() {
  const toast = useToast();
  const workers = useAsync(async () => {
    const res = await api.admin.workers();
    return ('workers' in res ? res.workers : res.items) as Worker[];
  }, []);
  const [status, setStatus] = useState('');
  const [kind, setKind] = useState('');
  const jobs = useAsync(() => api.admin.jobs({ status: status || undefined, kind: kind || undefined, limit: 200 }), [status, kind]);
  const [busy, setBusy] = useState<string | null>(null);
  useLiveRefresh(['job.progress'], () => { void workers.reload(); void jobs.reload(); }, 1500);

  async function retry(j: Job) {
    setBusy(j.job_id);
    try {
      await api.admin.retryJob(j.job_id);
      toast.push('Job re-queued', 'ok');
      void jobs.reload();
    } catch (err) {
      toast.error(err, 'Retry failed');
    } finally {
      setBusy(null);
    }
  }

  const kinds = [...new Set((jobs.data?.items ?? []).map((j) => j.kind))].sort();
  return (
    <div>
      <Section title="Workers" meta={workers.data ? `${workers.data.length}` : undefined}>
        {workers.loading && !workers.data ? <Loading /> : null}
        {workers.error ? <ErrorPanel error={workers.error} retry={() => void workers.reload(false)} /> : null}
        {workers.data ? <WorkersTable workers={workers.data} /> : null}
      </Section>
      <Section
        title="Jobs"
        meta={jobs.data ? `${jobs.data.items.length}${jobs.data.total !== undefined ? ` of ${jobs.data.total}` : ''}` : undefined}
        actions={
          <>
            <Select className="select--sm" style={{ width: 'auto' }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Job status">
              {JOB_STATUSES.map((s) => (
                <option key={s} value={s}>{s || 'Any status'}</option>
              ))}
            </Select>
            <Select className="select--sm" style={{ width: 'auto' }} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Job kind">
              <option value="">Any kind</option>
              {kinds.map((k) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </Select>
          </>
        }
      >
        {jobs.loading && !jobs.data ? <Loading /> : null}
        {jobs.error ? <ErrorPanel error={jobs.error} retry={() => void jobs.reload(false)} /> : null}
        {jobs.data ? (
          <Table
            rows={jobs.data.items}
            rowKey={(j) => j.job_id}
            empty="No jobs match."
            caption="Jobs"
            columns={[
              { key: 'id', header: 'Job', render: (j) => <span className="mono xs">{j.job_id}</span> },
              { key: 'kind', header: 'Kind', render: (j) => j.kind },
              { key: 'status', header: 'Status', render: (j) => <StatusBadge status={j.status} /> },
              { key: 'attempts', header: 'Attempts', num: true, render: (j) => `${j.attempts ?? '—'}${j.max_attempts ? `/${j.max_attempts}` : ''}` },
              { key: 'worker', header: 'Leased by', render: (j) => <span className="xs mono">{j.leased_by ?? '—'}</span> },
              { key: 'run', header: 'Run at', render: (j) => ago(j.run_at ?? j.created_at) },
              { key: 'err', header: 'Error', render: (j) => <span className="xs mono">{(j.last_error ?? j.error ?? '').toString().slice(0, 120) || '—'}</span> },
              { key: 'a', header: '', render: (j) => (j.status === 'failed' || j.status === 'dead' ? <Button size="sm" busy={busy === j.job_id} onClick={() => void retry(j)}>Retry</Button> : null) },
            ]}
          />
        ) : null}
      </Section>
    </div>
  );
}
