import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { UnitWorkspace } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { GoalForm } from '../components/GoalForm';
import { titleCase } from '../components/format';
import { DiscoveryList, GoalList, QuestionList } from '../components/Lists';
import { Badge, Button, Drawer, ErrorPanel, Loading, PageHeader, Section, Stat } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { ChildrenTable, ComparisonsTable, ConflictsSection, DependenciesList, MembersList, ProgressList, SynthesisList, SynthesisSummary, TaskOwnership } from '../components/WorkspaceParts';
import { Breadcrumbs } from '../shell/Breadcrumbs';

const LEAD_ROLES = new Set(['team_lead', 'department_lead', 'subsidiary_lead', 'regional_lead', 'executive']);

export function UnitWorkspacePage() {
  const { unitId = '' } = useParams();
  const me = useSession();
  const state = useAsync<UnitWorkspace>(() => api.workspace.unit(unitId), [unitId]);
  const [showNewGoal, setShowNewGoal] = useState(false);
  useLiveRefresh(
    ['goal.updated', 'loop.state', 'question.created', 'question.resolved', 'discovery.created', 'discovery.updated', 'claim.committed', 'claim.stale', 'claim.retracted', 'conflict.opened', 'conflict.resolved', 'membership.changed', 'org.changed'],
    () => void state.reload(),
  );

  const ws = state.data;
  const level = ws?.level ?? 'team';
  const isLead = me.principal.memberships.some((m) => m.unit_id === unitId && LEAD_ROLES.has(m.role)) || me.principal.is_admin || Boolean(ws?.members);
  const synth = ws?.synthesis ?? null;
  const openConflicts = (ws?.conflicts ?? []).filter((c) => c.status !== 'resolved');

  const isTeam = level === 'team';
  const isDepartment = level === 'department';
  const isUpper = level === 'subsidiary' || level === 'region' || level === 'executive';

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: ws ? `${ws.unit.name}` : '…' }]} />
      {state.loading && !ws ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {ws ? (
        <>
          <PageHeader
            title={<span className="row" style={{ gap: 8 }}>{ws.unit.name} <Badge tone="accent">{titleCase(ws.unit.type)} · {titleCase(level)} view</Badge></span>}
            meta={<>{ws.unit.member_count} members · path {ws.unit.path} {ws.unit.parent_id ? <>· <Link to={`/app/unit/${ws.unit.parent_id}`}>parent unit</Link></> : null}</>}
            actions={
              <>
                <Link to={`/app/network?view=collaboration&unit_id=${unitId}`} className="btn btn--sm">Network</Link>
                <Button size="sm" variant="primary" onClick={() => setShowNewGoal(true)}>New goal here</Button>
              </>
            }
          />
          <div className="grid grid--4" style={{ marginBottom: 20 }}>
            <Stat value={ws.goals.length} label="Goals" sub={`${ws.goals.filter((g) => g.status === 'active').length} active`} />
            <Stat value={ws.discoveries.length} label="Discoveries" sub={`${ws.discoveries.filter((d) => d.status === 'new').length} new`} />
            <Stat value={ws.open_questions.length} label="Open questions" />
            <Stat value={openConflicts.length} label="Contradictions" sub={openConflicts.length ? 'need attention' : 'none open'} />
          </div>

          {isDepartment || isUpper ? (
            <Section title={isDepartment ? 'Cross-team synthesis' : 'Aggregated view'} meta={synth?.method === 'model' ? 'model synthesis' : undefined}>
              <SynthesisSummary synthesis={synth} />
              <div className="grid grid--2" style={{ marginTop: 12 }}>
                {isDepartment ? (
                  <>
                    <div className="card"><div className="card__title">Recurring problems</div><SynthesisList items={synth?.recurring_problems} /></div>
                    <div className="card"><div className="card__title">Constraints</div><SynthesisList items={synth?.constraints} /></div>
                    <div className="card"><div className="card__title">Conflicting findings</div><SynthesisList items={synth?.conflicting_findings} /></div>
                    <div className="card"><div className="card__title">Opportunities requiring coordination</div><SynthesisList items={synth?.opportunities} /></div>
                  </>
                ) : (
                  <>
                    <div className="card"><div className="card__title">Local constraints</div><SynthesisList items={synth?.constraints} /></div>
                    <div className="card"><div className="card__title">Escalations</div><SynthesisList items={synth?.escalations} /></div>
                    <div className="card"><div className="card__title">Recurring problems</div><SynthesisList items={synth?.recurring_problems} /></div>
                    <div className="card"><div className="card__title">Decisions needed</div><SynthesisList items={synth?.decisions_needed} /></div>
                  </>
                )}
              </div>
            </Section>
          ) : null}

          {isDepartment ? (
            <Section title="Teams" meta={`${ws.children.length}`}>
              <ChildrenTable children={ws.children} />
            </Section>
          ) : null}
          {isUpper ? (
            <>
              <Section title="Comparison across units">
                <ComparisonsTable comparisons={ws.comparisons ?? []} />
              </Section>
              {ws.children.length ? (
                <Section title="Child units" meta={`${ws.children.length}`}>
                  <ChildrenTable children={ws.children} />
                </Section>
              ) : null}
            </>
          ) : null}

          <div className="split">
            <div>
              <Section title={isTeam ? 'Shared goals' : isUpper ? 'Aggregated objectives' : 'Goals'} meta={`${ws.goals.length}`}>
                <GoalList goals={ws.goals} empty="No goals in this scope." />
              </Section>
              {isTeam ? (
                <Section title="Task ownership">
                  <TaskOwnership goals={ws.goals} />
                </Section>
              ) : null}
              <Section title={isTeam ? 'Team discoveries' : 'Discoveries'} meta={`${ws.discoveries.length}`}>
                <DiscoveryList discoveries={ws.discoveries} empty="No discoveries at this level yet." />
              </Section>
              <Section title="Unresolved questions" meta={`${ws.open_questions.length}`}>
                <QuestionList questions={ws.open_questions} empty="No open questions." />
              </Section>
              <Section title="Contradictions" meta={`${openConflicts.length} open`}>
                <ConflictsSection conflicts={ws.conflicts} canResolve={isLead} onChanged={() => void state.reload()} />
              </Section>
            </div>
            <aside className="stack side-panel">
              <div className="card">
                <div className="card__title">Evidence-backed progress</div>
                <ProgressList progress={ws.progress ?? []} goals={ws.goals} />
              </div>
              <div className="card">
                <div className="card__title">Dependencies</div>
                <DependenciesList dependencies={ws.dependencies ?? []} goals={ws.goals} />
              </div>
              {isTeam && synth && synth.method !== 'none' ? (
                <div className="card">
                  <div className="card__title">Synthesis</div>
                  <p className="small">{synth.summary}</p>
                </div>
              ) : null}
              {ws.members ? (
                <div className="card">
                  <div className="card__title">Members</div>
                  <MembersList members={ws.members} />
                </div>
              ) : null}
            </aside>
          </div>
          <Drawer open={showNewGoal} onClose={() => setShowNewGoal(false)} title={`New goal in ${ws.unit.name}`} width={760}>
            {showNewGoal ? <GoalForm defaultScopeUnitId={unitId} onCancel={() => setShowNewGoal(false)} onDone={() => { setShowNewGoal(false); void state.reload(); }} /> : null}
          </Drawer>
        </>
      ) : null}
    </div>
  );
}
