import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { DemoPersona } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { describeError } from '../components/Toast';
import { Button, Field, Input } from '../components/ui';
import { BrandMark } from '../shell/Glyph';

export function LoginPage() {
  const { login, switchPersona } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get('next') || '/app';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [slug, setSlug] = useState('');
  const [showSlug, setShowSlug] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [personas, setPersonas] = useState<DemoPersona[] | null>(null);

  useEffect(() => {
    // Demo mode exposes the persona list without a session; anything else (401/404) simply hides it.
    api.auth
      .demoPersonas()
      .then((r) => setPersonas(r.items))
      .catch(() => setPersonas(null));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password, slug.trim() || undefined);
      navigate(next.startsWith('/') ? next : '/app', { replace: true });
    } catch (err) {
      setError(describeError(err, 'Sign-in failed'));
    } finally {
      setBusy(false);
    }
  }

  async function demo(user_id: string) {
    setBusy(true);
    setError(null);
    try {
      await switchPersona(user_id);
      navigate('/app', { replace: true });
    } catch (err) {
      setError(describeError(err, 'Could not sign in as the demonstration persona'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth__card card">
        <div className="auth__brand">
          <BrandMark /> Mycelic
        </div>
        <h1 style={{ marginBottom: 4 }}>Sign in</h1>
        <p className="muted small" style={{ marginBottom: 16 }}>Local-first organizational intelligence.</p>
        <form className="form" onSubmit={(e) => void submit(e)}>
          <Field label="Email" required>
            {(id) => <Input id={id} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />}
          </Field>
          <Field label="Password" required>
            {(id) => <Input id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          {showSlug ? (
            <Field label="Organization slug" hint="Only needed when your email belongs to more than one organization.">
              {(id) => <Input id={id} value={slug} onChange={(e) => setSlug(e.target.value)} />}
            </Field>
          ) : (
            <button type="button" className="btn btn--ghost btn--sm" style={{ alignSelf: 'flex-start' }} onClick={() => setShowSlug(true)}>
              Specify organization
            </button>
          )}
          {error ? <div className="error-panel small" role="alert">{error}</div> : null}
          <div className="form-actions">
            <Button type="submit" variant="primary" busy={busy}>Sign in</Button>
          </div>
        </form>
        <hr />
        <p className="small muted">
          New organization? <Link to="/register">Register</Link>
        </p>
        {personas && personas.length ? (
          <>
            <hr />
            <div className="card__title">Demonstration personas</div>
            <p className="xs muted">Simulated organization. Signing in issues a real session for the persona; authorization is the production path.</p>
            <div className="stack stack--sm">
              {personas.map((p) => (
                <button key={p.user_id} type="button" className="btn" style={{ justifyContent: 'space-between' }} disabled={busy} onClick={() => void demo(p.user_id)}>
                  <span>{p.name}</span>
                  <span className="xs muted">{p.level}{p.unit_names.length ? ` · ${p.unit_names.join(', ')}` : ''}</span>
                </button>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
