import { useState, type FormEvent } from 'react';
import { api } from '../../api/client';
import { fmtDate } from '../../components/format';
import { Button, ErrorPanel, Input, Loading, Select, StatusBadge, Table } from '../../components/ui';
import { useAsync } from '../../components/useAsync';

export function AuditPage() {
  const [action, setAction] = useState('');
  const [resource, setResource] = useState('');
  const [limit, setLimit] = useState(100);
  const [applied, setApplied] = useState({ action: '', resource: '', limit: 100 });
  const state = useAsync(() => api.admin.audit({ limit: applied.limit, action: applied.action || undefined, resource_id: applied.resource || undefined }), [applied]);
  const [expanded, setExpanded] = useState<number | null>(null);

  function apply(e: FormEvent) {
    e.preventDefault();
    setApplied({ action: action.trim(), resource: resource.trim(), limit });
  }

  return (
    <div>
      <form className="row" style={{ marginBottom: 12 }} onSubmit={apply}>
        <Input className="input--sm" style={{ width: 220 }} value={action} onChange={(e) => setAction(e.target.value)} placeholder="Action prefix, e.g. grant." aria-label="Action filter" />
        <Input className="input--sm" style={{ width: 240 }} value={resource} onChange={(e) => setResource(e.target.value)} placeholder="Resource id" aria-label="Resource filter" />
        <Select className="select--sm" style={{ width: 'auto' }} value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Limit">
          {[50, 100, 250, 500].map((n) => (
            <option key={n} value={n}>{n} rows</option>
          ))}
        </Select>
        <Button type="submit" size="sm">Filter</Button>
        <Button size="sm" variant="ghost" onClick={() => void state.reload()}>Refresh</Button>
      </form>
      {state.loading && !state.data ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {state.data ? (
        <Table
          rows={state.data.items}
          rowKey={(r) => String(r.id)}
          onRowClick={(r) => setExpanded(expanded === r.id ? null : r.id)}
          empty="No audit rows match."
          caption="Audit log"
          columns={[
            { key: 'at', header: 'When', render: (r) => <span className="nowrap">{fmtDate(r.at)}</span> },
            { key: 'actor', header: 'Actor', render: (r) => <span className="xs">{r.actor_type}{r.actor_id ? <span className="mono"> {r.actor_id}</span> : null}</span> },
            { key: 'action', header: 'Action', render: (r) => <span className="mono">{r.action}</span> },
            { key: 'res', header: 'Resource', render: (r) => <span className="xs">{r.resource_type ?? ''}{r.resource_id ? <span className="mono"> {r.resource_id}</span> : null}</span> },
            { key: 'outcome', header: 'Outcome', render: (r) => <StatusBadge status={r.outcome} /> },
            {
              key: 'detail',
              header: 'Detail',
              render: (r) => (
                <span className="xs mono">
                  {r.detail ? (expanded === r.id ? <pre className="xs">{JSON.stringify(r.detail, null, 2)}</pre> : JSON.stringify(r.detail).slice(0, 100)) : '—'}
                </span>
              ),
            },
            { key: 'req', header: 'Request', render: (r) => <span className="xs mono">{r.request_id ?? '—'}</span> },
          ]}
        />
      ) : null}
    </div>
  );
}
