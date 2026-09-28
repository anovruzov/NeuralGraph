import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useLiveEvents } from '../api/events';
import type { Notification } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { ago, artifactPath } from '../components/format';
import { Button, Loading } from '../components/ui';
import { useToast } from '../components/Toast';

export function NotificationsBell() {
  const { me, setUnread } = useAuth();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[] | null>(null);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const unread = me?.unread_notifications ?? 0;

  useLiveEvents(['notification'], () => {
    setUnread((n) => n + 1);
    if (open) void load();
  });

  async function load() {
    setLoading(true);
    try {
      const res = await api.notifications.list(false, 30);
      setItems(res.items);
      setUnread(res.items.filter((n) => !n.read_at).length);
    } catch (err) {
      toast.error(err, 'Could not load notifications');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  async function markAll() {
    try {
      await api.notifications.markRead();
      setItems((it) => it?.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })) ?? null);
      setUnread(0);
    } catch (err) {
      toast.error(err);
    }
  }

  async function markOne(n: Notification) {
    if (n.read_at) return;
    try {
      await api.notifications.markRead([n.id]);
      setItems((it) => it?.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)) ?? null);
      setUnread((c) => Math.max(0, c - 1));
    } catch (err) {
      toast.error(err);
    }
  }

  return (
    <div className="menu" ref={ref}>
      <button type="button" className="btn btn--ghost btn--icon bell" aria-label={`Notifications, ${unread} unread`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
          <path d="M8 2a3.5 3.5 0 0 0-3.5 3.5V9L3 11h10l-1.5-2V5.5A3.5 3.5 0 0 0 8 2z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
          <path d="M6.5 13a1.5 1.5 0 0 0 3 0" fill="none" stroke="currentColor" strokeWidth="1.3" />
        </svg>
        {unread > 0 ? <span className="bell__count">{unread > 99 ? '99+' : unread}</span> : null}
      </button>
      {open ? (
        <div className="menu__list" style={{ width: 340, maxHeight: 420, overflowY: 'auto' }} role="region" aria-label="Notifications">
          <div className="row row--between" style={{ padding: '4px 8px' }}>
            <span className="menu__head" style={{ padding: 0 }}>Notifications</span>
            <Button size="sm" variant="ghost" onClick={() => void markAll()} disabled={unread === 0}>Mark all read</Button>
          </div>
          {loading && !items ? <Loading /> : null}
          {items && items.length === 0 ? <div className="notif muted">No notifications.</div> : null}
          {items?.map((n) => {
            const to = n.ref_type && n.ref_id ? artifactPath(n.ref_type, n.ref_id) : null;
            const inner = (
              <>
                <div className="notif__title">{n.title}</div>
                {n.body ? <div className="small">{n.body}</div> : null}
                <div className="notif__meta">{n.kind} · {ago(n.at)}</div>
              </>
            );
            return (
              <div key={n.id} className={`notif ${n.read_at ? '' : 'notif--unread'}`} onClick={() => void markOne(n)}>
                {to ? (
                  <Link to={to} onClick={() => setOpen(false)} style={{ color: 'inherit', display: 'block' }}>
                    {inner}
                  </Link>
                ) : (
                  inner
                )}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
