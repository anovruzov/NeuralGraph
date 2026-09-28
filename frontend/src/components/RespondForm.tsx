import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { Holder, Question, Response } from '../api/types';
import { Button, Checkbox, Field, Select, Textarea } from './ui';
import { useToast } from './Toast';

/** Inline "respond from my own holder" form (POST /questions/{id}/respond). */
export function RespondForm({ question, holders, onDone, compact }: { question: Question; holders: Holder[]; onDone: (r: Response) => void; compact?: boolean }) {
  const toast = useToast();
  const routedHolderIds = new Set((question.routes ?? []).map((r) => r.holder_id));
  const mine = holders.filter((h) => h.owner_type === 'user');
  const preferred = mine.find((h) => routedHolderIds.has(h.holder_id)) ?? mine[0] ?? holders[0];
  const [content, setContent] = useState('');
  const [holderId, setHolderId] = useState(preferred?.holder_id ?? '');
  const [noEvidence, setNoEvidence] = useState(false);
  const [docIds, setDocIds] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!content.trim() && !noEvidence) {
      toast.push('Write an answer or mark "no evidence".', 'error');
      return;
    }
    setBusy(true);
    try {
      const body: { content: string; holder_id?: string; no_evidence?: boolean; doc_ids?: string[] } = { content: content.trim() };
      if (holderId) body.holder_id = holderId;
      if (noEvidence) body.no_evidence = true;
      const ids = docIds.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
      if (ids.length) body.doc_ids = ids;
      const res = await api.questions.respond(question.question_id, body);
      toast.push('Response sent', 'ok');
      setContent('');
      setDocIds('');
      setNoEvidence(false);
      onDone(res.response);
    } catch (err) {
      toast.error(err, 'Could not send the response');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={(e) => void submit(e)} aria-label="Respond to question">
      <Field label="Your answer" hint={compact ? undefined : 'Answer from your own records. Only policy-approved excerpts of cited documents are disclosed.'}>
        {(id) => <Textarea id={id} value={content} onChange={(e) => setContent(e.target.value)} rows={compact ? 3 : 5} disabled={noEvidence && !content} placeholder={noEvidence ? 'Optional note' : 'What do your records show?'} />}
      </Field>
      <div className="form-row">
        <Field label="Answer from holder">
          {(id) => (
            <Select id={id} value={holderId} onChange={(e) => setHolderId(e.target.value)}>
              {holders.length === 0 ? <option value="">No holder available</option> : null}
              {holders.map((h) => (
                <option key={h.holder_id} value={h.holder_id}>
                  {h.name}{routedHolderIds.has(h.holder_id) ? ' (routed)' : ''}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Cite document ids" hint="Optional, comma separated">
          {(id) => <input id={id} className="input" value={docIds} onChange={(e) => setDocIds(e.target.value)} placeholder="doc_…" />}
        </Field>
      </div>
      <div className="form-actions" style={{ justifyContent: 'space-between' }}>
        <Checkbox label="I have no evidence on this" checked={noEvidence} onChange={(e) => setNoEvidence(e.target.checked)} />
        <Button type="submit" variant="primary" busy={busy}>Send response</Button>
      </div>
    </form>
  );
}
