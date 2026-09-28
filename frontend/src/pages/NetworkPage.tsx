import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { NetworkNode, NetworkView } from '../api/types';
import { titleCase } from '../components/format';
import { NetworkGraph, NetworkList, NodeConnections } from '../components/NetworkGraph';
import { Empty, ErrorPanel, Loading, PageHeader, Select, Toggle } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useOrg } from '../components/useOrg';
import { Breadcrumbs } from '../shell/Breadcrumbs';

const VIEWS: NetworkView[] = ['hierarchy', 'collaboration', 'lineage'];

export function NetworkPage() {
  const [params, setParams] = useSearchParams();
  const { units } = useOrg();
  const view = (VIEWS.includes(params.get('view') as NetworkView) ? params.get('view') : 'hierarchy') as NetworkView;
  const goalId = params.get('goal_id') ?? '';
  const unitId = params.get('unit_id') ?? '';
  const [mode, setMode] = useState<'graph' | 'list'>('graph');
  const [selected, setSelected] = useState<NetworkNode | null>(null);
  const [typeFilter, setTypeFilter] = useState('');

  const goals = useAsync(() => api.goals.list({ limit: 200 }), []);
  const graph = useAsync(() => api.org.network({ view, goal_id: goalId || undefined, unit_id: unitId || undefined }), [view, goalId, unitId]);
  useLiveRefresh(['org.changed', 'membership.changed', 'claim.committed', 'discovery.created', 'question.routed', 'question.responded', 'conflict.opened'], () => void graph.reload());

  function set(next: Record<string, string>) {
    const p: Record<string, string> = { view, ...(goalId ? { goal_id: goalId } : {}), ...(unitId ? { unit_id: unitId } : {}), ...next };
    for (const k of Object.keys(p)) if (!p[k]) delete p[k];
    setParams(p, { replace: true });
    setSelected(null);
  }

  const data = graph.data;
  const types = useMemo(() => [...new Set((data?.nodes ?? []).map((n) => n.type))].sort(), [data]);
  const filtered = useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    if (!typeFilter) return data;
    const keep = new Set(data.nodes.filter((n) => n.type === typeFilter).map((n) => n.id));
    // keep neighbours so the filtered type stays in context
    for (const e of data.edges) {
      if (keep.has(e.source)) keep.add(e.target);
      if (keep.has(e.target)) keep.add(e.source);
    }
    return { nodes: data.nodes.filter((n) => keep.has(n.id)), edges: data.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) };
  }, [data, typeFilter]);

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Network' }]} />
      <PageHeader
        title="Network"
        meta="Hierarchy (units, people, holders), collaboration (who answered what) and lineage (goal → questions → claims → evidence). Filtered by what you may see."
        actions={<Toggle value={mode} onChange={setMode} ariaLabel="Display" options={[{ value: 'graph', label: 'Graph' }, { value: 'list', label: 'List' }]} />}
      />
      <div className="row" style={{ marginBottom: 12 }}>
        <Toggle value={view} onChange={(v) => set({ view: v })} ariaLabel="View" options={VIEWS.map((v) => ({ value: v, label: titleCase(v) }))} />
        <Select className="select--sm" style={{ width: 'auto', maxWidth: 240 }} value={unitId} onChange={(e) => set({ unit_id: e.target.value })} aria-label="Unit filter">
          <option value="">All units</option>
          {units.map((u) => (
            <option key={u.unit_id} value={u.unit_id}>{u.name}</option>
          ))}
        </Select>
        {view === 'lineage' ? (
          <Select className="select--sm" style={{ width: 'auto', maxWidth: 280 }} value={goalId} onChange={(e) => set({ goal_id: e.target.value })} aria-label="Goal filter">
            <option value="">All goals</option>
            {(goals.data?.items ?? []).map((g) => (
              <option key={g.goal_id} value={g.goal_id}>{g.title}</option>
            ))}
          </Select>
        ) : null}
        <Select className="select--sm" style={{ width: 'auto' }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="Node type filter">
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>{titleCase(t)}</option>
          ))}
        </Select>
        <span className="xs muted">{data ? `${filtered.nodes.length} nodes · ${filtered.edges.length} edges` : ''}</span>
      </div>
      {graph.loading && !data ? <Loading label="Laying out the network…" /> : null}
      {graph.error ? <ErrorPanel error={graph.error} retry={() => void graph.reload(false)} /> : null}
      {data ? (
        <div className="network">
          <div>
            {mode === 'graph' ? (
              <NetworkGraph nodes={filtered.nodes} edges={filtered.edges} selectedId={selected?.id ?? null} onSelect={setSelected} />
            ) : (
              <NetworkList nodes={filtered.nodes} edges={filtered.edges} onSelect={setSelected} />
            )}
          </div>
          <aside className="card side-panel" aria-live="polite">
            {selected ? <NodeConnections node={selected} nodes={filtered.nodes} edges={filtered.edges} /> : <Empty>Select a node to see its connections.</Empty>}
          </aside>
        </div>
      ) : null}
    </div>
  );
}
