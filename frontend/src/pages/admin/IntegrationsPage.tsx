import { useState, type FormEvent } from 'react';
import { api } from '../../api/client';
import { useLiveRefresh } from '../../api/events';
import type { Holder } from '../../api/types';
import { useSession } from '../../auth/AuthProvider';
import { ago, fmtNum } from '../../components/format';
import { Badge, Button, Empty, ErrorPanel, Field, Input, Loading, Section, Select, StatusBadge, Table, confirmAction } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { useOrg } from '../../components/useOrg';
import { useToast } from '../../components/Toast';

export function IntegrationsPage() {
  const me = useSession();
  const toast = useToast();
  const { units } = useOrg();
  const holders = useAsync(() => api.holders.list(), []);
  const users = useAsync(() => api.org.users(), []);
  useLiveRefresh(['holder.status'], () => void holders.reload());
  const [name, setName] = useState('');
  const [owner, setOwner] = useState(`user:${me.user.user_id}`);
  const [mode, setMode] = useState<'embedded' | 'external'>('embedded');
  const [domains, setDomains] = useState('');
  const [disclosure, setDisclosure] = useState('excerpt');
  const [busy, setBusy] = useState(false);
  const [shownKey, setShownKey] = useState<{ holder: Holder; key: string; rotated?: boolean } | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);

  async function register(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const [owner_type, owner_id] = owner.split(':') as ['user' | 'unit', string];
      const res = await api.holders.create({
        name: name.trim(),
        owner_type,
        owner_id,
        mode,
        domains: domains.split(',').map((s) => s.trim()).filter(Boolean),
        export_policy: { disclosure },
      });
      setShownKey({ holder: res.holder, key: res.key });
      setName('');
      toast.push('Holder registered', 'ok');
      void holders.reload();
    } catch (err) {
      toast.error(err, 'Could not register the holder');
    } finally {
      setBusy(false);
    }
  }

  async function rotate(h: Holder) {
    if (!confirmAction(`Rotate the key for ${h.name}? The old key stops working immediately.`)) return;
    setRowBusy(h.holder_id);
    try {
      const res = await api.holders.rotateKey(h.holder_id);
      setShownKey({ holder: h, key: res.key, rotated: true });
      toast.push('Key rotated', 'ok');
    } catch (err) {
      toast.error(err, 'Could not rotate');
    } finally {
      setRowBusy(null);
    }
  }

  async function revoke(h: Holder) {
    if (!confirmAction(`Revoke ${h.name}? It stops receiving questions and its key is disabled.`)) return;
    setRowBusy(h.holder_id);
    try {
      await api.holders.update(h.holder_id, { status: 'revoked' });
      toast.push('Holder revoked', 'ok');
      void holders.reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <div>
      <div className="grid grid--2" style={{ marginBottom: 20 }}>
        <div className="card">
          <div className="card__title">Register an evidence holder</div>
          <form className="form" onSubmit={(e) => void register(e)}>
            <Field label="Name" required>{(id) => <Input id={id} required value={name} onChange={(e) => setName(e.target.value)} placeholder="Ana's notes / Support team records" />}</Field>
            <div className="form-row">
              <Field label="Owner">
                {(id) => (
                  <Select id={id} value={owner} onChange={(e) => setOwner(e.target.value)}>
                    <optgroup label="People">
                      {(users.data?.items ?? [{ user_id: me.user.user_id, name: me.user.name }]).map((u) => (
                        <option key={u.user_id} value={`user:${u.user_id}`}>{u.name}</option>
                      ))}
                    </optgroup>
                    <optgroup label="Units">
                      {units.filter((u) => !u.archived_at).map((u) => (
                        <option key={u.unit_id} value={`unit:${u.unit_id}`}>{u.name} ({u.type})</option>
                      ))}
                    </optgroup>
                  </Select>
                )}
              </Field>
              <Field label="Mode" hint={mode === 'embedded' ? 'Runs inside the API process (single node).' : 'A separate `mycelic holder` process using the key.'}>
                {(id) => (
                  <Select id={id} value={mode} onChange={(e) => setMode(e.target.value as 'embedded' | 'external')}>
                    <option value="embedded">embedded</option>
                    <option value="external">external</option>
                  </Select>
                )}
              </Field>
            </div>
            <div className="form-row">
              <Field label="Domains" hint="Comma separated; questions are routed by domain">{(id) => <Input id={id} value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="deployments, incidents" />}</Field>
              <Field label="Disclosure">
                {(id) => (
                  <Select id={id} value={disclosure} onChange={(e) => setDisclosure(e.target.value)}>
                    <option value="excerpt">excerpt</option>
                    <option value="summary">summary</option>
                    <option value="none">none</option>
                  </Select>
                )}
              </Field>
            </div>
            <div className="form-actions"><Button type="submit" variant="primary" busy={busy}>Register</Button></div>
          </form>
        </div>
        <div className="card">
          <div className="card__title">Holder key</div>
          {shownKey ? (
            <div className="notice notice--ok">
              <strong>{shownKey.rotated ? 'New key' : 'Key'} for {shownKey.holder.name}.</strong> It is shown once; store it with the holder process.
              <div className="row" style={{ marginTop: 6 }}>
                <Input readOnly value={shownKey.key} className="mono" onFocus={(e) => e.currentTarget.select()} aria-label="Holder key" />
                <Button size="sm" onClick={() => { void navigator.clipboard?.writeText(shownKey.key); toast.push('Copied'); }}>Copy</Button>
              </div>
              <div className="xs" style={{ marginTop: 6 }}>
                External holder: <code>MYCELIC_HOLDER_KEY={'<key>'} python -m mycelic holder --holder-id {shownKey.holder.holder_id}</code>
              </div>
              <div style={{ marginTop: 6 }}><Button size="sm" variant="ghost" onClick={() => setShownKey(null)}>Dismiss</Button></div>
            </div>
          ) : (
            <Empty>Register or rotate a holder to see its key here (once).</Empty>
          )}
        </div>
      </div>
      <Section title="Holders" meta={holders.data ? `${holders.data.items.length}` : undefined}>
        {holders.loading && !holders.data ? <Loading /> : null}
        {holders.error ? <ErrorPanel error={holders.error} retry={() => void holders.reload(false)} /> : null}
        {holders.data ? (
          <Table
            rows={holders.data.items}
            rowKey={(h) => h.holder_id}
            empty="No holders registered."
            caption="Holders"
            columns={[
              { key: 'name', header: 'Holder', render: (h) => <span>{h.name}<div className="mono xs muted">{h.holder_id}</div></span> },
              { key: 'owner', header: 'Owner', render: (h) => <span>{h.owner_name} <span className="xs muted">({h.owner_type})</span></span> },
              { key: 'status', header: 'Status', render: (h) => <StatusBadge status={h.status} /> },
              { key: 'mode', header: 'Mode', render: (h) => h.mode },
              { key: 'domains', header: 'Domains', render: (h) => <span className="row" style={{ gap: 4 }}>{h.domains?.length ? h.domains.map((d) => <Badge key={d} tone="outline">{d}</Badge>) : '—'}</span> },
              { key: 'export', header: 'Export', render: (h) => <span className="xs">{h.export_policy?.disclosure ?? 'excerpt'} · {(h.export_policy?.answer_scopes ?? []).join('/')}</span> },
              { key: 'hb', header: 'Heartbeat', render: (h) => ago(h.last_heartbeat_at) },
              { key: 'stats', header: 'Docs / memories', render: (h) => `${fmtNum(h.stats?.documents)} / ${fmtNum(h.stats?.memories)}` },
              {
                key: 'a',
                header: '',
                render: (h) =>
                  h.status !== 'revoked' ? (
                    <span className="row" style={{ gap: 4 }}>
                      <Button size="sm" busy={rowBusy === h.holder_id} onClick={() => void rotate(h)}>Rotate key</Button>
                      <Button size="sm" variant="danger" busy={rowBusy === h.holder_id} onClick={() => void revoke(h)}>Revoke</Button>
                    </span>
                  ) : null,
              },
            ]}
          />
        ) : null}
      </Section>
      <ConnectedAppsAdmin />
      <ShardsAdmin holderNames={Object.fromEntries((holders.data?.items ?? []).map((h) => [h.holder_id, h.name]))} />
    </div>
  );
}

