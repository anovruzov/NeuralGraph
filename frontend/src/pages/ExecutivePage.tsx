import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { ExecutiveWorkspace } from '../api/types';
import { ClaimList, DiscoveryList, GoalList, QuestionList } from '../components/Lists';
import { Badge, Empty, ErrorPanel, Loading, PageHeader, Section, Stat } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { ComparisonsTable, ConflictsSection, SynthesisList, SynthesisSummary } from '../components/WorkspaceParts';
import { Breadcrumbs } from '../shell/Breadcrumbs';

export function ExecutivePage() {
  const state = useAsync<ExecutiveWorkspace>(() => api.workspace.executive(), []);
  useLiveRefresh(['goal.updated', 'discovery.created', 'discovery.updated', 'claim.committed', 'claim.stale', 'claim.retracted', 'conflict.opened', 'conflict.resolved', 'question.created', 'question.resolved'], () => void state.reload());
  const ws = state.data;
  const synth = ws?.synthesis ?? null;
  const openConflicts = (ws?.conflicts ?? []).filter((c) => c.status !== 'resolved');

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Executive' }]} />
      <PageHeader title="Executive workspace" meta={ws ? `${ws.unit.name} · organizational objectives, strategic discoveries, material uncertainties and decisions` : undefined} actions={ws ? <Link to={`/app/unit/${ws.unit.unit_id}`} className="btn btn--sm">Root unit</Link> : null} />
      {state.loading && !ws ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {ws ? (
        <>
          <div className="grid grid--4" style={{ marginBottom: 20 }}>
            <Stat value={ws.goals.length} label="Organizational objectives" sub={`${ws.goals.filter((g) => g.status === 'active').length} active`} />
            <Stat value={ws.strategic?.length ?? 0} label="Strategic discoveries" />
            <Stat value={ws.material_uncertainties?.length ?? 0} label="Material uncertainties" sub="hypothesis or stale claims" />
            <Stat value={(ws.decisions?.length ?? 0) + openConflicts.length} label="Decisions requiring attention" />
          </div>
          <Section title="Synthesis" meta={synth?.method === 'model' ? 'model synthesis' : undefined}>
            <SynthesisSummary synthesis={synth} />
          </Section>
          <div className="split split--wide">
            <div>
              <Section title="Decisions requiring attention" meta={`${ws.decisions?.length ?? 0}`}>
                {ws.decisions?.length ? (
                  <ul className="list">
                    {ws.decisions.map((d, i) => (
                      <li key={i}>
                        <div className="row" style={{ gap: 6 }}>
                          {d.kind ? <Badge tone="warn">{String(d.kind)}</Badge> : <Badge tone="warn">decision</Badge>}
                          <span>{d.text}</span>
                        </div>
                        <div className="row" style={{ gap: 4, marginTop: 4 }}>
                          {d.discovery_ids?.map((id) => (
                            <Link key={id} to={`/app/discoveries/${id}`} className="chip"><span className="chip__type">disc</span><span className="chip__label">{id.slice(0, 14)}</span></Link>
                          ))}
                          {d.claim_ids?.map((id) => (
                            <Link key={id} to={`/app/claims/${id}`} className="chip"><span className="chip__type">claim</span><span className="chip__label">{id.slice(0, 14)}</span></Link>
                          ))}
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>No decisions are flagged.</Empty>
                )}
                {synth?.decisions_needed?.length ? (
                  <div style={{ marginTop: 8 }}>
                    <div className="card__title">From synthesis</div>
                    <SynthesisList items={synth.decisions_needed} />
                  </div>
                ) : null}
              </Section>
              <Section title="Strategic discoveries" meta={`${ws.strategic?.length ?? 0}`}>
                <DiscoveryList discoveries={ws.strategic ?? []} empty="No discoveries have reached the executive level." />
              </Section>
              <Section title="Material uncertainties" meta={`${ws.material_uncertainties?.length ?? 0}`}>
                <ClaimList claims={ws.material_uncertainties ?? []} empty="No material uncertainties." />
                {synth?.material_uncertainties?.length ? (
                  <div style={{ marginTop: 8 }}>
                    <SynthesisList items={synth.material_uncertainties} />
                  </div>
                ) : null}
              </Section>
              <Section title="Organizational objectives" meta={`${ws.goals.length}`}>
                <GoalList goals={ws.goals} empty="No organizational objectives yet." />
              </Section>
              <Section title="Contradictions" meta={`${openConflicts.length} open`}>
                <ConflictsSection conflicts={ws.conflicts} canResolve onChanged={() => void state.reload()} />
              </Section>
              <Section title="Open questions" meta={`${ws.open_questions.length}`}>
                <QuestionList questions={ws.open_questions} empty="No open questions at the executive level." />
              </Section>
            </div>
            <aside className="stack side-panel">
              <div className="card">
                <div className="card__title">Units compared</div>
                <ComparisonsTable comparisons={ws.comparisons ?? []} />
              </div>
              <div className="card">
                <div className="card__title">Escalations</div>
                <SynthesisList items={synth?.escalations} empty="No escalations." />
              </div>
              <div className="card">
                <div className="card__title">Recurring problems</div>
                <SynthesisList items={synth?.recurring_problems} />
              </div>
              <div className="card">
                <div className="card__title">Opportunities</div>
                <SynthesisList items={synth?.opportunities} />
              </div>
            </aside>
          </div>
        </>
      ) : null}
    </div>
  );
}
