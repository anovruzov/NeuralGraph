import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { UNIT_TYPES, type Unit, type UnitTreeNode } from '../../api/types';
import { titleCase } from '../../components/format';
import { Badge, Button, Checkbox, ErrorPanel, Field, Input, Loading, Select, confirmAction } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { invalidateOrg, useOrg } from '../../components/useOrg';
import { useToast } from '../../components/Toast';
import { Glyph } from '../../shell/Glyph';

function TreeNode({ node, selected, onSelect }: { node: UnitTreeNode; selected: string | null; onSelect: (u: Unit) => void }) {
  const u = node.unit;
  return (
    <li>
      <div className={`tree__node ${selected === u.unit_id ? 'selected' : ''}`} onClick={() => onSelect(u)} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(u); }}>
        <Glyph type="unit" size={12} />
        <span className="name" style={u.archived_at ? { textDecoration: 'line-through', color: 'var(--text-3)' } : undefined}>{u.name}</span>
        <Badge tone="outline">{u.type}</Badge>
        <span className="xs muted">{u.member_count} members</span>
      </div>
      {node.children?.length ? (
        <ul>
          {node.children.map((c) => (
            <TreeNode key={c.unit.unit_id} node={c} selected={selected} onSelect={onSelect} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function HierarchyPage() {
  const toast = useToast();
  const { org, units, error, reload } = useOrg();
  const [selected, setSelected] = useState<Unit | null>(null);
  const [name, setName] = useState('');
  const [parent, setParent] = useState('');
  const [busy, setBusy] = useState(false);
  // create form
  const [newType, setNewType] = useState('team');
  const [newName, setNewName] = useState('');
  const [newParent, setNewParent] = useState('');
  // project scope
  const [scope, setScope] = useState<string[]>([]);
  const detail = useAsync(() => api.org.unit(selected!.unit_id), [selected?.unit_id], { enabled: Boolean(selected) });

  useEffect(() => {
    if (selected) {
      setName(selected.name);
      setParent(selected.parent_id ?? '');
    }
  }, [selected]);
  useEffect(() => {
    if (selected && units.length) {
      const fresh = units.find((u) => u.unit_id === selected.unit_id);
      if (fresh && fresh !== selected) setSelected(fresh);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [units]);
  useEffect(() => {
    const s = (selected?.settings as { scope_unit_ids?: string[] } | undefined)?.scope_unit_ids ?? (detail.data?.unit.settings as { scope_unit_ids?: string[] } | undefined)?.scope_unit_ids ?? [];
    setScope(s);
  }, [selected, detail.data]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const body: { type: string; name: string; parent_id?: string } = { type: newType, name: newName.trim() };
      if (newParent) body.parent_id = newParent;
      const res = await api.org.createUnit(body);
      toast.push(`Created ${res.unit.name}`, 'ok');
      setNewName('');
      invalidateOrg();
      reload();
      setSelected(res.unit);
    } catch (err) {
      toast.error(err, 'Could not create the unit');
    } finally {
      setBusy(false);
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!selected) return;
    setBusy(true);
    try {
      const body: { name?: string; parent_id?: string | null } = {};
      if (name.trim() !== selected.name) body.name = name.trim();
      if ((parent || null) !== (selected.parent_id ?? null)) body.parent_id = parent || null;
      if (!Object.keys(body).length) {
        toast.push('Nothing changed');
        return;
      }
      const res = await api.org.updateUnit(selected.unit_id, body);
      toast.push('Unit updated', 'ok');
      invalidateOrg();
      reload();
      setSelected(res.unit);
    } catch (err) {
      toast.error(err, 'Could not update the unit');
    } finally {
      setBusy(false);
    }
  }

  async function archive(flag: boolean) {
    if (!selected) return;
    if (flag && !confirmAction(`Archive ${selected.name}? Its goals and artifacts remain but the unit stops receiving routes.`)) return;
    setBusy(true);
    try {
      await api.org.updateUnit(selected.unit_id, { archived: flag });
      toast.push(flag ? 'Unit archived' : 'Unit restored', 'ok');
      invalidateOrg();
      reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  }

  async function saveScope() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.org.setProjectScope(selected.unit_id, scope);
      toast.push('Project scope saved', 'ok');
      invalidateOrg();
      reload();
      void detail.reload();
    } catch (err) {
      toast.error(err, 'Could not save the scope');
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorPanel error={error} retry={reload} />;
  if (!org) return <Loading />;
  const parentOptions = units.filter((u) => !u.archived_at && u.type !== 'project' && u.unit_id !== selected?.unit_id);
  return (
    <div className="split">
      <div>
        <div className="row row--between" style={{ marginBottom: 8 }}>
          <span className="small muted">{org.counts.users} users · {org.counts.memberships} memberships · {org.counts.holders} holders</span>
        </div>
        <div className="card tree">
          <ul>
            {org.tree.map((n) => (
              <TreeNode key={n.unit.unit_id} node={n} selected={selected?.unit_id ?? null} onSelect={setSelected} />
            ))}
          </ul>
          {units.filter((u) => u.type === 'project').length ? (
            <>
              <div className="card__title" style={{ marginTop: 12 }}>Projects (cross-functional)</div>
              <ul>
                {units.filter((u) => u.type === 'project').map((u) => (
                  <TreeNode key={u.unit_id} node={{ unit: u, children: [] }} selected={selected?.unit_id ?? null} onSelect={setSelected} />
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </div>
      <aside className="stack">
        <div className="card">
          <div className="card__title">Create unit</div>
          <form className="form" onSubmit={(e) => void create(e)}>
            <div className="form-row">
              <Field label="Type">
                {(id) => (
                  <Select id={id} value={newType} onChange={(e) => setNewType(e.target.value)}>
                    {UNIT_TYPES.filter((t) => t !== 'executive').map((t) => (
                      <option key={t} value={t}>{titleCase(t)}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Parent">
                {(id) => (
                  <Select id={id} value={newParent} onChange={(e) => setNewParent(e.target.value)}>
                    <option value="">Root (executive)</option>
                    {parentOptions.map((u) => (
                      <option key={u.unit_id} value={u.unit_id}>{u.name} ({u.type})</option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>
            <Field label="Name" required>{(id) => <Input id={id} required value={newName} onChange={(e) => setNewName(e.target.value)} />}</Field>
            <div className="form-actions"><Button type="submit" variant="primary" busy={busy}>Create</Button></div>
          </form>
        </div>
        {selected ? (
          <div className="card">
            <div className="row row--between">
              <div className="card__title" style={{ marginBottom: 0 }}>{selected.name}</div>
              <Link to={`/app/unit/${selected.unit_id}`} className="xs">Workspace →</Link>
            </div>
            <dl className="kv" style={{ margin: '8px 0' }}>
              <dt>Id</dt>
              <dd className="mono">{selected.unit_id}</dd>
              <dt>Type</dt>
              <dd>{selected.type} · level {selected.level}</dd>
              <dt>Path</dt>
              <dd className="mono xs">{selected.path}</dd>
            </dl>
            <form className="form" onSubmit={(e) => void save(e)}>
              <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
              {selected.type !== 'executive' ? (
                <Field label="Parent (move)">
                  {(id) => (
                    <Select id={id} value={parent} onChange={(e) => setParent(e.target.value)}>
                      <option value="">Root (executive)</option>
                      {parentOptions.map((u) => (
                        <option key={u.unit_id} value={u.unit_id}>{u.name} ({u.type})</option>
                      ))}
                    </Select>
                  )}
                </Field>
              ) : null}
              <div className="form-actions" style={{ justifyContent: 'space-between' }}>
                {selected.type !== 'executive' ? (
                  selected.archived_at ? <Button variant="ghost" busy={busy} onClick={() => void archive(false)}>Restore</Button> : <Button variant="danger" busy={busy} onClick={() => void archive(true)}>Archive</Button>
                ) : <span />}
                <Button type="submit" variant="primary" busy={busy}>Save</Button>
              </div>
            </form>
            {selected.type === 'project' ? (
              <div style={{ marginTop: 12 }}>
                <div className="card__title">Project scope</div>
                <div className="xs muted" style={{ marginBottom: 6 }}>Units this project spans; members of the project may see those units' knowledge.</div>
                <div className="stack stack--sm" style={{ maxHeight: 220, overflowY: 'auto' }}>
                  {units.filter((u) => u.type !== 'project' && !u.archived_at).map((u) => (
                    <Checkbox key={u.unit_id} label={`${u.name} (${u.type})`} checked={scope.includes(u.unit_id)} onChange={(e) => setScope((s) => (e.target.checked ? [...s, u.unit_id] : s.filter((x) => x !== u.unit_id)))} />
                  ))}
                </div>
                <div className="form-actions" style={{ marginTop: 8 }}><Button size="sm" variant="primary" busy={busy} onClick={() => void saveScope()}>Save scope</Button></div>
              </div>
            ) : null}
            {detail.data ? (
              <div style={{ marginTop: 12 }}>
                <div className="card__title">Members ({detail.data.members.length})</div>
                <ul className="list list--tight">
                  {detail.data.members.map((m) => (
                    <li key={`${m.user_id}-${m.role}`} className="small row row--between">
                      <span>{m.user_name} <span className="xs muted">{m.email}</span></span>
                      <Badge tone="outline">{titleCase(m.role)}</Badge>
                    </li>
                  ))}
                </ul>
                {detail.data.holders.length ? <div className="xs muted" style={{ marginTop: 6 }}>Holders: {detail.data.holders.map((h) => h.name).join(', ')}</div> : null}
                {detail.data.projects.length ? <div className="xs muted">Projects spanning: {detail.data.projects.map((p) => p.name).join(', ')}</div> : null}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="card"><div className="xs muted">Select a unit in the tree to rename, move, archive or edit its project scope.</div></div>
        )}
      </aside>
    </div>
  );
}
