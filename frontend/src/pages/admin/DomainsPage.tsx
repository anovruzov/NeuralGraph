// The tenant's knowledge-domain taxonomy: stable dot-path ids, independent of the org hierarchy. Holders classify
// ingested records into it and publish the domains they hold (counts only); questions are routed by it. Personal
// domains live only in their holder and never appear here.
import { useMemo, useState, type FormEvent } from 'react';
import { api } from '../../api/client';
import type { TaxonomyDomain } from '../../api/types';
import { Badge, Button, ErrorPanel, Field, Input, Loading, Section, Select, Table, confirmAction } from '../../components/ui';
import { useAsync } from '../../components/useAsync';
import { useToast } from '../../components/Toast';

export function DomainsPage() {
  const toast = useToast();
  const tax = useAsync(() => api.integrations.domains(), []);
  const [parent, setParent] = useState('');
  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');
  const [alias, setAlias] = useState('');
  const [aliasTo, setAliasTo] = useState('');
  const [busy, setBusy] = useState(false);
  const items = useMemo(() => (tax.data?.items ?? []).slice().sort((a, b) => a.domain_id.localeCompare(b.domain_id)), [tax.data]);

  if (tax.loading && !tax.data) return <Loading />;
  if (tax.error) return <ErrorPanel error={tax.error} retry={() => void tax.reload()} />;

  async function save(body: Parameters<typeof api.integrations.setDomains>[0], ok: string) {
    setBusy(true);
    try {
      const res = await api.integrations.setDomains(body);
      tax.setData(res);
      toast.push(ok, 'ok');
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  }

  function add(e: FormEvent) {
    e.preventDefault();
    const s = slug.trim().toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-|-$/g, '');
    if (!s) return;
    const domain_id = parent ? `${parent}.${s}` : s;
    void save({ upsert: [{ domain_id, name: name.trim() || s }] }, `Added ${domain_id}`).then(() => { setSlug(''); setName(''); });
  }

  return (
    <div className="stack">
      <Section title="Knowledge domains" meta={`version ${tax.data?.taxonomy_version ?? 1} · ${tax.data?.configured ? 'customized' : 'defaults'}`}>
        <p className="sm muted" style={{ maxWidth: 760 }}>
          Domains are what records are about, independent of who holds them. A question about <code>engineering</code> reaches holders whose records are in
          any of its subdomains. Deprecating a domain keeps its records but routes nothing new to it. Ids never change and are never reused.
        </p>
        <Table<TaxonomyDomain>
          rowKey={(d) => d.domain_id}
          rows={items}
          columns={[
            { key: 'id', header: 'Domain', render: (d) => <><code style={{ paddingLeft: (d.domain_id.split('.').length - 1) * 16 }}>{d.domain_id}</code><div className="xs muted">{d.name}</div></> },
            { key: 'status', header: 'Status', render: (d) => <Badge tone={d.status === 'active' ? 'ok' : 'outline'}>{d.status}</Badge> },
            {
              key: 'act', header: '', render: (d) =>
                d.status === 'active' && d.domain_id !== 'unclassified' ? (
                  <Button size="sm" variant="ghost" disabled={busy}
                    onClick={() => { if (confirmAction(`Deprecate ${d.domain_id}? Its records stay; nothing new is routed to it.`)) void save({ deprecate: [d.domain_id] }, `Deprecated ${d.domain_id}`); }}>
                    Deprecate
                  </Button>
                ) : null,
            },
          ]}
        />
      </Section>
      <Section title="Add a domain">
        <form onSubmit={add} className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
          <Field label="Under">
            {(id) => (
              <Select id={id} value={parent} onChange={(e) => setParent(e.target.value)}>
                <option value="">(top level)</option>
                {items.filter((d) => d.status === 'active' && d.domain_id.split('.').length < 4).map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_id}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Id segment">{(id) => <Input id={id} value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="payments" required />}</Field>
          <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Payments" />}</Field>
          <Button type="submit" variant="primary" busy={busy}>Add</Button>
        </form>
      </Section>
      <Section title="Aliases" meta="Older flat names that resolve to a domain (e.g. deployments → infrastructure.ci-cd)">
        <form onSubmit={(e) => { e.preventDefault(); if (alias.trim() && aliasTo) void save({ aliases: [{ alias: alias.trim().toLowerCase(), domain_id: aliasTo }] }, 'Alias saved').then(() => setAlias('')); }}
          className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
          <Field label="Alias">{(id) => <Input id={id} value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="billing" />}</Field>
          <Field label="Domain">
            {(id) => (
              <Select id={id} value={aliasTo} onChange={(e) => setAliasTo(e.target.value)}>
                <option value="">Choose…</option>
                {items.filter((d) => d.status === 'active').map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_id}</option>)}
              </Select>
            )}
          </Field>
          <Button type="submit" busy={busy}>Save alias</Button>
        </form>
        <div className="xs muted" style={{ marginTop: 8 }}>
          {(tax.data?.aliases ?? []).map((a) => `${a.alias} → ${a.domain_id}`).join(' · ')}
        </div>
      </Section>
    </div>
  );
}
