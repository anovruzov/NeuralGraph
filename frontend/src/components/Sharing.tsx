// Sharing controls: grants given/received, create grant, revoke (GET/POST/DELETE /grants).
import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { Goal, Grant, GrantBody, GrantLevel, GrantResourceType, Holder } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { fmtDate, titleCase } from './format';
import { Button, Empty, ErrorPanel, Field, Input, Loading, Select, StatusBadge, Table, confirmAction } from './ui';
import { useAsync } from './useAsync';
import { useOrg } from './useOrg';
import { useToast } from './Toast';

const LEVEL_HELP: Record<GrantLevel, string> = {
  read: 'see the artifact exists and its summary',
  artifact: 'see policy-approved disclosures / search the holder',
  raw: 'read the full evidence text',
};

export function GrantForm({ holders, goals, onDone }: { holders: Holder[]; goals: Goal[]; onDone: () => void }) {
  const toast = useToast();
  const { units } = useOrg();
  const [granteeType, setGranteeType] = useState<'user' | 'unit'>('unit');
  const [granteeId, setGranteeId] = useState('');
  const [resourceType, setResourceType] = useState<GrantResourceType>('holder');
  const [resourceId, setResourceId] = useState(holders[0]?.holder_id ?? '');
  const [level, setLevel] = useState<GrantLevel>('artifact');
  const [reason, setReason] = useState('');
  const [expiry, setExpiry] = useState('');
  const [busy, setBusy] = useState(false);

  const ownResources: { id: string; label: string }[] =
    resourceType === 'holder'
      ? holders.map((h) => ({ id: h.holder_id, label: h.name }))
      : resourceType === 'goal'
        ? goals.map((g) => ({ id: g.goal_id, label: g.title }))
        : [];

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const body: GrantBody = { grantee_type: granteeType, grantee_id: granteeId.trim(), resource_type: resourceType, resource_id: resourceId.trim(), level };
      if (reason.trim()) body.reason = reason.trim();
      if (expiry) body.expires_in_seconds = Number(expiry);
      await api.grants.create(body);
      toast.push('Access granted', 'ok');
      setReason('');
      onDone();
    } catch (err) {
      toast.error(err, 'Could not create the grant');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={(e) => void submit(e)} aria-label="Share access">
      <div className="form-row">
        <Field label="Share with">
          {(id) => (
            <Select id={id} value={granteeType} onChange={(e) => { setGranteeType(e.target.value as 'user' | 'unit'); setGranteeId(''); }}>
              <option value="unit">A unit</option>
              <option value="user">A person</option>
            </Select>
          )}
        </Field>
        <Field label={granteeType === 'unit' ? 'Unit' : 'User id'} required hint={granteeType === 'user' ? 'usr_… (from the members list or the network view)' : undefined}>
          {(id) =>
            granteeType === 'unit' ? (
              <Select id={id} value={granteeId} required onChange={(e) => setGranteeId(e.target.value)}>
                <option value="">Choose a unit…</option>
                {units.filter((u) => !u.archived_at).map((u) => (
                  <option key={u.unit_id} value={u.unit_id}>{u.name} ({u.type})</option>
                ))}
              </Select>
            ) : (
              <Input id={id} value={granteeId} required onChange={(e) => setGranteeId(e.target.value)} placeholder="usr_…" />
            )
          }
        </Field>
      </div>
      <div className="form-row">
        <Field label="Resource type">
          {(id) => (
            <Select id={id} value={resourceType} onChange={(e) => { const t = e.target.value as GrantResourceType; setResourceType(t); setResourceId(t === 'holder' ? holders[0]?.holder_id ?? '' : t === 'goal' ? goals[0]?.goal_id ?? '' : ''); }}>
              <option value="holder">Holder (my memory)</option>
              <option value="goal">Goal</option>
              <option value="claim">Claim</option>
              <option value="discovery">Discovery</option>
              <option value="evidence_ref">Evidence reference</option>
            </Select>
          )}
        </Field>
        <Field label="Resource" required hint={ownResources.length ? undefined : 'Paste the id of a resource you own'}>
          {(id) =>
            ownResources.length ? (
              <Select id={id} value={resourceId} required onChange={(e) => setResourceId(e.target.value)}>
                {ownResources.map((r) => (
                  <option key={r.id} value={r.id}>{r.label}</option>
                ))}
              </Select>
            ) : (
              <Input id={id} value={resourceId} required onChange={(e) => setResourceId(e.target.value)} placeholder={`${resourceType}_…`} />
            )
          }
        </Field>
      </div>
      <div className="form-row">
        <Field label="Level" hint={LEVEL_HELP[level]}>
          {(id) => (
            <Select id={id} value={level} onChange={(e) => setLevel(e.target.value as GrantLevel)}>
              <option value="read">read</option>
              <option value="artifact">artifact</option>
              <option value="raw">raw</option>
            </Select>
          )}
        </Field>
        <Field label="Expires">
          {(id) => (
            <Select id={id} value={expiry} onChange={(e) => setExpiry(e.target.value)}>
              <option value="">Never</option>
              <option value="86400">1 day</option>
              <option value="604800">7 days</option>
              <option value="2592000">30 days</option>
              <option value="7776000">90 days</option>
            </Select>
          )}
        </Field>
      </div>
      <Field label="Reason" hint="Recorded in the audit log">
        {(id) => <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />}
      </Field>
      <div className="form-actions">
        <Button type="submit" variant="primary" busy={busy}>Grant access</Button>
      </div>
    </form>
  );
}

