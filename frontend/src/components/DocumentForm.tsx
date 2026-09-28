import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { Document, Holder } from '../api/types';
import { Button, Field, Input, Select, Textarea, Toggle } from './ui';
import { useToast } from './Toast';

/** Add a document to one of the caller's holders: pasted text (JSON body) or a file (multipart). */
export function DocumentForm({ holders, defaultHolderId, onDone }: { holders: Holder[]; defaultHolderId?: string; onDone?: (doc: Document, holderId: string) => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<'text' | 'file'>('text');
  const [holderId, setHolderId] = useState(defaultHolderId ?? holders[0]?.holder_id ?? '');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [kind, setKind] = useState('note');
  const [observedAt, setObservedAt] = useState('');
  const [domains, setDomains] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const effectiveHolder = holders.some((h) => h.holder_id === holderId) ? holderId : holders[0]?.holder_id ?? '';

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!effectiveHolder) {
      toast.push('Register a holder first.', 'error');
      return;
    }
    setBusy(true);
    try {
      const domainList = domains.split(',').map((s) => s.trim()).filter(Boolean);
      let doc: Document;
      if (mode === 'file') {
        if (!file) {
          toast.push('Choose a file (.txt, .md, .json, .csv).', 'error');
          setBusy(false);
          return;
        }
        const res = await api.holders.uploadDocument(effectiveHolder, file, { title: title || undefined, kind: kind || undefined, domains: domainList });
        doc = res.document;
      } else {
        const body: Parameters<typeof api.holders.addDocument>[1] = { title: title.trim(), text };
        if (kind) body.kind = kind;
        if (observedAt) body.observed_at = new Date(observedAt).toISOString();
        if (domainList.length) body.domains = domainList;
        const res = await api.holders.addDocument(effectiveHolder, body);
        doc = res.document;
      }
      toast.push(doc.status === 'indexing' ? 'Document accepted; indexing in the background' : 'Document added', 'ok');
      setTitle('');
      setText('');
      setFile(null);
      onDone?.(doc, effectiveHolder);
    } catch (err) {
      toast.error(err, 'Could not add the document');
    } finally {
      setBusy(false);
    }
  }

  if (!holders.length) return <div className="notice">You own no evidence holder yet. An administrator (or you, from the Memory page) can register one.</div>;

  return (
    <form className="form" onSubmit={(e) => void submit(e)} aria-label="Add document">
      <div className="row row--between">
        <Toggle value={mode} onChange={setMode} ariaLabel="Document source" options={[{ value: 'text', label: 'Paste text' }, { value: 'file', label: 'Upload file' }]} />
        <Select className="select--sm" style={{ width: 'auto' }} value={effectiveHolder} onChange={(e) => setHolderId(e.target.value)} aria-label="Holder">
          {holders.map((h) => (
            <option key={h.holder_id} value={h.holder_id}>{h.name}</option>
          ))}
        </Select>
      </div>
      <div className="form-row">
        <Field label="Title" required={mode === 'text'}>
          {(id) => <Input id={id} value={title} required={mode === 'text'} onChange={(e) => setTitle(e.target.value)} placeholder={mode === 'file' ? 'Defaults to the file name' : ''} />}
        </Field>
        <Field label="Kind">
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)}>
              {['note', 'report', 'conversation', 'record', 'policy', 'other'].map((k) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      {mode === 'text' ? (
        <Field label="Text" required>
          {(id) => <Textarea id={id} required value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="Paste notes, a report, a conversation…" />}
        </Field>
      ) : (
        <Field label="File" hint=".txt, .md, .json or .csv (PDF is not supported)">
          {(id) => <input id={id} type="file" className="input" accept=".txt,.md,.json,.csv,text/plain,text/markdown,application/json,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
        </Field>
      )}
      <div className="form-row">
        {mode === 'text' ? (
          <Field label="Observed at" hint="When the facts were true">
            {(id) => <Input id={id} type="date" value={observedAt} onChange={(e) => setObservedAt(e.target.value)} />}
          </Field>
        ) : null}
        <Field label="Domains" hint="Comma separated, optional">
          {(id) => <Input id={id} value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="deployments, support" />}
        </Field>
      </div>
      <div className="form-actions">
        <Button type="submit" variant="primary" busy={busy}>Add to memory</Button>
      </div>
    </form>
  );
}
