import { useId } from 'react';
import type { Advisory } from '../../features/live/types';
import { formatTime } from '../../lib/time';

export function AdvisoryCard({
  advisory,
  timezone,
  onPreview,
}: {
  advisory: Advisory;
  timezone: string;
  onPreview: (advisory: Advisory) => void;
}) {
  const heading = useId();
  return (
    <article
      className={`advisory-card compact-advisory priority-${advisory.priority}`}
      aria-labelledby={heading}
    >
      <div className="card-meta">
        <span className="priority-label">
          <span className="priority-dot" aria-hidden="true" />
          {advisory.priority.replaceAll('_', ' ')}
        </span>
        <span>v{advisory.version}</span>
      </div>
      <h3 id={heading}>{advisory.title}</h3>
      <p className="advisory-reason">{advisory.reason}</p>
      <div className="advisory-window">
        <span>
          {formatTime(advisory.windowStartUtc, timezone, 'clock')} –{' '}
          {formatTime(advisory.windowEndUtc, timezone, 'clock')}
        </span>
        <span>
          Expires {formatTime(advisory.expiresAtUtc, timezone, 'clock')}
        </span>
      </div>
      <div className="advisory-card-footer">
        <span>{advisory.quality} evidence</span>
        <button onClick={() => onPreview(advisory)}>
          {advisory.projectionId ? 'Preview' : 'Review'}
          <span aria-hidden="true">↗</span>
        </button>
      </div>
    </article>
  );
}
