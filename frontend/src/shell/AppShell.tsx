import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { LEVEL_ORDER, type Membership } from '../api/types';
import { useAuth } from '../auth/AuthProvider';
import { DemoBanner } from '../auth/DemoBanner';
import { titleCase } from '../components/format';
import { Button } from '../components/ui';
import { useToast } from '../components/Toast';
import { onLiveStatus } from '../api/events';
import { BrandMark, Glyph } from './Glyph';
import { NotificationsBell } from './NotificationsBell';
import { PersonaSwitcher } from './PersonaSwitcher';

interface LevelGroup {
  level: string;
  units: { unit_id: string; name: string; role: string; unit_type: string }[];
}

/** Units the principal may open, grouped by level (from principal.memberships; org_admin memberships carry no workspace). */
export function unitGroups(memberships: Membership[]): LevelGroup[] {
  const byLevel = new Map<string, LevelGroup['units']>();
  const seen = new Set<string>();
  for (const m of memberships) {
    if (m.role === 'org_admin' || !m.unit_id) continue;
    const level = m.level || 'team';
    if (seen.has(m.unit_id)) continue;
    seen.add(m.unit_id);
    const list = byLevel.get(level) ?? [];
    list.push({ unit_id: m.unit_id, name: m.unit_name || m.unit_id, role: m.role, unit_type: m.unit_type || '' });
    byLevel.set(level, list);
  }
  const order = [...LEVEL_ORDER].reverse();
  return [...byLevel.entries()]
    .sort((a, b) => order.indexOf(a[0] as (typeof order)[number]) - order.indexOf(b[0] as (typeof order)[number]))
    .map(([level, units]) => ({ level, units: units.sort((a, b) => a.name.localeCompare(b.name)) }));
}

export function AppShell() {
  const { me, logout } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [live, setLive] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => onLiveStatus(setLive), []);
  useEffect(() => {
    setNavOpen(false);
    setMenuOpen(false);
  }, [location.pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [menuOpen]);

  const groups = useMemo(() => unitGroups(me?.principal.memberships ?? []), [me]);
  if (!me) return null;
  const { principal, tenant, levels } = me;
  const showExecutive = levels.includes('executive') || principal.highest_level === 'executive';

  async function onLogout() {
    try {
      await logout();
      navigate('/login');
    } catch (err) {
      toast.error(err, 'Logout failed');
    }
  }

  return (
    <div className="shell">
      {navOpen ? <div className="sidebar-backdrop" onClick={() => setNavOpen(false)} aria-hidden /> : null}
      <aside className={`sidebar ${navOpen ? 'open' : ''}`} aria-label="Primary navigation">
        <div className="sidebar__brand">
          <BrandMark />
          <span>Mycelic</span>
        </div>
        <nav>
          <NavLink to="/app" end>
            <Glyph type="home" /> Home
          </NavLink>
          <NavLink to="/app/memory">
            <Glyph type="memory" /> Memory
          </NavLink>
          <NavLink to="/app/chat">
            <Glyph type="chat" /> Chat
          </NavLink>
          <NavLink to="/app/goals">
            <Glyph type="goal" /> Goals
          </NavLink>
          {groups.length ? <div className="sidebar__section">Levels</div> : null}
          {groups.map((g) => (
            <div key={g.level}>
              <div className="xs muted" style={{ padding: '6px 8px 2px' }}>{titleCase(g.level)}</div>
              {g.units.map((u) => (
                <NavLink key={u.unit_id} to={`/app/unit/${u.unit_id}`} className="sidebar__unit" title={`${u.name} (${titleCase(u.role)})`}>
                  <Glyph type="unit" size={12} /> <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{u.name}</span>
                </NavLink>
              ))}
            </div>
          ))}
          {showExecutive ? (
            <NavLink to="/app/executive" style={{ marginTop: 8 }}>
              <Glyph type="executive" /> Executive
            </NavLink>
          ) : null}
          <NavLink to="/app/network">
            <Glyph type="network" /> Network
          </NavLink>
          {principal.is_admin ? (
            <NavLink to="/app/admin">
              <Glyph type="admin" /> Admin
            </NavLink>
          ) : null}
          {principal.is_admin && me.onboarding && !me.onboarding.complete ? (
            <NavLink to="/onboarding" className="sidebar__unit">
              Onboarding checklist
            </NavLink>
          ) : null}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <button type="button" className="btn btn--ghost btn--icon topbar__toggle" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen((o) => !o)}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
              <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" />
            </svg>
          </button>
          <Link to="/app" className="topbar__org" style={{ color: 'inherit' }}>{tenant.name}</Link>
          <span className="xs muted" title={live ? 'Live updates connected' : 'Live updates disconnected'} aria-label={live ? 'Live updates connected' : 'Live updates disconnected'}>
            <span className="dot" style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: live ? 'var(--ok)' : 'var(--line-strong)' }} />
          </span>
          <span className="topbar__spacer" />
          <PersonaSwitcher />
          <NotificationsBell />
          <div className="menu" ref={menuRef}>
            <button type="button" className="btn btn--ghost" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)}>
              <Glyph type="user" size={12} /> {me.user.name}
            </button>
            {menuOpen ? (
              <div className="menu__list" role="menu">
                <div className="menu__head">
                  {me.user.email}
                  <br />
                  {principal.roles.map(titleCase).join(', ') || 'No role'} · highest level {titleCase(principal.highest_level)}
                </div>
                {principal.is_admin ? (
                  <Link to="/onboarding" role="menuitem">Onboarding checklist</Link>
                ) : null}
                <button type="button" role="menuitem" onClick={() => void onLogout()}>Sign out</button>
              </div>
            ) : null}
          </div>
        </header>
        <DemoBanner />
        <main className="content" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function NotFound() {
  return (
    <div className="stack">
      <h1>Page not found</h1>
      <p className="muted">There is nothing at this address.</p>
      <div>
        <Button onClick={() => window.history.back()}>Go back</Button> <Link to="/app" className="btn">Home</Link>
      </div>
    </div>
  );
}
