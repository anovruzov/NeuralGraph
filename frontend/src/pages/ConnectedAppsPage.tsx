// Connected apps: what feeds a memory (a personal holder, or a unit's holder for its leads), from which sources, and how
// fresh. Connectors run inside the holder; this page only asks the holder to act. Honest status throughout: a
// connector tested only against offline fixtures says so, and planned connectors cannot be connected.
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { Connector, ConnectorCatalogItem, ConnectorSource, Holder } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { ago, fmtNum, titleCase } from '../components/format';
import { Badge, Button, Drawer, Empty, ErrorPanel, Field, Input, Loading, PageHeader, Section, Select, StatusBadge, Table, Textarea, confirmAction, type Tone } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';

const LEAD_ROLES = new Set(['team_lead', 'department_lead', 'subsidiary_lead', 'regional_lead', 'executive']);

const STATUS_LABEL: Record<string, [string, Tone, string]> = {
  'live-verified': ['Live verified', 'ok', 'Run against the real service and recorded.'],
  'tested-offline': ['Tested offline', 'info', 'Exercised end to end against faithful offline fixtures; not yet verified against the live service.'],
  implemented: ['Implemented', 'warn', 'Built to the provider documentation; no offline conformance tests yet.'],
  scaffold: ['Planned', 'outline', 'The shape exists; it does not work yet and cannot be connected.'],
};

function ConnectorStatus({ status }: { status: string }) {
  const [label, tone, title] = STATUS_LABEL[status] ?? [titleCase(status), 'neutral' as Tone, ''];
  return <Badge tone={tone} title={title}>{label}</Badge>;
}

const VISIBILITY_TONE: Record<string, Tone> = { public: 'ok', members: 'warn', private: 'danger' };

