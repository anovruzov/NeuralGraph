import { useState, type FormEvent } from 'react';
import { api } from '../../api/client';
import { useLiveRefresh } from '../../api/events';
import { ROLES, type Invitation, type MembershipRow, type User } from '../../api/types';
import { fmtDate, titleCase } from '../../components/format';
import { Button, Empty, ErrorPanel, Field, Input, Loading, Section, Select, StatusBadge, Table, confirmAction } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { invalidateOrg, useOrg } from '../../components/useOrg';
import { useToast } from '../../components/Toast';

export function MembersPage() {
  const toast = useToast();
  const { units } = useOrg();
  const users = useAsync(() => api.org.users(), []);
  const memberships = useAsync(() => api.org.memberships(), []);
  const invitations = useAsync(() => api.org.invitations(), []);
  useLiveRefresh(['membership.changed', 'org.changed'], () => {
    void users.reload();
    void memberships.reload();
    void invitations.reload();
  });

  // invite form
  const [invEmail, setInvEmail] = useState('');
  const [invRole, setInvRole] = useState('employee');
  const [invUnit, setInvUnit] = useState('');
  const [inviting, setInviting] = useState(false);
  const [lastAccept, setLastAccept] = useState<{ email: string; url: string } | null>(null);
  // membership form
  const [mUser, setMUser] = useState('');
  const [mUnit, setMUnit] = useState('');
  const [mRole, setMRole] = useState('employee');
  const [adding, setAdding] = useState(false);
  const [busyRow, setBusyRow] = useState<string | null>(null);

  async function invite(e: FormEvent) {
    e.preventDefault();
    setInviting(true);
    try {
      const body: { email: string; role: string; unit_id?: string } = { email: invEmail.trim(), role: invRole };
      if (invUnit) body.unit_id = invUnit;
      const res = await api.org.invite(body);
      setLastAccept({ email: res.invitation.email, url: res.accept_url });
      setInvEmail('');
      toast.push('Invitation created', 'ok');
      void invitations.reload();
    } catch (err) {
      toast.error(err, 'Could not invite');
    } finally {
      setInviting(false);
    }
  }

  async function deleteInvitation(inv: Invitation) {
    if (!inv.invitation_id || !confirmAction(`Delete the invitation for ${inv.email}?`)) return;
    try {
      await api.org.deleteInvitation(inv.invitation_id);
      toast.push('Invitation deleted', 'ok');
      void invitations.reload();
    } catch (err) {
      toast.error(err);
    }
  }

  async function addMembership(e: FormEvent) {
    e.preventDefault();
    setAdding(true);
    try {
      await api.org.addMembership({ user_id: mUser, unit_id: mUnit, role: mRole });
      toast.push('Membership added', 'ok');
      invalidateOrg();
      void memberships.reload();
      void users.reload();
    } catch (err) {
      toast.error(err, 'Could not add the membership');
    } finally {
      setAdding(false);
    }
  }

  async function revoke(m: MembershipRow) {
    if (!confirmAction(`Remove ${m.user_name} as ${titleCase(m.role)} of ${m.unit_name}? Pending routes to their holders for that unit are revoked.`)) return;
    setBusyRow(m.membership_id);
    try {
      await api.org.revokeMembership({ user_id: m.user_id, unit_id: m.unit_id, role: m.role });
      toast.push('Membership revoked', 'ok');
      invalidateOrg();
      void memberships.reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusyRow(null);
    }
  }

  async function toggleUser(u: User) {
    const next = u.status === 'active' ? 'disabled' : 'active';
    if (!confirmAction(`${next === 'disabled' ? 'Disable' : 'Re-enable'} ${u.name}?`)) return;
    setBusyRow(u.user_id);
    try {
      await api.org.updateUser(u.user_id, { status: next });
      toast.push(`User ${next}`, 'ok');
      void users.reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusyRow(null);
    }
  }

  const activeUnits = units.filter((u) => !u.archived_at);
  return (
    <div>
      <div className="grid grid--2" style={{ marginBottom: 20 }}>
        <div className="card">
          <div className="card__title">Invite a person</div>
          <form className="form" onSubmit={(e) => void invite(e)}>
            <Field label="Email" required>{(id) => <Input id={id} type="email" required value={invEmail} onChange={(e) => setInvEmail(e.target.value)} />}</Field>
            <div className="form-row">
              <Field label="Role">
                {(id) => (
                  <Select id={id} value={invRole} onChange={(e) => setInvRole(e.target.value)}>
                    {ROLES.map((r) => (
                      <option key={r} value={r}>{titleCase(r)}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Unit" hint="Optional for org_admin">
                {(id) => (
                  <Select id={id} value={invUnit} onChange={(e) => setInvUnit(e.target.value)}>
                    <option value="">No unit</option>
                    {activeUnits.map((u) => (
                      <option key={u.unit_id} value={u.unit_id}>{u.name} ({u.type})</option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>
            <div className="form-actions"><Button type="submit" variant="primary" busy={inviting}>Create invitation</Button></div>
          </form>
          {lastAccept ? (
            <div className="notice notice--ok" style={{ marginTop: 10 }}>
              <strong>Send this link to {lastAccept.email}.</strong> It is shown once.
              <div className="row" style={{ marginTop: 6 }}>
                <Input readOnly value={lastAccept.url} onFocus={(e) => e.currentTarget.select()} aria-label="Accept URL" />
                <Button size="sm" onClick={() => { void navigator.clipboard?.writeText(lastAccept.url); toast.push('Copied'); }}>Copy</Button>
              </div>
            </div>
          ) : null}
        </div>
        <div className="card">
          <div className="card__title">Add a membership</div>
          <form className="form" onSubmit={(e) => void addMembership(e)}>
            <Field label="User" required>
              {(id) => (
                <Select id={id} required value={mUser} onChange={(e) => setMUser(e.target.value)}>
                  <option value="">Choose…</option>
                  {(users.data?.items ?? []).map((u) => (
                    <option key={u.user_id} value={u.user_id}>{u.name} · {u.email}</option>
                  ))}
                </Select>
              )}
            </Field>
            <div className="form-row">
              <Field label="Unit" required>
                {(id) => (
                  <Select id={id} required value={mUnit} onChange={(e) => setMUnit(e.target.value)}>
                    <option value="">Choose…</option>
                    {activeUnits.map((u) => (
                      <option key={u.unit_id} value={u.unit_id}>{u.name} ({u.type})</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Role">
                {(id) => (
                  <Select id={id} value={mRole} onChange={(e) => setMRole(e.target.value)}>
                    {ROLES.map((r) => (
                      <option key={r} value={r}>{titleCase(r)}</option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>
            <div className="form-actions"><Button type="submit" variant="primary" busy={adding}>Add membership</Button></div>
          </form>
        </div>
      </div>
      <Section title="Users" meta={users.data ? `${users.data.items.length}` : undefined}>
        {users.loading && !users.data ? <Loading /> : null}
        {users.error ? <ErrorPanel error={users.error} retry={() => void users.reload(false)} /> : null}
        {users.data ? (
          <Table
            rows={users.data.items}
            rowKey={(u) => u.user_id}
            empty="No users."
            caption="Users"
            columns={[
              { key: 'name', header: 'Name', render: (u) => <span>{u.name} {u.is_demo ? <span className="badge badge--outline">demo</span> : null}</span> },
              { key: 'email', header: 'Email', render: (u) => u.email },
              { key: 'status', header: 'Status', render: (u) => <StatusBadge status={u.status} /> },
              { key: 'mem', header: 'Memberships', render: (u) => <span className="xs">{(u.memberships ?? []).map((m) => `${m.unit_name ?? m.unit_id}: ${titleCase(m.role)}`).join(', ') || '—'}</span> },
              { key: 'id', header: 'Id', render: (u) => <span className="mono xs">{u.user_id}</span> },
              { key: 'created', header: 'Created', render: (u) => fmtDate(u.created_at, false) },
              { key: 'a', header: '', render: (u) => <Button size="sm" variant={u.status === 'active' ? 'danger' : 'default'} busy={busyRow === u.user_id} onClick={() => void toggleUser(u)}>{u.status === 'active' ? 'Disable' : 'Enable'}</Button> },
            ]}
          />
        ) : null}
      </Section>
      <Section title="Memberships" meta={memberships.data ? `${memberships.data.items.length}` : undefined}>
        {memberships.loading && !memberships.data ? <Loading /> : null}
        {memberships.error ? <ErrorPanel error={memberships.error} retry={() => void memberships.reload(false)} /> : null}
        {memberships.data ? (
          <Table
            rows={memberships.data.items}
            rowKey={(m) => m.membership_id}
            empty="No memberships."
            caption="Memberships"
            columns={[
              { key: 'user', header: 'User', render: (m) => <span>{m.user_name} <span className="xs muted">{m.email}</span></span> },
              { key: 'unit', header: 'Unit', render: (m) => <span>{m.unit_name} <span className="xs muted">({m.unit_type})</span></span> },
              { key: 'role', header: 'Role', render: (m) => titleCase(m.role) },
              { key: 'status', header: 'Status', render: (m) => <StatusBadge status={m.status} /> },
              { key: 'created', header: 'Since', render: (m) => fmtDate(m.created_at, false) },
              { key: 'a', header: '', render: (m) => (m.status === 'active' ? <Button size="sm" variant="danger" busy={busyRow === m.membership_id} onClick={() => void revoke(m)}>Revoke</Button> : null) },
            ]}
          />
        ) : null}
      </Section>
      <Section title="Invitations" meta={invitations.data ? `${invitations.data.items.length}` : undefined}>
        {invitations.loading && !invitations.data ? <Loading /> : null}
        {invitations.error ? <ErrorPanel error={invitations.error} retry={() => void invitations.reload(false)} /> : null}
        {invitations.data ? (
          invitations.data.items.length ? (
            <Table
              rows={invitations.data.items}
              rowKey={(i) => i.invitation_id ?? i.email}
              caption="Invitations"
              columns={[
                { key: 'email', header: 'Email', render: (i) => i.email },
                { key: 'role', header: 'Role', render: (i) => titleCase(i.role) },
                { key: 'unit', header: 'Unit', render: (i) => i.unit_name ?? '—' },
                { key: 'status', header: 'Status', render: (i) => <StatusBadge status={i.status} /> },
                { key: 'exp', header: 'Expires', render: (i) => fmtDate(i.expires_at) },
                { key: 'a', header: '', render: (i) => (i.status === 'pending' ? <Button size="sm" variant="danger" onClick={() => void deleteInvitation(i)}>Delete</Button> : null) },
              ]}
            />
          ) : (
            <Empty>No invitations. The accept link is only shown once, right after creation.</Empty>
          )
        ) : null}
      </Section>
    </div>
  );
}
