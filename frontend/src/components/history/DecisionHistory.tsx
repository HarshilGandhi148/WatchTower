import { useEffect, useId, useRef } from 'react';
import type {
  DashboardSnapshot,
  HistoryEntry,
} from '../../features/live/types';
import { formatTime, isExpired } from '../../lib/time';

export function DecisionHistory({
  id,
  state,
  now,
  timezone,
  onPreview,
}: {
  id?: string;
  state: DashboardSnapshot;
  now: number;
  timezone: string;
  onPreview: (entry: HistoryEntry) => void;
}) {
  const titleId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  const inferred = state.advisories
    .filter(
      (a) =>
        a.status === 'superseded' ||
        a.status === 'expired' ||
        (a.status === 'pending_review' && isExpired(a.expiresAtUtc, now)),
    )
    .filter(
      (a) =>
        !state.history.some(
          (h) => h.advisoryId === a.id && h.version === a.version,
        ),
    );
  return (
    <section
      id={id}
      className="panel history-panel history-page"
      aria-labelledby={titleId}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">Controller record</p>
          <h2 id={titleId} ref={heading} tabIndex={-1}>
            Decision history
          </h2>
        </div>
        <span className="tag">{state.history.length} recorded</span>
      </div>
      <div className="history-summary" aria-label="Decision totals">
        {[
          ['approved', 'Approved'],
          ['edited', 'Edited'],
          ['ignored', 'Ignored'],
        ].map(([status, label]) => (
          <div key={status}>
            <strong>
              {state.history.filter((h) => h.status === status).length}
            </strong>
            <span>{label}</span>
          </div>
        ))}
        <div>
          <strong>
            {inferred.length +
              state.history.filter(
                (h) => h.status === 'expired' || h.status === 'superseded',
              ).length}
          </strong>
          <span>Expired / superseded</span>
        </div>
      </div>
      {!state.history.length && !inferred.length ? (
        <div className="history-empty">
          <span className="history-empty-icon" aria-hidden="true">
            ↺
          </span>
          <div>
            <h3>No decisions recorded</h3>
            <p className="empty-copy">
              Controller decisions and advisory versions will appear here.
            </p>
          </div>
        </div>
      ) : (
        <div
          className="table-scroll history-records"
          role="region"
          aria-label="History records"
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                <th scope="col">Advisory / version</th>
                <th scope="col">State</th>
                <th scope="col">Controller / time</th>
                <th scope="col">Recorded scenario</th>
              </tr>
            </thead>
            <tbody>
              {[...state.history]
                .sort((a, b) => Date.parse(b.atUtc) - Date.parse(a.atUtc))
                .map((h) => (
                  <tr key={h.id}>
                    <th scope="row">
                      {h.title}
                      <small>Version {h.version}</small>
                    </th>
                    <td>
                      <span className="tag">{h.status}</span>
                    </td>
                    <td>
                      {h.actor}
                      <small>{formatTime(h.atUtc, timezone)}</small>
                    </td>
                    <td>
                      <details className="record-details">
                        <summary>Snapshot details</summary>
                        <small>
                          {h.advisoryId} · {h.snapshotId}
                        </small>
                      </details>
                      {h.parameters.map((p) => (
                        <small key={p.label}>
                          {p.label}: {p.value} {p.unit}
                        </small>
                      ))}
                      {h.status === 'approved' && h.projectionId && (
                        <button onClick={() => onPreview(h)}>
                          View accepted projection
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              {inferred.map((a) => (
                <tr key={`${a.id}-${a.version}`}>
                  <th scope="row">
                    {a.title}
                    <small>v{a.version}</small>
                  </th>
                  <td>
                    {a.status === 'pending_review' ? 'expired' : a.status}
                  </td>
                  <td>Awaiting server history</td>
                  <td>
                    <small>Snapshot {a.snapshotId}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
