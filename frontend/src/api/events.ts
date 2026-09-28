// Server-Sent Events subscription (API.md "Live updates"): GET /api/events/stream?since=<id>.
// One shared EventSource per tab; hooks subscribe by kind. Reconnects with the last seen id.
import { useEffect, useRef } from 'react';
import { API_BASE } from './client';
import { EVENT_KINDS, type EventKind, type LiveEvent } from './types';

type Handler = (event: LiveEvent) => void;

interface Subscription {
  kinds: Set<string> | null; // null = all kinds
  handler: Handler;
}

const subscriptions = new Set<Subscription>();
let source: EventSource | null = null;
let lastId: string | null = null;
let reconnectTimer: number | null = null;
let backoffMs = 1000;
let enabled = false;
const statusListeners = new Set<(connected: boolean) => void>();
let connected = false;

function setConnected(v: boolean) {
  if (connected === v) return;
  connected = v;
  statusListeners.forEach((fn) => fn(v));
}

function dispatch(kind: string, raw: MessageEvent<string>) {
  let data: Partial<LiveEvent> = {};
  try {
    data = JSON.parse(raw.data) as Partial<LiveEvent>;
  } catch {
    return;
  }
  if (raw.lastEventId) lastId = raw.lastEventId;
  const event: LiveEvent = {
    id: raw.lastEventId || String(data.id ?? ''),
    tenant_id: data.tenant_id ?? '',
    kind: (data.kind as EventKind) ?? kind,
    ref_type: data.ref_type ?? null,
    ref_id: data.ref_id ?? null,
    payload: data.payload ?? {},
    at: data.at ?? new Date().toISOString(),
  };
  for (const sub of subscriptions) {
    if (sub.kinds === null || sub.kinds.has(event.kind)) {
      try {
        sub.handler(event);
      } catch (err) {
        console.error('event handler failed', err);
      }
    }
  }
}

function connect() {
  if (source || !enabled) return;
  const url = `${API_BASE}/events/stream${lastId ? `?since=${encodeURIComponent(lastId)}` : ''}`;
  const es = new EventSource(url, { withCredentials: true });
  source = es;
  es.onopen = () => {
    backoffMs = 1000;
    setConnected(true);
  };
  for (const kind of EVENT_KINDS) {
    es.addEventListener(kind, (e) => dispatch(kind, e as MessageEvent<string>));
  }
  // Unnamed events (if the server ever sends them) carry `kind` in the data.
  es.onmessage = (e) => dispatch('message', e);
  es.onerror = () => {
    setConnected(false);
    es.close();
    if (source === es) source = null;
    if (!enabled) return;
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, backoffMs);
    backoffMs = Math.min(backoffMs * 2, 30_000);
  };
}

/** Start the shared stream (called when a session exists). */
export function startLiveEvents(): void {
  enabled = true;
  connect();
}

/** Stop the shared stream (on logout). */
export function stopLiveEvents(): void {
  enabled = false;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (source) {
    source.close();
    source = null;
  }
  setConnected(false);
}

export function subscribeLiveEvents(kinds: readonly string[] | null, handler: Handler): () => void {
  const sub: Subscription = { kinds: kinds ? new Set(kinds) : null, handler };
  subscriptions.add(sub);
  if (enabled) connect();
  return () => {
    subscriptions.delete(sub);
  };
}

export function onLiveStatus(fn: (connected: boolean) => void): () => void {
  statusListeners.add(fn);
  fn(connected);
  return () => {
    statusListeners.delete(fn);
  };
}

/**
 * Subscribe to live events of the given kinds (empty array or null = every kind).
 * The handler is kept in a ref so callers may pass inline closures.
 */
export function useLiveEvents(kinds: readonly (EventKind | string)[] | null, handler: Handler): void {
  const ref = useRef(handler);
  ref.current = handler;
  const key = kinds ? kinds.join('|') : '*';
  useEffect(() => {
    const list = key === '*' ? null : key.split('|').filter(Boolean);
    return subscribeLiveEvents(list && list.length ? list : null, (e) => ref.current(e));
  }, [key]);
}

/** Debounced refresh helper: calls `refresh` at most once per `waitMs` after matching events. */
export function useLiveRefresh(kinds: readonly (EventKind | string)[] | null, refresh: () => void, waitMs = 400): void {
  const timer = useRef<number | null>(null);
  const fn = useRef(refresh);
  fn.current = refresh;
  useLiveEvents(kinds, () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      fn.current();
    }, waitMs);
  });
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
}
