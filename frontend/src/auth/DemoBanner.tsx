import { useAuth } from './AuthProvider';

/** Shown whenever the tenant is a demonstration tenant (DECISIONS.md D10). */
export function DemoBanner() {
  const { me } = useAuth();
  if (!me?.tenant?.is_demo) return null;
  return (
    <div className="demo-banner" role="note">
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
        <polygon points="8,1 14.5,4.75 14.5,11.25 8,15 1.5,11.25 1.5,4.75" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <circle cx="8" cy="8" r="2" fill="currentColor" />
      </svg>
      <strong>Demonstration data</strong>
      <span>— simulated organization. Accounts, evidence and activity are seeded; authorization is enforced exactly as in production.</span>
    </div>
  );
}
