import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import { useSession } from '../auth/AuthProvider';
import { GoalForm } from '../components/GoalForm';
import { GoalList } from '../components/Lists';
import { Button, Checkbox, Drawer, ErrorPanel, Loading, PageHeader, Select } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useOrg } from '../components/useOrg';
import { Breadcrumbs } from '../shell/Breadcrumbs';

const STATUSES = ['', 'draft', 'active', 'paused', 'completed', 'archived'];

export function GoalsPage() {
  const me = useSession();
  const navigate = useNavigate();
  const { units } = useOrg();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState(params.get('status') ?? '');
  const [mine, setMine] = useState(params.get('mine') === '1');
  const [scope, setScope] = useState(params.get('scope_unit_id') ?? '');
  const [archived, setArchived] = useState(params.get('include_archived') === '1');
  const [showNew, setShowNew] = useState(params.get('new') === '1');
  const list = useAsync(() => api.goals.list({ status: status || undefined, mine: mine || undefined, scope_unit_id: scope || undefined, include_archived: archived || undefined, limit: 200 }), [status, mine, scope, archived]);
  useLiveRefresh(['goal.updated', 'loop.state'], () => void list.reload());

  useEffect(() => {
    const p: Record<string, string> = {};
    if (status) p.status = status;
    if (mine) p.mine = '1';
    if (scope) p.scope_unit_id = scope;
    if (archived) p.include_archived = '1';
    if (showNew) p.new = '1';
    setParams(p, { replace: true });
  }, [status, mine, scope, archived, showNew, setParams]);

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Goals' }]} />
      <PageHeader title="Goals" meta="Each goal runs its own discovery loop within a budget." actions={<Button variant="primary" onClick={() => setShowNew(true)}>New goal</Button>} />
      <div className="row" style={{ marginBottom: 12 }}>
        <Select className="select--sm" style={{ width: 'auto' }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status filter">
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s ? s : 'Any status'}</option>
          ))}
        </Select>
        <Select className="select--sm" style={{ width: 'auto', maxWidth: 240 }} value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Scope unit filter">
          <option value="">Any scope</option>
          {units.map((u) => (
            <option key={u.unit_id} value={u.unit_id}>{u.name}</option>
          ))}
        </Select>
        <Checkbox label="Mine only" checked={mine} onChange={(e) => setMine(e.target.checked)} />
        <Checkbox label="Include archived" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
        <span className="xs muted">{list.data ? `${list.data.items.length}${list.data.total !== undefined ? ` of ${list.data.total}` : ''}` : ''}</span>
      </div>
      {list.loading && !list.data ? <Loading /> : null}
      {list.error ? <ErrorPanel error={list.error} retry={() => void list.reload(false)} /> : null}
      {list.data ? <GoalList goals={list.data.items} empty={mine ? 'No goals owned by or assigned to you.' : 'No goals visible to you. Create the first one.'} /> : null}
      <Drawer open={showNew} onClose={() => setShowNew(false)} title="New goal" width={760}>
        <GoalForm
          key={me.user.user_id}
          onCancel={() => setShowNew(false)}
          onDone={(g) => {
            setShowNew(false);
            navigate(`/app/goals/${g.goal_id}`);
          }}
        />
      </Drawer>
    </div>
  );
}
