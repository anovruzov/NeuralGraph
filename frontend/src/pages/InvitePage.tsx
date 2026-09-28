import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthProvider';
import { titleCase } from '../components/format';
import { describeError } from '../components/Toast';
import { Button, ErrorPanel, Field, Input, Loading } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { BrandMark } from '../shell/Glyph';

export function InvitePage() {
  const { token = '' } = useParams();
  const { acceptInvitation } = useAuth();
  const navigate = useNavigate();
  const inv = useAsync(() => api.auth.invitation(token), [token]);
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await acceptInvitation(token, name.trim(), password);
      navigate('/app', { replace: true });
    } catch (err) {
      setError(describeError(err, 'Could not accept the invitation'));
    } finally {
      setBusy(false);
    }
  }

  const invitation = inv.data?.invitation;
  const usable = invitation && (invitation.status === 'pending' || invitation.status === 'active' || invitation.status === 'open');
  return (
    <div className="auth">
      <div className="auth__card card">
        <div className="auth__brand">
          <BrandMark /> Mycelic
        </div>
        <h1 style={{ marginBottom: 12 }}>Accept invitation</h1>
        {inv.loading ? <Loading /> : null}
        {inv.error ? <ErrorPanel error={inv.error} title="This invitation could not be loaded" /> : null}
        {invitation ? (
          <>
            <dl className="kv" style={{ marginBottom: 16 }}>
              <dt>Organization</dt>
              <dd>{invitation.org_name ?? '—'}</dd>
              <dt>Email</dt>
              <dd>{invitation.email}</dd>
              <dt>Role</dt>
              <dd>{titleCase(invitation.role)}</dd>
              <dt>Unit</dt>
              <dd>{invitation.unit_name ?? 'Organization-wide'}</dd>
              <dt>Status</dt>
              <dd>{titleCase(invitation.status)}</dd>
            </dl>
            {usable ? (
              <form className="form" onSubmit={(e) => void submit(e)}>
                <Field label="Your name" required>{(id) => <Input id={id} required value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" autoFocus />}</Field>
                <Field label="Password" required hint="At least 8 characters.">
                  {(id) => <Input id={id} type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />}
                </Field>
                <Field label="Confirm password" required>{(id) => <Input id={id} type="password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />}</Field>
                {error ? <div className="error-panel small" role="alert">{error}</div> : null}
                <div className="form-actions">
                  <Button type="submit" variant="primary" busy={busy}>Join {invitation.org_name ?? 'organization'}</Button>
                </div>
              </form>
            ) : (
              <div className="notice notice--warn">This invitation is {invitation.status}. Ask your administrator for a new one.</div>
            )}
          </>
        ) : null}
        <hr />
        <p className="small muted">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