function GrantsTable({ rows, canRevoke, onRevoke, direction }: { rows: Grant[]; canRevoke: boolean; onRevoke: (g: Grant) => void; direction: 'given' | 'received' }) {
  const { unitName } = useOrg();
  return (
    <Table
      rows={rows}
      rowKey={(g) => g.grant_id}
      empty={direction === 'given' ? 'You have not shared anything.' : 'Nothing has been shared with you.'}
      columns={[
        {
          key: 'who',
          header: direction === 'given' ? 'Grantee' : 'Grantor',
          render: (g) =>
            direction === 'given' ? (
              <span>{g.grantee_type === 'unit' ? unitName(g.grantee_id) : g.grantee_name ?? g.grantee_id} <span className="xs muted">({g.grantee_type})</span></span>
            ) : (
              <span>{g.grantor_name ?? g.grantor_id}</span>
            ),
        },
        { key: 'res', header: 'Resource', render: (g) => <span>{titleCase(g.resource_type)} <span className="mono xs">{g.resource_name ?? g.resource_id}</span></span> },
        { key: 'level', header: 'Level', render: (g) => <StatusBadge status={g.level} /> },
        { key: 'status', header: 'Status', render: (g) => <StatusBadge status={g.status} /> },
        { key: 'exp', header: 'Expires', render: (g) => (g.expires_at ? fmtDate(g.expires_at) : 'never') },
        { key: 'reason', header: 'Reason', render: (g) => g.reason || '—' },
        {
          key: 'actions',
          header: '',
          render: (g) =>
            canRevoke && g.status === 'active' ? (
              <Button size="sm" variant="danger" onClick={() => onRevoke(g)}>Revoke</Button>
            ) : null,
        },
      ]}
    />
  );
}

export function SharingSection({ holders, goals }: { holders: Holder[]; goals: Goal[] }) {
  const toast = useToast();
  const me = useSession();
  const state = useAsync(() => api.grants.list(), []);
  const [showForm, setShowForm] = useState(false);
  useLiveRefresh(['grant.changed'], () => void state.reload());

  async function revoke(g: Grant) {
    if (!confirmAction('Revoke this grant? Pending routes that depended on it are withdrawn.')) return;
    try {
      await api.grants.revoke(g.grant_id);
      toast.push('Grant revoked', 'ok');
      void state.reload();
    } catch (err) {
      toast.error(err, 'Could not revoke');
    }
  }

  return (
    <div className="stack">
      <div className="row row--between">
        <span className="small muted">Explicit grants are the only way private evidence becomes visible to others.</span>
        <Button size="sm" onClick={() => setShowForm((s) => !s)} aria-expanded={showForm}>{showForm ? 'Close' : 'Share access'}</Button>
      </div>
      {showForm ? (
        <div className="card">
          <GrantForm holders={holders} goals={goals.filter((g) => g.owner_type === 'user' && g.owner_id === me.user.user_id)} onDone={() => { setShowForm(false); void state.reload(); }} />
        </div>
      ) : null}
      {state.loading && !state.data ? <Loading /> : null}
      {state.error ? <ErrorPanel error={state.error} retry={() => void state.reload(false)} /> : null}
      {state.data ? (
        <>
          <h4>Given by me</h4>
          <GrantsTable rows={state.data.given} canRevoke onRevoke={(g) => void revoke(g)} direction="given" />
          <h4>Received</h4>
          {state.data.received.length ? <GrantsTable rows={state.data.received} canRevoke={me.principal.is_admin} onRevoke={(g) => void revoke(g)} direction="received" /> : <Empty>Nothing has been shared with you.</Empty>}
        </>
      ) : null}
    </div>
  );
}