/** Connector metadata across the organization: types, scopes, status and counts. Never source names or content. */
function ConnectedAppsAdmin() {
  const rows = useAsync(() => api.integrations.admin(), []);
  return (
    <Section title="Connected apps" meta={rows.data ? `${rows.data.items.length} connection(s) · ${Object.entries(rows.data.totals.by_type).map(([t, n]) => `${t} ${n}`).join(', ') || 'none'}` : undefined}>
      <p className="sm muted" style={{ maxWidth: 760 }}>
        Personal connections belong to their owner; organization-wide ones to the unit's leads (or an administrator). This view shows metadata only:
        what is connected, its permissions and health. Source names and content stay in the holders.
      </p>
      {rows.error ? (
        <ErrorPanel error={rows.error} retry={() => void rows.reload()} />
      ) : rows.loading && !rows.data ? (
        <Loading />
      ) : (
        <Table
          rows={rows.data?.items ?? []}
          rowKey={(r) => r.connector_id}
          empty="No apps are connected yet."
          columns={[
            { key: 'type', header: 'App', render: (r) => <b>{r.connector_type}</b> },
            { key: 'holder', header: 'Feeds', render: (r) => <span>{r.holder_name}<div className="xs muted">{r.scope}</div></span> },
            { key: 'status', header: 'Status', render: (r) => <span><StatusBadge status={r.status} />{r.status_code ? <div className="xs muted">{r.status_code}</div> : null}</span> },
            { key: 'scopes', header: 'Granted', render: (r) => <span className="xs">{r.granted_scopes.join(', ') || '—'}</span> },
            { key: 'sources', header: 'Sources', num: true, render: (r) => `${fmtNum(r.sources_included)}${r.sources_pending ? ` (+${r.sources_pending} pending)` : ''}` },
            { key: 'records', header: 'Records', num: true, render: (r) => fmtNum(r.records) },
            { key: 'sync', header: 'Last sync', render: (r) => (r.last_sync_at ? ago(r.last_sync_at) : 'never') },
          ]}
        />
      )}
    </Section>
  );
}

