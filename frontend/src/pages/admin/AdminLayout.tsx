import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useSession } from '../../auth/AuthProvider';
import { PageHeader } from '../../components/ui';
import { Breadcrumbs } from '../../shell/Breadcrumbs';

const TABS: { to: string; label: string; end?: boolean }[] = [
  { to: '/app/admin', label: 'Overview', end: true },
  { to: '/app/admin/members', label: 'Members' },
  { to: '/app/admin/hierarchy', label: 'Hierarchy' },
  { to: '/app/admin/policies', label: 'Policies' },
  { to: '/app/admin/models', label: 'Models' },
  { to: '/app/admin/budgets', label: 'Budgets' },
  { to: '/app/admin/integrations', label: 'Integrations' },
  { to: '/app/admin/domains', label: 'Domains' },
  { to: '/app/admin/workers', label: 'Workers' },
  { to: '/app/admin/audit', label: 'Audit log' },
  { to: '/app/admin/deployment', label: 'Deployment' },
];

export function AdminLayout() {
  const me = useSession();
  const location = useLocation();
  const current = TABS.find((t) => (t.end ? location.pathname === t.to : location.pathname.startsWith(t.to)));
  if (!me.principal.is_admin) {
    return (
      <div>
        <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Admin' }]} />
        <div className="notice notice--warn" role="alert">
          <strong>Not authorized.</strong> Administration requires the organization administrator role. Administrative rights are separate from knowledge access.
        </div>
      </div>
    );
  }
  return (
    <div>
      <Breadcrumbs items={[{ label: 'Home', to: '/app' }, { label: 'Admin', to: '/app/admin' }, ...(current && !current.end ? [{ label: current.label }] : [])]} />
      <PageHeader title="Administration" meta="People, hierarchy, policies, models, holders, workers and deployment. Administrators see no claims or evidence unless a membership grants it." />
      <nav className="tabs" aria-label="Admin sections">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
