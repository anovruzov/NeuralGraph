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
    </div>
  );
}
