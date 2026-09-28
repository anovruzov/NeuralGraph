import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { Chat, ChatMessage } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { CitationChip } from '../components/ArtifactDrawer';
import { ago, titleCase } from '../components/format';
import { Badge, Button, Empty, ErrorPanel, Loading, PageHeader, Select, Textarea } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

export function ChatPage() {
  const me = useSession();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { chatId } = useParams();
  const list = useAsync(() => api.chats.list(), []);
  const thread = useAsync(() => api.chats.get(chatId!), [chatId], { enabled: Boolean(chatId) });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [agent, setAgent] = useState<string>(`user:${me.user.user_id}`);
  const [creating, setCreating] = useState(false);
  const [contextSummary, setContextSummary] = useState<{ claims: number; discoveries: number; memories: number } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (thread.data) setMessages(thread.data.messages);
  }, [thread.data]);
  useEffect(() => {
    const draft = (location.state as { draft?: string } | null)?.draft;
    if (draft) setText(draft);
  }, [location.state]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages.length]);

  const unitOptions = me.principal.memberships.filter((m) => m.role !== 'org_admin').map((m) => ({ id: m.unit_id, name: m.unit_name || m.unit_id }));
  const uniqueUnits = unitOptions.filter((u, i) => unitOptions.findIndex((x) => x.id === u.id) === i);

  async function createChat(e: FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      const [agent_type, agent_id] = agent.split(':') as ['user' | 'unit', string];
      const { chat } = await api.chats.create({ agent_type, agent_id });
      await list.reload();
      navigate(`/app/chat/${chat.chat_id}`);
    } catch (err) {
      toast.error(err, 'Could not create the chat');
    } finally {
      setCreating(false);
    }
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (!chatId || !text.trim()) return;
    const body = text.trim();
    setSending(true);
    const optimistic: ChatMessage = { id: `tmp-${Date.now()}`, role: 'user', content: body, citations: [], at: new Date().toISOString() };
    setMessages((m) => [...m, optimistic]);
    setText('');
    try {
      const res = await api.chats.send(chatId, body);
      setContextSummary(res.context_summary);
      const reply: ChatMessage = { ...res.message, citations: res.message.citations?.length ? res.message.citations : res.citations ?? [] };
      setMessages((m) => [...m, reply]);
      void list.reload();
    } catch (err) {
      toast.error(err, 'Message failed');
      setMessages((m) => m.filter((x) => x.id !== optimistic.id));
      setText(body);
    } finally {
      setSending(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  const chat: Chat | undefined = thread.data?.chat ?? list.data?.items.find((c) => c.chat_id === chatId);
  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Chat', to: '/app/chat' }, ...(chat ? [{ label: chat.title || chat.agent_name }] : [])]} />
      <PageHeader title="Agent chat" meta="Scoped agents answer with citations drawn only from knowledge you are authorized to see." />
      <div className="chat">
        <div className="chat__list stack">
          <form className="stack stack--sm" onSubmit={(e) => void createChat(e)} aria-label="New chat">
            <Select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
              <option value={`user:${me.user.user_id}`}>My personal agent</option>
              {uniqueUnits.map((u) => (
                <option key={u.id} value={`unit:${u.id}`}>{u.name} agent</option>
              ))}
            </Select>
            <Button type="submit" variant="primary" size="sm" busy={creating}>New chat</Button>
          </form>
          {list.loading && !list.data ? <Loading /> : null}
          {list.error ? <ErrorPanel error={list.error} retry={() => void list.reload(false)} /> : null}
          {list.data ? (
            list.data.items.length ? (
              <ul className="list list--tight">
                {list.data.items.map((c) => (
                  <li key={c.chat_id}>
                    <Link to={`/app/chat/${c.chat_id}`} style={{ fontWeight: c.chat_id === chatId ? 600 : 400, color: 'inherit', display: 'block' }}>
                      {c.title || c.agent_name}
                    </Link>
                    <div className="xs muted">{c.agent_type === 'unit' ? `${c.agent_name} agent` : 'personal agent'} · {ago(c.updated_at)}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No chats yet.</Empty>
            )
          ) : null}
        </div>
        <div className="chat__thread">
          {!chatId ? <Empty>Choose a chat or start a new one.</Empty> : null}
          {chatId && thread.loading && !thread.data ? <Loading /> : null}
          {thread.error ? <ErrorPanel error={thread.error} retry={() => void thread.reload(false)} /> : null}
          {chatId && thread.data ? (
            <>
              <div className="row row--between">
                <div className="row" style={{ gap: 6 }}>
                  <Badge tone="accent">{chat?.agent_type === 'unit' ? `${chat.agent_name} agent` : 'Personal agent'}</Badge>
                  {contextSummary ? (
                    <span className="xs muted">context: {contextSummary.claims} claims · {contextSummary.discoveries} discoveries · {contextSummary.memories} memories</span>
                  ) : null}
                </div>
              </div>
              <div className="chat__messages" role="log" aria-live="polite">
                {messages.length === 0 ? <Empty>Ask the agent something. Answers cite claims, discoveries, memories and evidence you may see.</Empty> : null}
                {messages.map((m) => (
                  <div key={m.id} className={`msg ${m.role === 'user' ? 'msg--user' : ''}`}>
                    <div className="msg__content">{m.content}</div>
                    <div className="msg__meta">
                      <span>{titleCase(m.role)} · {ago(m.at)}</span>
                      {m.citations?.map((c, i) => (
                        <CitationChip key={`${c.type}-${c.id}-${i}`} citation={c} />
                      ))}
                    </div>
                  </div>
                ))}
                {sending ? <div className="loading"><span className="spinner" aria-hidden /> Thinking…</div> : null}
                <div ref={endRef} />
              </div>
              <form className="chat__compose" onSubmit={(e) => void send(e)}>
                <Textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} placeholder="Message… (Enter to send, Shift+Enter for a new line)" aria-label="Message" rows={2} style={{ flex: 1 }} />
                <Button type="submit" variant="primary" busy={sending} disabled={!text.trim()}>Send</Button>
              </form>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
