import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useLiveRefresh } from '../../api/events';
import type { Policies } from '../../api/types';
import { titleCase } from '../../components/format';
import { Button, ErrorPanel, Field, Input, Loading, Select } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { useToast } from '../../components/Toast';

const WEIGHT_KEYS = ['goal_value', 'uncertainty', 'impact', 'missing_evidence', 'information_gain', 'cost'] as const;
const TIERS = ['light', 'standard', 'heavy'];

function NumberField({ label, value, onChange, step = 1, min = 0, hint }: { label: string; value: number; onChange: (v: number) => void; step?: number; min?: number; hint?: string }) {
  return (
    <Field label={label} hint={hint}>
      {(id) => <Input id={id} type="number" step={step} min={min} value={Number.isFinite(value) ? value : ''} onChange={(e) => onChange(Number(e.target.value))} />}
    </Field>
  );
}

export function PoliciesPage() {
  const toast = useToast();
  const state = useAsync(() => api.org.policies(), []);
  const [p, setP] = useState<Policies | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useLiveRefresh(['policy.updated'], () => void state.reload());
  useEffect(() => {
    if (state.data) setP(state.data.policies);
  }, [state.data]);

  async function save(key: keyof Policies | string, value: unknown) {
    setBusy(String(key));
    try {
      const res = await api.org.setPolicy(String(key), value);
      setP(res.policies);
      toast.push(`${titleCase(String(key))} saved`, 'ok');
    } catch (err) {
      toast.error(err, 'Could not save the policy');
    } finally {
      setBusy(null);
    }
  }

  if (state.loading && !p) return <Loading />;
  if (state.error) return <ErrorPanel error={state.error} retry={() => void state.reload(false)} />;
  if (!p) return null;
  const weights = p.priority_weights ?? ({} as Policies['priority_weights']);
  const budget = p.default_goal_budget ?? { tokens: 0, usd: 0, questions: 0, followup_depth: 0 };
  const tiers = p.model_tiers ?? {};
  const exp = p.holder_default_export ?? {};
  return (
    <div className="grid grid--2">
      <div className="card">
        <div className="card__title">Commit gate</div>
        <div className="form">
          <NumberField label="Minimum independent roots" hint="Distinct known source roots needed for a claim to be 'supported' (copies count once; unknown roots never count)." value={p.min_independent_roots} onChange={(v) => setP({ ...p, min_independent_roots: v })} min={1} />
          <div className="form-actions"><Button size="sm" variant="primary" busy={busy === 'min_independent_roots'} onClick={() => void save('min_independent_roots', p.min_independent_roots)}>Save</Button></div>
          <NumberField label="Freshness (days)" hint="Evidence older than this makes a claim 'stale' and schedules re-verification." value={p.freshness_days} onChange={(v) => setP({ ...p, freshness_days: v })} min={1} />
          <div className="form-actions"><Button size="sm" variant="primary" busy={busy === 'freshness_days'} onClick={() => void save('freshness_days', p.freshness_days)}>Save</Button></div>
        </div>
      </div>
      <div className="card">
        <div className="card__title">Default goal budget</div>
        <div className="form">
          <div className="form-row">
            <NumberField label="Tokens" value={budget.tokens} onChange={(v) => setP({ ...p, default_goal_budget: { ...budget, tokens: v } })} />
            <NumberField label="USD" step={0.5} value={budget.usd} onChange={(v) => setP({ ...p, default_goal_budget: { ...budget, usd: v } })} />
            <NumberField label="Questions" value={budget.questions} onChange={(v) => setP({ ...p, default_goal_budget: { ...budget, questions: v } })} />
            <NumberField label="Follow-up depth" value={budget.followup_depth ?? 0} onChange={(v) => setP({ ...p, default_goal_budget: { ...budget, followup_depth: v } })} />
          </div>
          <div className="form-actions"><Button size="sm" variant="primary" busy={busy === 'default_goal_budget'} onClick={() => void save('default_goal_budget', budget)}>Save</Button></div>
        </div>
      </div>
      <div className="card">
        <div className="card__title">Priority weights (heuristic)</div>
        <div className="xs muted" style={{ marginBottom: 8 }}>Question priority = Σ weight × estimate. Estimates are heuristic and labelled as such wherever shown.</div>
        <div className="form">
          <div className="form-row">
            {WEIGHT_KEYS.map((k) => (
              <NumberField key={k} label={titleCase(k)} step={0.1} value={weights[k] ?? 0} onChange={(v) => setP({ ...p, priority_weights: { ...weights, [k]: v } })} />
            ))}
          </div>
          <div className="form-actions"><Button size="sm" variant="primary" busy={busy === 'priority_weights'} onClick={() => void save('priority_weights', weights)}>Save</Button></div>
        </div>
      </div>
      <div className="card">
        <div className="card__title">Model tier per task</div>
        <div className="form">
          <div className="form-row">
            {Object.entries(tiers).map(([task, tier]) => (
              <Field key={task} label={titleCase(task)}>
                {(id) => (
                  <Select id={id} value={tier} onChange={(e) => setP({ ...p, model_tiers: { ...tiers, [task]: e.target.value } })}>
                    {TIERS.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </Select>
                )}
              </Field>
            ))}
          </div>
          <div className="form-actions"><Button size="sm" variant="primary" busy={busy === 'model_tiers'} onClick={() => void save('model_tiers', tiers)}>Save</Button></div>
        </div>
      </div>
      <div className="card">
        <div className="card__title">Holder default export policy</div>
        <div className="form">
          <div className="form-row">
            <Field label="Disclosure">
              {(id) => (
                <Select id={id} value={exp.disclosure ?? 'excerpt'} onChange={(e) => setP({ ...p, holder_default_export: { ...exp, disclosure: e.target.value } })}>
                  <option value="excerpt">excerpt</option>
                  <option value="summary">summary</option>
                  <option value="none">none</option>
                </Select>
              )}
            </Field>
            <NumberField label="Max excerpt chars" value={exp.max_excerpt_chars ?? 480} onChange={(v) => setP({ ...p, holder_default_export: { ...exp, max_excerpt_chars: v } })} />
            <Field label="Answer scopes" hint="Comma separated: unit, org">
              {(id) => <Input id={id} value={(exp.answer_scopes ?? []).join(', ')} onChange={(e) => setP({ ...p, holder_default_export: { ...exp, answer_scopes: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) } })} />}
            </Field>
          </div>
          <div className="form-actions"><Button size="sm" variant="primary" busy={busy === 'holder_default_export'} onClick={() => void save('holder_default_export', exp)}>Save</Button></div>
        </div>
      </div>
    </div>
  );
}
