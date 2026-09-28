// Small component set: buttons, fields, cards, badges, tables, states, drawer, tabs.
import { useEffect, useId, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { describeError } from './Toast';
import { titleCase } from './format';

// ---------------------------------------------------------------- buttons
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'default' | 'primary' | 'danger' | 'ghost';
  size?: 'sm' | 'md';
  busy?: boolean;
};

export function Button({ variant = 'default', size = 'md', busy, className = '', children, disabled, type = 'button', ...rest }: ButtonProps) {
  const cls = ['btn', variant !== 'default' ? `btn--${variant}` : '', size === 'sm' ? 'btn--sm' : '', className].filter(Boolean).join(' ');
  return (
    <button type={type} className={cls} disabled={disabled || busy} {...rest}>
      {busy ? <span className="spinner" aria-hidden /> : null}
      {children}
    </button>
  );
}

export function LinkButton({ to, children, variant = 'default', size = 'md', className = '' }: { to: string; children: ReactNode; variant?: 'default' | 'primary' | 'ghost'; size?: 'sm' | 'md'; className?: string }) {
  const cls = ['btn', variant !== 'default' ? `btn--${variant}` : '', size === 'sm' ? 'btn--sm' : '', className].filter(Boolean).join(' ');
  return (
    <Link to={to} className={cls}>
      {children}
    </Link>
  );
}

// ---------------------------------------------------------------- fields
interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: (id: string) => ReactNode;
  className?: string;
}

export function Field({ label, hint, error, required, children, className = '' }: FieldProps) {
  const id = useId();
  return (
    <div className={`field ${className}`}>
      <label htmlFor={id}>
        {label}
        {required ? <span aria-hidden> *</span> : null}
      </label>
      {children(id)}
      {hint ? <div className="field__hint">{hint}</div> : null}
      {error ? <div className="field__error" role="alert">{error}</div> : null}
    </div>
  );
}

export function Input({ className = '', ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`input ${className}`} {...rest} />;
}

export function Textarea({ className = '', ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`textarea ${className}`} {...rest} />;
}

export function Select({ className = '', children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`select ${className}`} {...rest}>
      {children}
    </select>
  );
}

export function Checkbox({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="checkbox">
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  );
}