const MIB = 1024 * 1024;

/** Where each holder's memory lives on disk: one file per holder until measured load justifies splitting a domain out.
 * Counts and sizes only; a split runs only when an administrator approves a recommendation. */
function ShardsAdmin({ holderNames }: { holderNames: Record<string, string> }) {
  const shards = useAsync(() => api.integrations.shards(), []);
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const split = async (r: { holder_id: string; domain_ids: string[] }) => {
    const name = holderNames[r.holder_id] ?? r.holder_id;
    if (!confirmAction(`Move ${r.domain_ids.join(', ')} of ${name} into its own file? Searches of a split memory read every file and are slower.`)) return;
    setBusy(r.holder_id);
    try {
      const out = await api.integrations.split(r.holder_id, r.domain_ids);
      toast.push(`Split started (${out.migration.state})`, 'ok');
      await shards.reload();
    } catch (e) {
      toast.error(e, 'Split refused');
    } finally {
      setBusy(null);
    }
  };
  const recs = shards.data?.recommendations ?? [];
  return (
    <Section title="Memory storage" meta={shards.data ? `${shards.data.items.length} file(s)` : undefined}>
      <p className="sm muted" style={{ maxWidth: 760 }}>
        Each memory starts as one file. When one grows past a measured limit for a day (vector index size, memory count, file size or latency), a
        domain can be moved into its own file. Splitting is never automatic, and it makes searches of that memory slower.
      </p>
      {shards.error ? (
        <ErrorPanel error={shards.error} retry={() => void shards.reload()} />
      ) : shards.loading && !shards.data ? (
        <Loading />
      ) : (
        <>
          {recs.length ? (
            <ul className="sm" style={{ margin: '0 0 12px', paddingLeft: 18 }}>
              {recs.map((r) => (
                <li key={`${r.holder_id}:${r.shard_id}`}>
                  <b>{holderNames[r.holder_id] ?? r.holder_id}</b>: {r.metric} over its limit.{' '}
                  {r.action === 'split' ? (
                    <>
                      Recommended: move <code>{r.domain_ids.join(', ')}</code> into its own file.{' '}
                      <Button size="sm" busy={busy === r.holder_id} onClick={() => void split(r)}>Approve split</Button>
                    </>
                  ) : r.action === 'split_by_time' ? (
                    'One domain carries the whole load; only a split by time would help (not available yet).'
                  ) : r.action === 'quota_reached' ? (
                    'This memory already has the maximum number of files.'
                  ) : (
                    'No domain can be moved.'
                  )}
                </li>
              ))}
            </ul>
          ) : null}
          <Table
            rows={shards.data?.items ?? []}
            rowKey={(r) => `${r.holder_id}:${r.shard_id}`}
            empty="No memory has reported its storage yet."
            columns={[
              { key: 'holder', header: 'Memory', render: (r) => <span>{holderNames[r.holder_id] ?? r.holder_id}<div className="xs muted">{r.shard_id}</div></span> },
              { key: 'part', header: 'Holds', render: (r) => (r.partition.domain_ids?.length ? <code>{r.partition.domain_ids.join(', ')}</code> : <span className="muted">everything else</span>) },
              { key: 'status', header: 'Status', render: (r) => <span><StatusBadge status={r.status} />{r.health && r.health !== 'ok' ? <> <Badge tone="warn">{r.health}</Badge></> : null}</span> },
              { key: 'records', header: 'Records', num: true, render: (r) => fmtNum(r.stats.records ?? 0) },
              { key: 'mem', header: 'Memories', num: true, render: (r) => fmtNum(r.stats.active_memories ?? 0) },
              { key: 'size', header: 'Index / file', num: true, render: (r) => `${((r.stats.matrix_bytes ?? 0) / MIB).toFixed(1)} / ${((r.stats.file_bytes ?? 0) / MIB).toFixed(1)} MiB` },
              { key: 'backup', header: 'Last backup', render: (r) => (r.last_backup_at ? ago(r.last_backup_at) : 'never') },
            ]}
          />
        </>
      )}
    </Section>
  );
}
