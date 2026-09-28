import { useEffect, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { useLiveRefresh } from '../api/events';
import type { Document, DocumentDetail, Holder, MemoryResult } from '../api/types';
import { useAuth, useSession } from '../auth/AuthProvider';
import { DocumentForm } from '../components/DocumentForm';
import { ago, fmtDate, fmtNum, fmtScore, truncate } from '../components/format';
import { Badge, Button, Drawer, Empty, ErrorPanel, Field, Input, Loading, PageHeader, Section, StatusBadge, Table, Textarea } from '../components/ui';
import { useAsync } from '../components/useAsync';
import { useToast } from '../components/Toast';
import { Breadcrumbs } from '../shell/Breadcrumbs';

function MemoryResults({ results }: { results: MemoryResult[] }) {
  if (!results.length) return <Empty>No matching memories.</Empty>;
  return (
    <ul className="list">
      {results.map((m) => (
        <li key={m.memory_id}>
          <div className="small">{m.text}</div>
          <div className="item__meta">
            {m.title ? <span>{m.title}</span> : null}
            <span>{m.kind}</span>
            {m.score !== undefined ? <span>score {fmtScore(m.score)}</span> : null}
            {m.holder_name ? <span>{m.holder_name}</span> : null}
            <span>{fmtDate(m.observed_at ?? m.created_at, false)}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function DocumentDrawer({ holderId, doc, onClose, onChanged }: { holderId: string; doc: Document; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const detail = useAsync<DocumentDetail>(() => api.holders.document(holderId, doc.doc_id), [holderId, doc.doc_id]);
  const [mode, setMode] = useState<'view' | 'revise' | 'retract'>('view');
  const [text, setText] = useState('');
  const [title, setTitle] = useState(doc.title);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (detail.data && mode === 'revise' && !text) setText(detail.data.text);
  }, [detail.data, mode, text]);

  async function revise(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.holders.revise(holderId, doc.doc_id, { text, title: title.trim() || undefined });
      toast.push('New version recorded; affected claims will be re-verified', 'ok');
      onChanged();
      onClose();
    } catch (err) {
      toast.error(err, 'Could not revise');
    } finally {
      setBusy(false);
    }
  }
  async function retract(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.holders.retract(holderId, doc.doc_id, reason.trim());
      toast.push('Document retracted', 'ok');
      onChanged();
      onClose();
    } catch (err) {
      toast.error(err, 'Could not retract');
    } finally {
      setBusy(false);
    }
  }

  const forbidden = detail.error instanceof ApiError && detail.error.status === 403;
  return (
    <Drawer open onClose={onClose} title={doc.title} width={680}>
      <div className="stack">
        <div className="row" style={{ gap: 6 }}>
          <StatusBadge status={doc.status} />
          <Badge tone="outline">{doc.kind}</Badge>
          <Badge tone="outline">v{doc.version}</Badge>
          {doc.domains?.map((d) => (
            <Badge key={d} tone="outline">{d}</Badge>
          ))}
        </div>
        <dl className="kv">
          <dt>Document id</dt>
          <dd className="mono">{doc.doc_id}</dd>
          <dt>Source root</dt>
          <dd className="mono">{doc.source_root_id}</dd>
          <dt>Observed</dt>
          <dd>{fmtDate(doc.observed_at)}</dd>
          <dt>Size</dt>
          <dd>{fmtNum(doc.chars)} chars · {fmtNum(doc.chunks)} chunks</dd>
        </dl>
        {mode === 'view' ? (
          <>
            {detail.loading ? <Loading /> : null}
            {forbidden ? <div className="notice notice--warn">Raw text is only visible to the holder's owner or with a raw grant.</div> : detail.error ? <ErrorPanel error={detail.error} /> : null}
            {detail.data ? <pre>{detail.data.text}</pre> : null}
            {doc.status !== 'retracted' ? (
              <div className="row">
                <Button size="sm" onClick={() => setMode('revise')} disabled={!detail.data}>Revise</Button>
                <Button size="sm" variant="danger" onClick={() => setMode('retract')}>Retract</Button>
              </div>
            ) : null}
          </>
        ) : null}
        {mode === 'revise' ? (
          <form className="form" onSubmit={(e) => void revise(e)}>
            <Field label="Title">{(id) => <Input id={id} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
            <Field label="Text" hint="Creates a new version; claims that cite the old version are re-verified.">
              {(id) => <Textarea id={id} rows={14} value={text} onChange={(e) => setText(e.target.value)} className="textarea--mono" />}
            </Field>
            <div className="form-actions">
              <Button onClick={() => setMode('view')}>Cancel</Button>
              <Button type="submit" variant="primary" busy={busy}>Save new version</Button>
            </div>
          </form>
        ) : null}
        {mode === 'retract' ? (
          <form className="form" onSubmit={(e) => void retract(e)}>
            <div className="notice notice--warn">Retracting marks every claim that depends on this source as retracted or stale.</div>
            <Field label="Reason" required>{(id) => <Input id={id} required value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
            <div className="form-actions">
              <Button onClick={() => setMode('view')}>Cancel</Button>
              <Button type="submit" variant="danger" busy={busy}>Retract document</Button>
            </div>
          </form>
        ) : null}
      </div>
    </Drawer>
  );
}

function HolderDocuments({ holder }: { holder: Holder }) {
  const docs = useAsync(() => api.holders.documents(holder.holder_id), [holder.holder_id]);
  const [selected, setSelected] = useState<Document | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  useLiveRefresh(['document.ingested', 'document.revised', 'document.retracted'], () => void docs.reload());
  return (
    <div className="stack">
      <div className="row row--between">
        <div className="item__meta">
          <span>{holder.mode}</span>
          <span>owner: {holder.owner_name}</span>
          <span>heartbeat {ago(holder.last_heartbeat_at)}</span>
          <span>export: {holder.export_policy?.disclosure ?? 'excerpt'} ≤ {holder.export_policy?.max_excerpt_chars ?? '—'} chars · answers {(holder.export_policy?.answer_scopes ?? []).join('/') || '—'} scope</span>
        </div>
        <Button size="sm" onClick={() => setShowAdd((s) => !s)}>{showAdd ? 'Close' : 'Add document'}</Button>
      </div>
      {showAdd ? (
        <div className="card">
          <DocumentForm holders={[holder]} defaultHolderId={holder.holder_id} onDone={() => { setShowAdd(false); void docs.reload(); }} />
        </div>
      ) : null}
      {docs.loading && !docs.data ? <Loading /> : null}
      {docs.error ? <ErrorPanel error={docs.error} retry={() => void docs.reload(false)} /> : null}
      {docs.data ? (
        <Table
          rows={docs.data.items}
          rowKey={(d) => d.doc_id}
          onRowClick={setSelected}
          empty="No documents in this holder."
          caption={`Documents in ${holder.name}`}
          columns={[
            { key: 'title', header: 'Title', render: (d) => <span className="item__title">{d.title}</span> },
            { key: 'kind', header: 'Kind', render: (d) => d.kind },
            { key: 'status', header: 'Status', render: (d) => <StatusBadge status={d.status} /> },
            { key: 'v', header: 'Version', num: true, render: (d) => d.version },
            { key: 'observed', header: 'Observed', render: (d) => fmtDate(d.observed_at, false) },
            { key: 'chunks', header: 'Chunks', num: true, render: (d) => d.chunks },
            { key: 'domains', header: 'Domains', render: (d) => d.domains?.join(', ') || '—' },
          ]}
        />
      ) : null}
      {selected ? <DocumentDrawer holderId={holder.holder_id} doc={selected} onClose={() => setSelected(null)} onChanged={() => void docs.reload()} /> : null}
    </div>
  );
}

export function MemoryPage() {
  const me = useSession();
  const { refresh } = useAuth();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get('q') ?? '');
  const [results, setResults] = useState<MemoryResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<unknown>(null);
  const holdersState = useAsync(() => api.holders.list(), []);
  const recent = useAsync(() => api.memory.recent(20), []);
  const [activeHolder, setActiveHolder] = useState<string | null>(null);
  useLiveRefresh(['document.ingested', 'document.revised', 'document.retracted', 'holder.status'], () => {
    void recent.reload();
    void holdersState.reload();
    void refresh();
  });

  const mine = (holdersState.data?.items ?? []).filter((h) => (h.owner_type === 'user' && h.owner_id === me.user.user_id) || me.principal.holder_ids.includes(h.holder_id));
  const others = (holdersState.data?.items ?? []).filter((h) => !mine.includes(h));
  const all = [...mine, ...others];
  const current = all.find((h) => h.holder_id === activeHolder) ?? all[0];

  async function search(e?: FormEvent) {
    e?.preventDefault();
    if (!q.trim()) {
      setResults(null);
      return;
    }
    setSearching(true);
    setSearchError(null);
    try {
      const res = await api.memory.search(q.trim(), 12);
      setResults(res.results);
      setParams({ q: q.trim() }, { replace: true });
    } catch (err) {
      setSearchError(err);
    } finally {
      setSearching(false);
    }
  }
  useEffect(() => {
    if (params.get('q')) void search();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Memory' }]} />
      <PageHeader title="Private memory" meta="Your evidence stays in your holders. Only policy-approved excerpts leave with a response you send." />
      <div className="split">
        <div>
          <Section title="Search my holders">
            <form className="row" onSubmit={(e) => void search(e)}>
              <div className="search" style={{ flex: 1 }}>
                <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="m10.5 10.5 3.5 3.5" stroke="currentColor" strokeWidth="1.4" /></svg>
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search across my evidence…" aria-label="Search my memory" />
              </div>
              <Button type="submit" variant="primary" busy={searching}>Search</Button>
            </form>
            {searchError ? <div style={{ marginTop: 8 }}><ErrorPanel error={searchError} /></div> : null}
            {results ? <div style={{ marginTop: 12 }}><MemoryResults results={results} /></div> : null}
          </Section>
          <Section title="Documents" meta={current ? current.name : undefined}>
            {holdersState.loading && !holdersState.data ? <Loading /> : null}
            {holdersState.error ? <ErrorPanel error={holdersState.error} retry={() => void holdersState.reload(false)} /> : null}
            {all.length > 1 ? (
              <div className="tabs" role="tablist">
                {all.map((h) => (
                  <button key={h.holder_id} type="button" role="tab" aria-selected={current?.holder_id === h.holder_id} className={current?.holder_id === h.holder_id ? 'active' : ''} onClick={() => setActiveHolder(h.holder_id)}>
                    {h.name} {mine.includes(h) ? '' : <span className="xs muted">({h.owner_type === 'unit' ? 'unit' : 'shared'})</span>}
                  </button>
                ))}
              </div>
            ) : null}
            {current ? <HolderDocuments key={current.holder_id} holder={current} /> : holdersState.data ? <Empty>You have no holder. Ask an administrator to register one for you, or register it under Admin → Integrations.</Empty> : null}
          </Section>
        </div>
        <aside className="stack">
          <div className="card">
            <div className="card__title">Holders</div>
            {all.length ? (
              <ul className="list list--tight">
                {all.map((h) => (
                  <li key={h.holder_id}>
                    <div className="row row--between">
                      <span className="item__title">{h.name}</span>
                      <StatusBadge status={h.status} />
                    </div>
                    <div className="item__meta">
                      <span>{fmtNum(h.stats?.documents)} docs</span>
                      <span>{fmtNum(h.stats?.memories)} memories</span>
                      <span>{fmtNum(h.stats?.questions_answered)} answered</span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="xs muted">No holders.</div>
            )}
          </div>
          <div className="card">
            <div className="card__title">Recent memories</div>
            {recent.loading && !recent.data ? <Loading /> : null}
            {recent.data ? (
              recent.data.items.length ? (
                <ul className="list list--tight">
                  {recent.data.items.map((m) => (
                    <li key={m.memory_id} className="small">
                      {truncate(m.text, 160)}
                      <div className="xs muted">{m.title ? `${m.title} · ` : ''}{m.kind} · {ago(m.observed_at ?? m.created_at)}</div>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="xs muted">Nothing indexed yet.</div>
              )
            ) : null}
          </div>
        </aside>
      </div>
    </div>
  );
}