// ---------------------------------------------------------------- containers
export function Card({ title, children, className = '', actions }: { title?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <div className={`card ${className}`}>
      {title || actions ? (
        <div className="row row--between" style={{ marginBottom: 8 }}>
          {title ? <div className="card__title" style={{ marginBottom: 0 }}>{title}</div> : <span />}
          {actions}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function Section({ title, meta, actions, children, id }: { title: ReactNode; meta?: ReactNode; actions?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="section" id={id} aria-label={typeof title === 'string' ? title : undefined}>
      <div className="section__head">
        <div className="row">
          <h2>{title}</h2>
          {meta ? <span className="muted">{meta}</span> : null}
        </div>
        {actions ? <div className="row">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function Stat({ value, label, sub }: { value: ReactNode; label: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card stat">
      <div className="stat__value">{value}</div>
      <div className="stat__label">{label}</div>
      {sub ? <div className="stat__sub">{sub}</div> : null}
    </div>
  );
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{v ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PageHeader({ title, meta, actions, children }: { title: ReactNode; meta?: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-header">
      <div style={{ minWidth: 0 }}>
        <h1>{title}</h1>
        {meta ? <div className="page-header__meta">{meta}</div> : null}
        {children}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------- states
export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden /> {label}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorPanel({ error, retry, title }: { error: unknown; retry?: () => void; title?: string }) {
  if (error instanceof ApiError && error.status === 403) {
    return (
      <div className="notice notice--warn" role="alert">
        <strong>Not authorized.</strong> {error.message || 'You do not have access to this resource.'}
        {error.requestId ? <div className="xs">Request {error.requestId}</div> : null}
      </div>
    );
  }
  if (error instanceof ApiError && error.status === 404) {
    return (
      <div className="notice" role="alert">
        <strong>Not found.</strong> {error.message}
      </div>
    );
  }
  return (
    <div className="error-panel" role="alert">
      <h3>{title ?? 'Could not load'}</h3>
      <div className="small">{describeError(error)}</div>
      {error instanceof ApiError && error.requestId ? <div className="xs" style={{ opacity: 0.8 }}>Request {error.requestId}</div> : null}
      {retry ? (
        <div style={{ marginTop: 8 }}>
          <Button size="sm" onClick={retry}>Retry</Button>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- badges
export type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info' | 'accent' | 'outline';

export function Badge({ tone = 'neutral', children, live, title }: { tone?: Tone; children: ReactNode; live?: boolean; title?: string }) {
  const cls = ['badge', tone !== 'neutral' ? `badge--${tone}` : '', live ? 'badge--live' : ''].filter(Boolean).join(' ');
  return (
    <span className={cls} title={title}>
      {live ? <span className="dot" aria-hidden /> : null}
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, Tone> = {
  // claims
  hypothesis: 'warn', supported: 'ok', contested: 'danger', stale: 'warn', retracted: 'neutral',
  // goals
  draft: 'neutral', active: 'ok', paused: 'warn', completed: 'info', archived: 'neutral',
  // loops
  waiting: 'info', budget_exhausted: 'danger', blocked: 'danger', failed: 'danger', stopped: 'neutral',
  // questions / routes
  open: 'info', routed: 'info', collecting: 'info', evaluating: 'info', resolved: 'ok', cancelled: 'neutral', timeout: 'warn', pending: 'info', sent: 'info', responded: 'ok', answered: 'ok', declined: 'warn', no_evidence: 'warn', revoked: 'neutral',
  // discoveries
  new: 'accent', reviewed: 'info', accepted: 'ok', dismissed: 'neutral', escalated: 'warn',
  // misc
  online: 'ok', offline: 'danger', degraded: 'warn', indexing: 'info', indexed: 'ok', ok: 'ok', error: 'danger', dead: 'danger', queued: 'info', leased: 'info', done: 'ok', deny: 'danger', disabled: 'neutral', unresolved: 'warn', investigating: 'info',
};

export function StatusBadge({ status, live }: { status: string | null | undefined; live?: boolean }) {
  if (!status) return <Badge tone="outline">—</Badge>;
  return (
    <Badge tone={STATUS_TONES[status] ?? 'neutral'} live={live}>
      {titleCase(status)}
    </Badge>
  );
}

// ---------------------------------------------------------------- table
export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  num?: boolean;
  width?: string | number;
}

export function Table<T>({ columns, rows, rowKey, onRowClick, empty = 'Nothing here yet.', caption }: { columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string; onRowClick?: (row: T) => void; empty?: ReactNode; caption?: string }) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table className={`table table--cards ${onRowClick ? 'table--clickable' : ''}`}>
        {caption ? <caption className="visually-hidden">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.num ? 'num' : ''} style={c.width ? { width: c.width } : undefined} scope="col">
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)} onClick={onRowClick ? () => onRowClick(r) : undefined}>
              {columns.map((c) => (
                <td key={c.key} className={c.num ? 'num' : ''} data-label={typeof c.header === 'string' ? c.header : ''}>
                  {c.render(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- drawer
export function Drawer({ open, onClose, title, children, width }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    ref.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden />
      <div className="drawer" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : 'Panel'} ref={ref} tabIndex={-1} style={width ? { width: `min(${width}px, 100vw)` } : undefined}>
        <div className="drawer__head">
          <h3>{title}</h3>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close panel">Close</Button>
        </div>
        <div className="drawer__body">{children}</div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- tabs / toggles
export function Toggle<T extends string>({ value, options, onChange, ariaLabel }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; ariaLabel?: string }) {
  return (
    <div className="btn-group" role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button key={o.value} type="button" className={`btn btn--sm ${o.value === value ? 'active' : ''}`} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Meter({ value, tone }: { value: number; tone?: 'warn' | 'danger' }) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <div className={`meter ${tone ? `meter--${tone}` : ''}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct * 100)}>
      <span style={{ width: `${pct * 100}%` }} />
    </div>
  );
}

/** Confirm helper (window.confirm keeps the UI small; replace if a modal is wanted later). */
export function confirmAction(text: string): boolean {
  return window.confirm(text);
}