export function ConnectedAppsPage() {
  const me = useSession();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const holders = useAsync(() => api.holders.list(), []);
  const catalog = useAsync(() => api.integrations.catalog(), []);
  const ledUnits = useMemo(() => new Set(me.principal.memberships.filter((m) => LEAD_ROLES.has(String(m.role))).map((m) => m.unit_id)), [me]);
  const manageable = useMemo(
    () =>
      (holders.data?.items ?? []).filter(
        (h) => h.status !== 'revoked' && ((h.owner_type === 'user' && h.owner_id === me.user.user_id) || (h.owner_type === 'unit' && (ledUnits.has(h.owner_id) || me.principal.is_admin))),
      ),
    [holders.data, me, ledUnits],
  );
  const [holderId, setHolderId] = useState<string>('');
  useEffect(() => {
    const first = manageable[0];
    if (!holderId && first) setHolderId(first.holder_id);
  }, [manageable, holderId]);
  const holder = manageable.find((h) => h.holder_id === holderId) ?? null;
  const connectors = useAsync(() => (holderId ? api.integrations.connectors(holderId) : Promise.resolve({ items: [] as Connector[] })), [holderId]);
  useLiveRefresh(['holder.status'], () => void holders.reload());
  const [connectType, setConnectType] = useState<ConnectorCatalogItem | null>(null);
  const [sourcesOf, setSourcesOf] = useState<Connector | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const connected = params.get('connected');
    if (connected) {
      toast.push('App connected. Choose which sources to include.', 'ok');
      params.delete('connected');
      setParams(params, { replace: true });
    }
  }, [params, setParams, toast]);

  if (holders.loading && !holders.data) return <Loading />;
  if (holders.error) return <ErrorPanel error={holders.error} retry={() => void holders.reload()} />;

  async function act(c: Connector, what: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(`${c.connector_id}:${what}`);
    try {
      await fn();
      toast.push(ok, 'ok');
      await connectors.reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(null);
    }
  }

  const connectorRows = connectors.data?.items ?? [];
  const isOrg = holder?.owner_type === 'unit';

  return (
    <div className="page">
      <PageHeader
        title="Connected apps"
        meta={holder ? <>Feeding <b>{holder.name}</b> · {isOrg ? 'organization-wide (unit memory)' : 'personal memory'}</> : null}
        actions={
          manageable.length > 1 ? (
            <Select aria-label="Memory" value={holderId} onChange={(e) => setHolderId(e.target.value)}>
              {manageable.map((h) => (
                <option key={h.holder_id} value={h.holder_id}>
                  {h.name} ({h.owner_type === 'unit' ? 'unit' : 'personal'})
                </option>
              ))}
            </Select>
          ) : null
        }
      >
        <p className="muted sm" style={{ maxWidth: 760 }}>
          Apps feed a memory that stays in its holder. Nothing is ingested until you include a source, private and members-only sources stay
          with their members, and Mycelic exports only what a question needs. {isOrg ? 'Each source you include here is mapped to this unit only.' : ''}
        </p>
      </PageHeader>

      {!manageable.length ? (
        <Empty>You have no memory to connect apps to. Ask an administrator to register a personal holder for you, or lead a unit with one.</Empty>
      ) : (
        <>
          <FlowStrip holder={holder} connectors={connectorRows} />

          <Section title="Connections" meta={connectors.loading ? 'loading…' : `${connectorRows.length} connected`}>
            {connectors.error ? (
              <ErrorPanel error={connectors.error} retry={() => void connectors.reload()} />
            ) : (
              <Table<Connector>
                rowKey={(c) => c.connector_id}
                rows={connectorRows}
                empty="No apps connected yet. Pick one below."
                columns={[
                  { key: 'app', header: 'App', render: (c) => <><b>{c.display_name}</b><div className="xs muted">{c.account_label || c.connector_type}</div></> },
                  { key: 'status', header: 'Status', render: (c) => <><StatusBadge status={c.status} />{c.status_code ? <div className="xs muted">{c.status_code}</div> : null}</> },
                  { key: 'sources', header: 'Sources', render: (c) => <>{fmtNum(c.counts?.sources_included ?? 0)} included{c.counts?.sources_pending ? <div className="xs muted">{c.counts.sources_pending} awaiting review</div> : null}</> },
                  { key: 'records', header: 'Records', num: true, render: (c) => fmtNum(c.counts?.records ?? 0) },
                  { key: 'sync', header: 'Last sync', render: (c) => <span title={c.last_sync_at ?? ''}>{c.last_sync_at ? ago(c.last_sync_at) : 'never'}</span> },
                  {
                    key: 'actions',
                    header: '',
                    render: (c) => (
                      <div className="row" style={{ gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        <Button size="sm" onClick={() => setSourcesOf(c)}>Sources</Button>
                        <Button size="sm" busy={busy === `${c.connector_id}:sync`} disabled={c.status !== 'active'}
                          onClick={() => void act(c, 'sync', () => api.integrations.sync(holderId, c.connector_id), 'Synced')}>
                          Sync now
                        </Button>
                        {c.status === 'active' ? (
                          <Button size="sm" busy={busy === `${c.connector_id}:pause`} onClick={() => void act(c, 'pause', () => api.integrations.update(holderId, c.connector_id, { status: 'paused' }), 'Paused')}>Pause</Button>
                        ) : c.status === 'paused' ? (
                          <Button size="sm" busy={busy === `${c.connector_id}:resume`} onClick={() => void act(c, 'resume', () => api.integrations.update(holderId, c.connector_id, { status: 'active' }), 'Resumed')}>Resume</Button>
                        ) : null}
                        {c.status !== 'disconnected' ? (
                          <Button size="sm" variant="ghost" busy={busy === `${c.connector_id}:disconnect`}
                            onClick={() => {
                              if (!confirmAction(`Disconnect ${c.display_name}? Its credentials are destroyed and syncing stops.`)) return;
                              const purge = confirmAction('Also delete everything it ingested? (Cancel keeps the records.)');
                              void act(c, 'disconnect', () => api.integrations.disconnect(holderId, c.connector_id, purge ? 'delete' : 'keep'), purge ? 'Disconnected and purged' : 'Disconnected');
                            }}>
                            Disconnect
                          </Button>
                        ) : null}
                      </div>
                    ),
                  },
                ]}
              />
            )}
          </Section>

          <Section title="Connect an app" meta="Status is stated honestly: only “Live verified” has run against the real service.">
            {catalog.error ? <ErrorPanel error={catalog.error} retry={() => void catalog.reload()} /> : null}
            <div className="grid grid--auto">
              {(catalog.data?.items ?? [])
                .slice()
                .sort((a, b) => Number(b.connectable) - Number(a.connectable) || a.display_name.localeCompare(b.display_name))
                .map((it) => {
                  const fits = it.ownership.includes(isOrg ? 'org' : 'personal');
                  return (
                    <div key={it.connector_type} className="card" style={{ opacity: it.connectable ? 1 : 0.7 }}>
                      <div className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
                        <b>{it.display_name}</b>
                        <ConnectorStatus status={it.status} />
                      </div>
                      <div className="xs muted" style={{ margin: '6px 0' }}>
                        {it.modes.join(' + ')} · {it.auth_kinds.join(', ')} · {it.ownership.map((o) => (o === 'org' ? 'organization' : 'personal')).join(' / ')}
                      </div>
                      {it.scopes.length ? (
                        <details className="xs">
                          <summary>Permissions requested ({it.scopes.length})</summary>
                          <ul style={{ margin: '4px 0 0 16px' }}>
                            {it.scopes.map((s) => (
                              <li key={s.scope}><code>{s.scope}</code>{s.required ? '' : ' (optional)'}: {s.reason}</li>
                            ))}
                          </ul>
                        </details>
                      ) : null}
                      <div style={{ marginTop: 8 }}>
                        <Button size="sm" variant="primary" disabled={!it.connectable || !fits} onClick={() => setConnectType(it)}
                          title={!it.connectable ? 'Planned or disabled for your organization' : !fits ? `Cannot feed a ${isOrg ? 'unit' : 'personal'} memory` : ''}>
                          {it.connectable ? 'Connect' : 'Planned'}
                        </Button>
                      </div>
                    </div>
                  );
                })}
            </div>
          </Section>
        </>
      )}

      <ConnectDrawer item={connectType} holder={holder} onClose={() => setConnectType(null)} onDone={() => { setConnectType(null); void connectors.reload(); }} />
      <SourcesDrawer connector={sourcesOf} holderId={holderId} onClose={() => { setSourcesOf(null); void connectors.reload(); }} />
    </div>
  );
}

function FlowStrip({ holder, connectors }: { holder: Holder | null; connectors: Connector[] }) {
  if (!holder) return null;
  const ingest = (holder.stats as unknown as { ingest?: { records?: number; by_app?: Record<string, number> } })?.ingest;
  const apps = Object.entries(ingest?.by_app ?? {});
  const published = holder.published_domains ?? [];
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="row" style={{ gap: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>
        <FlowStep title="Sources" body={connectors.length ? `${connectors.reduce((n, c) => n + (c.counts?.sources_included ?? 0), 0)} included from ${connectors.length} app(s)` : 'none yet'} />
        <span className="muted" aria-hidden>→</span>
        <FlowStep title="Records" body={apps.length ? apps.map(([a, n]) => `${a} ${fmtNum(n)}`).join(' · ') : `${fmtNum(Math.max(ingest?.records ?? 0, connectors.reduce((n, c) => n + (c.counts?.records ?? 0), 0)))}`} />
        <span className="muted" aria-hidden>→</span>
        <FlowStep title="Domains (routable)" body={published.length ? published.join(', ') : 'none published yet'} />
        <span className="muted" aria-hidden>→</span>
        <FlowStep title="Discoveries" body="Questions reach this memory by its domains; answers cite records, never copy them." />
      </div>
    </div>
  );
}

function FlowStep({ title, body }: { title: string; body: string }) {
  return (
    <div style={{ flex: '1 1 160px', minWidth: 0 }}>
      <div className="xs muted">{title}</div>
      <div className="sm" style={{ overflowWrap: 'anywhere' }}>{body}</div>
    </div>
  );
}

function ConnectDrawer({ item, holder, onClose, onDone }: { item: ConnectorCatalogItem | null; holder: Holder | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [auth, setAuth] = useState('');
  const [token, setToken] = useState('');
  const [config, setConfig] = useState('{}');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setAuth(item?.auth_kinds.find((k) => k !== 'none') ?? item?.auth_kinds[0] ?? 'none');
    setToken('');
    setConfig(item?.connector_type === 'local_export' ? '{"paths": ["export.jsonl"], "source_app": "notes", "account_id": "local"}' : '{}');
  }, [item]);
  if (!item || !holder) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!item || !holder) return;
    let cfg: Record<string, unknown> = {};
    try {
      cfg = config.trim() ? JSON.parse(config) : {};
    } catch {
      toast.push('Configuration must be valid JSON', 'error');
      return;
    }
    setBusy(true);
    try {
      const res = await api.integrations.connect(holder.holder_id, {
        connector_type: item.connector_type,
        auth: auth === 'none' ? undefined : auth === 'oauth2' ? { kind: 'oauth2' } : { kind: auth, token },
        config: cfg,
      });
      setToken('');
      if (res.next.action === 'redirect' && res.next.url) {
        window.location.assign(res.next.url);
        return;
      }
      toast.push(`${item.display_name} connected: ${res.discovered?.discovered ?? 0} source(s) found, awaiting your review`, 'ok');
      onDone();
    } catch (err) {
      toast.error(err, 'Could not connect');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer open onClose={onClose} title={`Connect ${item.display_name}`}>
      <form onSubmit={submit} className="stack">
        <p className="sm muted">
          <ConnectorStatus status={item.status} /> {STATUS_LABEL[item.status]?.[2]}
        </p>
        {item.terms_notes ? <p className="xs muted">{item.terms_notes}</p> : null}
        <Field label="Authorization">
          {(id) => (
            <Select id={id} value={auth} onChange={(e) => setAuth(e.target.value)}>
              {item.auth_kinds.map((k) => (
                <option key={k} value={k}>{k === 'pat' ? 'Access token (you paste it once)' : k === 'oauth2' ? 'Sign in with the app (OAuth)' : k === 'none' ? 'No credentials' : k}</option>
              ))}
            </Select>
          )}
        </Field>
        {auth !== 'none' && auth !== 'oauth2' ? (
          <Field label="Token" hint="Sent once to the holder, which encrypts it; it is never shown again or stored by the coordinator.">
            {(id) => <Input id={id} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} required />}
          </Field>
        ) : null}
        <Field label="Configuration (JSON)" hint={item.connector_type === 'local_export' ? 'File names inside this memory’s import directory.' : 'Optional connector settings.'}>
          {(id) => <Textarea id={id} rows={4} value={config} onChange={(e) => setConfig(e.target.value)} spellCheck={false} />}
        </Field>
        <div className="row" style={{ gap: 8 }}>
          <Button type="submit" variant="primary" busy={busy}>{auth === 'oauth2' ? 'Continue to the app' : 'Connect'}</Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Drawer>
  );
}

