import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from './AuthProvider';
import { ErrorPanel, Loading } from '../components/ui';

export function RequireAuth() {
  const { me, loading, error, refresh } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="auth">
        <Loading label="Checking session…" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="auth">
        <div className="auth__card">
          <ErrorPanel error={error} retry={() => void refresh()} title="The API is not reachable" />
        </div>
      </div>
    );
  }
  if (!me) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  return <Outlet />;
}

/** Redirects an authenticated user away from the public auth pages. */
export function RedirectIfAuthed({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return <div className="auth"><Loading /></div>;
  if (me) return <Navigate to={me.onboarding?.complete === false && me.principal.is_admin ? '/onboarding' : '/app'} replace />;
  return <>{children}</>;
}
