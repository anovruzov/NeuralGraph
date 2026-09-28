import type { ClaimSupport, DiscoverySupport } from '../api/types';
import { Badge } from './ui';

/** Independent-support summary (DECISIONS.md D11): distinct known roots count, copies count once, unknown roots never count. */
export function SupportSummary({ support, compact }: { support: ClaimSupport | DiscoverySupport | null | undefined; compact?: boolean }) {
  if (!support) return <span className="muted xs">support unknown</span>;
  const roots = support.independent_roots ?? 0;
  const copied = support.copied_refs ?? 0;
  const unknown = support.unknown_independence ?? 0;
  if (compact) {
    return (
      <span className="row" style={{ gap: 4 }}>
        <Badge tone={roots >= 2 ? 'ok' : roots === 1 ? 'warn' : 'neutral'} title="Independent source roots">{roots} independent</Badge>
        {copied ? <Badge tone="outline" title="References sharing a root already counted">{copied} copied</Badge> : null}
        {unknown ? <Badge tone="outline" title="References whose source root is unknown; never counted as independent">{unknown} unknown</Badge> : null}
      </span>
    );
  }
  return (
    <dl className="kv">
      <dt>Independent roots</dt>
      <dd>
        <strong>{roots}</strong> <span className="muted xs">distinct known source roots across holders</span>
      </dd>
      <dt>Copied references</dt>
      <dd>
        {copied} <span className="muted xs">share a root that is already counted</span>
      </dd>
      <dt>Unknown independence</dt>
      <dd>
        {unknown} <span className="muted xs">root not determinable by the holder; never counted</span>
      </dd>
      <dt>Holders</dt>
      <dd>{support.holders?.length ? support.holders.length : 0}</dd>
    </dl>
  );
}
