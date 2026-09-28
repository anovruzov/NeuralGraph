import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { subscribeLiveEvents } from '../api/events';
import type { OrgResponse, Unit } from '../api/types';

let cache: OrgResponse | null = null;
let inflight: Promise<OrgResponse> | null = null;
const listeners = new Set<() => void>();

function load(force = false): Promise<OrgResponse> {
  if (cache && !force) return Promise.resolve(cache);
  if (!inflight) {
    inflight = api.org
      .get()
      .then((r) => {
        cache = r;
        listeners.forEach((l) => l());
        return r;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export function invalidateOrg(): void {
  cache = null;
  void load(true).catch(() => undefined);
}

let subscribed = false;
function ensureSubscribed() {
  if (subscribed) return;
  subscribed = true;
  subscribeLiveEvents(['org.changed', 'membership.changed'], () => invalidateOrg());
}

/** Cached GET /org (any member) for unit pickers, trees and names. */
export function useOrg(): { org: OrgResponse | null; units: Unit[]; error: unknown; reload: () => void; unitName: (id: string | null | undefined) => string } {
  const [org, setOrg] = useState<OrgResponse | null>(cache);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    ensureSubscribed();
    const l = () => setOrg(cache);
    listeners.add(l);
    load().then(setOrg).catch(setError);
    return () => {
      listeners.delete(l);
    };
  }, []);
  const reload = useCallback(() => {
    load(true).then(setOrg).catch(setError);
  }, []);
  const units = org?.units ?? [];
  const unitName = useCallback(
    (id: string | null | undefined) => {
      if (!id) return '';
      return units.find((u) => u.unit_id === id)?.name ?? id;
    },
    [units],
  );
  return { org, units, error, reload, unitName };
}
