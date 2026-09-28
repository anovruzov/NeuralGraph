import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { Goal, OnboardingSteps } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { Button, Loading, PageHeader } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

interface StepDef {
  key: keyof OnboardingSteps;
  title: string;
  text: string;
  to: string;
  cta: string;
}

const STEPS: StepDef[] = [
  { key: 'organization', title: 'Organization', text: 'Create the units of your hierarchy (regions, subsidiaries, departments, teams) and any cross-functional projects.', to: '/app/admin/hierarchy', cta: 'Open hierarchy editor' },
  { key: 'members', title: 'Members', text: 'Invite people and give them memberships with a role in a unit. Leads of a unit see the abstraction of everything below them.', to: '/app/admin/members', cta: 'Invite members' },
  { key: 'holder', title: 'Evidence holder', text: 'Register at least one evidence holder (a private memory store). The key is shown once; embedded holders run inside the API for single-node use.', to: '/app/admin/integrations', cta: 'Register a holder' },
  { key: 'model', title: 'Model', text: 'Configure a model provider on the server (keys are environment-only) and map tiers to tasks.', to: '/app/admin/models', cta: 'Review models' },
  { key: 'goal', title: 'First goal', text: 'Create a goal with an objective and success criteria. The discovery loop works towards it.', to: '/app/goals?new=1', cta: 'Create a goal' },
  { key: 'loop_active', title: 'Discovery loop running', text: 'Activate a goal so the loop starts observing, asking bounded questions and committing supported claims.', to: '/app/goals', cta: 'Open goals' },
];

export function OnboardingPage() {
  const { me, refresh } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const demoGoals = useAsync(async () => {
    const res = await api.goals.list({ include_archived: false, limit: 100 });
    return res.items.filter((g) => g.is_demo);
  }, []);
  useLiveRefresh(['goal.updated', 'loop.state', 'org.changed', 'membership.changed', 'holder.status', 'policy.updated'], () => {
    void refresh();
    void demoGoals.reload();
  });

  if (!me) return <Loading />;
  const steps = me.onboarding?.steps;
  const done = steps ? STEPS.filter((s) => steps[s.key]).length : 0;
  const demoGoal: Goal | undefined = demoGoals.data?.find((g) => g.status !== 'active' && g.status !== 'archived') ?? demoGoals.data?.[0];

  async function activateDemo() {
    if (!demoGoal) return;
    setBusy(true);
    try {
      await api.goals.action(demoGoal.goal_id, { action: 'activate' });
      toast.push('Demonstration goal activated', 'ok');
      await refresh();
      navigate(`/app/goals/${demoGoal.goal_id}`);
    } catch (err) {
      toast.error(err, 'Could not activate the demonstration goal');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Onboarding' }]} />
      <PageHeader title="Onboarding checklist" meta={`${done} of ${STEPS.length} steps complete for ${me.tenant.name}`} actions={me.onboarding?.complete ? <Link to="/app" className="btn btn--primary">Go to home</Link> : null} />
      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 720px)' }}>
        <div className="card">
          {STEPS.map((s, i) => {
            const ok = Boolean(steps?.[s.key]);
            return (
              <div key={s.key} className="onboarding-step">
                <div className={`onboarding-step__mark ${ok ? 'onboarding-step__mark--done' : ''}`} aria-label={ok ? 'done' : 'to do'}>
                  {ok ? '✓' : i + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div className="item__title">{s.title}</div>
                  <div className="small muted">{s.text}</div>
                  {!ok ? (
                    <div style={{ marginTop: 6 }} className="row">
                      <Link to={s.to} className="btn btn--sm">{s.cta}</Link>
                      {s.key === 'loop_active' && demoGoal ? (
                        <Button size="sm" variant="primary" busy={busy} onClick={() => void activateDemo()}>Activate the demonstration goal</Button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
        {demoGoal && !steps?.loop_active ? (
          <div className="notice notice--info">
            A demonstration goal "{demoGoal.title}" exists. Activating it starts the loop with simulated evidence so you can see the full workflow.
          </div>
        ) : null}
      </div>
    </div>
  );
}
