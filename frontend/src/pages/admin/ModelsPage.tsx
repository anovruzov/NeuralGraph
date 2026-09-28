import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { ModelsResponse } from '../../api/types';
import { titleCase } from '../../components/format';
import { Button, ErrorPanel, Field, Loading, Select, Table } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { useToast } from '../../components/Toast';

export function ModelsPage() {
  const toast = useToast();
  const state = useAsync<ModelsResponse>(() => api.admin.models(), []);
  const [policy, setPolicy] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (state.data) setPolicy(state.data.policy_tiers ?? {});
  }, [state.data]);

  async function save() {
    setBusy(true);
    try {
      const res = await api.admin.setModels(policy);
      setPolicy(res.policy_tiers ?? policy);
      toast.push('Task tiers saved', 'ok');
      void state.reload();
    } catch (err) {
      toast.error(err, 'Could not save');
    } finally {
      setBusy(false);
    }
  }

  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorPanel error={state.error} retry={() => void state.reload(false)} />;
  const d = state.data;
  if (!d) return null;
  const tierNames = Object.keys(d.tiers ?? {});
  const rows = tierNames.map((t) => ({ tier: t, ...(d.tiers[t] as { provider: string; model: string }) }));
  return (
    <div className="grid grid--2">
      <div className="stack">
        <div className="card">
          <div className="card__title">Configured tiers (read-only)</div>
          <div className="notice notice--info xs" style={{ marginBottom: 8 }}>Provider API keys are environment-only (MYCELIC_* variables on the server). The UI never sees or sets them.</div>
          <Table
            rows={rows}
            rowKey={(r) => r.tier}
            empty="No model tiers configured. Set the provider environment variables and restart the API."
            columns={[
              { key: 'tier', header: 'Tier', render: (r) => titleCase(r.tier) },
              { key: 'provider', header: 'Provider', render: (r) => r.provider },
              { key: 'model', header: 'Model', render: (r) => <span className="mono">{r.model}</span> },
            ]}
          />
          <dl className="kv" style={{ marginTop: 10 }}>
            <dt>Embedding</dt>
            <dd>{d.embedding?.provider} · <span className="mono">{d.embedding?.model}</span></dd>
          </dl>
        </div>
        <div className="card">
          <div className="card__title">Prices</div>
          {Object.keys(d.prices ?? {}).length ? <pre className="xs">{JSON.stringify(d.prices, null, 2)}</pre> : <div className="xs muted">No price table.</div>}
        </div>
      </div>
      <div className="card">
        <div className="card__title">Tier per task (policy)</div>
        <div className="xs muted" style={{ marginBottom: 8 }}>Which configured tier each model task runs on. Light: drafting and classification; standard: evaluation and verification; heavy: synthesis.</div>
        <div className="form">
          {Object.keys(policy).length ? (
            <div className="form-row">
              {Object.entries(policy).map(([task, tier]) => (
                <Field key={task} label={titleCase(task)}>
                  {(id) => (
                    <Select id={id} value={tier} onChange={(e) => setPolicy({ ...policy, [task]: e.target.value })}>
                      {(tierNames.length ? tierNames : ['light', 'standard', 'heavy']).map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </Select>
                  )}
                </Field>
              ))}
            </div>
          ) : (
            <div className="xs muted">No task policy returned.</div>
          )}
          <div className="form-actions"><Button variant="primary" busy={busy} onClick={() => void save()}>Save</Button></div>
        </div>
      </div>
    </div>
  );
}