function SourcesDrawer({ connector, holderId, onClose }: { connector: Connector | null; holderId: string; onClose: () => void }) {
  const toast = useToast();
  const sources = useAsync(
    () => (connector ? api.integrations.sources(holderId, connector.connector_id) : Promise.resolve({ items: [] as ConnectorSource[] })),
    [connector?.connector_id, holderId],
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [hook, setHook] = useState<{ url: string; secret?: string } | null>(null);
  useEffect(() => setHook(null), [connector?.connector_id]);
  if (!connector) return null;

  async function set(s: ConnectorSource, selection: 'included' | 'excluded') {
    if (!connector) return;
    let existing: 'keep' | 'delete' = 'keep';
    if (selection === 'excluded' && s.selection === 'included') {
      existing = confirmAction(`Also delete what was already ingested from ${s.name || s.external_id}? (Cancel keeps it.)`) ? 'delete' : 'keep';
    }
    setBusy(s.source_id);
    try {
      await api.integrations.setSources(holderId, connector.connector_id, [{ source_id: s.source_id, selection }], existing);
      await sources.reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(null);
    }
  }

  async function webhook() {
    if (!connector) return;
    let secret: string | undefined;
    if (connector.connector_type === 'slack') {
      secret = window.prompt("Paste your Slack app's signing secret (it is stored encrypted on the server):") || undefined;
      if (!secret) return;
    }
    try {
      const res = await api.integrations.webhook(holderId, connector.connector_id, secret);
      setHook({ url: res.url, secret: res.secret });
    } catch (err) {
      toast.error(err, 'Could not create the webhook');
    }
  }

  const rows = sources.data?.items ?? [];
  return (
    <Drawer open onClose={onClose} title={`${connector.display_name}: sources`} width={720}>
      <p className="sm muted">
        New sources wait for your review. Including a source ingests it into this memory only; its access rules travel with every record.
      </p>
      <div className="row" style={{ gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <Button size="sm" onClick={async () => { try { const r = await api.integrations.discover(holderId, connector.connector_id); toast.push(`${r.discovered ?? 0} source(s) found`, 'ok'); void sources.reload(); } catch (err) { toast.error(err); } }}>
          Rediscover
        </Button>
        {connector.mode.includes('webhook') ? <Button size="sm" onClick={() => void webhook()}>Set up live updates (webhook)</Button> : null}
        {rows.some((r) => r.selection === 'pending_review') ? (
          <Button size="sm" variant="primary"
            onClick={async () => {
              const pending = rows.filter((r) => r.selection === 'pending_review' && r.visibility === 'public');
              if (!pending.length) { toast.push('Only public sources can be included in bulk; review members-only and private ones one by one.', 'info'); return; }
              try { await api.integrations.setSources(holderId, connector.connector_id, pending.map((r) => ({ source_id: r.source_id, selection: 'included' }))); void sources.reload(); } catch (err) { toast.error(err); }
            }}>
            Include all public sources awaiting review
          </Button>
        ) : null}
      </div>
      {hook ? (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="sm"><b>Delivery URL</b>: <code style={{ overflowWrap: 'anywhere' }}>{hook.url}</code></div>
          {hook.secret ? <div className="sm"><b>Secret (shown once)</b>: <code style={{ overflowWrap: 'anywhere' }}>{hook.secret}</code></div> : null}
          <div className="xs muted">Webhooks only notify: the holder fetches each change with its own access, and a regular poll repairs anything missed.</div>
        </div>
      ) : null}
      {sources.loading && !sources.data ? <Loading /> : sources.error ? <ErrorPanel error={sources.error} retry={() => void sources.reload()} /> : (
        <Table<ConnectorSource>
          rowKey={(s) => s.source_id}
          rows={rows}
          empty="No sources discovered yet."
          columns={[
            { key: 'name', header: 'Source', render: (s) => <><b>{s.name || s.external_id}</b><div className="xs muted">{s.source_type}{s.access_state !== 'ok' ? ` · ${s.access_state}` : ''}</div></> },
            { key: 'vis', header: 'Access', render: (s) => <Badge tone={VISIBILITY_TONE[s.visibility] ?? 'neutral'} title={s.visibility === 'public' ? 'Visible within the workspace' : `${s.members} member(s)`}>{titleCase(s.visibility)}{s.visibility !== 'public' ? ` · ${s.members}` : ''}</Badge> },
            { key: 'sel', header: 'Selection', render: (s) => <StatusBadge status={s.selection === 'pending_review' ? 'pending' : s.selection === 'included' ? 'active' : 'disabled'} /> },
            {
              key: 'act', header: '', render: (s) => (
                <div className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                  {s.selection !== 'included' ? <Button size="sm" busy={busy === s.source_id} onClick={() => void set(s, 'included')}>Include</Button> : null}
                  {s.selection !== 'excluded' ? <Button size="sm" variant="ghost" busy={busy === s.source_id} onClick={() => void set(s, 'excluded')}>Exclude</Button> : null}
                </div>
              ),
            },
          ]}
        />
      )}
    </Drawer>
  );
}
