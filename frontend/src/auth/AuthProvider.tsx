import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, ApiError, setUnauthorizedHandler } from '../api/client';
import { startLiveEvents, stopLiveEvents } from '../api/events';
import type { MeResponse, RegisterBody } from '../api/types';

interface AuthState {
  me: MeResponse | null;
  loading: boolean;
  error: unknown;
  refresh: () => Promise<MeResponse | null>;
  login: (email: string, password: string, tenant_slug?: string) => Promise<void>;
  register: (body: RegisterBody) => Promise<void>;
  acceptInvitation: (token: string, name: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchPersona: (user_id: string) => Promise<void>;
  /** Set locally after /notifications/read or a notification event. */
  setUnread: (n: number | ((prev: number) => number)) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(async (): Promise<MeResponse | null> => {
    try {
      const data = await api.auth.me();
      setMe(data);
      setError(null);
      return data;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setMe(null);
        setError(null);
      } else {
        setError(err);
      }
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (me) startLiveEvents();
    else stopLiveEvents();
  }, [me]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setMe(null);
      stopLiveEvents();
      const here = window.location.pathname + window.location.search;
      if (!here.startsWith('/login') && !here.startsWith('/register') && !here.startsWith('/invite/')) {
        window.location.assign(`/login?next=${encodeURIComponent(here)}`);
      }
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(
    async (email: string, password: string, tenant_slug?: string) => {
      await api.auth.login(tenant_slug ? { email, password, tenant_slug } : { email, password });
      await refresh();
    },
    [refresh],
  );

  const register = useCallback(
    async (body: RegisterBody) => {
      await api.auth.register(body);
      await refresh();
    },
    [refresh],
  );

  const acceptInvitation = useCallback(
    async (token: string, name: string, password: string) => {
      await api.auth.acceptInvitation(token, { name, password });
      await refresh();
    },
    [refresh],
  );

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      stopLiveEvents();
      setMe(null);
    }
  }, []);

  const switchPersona = useCallback(
    async (user_id: string) => {
      stopLiveEvents();
      await api.auth.demoSwitch(user_id);
      await refresh();
    },
    [refresh],
  );

  const setUnread = useCallback((n: number | ((prev: number) => number)) => {
    setMe((prev) => (prev ? { ...prev, unread_notifications: typeof n === 'function' ? n(prev.unread_notifications) : n } : prev));
  }, []);

  const value = useMemo<AuthState>(
    () => ({ me, loading, error, refresh, login, register, acceptInvitation, logout, switchPersona, setUnread }),
    [me, loading, error, refresh, login, register, acceptInvitation, logout, switchPersona, setUnread],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

/** Non-null session for pages rendered under RequireAuth. */
export function useSession(): MeResponse {
  const { me } = useAuth();
  if (!me) throw new Error('useSession requires an authenticated session');
  return me;
}
