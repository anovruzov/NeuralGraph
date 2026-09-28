import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { useLiveRefresh } from '../../api/events';
import type { AdminOverview } from '../../api/types';
import { fmtNum, fmtUsd, titleCase } from '../../components/format';
import { Badge, ErrorPanel, Loading, Meter, Section, Stat, Table } from '../../components/ui';
import { useAsync } from '../../components/useAsync';

export function BudgetsPage() {
  const overview = useAsync<AdminOverview>(() => api.admin.overview(), []);
  const usage = useAsync(() => api.admin.usage(), []);
  useLiveRefresh(['loop.state', 'goal.updated', 'job.progress'], () => { void overview.reload(); void usage.reload(); }, 2000);
  const d = overview.data;
  if (overview.loading && !d) return <Loading />;
  if (overview.error) return <ErrorPanel error={overview.error} retry={() => void overview.reload(false)} />;
  if (!d) return null;
  const totalBudget = d.budgets.reduce((s, b) => s + (b.budget?.usd ?? 0), 0);
  const totalSpent = d.budgets.reduce((s, b) => s + (b.spent?.usd ?? 0), 0);
  return (
    <div>
      <div className="grid grid--4" style={{ marginBottom: 20 }}>
        <Stat value={fmtUsd(d.usage?.today?.usd)} label="Today" sub={`${fmtNum(d.usage?.today?.calls)} calls · ${fmtNum(d.usage?.today?.tokens)} tokens`} />
        <Stat value={fmtUsd(d.usage?.month?.usd)} label="This month" sub={`${fmtNum(d.usage?.month?.calls)} calls · ${fmtNum(d.usage?.month?.tokens)} tokens`} />
        <Stat value={fmtUsd(totalSpent)} label="Spent against goal budgets" sub={`of ${fmtUsd(totalBudget)} allocated`} />
        <Stat value={d.budgets.filter((b) => b.budget?.usd && (b.spent?.usd ?? 0) / b.budget.usd > 0.9).length} label="Goals near budget" sub="> 90% of USD budget" />
      </div>
      <Section title="Goal budgets" meta={`${d.budgets.length}`}>
        <Table
          rows={d.budgets}
          rowKey={(b) => b.goal_id}
          empty="No goals with budgets."
          caption="Goal budgets"
          columns={[
            { key: 'goal', header: 'Goal', render: (b) => <Link to={`/app/goals/${b.goal_id}`}>{b.title}</Link> },
            {
              key: 'usd',
              header: 'USD',
              render: (b) => {
                const r = b.budget?.usd ? (b.spent?.usd ?? 0) / b.budget.usd : 0;
                return (
                  <div style={{ minWidth: 160 }}>
                    <div className="xs">{fmtUsd(b.spent?.usd)} / {fmtUsd(b.budget?.usd)}</div>
                    <Meter value={r} tone={r > 0.9 ? 'danger' : r > 0.7 ? 'warn' : undefined} />
                  </div>
                );
              },
            },
            { key: 'tokens', header: 'Tokens', render: (b) => `${fmtNum(b.spent?.tokens)} / ${fmtNum(b.budget?.tokens)}` },
            { key: 'q', header: 'Questions', render: (b) => `${fmtNum(b.spent?.questions)} / ${fmtNum(b.budget?.questions)}` },
            { key: 'depth', header: 'Follow-up depth', num: true, render: (b) => b.budget?.followup_depth ?? '—' },
            {
              key: 'loop',
              header: 'Loop',
              render: (b) => {
                const l = d.loops.find((x) => x.goal_id === b.goal_id);
                return l ? <Badge tone={l.state === 'budget_exhausted' ? 'danger' : 'outline'}>{titleCase(l.state)}</Badge> : '—';
              },
            },
          ]}
        />
      </Section>
      <Section title="Usage by tier">
        <Table
          rows={Object.entries(d.usage?.by_tier ?? {}).map(([tier, u]) => ({ tier, ...u }))}
          rowKey={(r) => r.tier}
          empty="No model calls recorded."
          columns={[
            { key: 'tier', header: 'Tier', render: (r) => titleCase(r.tier) },
            { key: 'calls', header: 'Calls', num: true, render: (r) => fmtNum(r.calls) },
            { key: 'tokens', header: 'Tokens', num: true, render: (r) => fmtNum(r.tokens) },
            { key: 'usd', header: 'USD', num: true, render: (r) => fmtUsd(r.usd) },
          ]}
        />
      </Section>
      <Section title="Usage ledger" meta={usage.data ? `${usage.data.items.length} rows` : undefined}>
        {usage.error ? <ErrorPanel error={usage.error} /> : null}
        {usage.data ? (
          <Table
            rows={usage.data.items.slice(0, 200)}
            rowKey={(r, ) => JSON.stringify(r)}
            empty="No usage rows."
            columns={[
              { key: 'at', header: 'When', render: (r) => String(r.at ?? r.day ?? '—') },
              { key: 'task', header: 'Task', render: (r) => String(r.task ?? '—') },
              { key: 'tier', header: 'Tier', render: (r) => String(r.tier ?? '—') },
              { key: 'model', header: 'Model', render: (r) => <span className="mono xs">{String(r.provider ?? '')}{r.model ? `:${String(r.model)}` : ''}</span> },
              { key: 'calls', header: 'Calls', num: true, render: (r) => fmtNum(Number(r.calls ?? 0)) },
              { key: 'tokens', header: 'Tokens', num: true, render: (r) => fmtNum(Number(r.tokens ?? 0)) },
              { key: 'usd', header: 'USD', num: true, render: (r) => fmtUsd(Number(r.usd ?? 0)) },
            ]}
          />
        ) : null}
      </Section>
    </div>
  );
}
