import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { describeError } from '../components/Toast';
import { Button, Field, Input } from '../components/ui';
import { BrandMark } from '../shell/Glyph';

function slugify(s: string): string {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
}

/** Organization onboarding: creates the tenant, the executive root unit and the first admin/executive user. */
export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [orgName, setOrgName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
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
      await register({ org_name: orgName.trim(), slug: slug.trim(), email: email.trim(), name: name.trim(), password });
      navigate('/onboarding', { replace: true });
    } catch (err) {
      setError(describeError(err, 'Registration failed'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth__card auth__card--wide card">
        <div className="auth__brand">
          <BrandMark /> Mycelic
        </div>
        <h1 style={{ marginBottom: 4 }}>Register an organization</h1>
        <p className="muted small" style={{ marginBottom: 16 }}>You become the organization administrator and executive of the root unit. Invite people and register evidence holders afterwards.</p>
        <form className="form" onSubmit={(e) => void submit(e)}>
          <fieldset>
            <legend>Organization</legend>
            <div className="form-row">
              <Field label="Organization name" required>
                {(id) => (
                  <Input
                    id={id}
                    required
                    value={orgName}
                    autoFocus
                    onChange={(e) => {
                      setOrgName(e.target.value);
                      if (!slugTouched) setSlug(slugify(e.target.value));
                    }}
                  />
                )}
              </Field>
              <Field label="Slug" required hint="Lowercase letters, digits and dashes; used to identify the organization at sign-in.">
                {(id) => (
                  <Input
                    id={id}
                    required
                    pattern="[a-z0-9][a-z0-9-]*"
                    value={slug}
                    onChange={(e) => {
                      setSlugTouched(true);
                      setSlug(slugify(e.target.value));
                    }}
                  />
                )}
              </Field>
            </div>
          </fieldset>
          <fieldset>
            <legend>Your account</legend>
            <div className="form-row">
              <Field label="Your name" required>{(id) => <Input id={id} required value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />}</Field>
              <Field label="Email" required>{(id) => <Input id={id} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />}</Field>
            </div>
            <div className="form-row" style={{ marginTop: 12 }}>
              <Field label="Password" required hint="At least 8 characters.">
                {(id) => <Input id={id} type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />}
              </Field>
              <Field label="Confirm password" required>{(id) => <Input id={id} type="password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />}</Field>
            </div>
          </fieldset>
          {error ? <div className="error-panel small" role="alert">{error}</div> : null}
          <div className="form-actions">
            <Link to="/login" className="btn btn--ghost">Back to sign in</Link>
            <Button type="submit" variant="primary" busy={busy}>Create organization</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
