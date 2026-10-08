import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { DemoPersona } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { useToast } from '../components/Toast';

/** Demo-mode only: switch to a seeded persona through POST /demo/switch (a real session; authorization is unchanged). */
export function PersonaSwitcher() {
  const { me, switchPersona } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [personas, setPersonas] = useState<DemoPersona[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!me?.demo_mode) return;
    api.auth
      .demoPersonas()
      .then((r) => setPersonas(r.items))
      .catch(() => setPersonas([]));
  }, [me?.demo_mode]);

  if (!me?.demo_mode || personas.length === 0) return null;

  async function onChange(user_id: string) {
    if (!user_id || user_id === me?.user.user_id) return;
    setBusy(true);
    try {
      await switchPersona(user_id);
      navigate('/app');
      toast.push('Switched persona');
    } catch (err) {
      toast.error(err, 'Could not switch persona');
    } finally {
      setBusy(false);
    }
  }

  return (
    <label className="row persona-switcher" style={{ gap: 6 }}>
      <span className="xs muted nowrap persona-switcher__label">Persona</span>
      <select className="select select--sm persona-switcher__select" value={me.user.user_id} disabled={busy} onChange={(e) => void onChange(e.target.value)} aria-label="Switch demonstration persona">
        {!personas.some((p) => p.user_id === me.user.user_id) ? <option value={me.user.user_id}>{me.user.name}</option> : null}
        {personas.map((p) => (
          <option key={p.user_id} value={p.user_id}>
            {p.name} — {p.level}{p.unit_names.length ? ` · ${p.unit_names.join(', ')}` : ''}
          </option>
        ))}
      </select>
    </label>
  );
}
