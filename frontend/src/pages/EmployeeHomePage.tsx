import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { Goal, Loop } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { DocumentForm } from '../components/DocumentForm';
import { ago, fmtNum, titleCase, truncate } from '../components/format';
import { DiscoveryList, GoalList } from '../components/Lists';
import { RespondForm } from '../components/RespondForm';
import { SharingSection } from '../components/Sharing';
import { Badge, Button, Empty, ErrorPanel, Input, Loading, PageHeader, Section, StatusBadge } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

function mergeLoops(goals: Goal[], loops: Loop[]): Goal[] {
  const byGoal = new Map(loops.map((l) => [l.goal_id, l]));
  return goals.map((g) => (g.loop ? g : { ...g, loop: byGoal.get(g.goal_id) ?? null }));
}

export function EmployeeHomePage() {
  const me = useSession();
  const toast = useToast();
  const navigate = useNavigate();
  const ws = useAsync(() => api.workspace.employee(), []);
  const [ask, setAsk] = useState('');
  const [asking, setAsking] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [answered, setAnswered] = useState<Set<string>>(new Set());

  useLiveRefresh(
    ['goal.updated', 'loop.state', 'question.created', 'question.routed', 'question.responded', 'question.resolved', 'discovery.created', 'discovery.updated', 'document.ingested', 'document.revised', 'document.retracted', 'holder.status', 'grant.changed'],
    () => void ws.reload(),
  );

  async function startChat(e: FormEvent) {
    e.preventDefault();
    setAsking(true);
    try {
      const { chat } = await api.chats.create({ agent_type: 'user', agent_id: me.user.user_id });
      navigate(`/app/chat/${chat.chat_id}`, { state: { draft: ask } });
    } catch (err) {
      toast.error(err, 'Could not open a chat');
    } finally {
      setAsking(false);
    }
  }

  const data = ws.data;
  const holders = data?.holders ?? me.holders ?? [];
  const goals = data ? mergeLoops(data.goals, data.loop_states ?? []) : [];
  const questions = (data?.questions_needing_input ?? []).filter((q) => !answered.has(q.question_id));

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home' }]} />
      <PageHeader title={`Hello, ${me.user.name.split(' ')[0]}`} meta={`${titleCase(me.principal.highest_level)} · ${me.principal.memberships.filter((m) => m.role !== 'org_admin').map((m) => m.unit_name).filter(Boolean).join(', ') || 'no unit membership'}`} />
      {ws.loading && !data ? <Loading /> : null}
      {ws.error ? <ErrorPanel error={ws.error} retry={() => void ws.reload(false)} /> : null}
      {data ? (
        <div className="split">
          <div>
            <Section title="Questions needing my input" meta={questions.length ? `${questions.length}` : undefined}>
              {questions.length ? (
                <ul className="list">
                  {questions.map((q) => (
                    <li key={q.question_id}>
                      <div className="row" style={{ gap: 6 }}>
                        <Link to={`/app/questions/${q.question_id}`} className="item__title">{q.text}</Link>
                        <StatusBadge status={q.status} />
                      </div>
                      <div className="item__meta">
                        {q.goal_title ? <span>goal: {q.goal_title}</span> : null}
                        <span>asked by {q.asker?.name}</span>
                        <span>{ago(q.created_at)}</span>
                        {q.valid_to ? <span>answer by {ago(q.valid_to).replace(' ago', '')}</span> : null}
                      </div>
                      <div style={{ marginTop: 8 }}>
                        <RespondForm question={q} holders={holders} compact onDone={() => { setAnswered((s) => new Set(s).add(q.question_id)); void ws.reload(); }} />
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>No open questions are routed to your holders.</Empty>
              )}
            </Section>
            <Section title="My goals" meta={`${goals.length}`} actions={<Link to="/app/goals" className="btn btn--sm">All goals</Link>}>
              <GoalList goals={goals} empty="No goals assigned to you. Create one from Goals." />
            </Section>
            <Section title="My discoveries" actions={<Link to="/app/goals" className="btn btn--sm btn--ghost">By goal</Link>}>
              <DiscoveryList discoveries={data.discoveries} empty="No discoveries yet. They appear when the loop commits supported findings." />
            </Section>
            <Section title="Sharing">
              <SharingSection holders={holders} goals={goals} />
            </Section>
          </div>
          <aside className="stack">
            <div className="card">
              <div className="card__title">Ask my agent</div>
              <form className="row" onSubmit={(e) => void startChat(e)}>
                <Input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="What do I know about…" aria-label="Question for my personal agent" style={{ flex: 1 }} />
                <Button type="submit" variant="primary" busy={asking}>Ask</Button>
              </form>
              <div className="xs muted" style={{ marginTop: 6 }}>
                Answers cite only knowledge you are authorized to see. <Link to="/app/chat">All chats</Link>
              </div>
            </div>
            <div className="card">
              <div className="row row--between">
                <div className="card__title" style={{ marginBottom: 0 }}>Private memory</div>
                <Link to="/app/memory" className="xs">Open</Link>
              </div>
              {holders.length ? (
                <ul className="list list--tight" style={{ marginTop: 8 }}>
                  {holders.map((h) => (
                    <li key={h.holder_id}>
                      <div className="row row--between">
                        <span className="item__title">{h.name}</span>
                        <StatusBadge status={h.status} />
                      </div>
                      <div className="item__meta">
                        <span>{fmtNum(h.stats?.documents)} docs</span>
                        <span>{fmtNum(h.stats?.memories)} memories</span>
                        <span>{fmtNum(h.stats?.questions_answered)} answered</span>
                        <span>{h.mode}</span>
                        <span>heartbeat {ago(h.last_heartbeat_at)}</span>
                      </div>
                      {h.domains?.length ? (
                        <div className="row" style={{ gap: 4, marginTop: 4 }}>
                          {h.domains.map((d) => (
                            <Badge key={d} tone="outline">{d}</Badge>
                          ))}
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>No holder yet.</Empty>
              )}
              <div style={{ marginTop: 10 }}>
                <Button size="sm" onClick={() => setShowUpload((s) => !s)} aria-expanded={showUpload}>{showUpload ? 'Close' : 'Add a document'}</Button>
              </div>
              {showUpload ? (
                <div style={{ marginTop: 10 }}>
                  <DocumentForm holders={holders} onDone={() => { setShowUpload(false); void ws.reload(); }} />
                </div>
              ) : null}
            </div>
            <div className="card">
              <div className="card__title">Recent memories</div>
              {data.recent_memory?.length ? (
                <ul className="list list--tight">
                  {data.recent_memory.slice(0, 8).map((m) => (
                    <li key={m.memory_id} className="small">
                      {truncate(m.text, 140)}
                      <div className="xs muted">{m.title ? `${m.title} · ` : ''}{m.kind} · {ago(m.observed_at ?? m.created_at)}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="xs muted">Nothing indexed yet.</div>
              )}
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
