import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import type {
  Advisory,
  FlowPoint,
  Projection,
} from '../../features/live/types';
import {
  displayValue,
  finite,
  horizonOutcomes,
  modelSample,
} from '../../features/projection/comparison';
import { supportedPaths } from '../../features/projection/aircraftPaths';
import { ageLabel, formatTime } from '../../lib/time';

export function ProjectionDrawer({
  projection,
  expectedSnapshotId,
  title,
  acceptedBy,
  advisory,
  now,
  timezone,
  minute,
  mode = 'LIVE',
  selectedAircraftId = null,
  onSelectAircraft,
  footer,
  onClose,
}: {
  projection: Projection | undefined;
  expectedSnapshotId: string;
  title: string;
  acceptedBy?: string;
  advisory?: Advisory;
  now: number;
  timezone: string;
  minute: number;
  mode?: 'LIVE' | 'TEST';
  selectedAircraftId?: string | null;
  onSelectAircraft?: (id: string) => void;
  footer?: ReactNode;
  onClose: () => void;
}) {
  const id = useId();
  const close = useRef<HTMLButtonElement>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const [tab, setTab] = useState<'overview' | 'evidence'>('overview');
  useEffect(() => {
    close.current?.focus();
  }, []);
  const consistent = Boolean(
    projection && projection.snapshotId === expectedSnapshotId,
  );
  const supported = consistent ? projection : undefined;
  const sample = supported ? modelSample(supported, minute) : undefined;
  const outcomes = supported ? horizonOutcomes(supported) : undefined;
  const paths = supportedPaths({
    projection,
    expectedSnapshotId,
    minute,
    mode,
  });
  const peak = (series: readonly FlowPoint[]) => {
    const values = series
      .filter((point) => point.minute >= 0 && point.minute <= 15)
      .map((point) => point.pressure);
    return values.length && values.every(finite)
      ? Math.max(...(values as number[]))
      : null;
  };
  const rows = supported
    ? [
        [
          'Modeled runway queue',
          displayValue(sample?.baseline?.runwayQueue, 'aircraft'),
          displayValue(sample?.proposed?.runwayQueue, 'aircraft'),
        ],
        [
          'Upstream waiting',
          displayValue(sample?.baseline?.upstreamWaiting, 'aircraft'),
          displayValue(sample?.proposed?.upstreamWaiting, 'aircraft'),
        ],
        [
          'Total waiting',
          displayValue(
            sample?.baseline?.totalWaitingAircraftMinutes,
            'aircraft-min',
          ),
          displayValue(
            sample?.proposed?.totalWaitingAircraftMinutes,
            'aircraft-min',
          ),
        ],
        [
          'Cumulative departures',
          displayValue(sample?.baseline?.departures, 'aircraft'),
          displayValue(sample?.proposed?.departures, 'aircraft'),
        ],
        [
          'Traffic pressure',
          displayValue(sample?.baseline?.pressure, supported.pressureUnit),
          displayValue(sample?.proposed?.pressure, supported.pressureUnit),
        ],
        [
          'Sampled pressure peak (0–15 min)',
          displayValue(peak(supported.baseline), supported.pressureUnit),
          displayValue(peak(supported.proposed), supported.pressureUnit),
        ],
      ]
    : [];
  return (
    <section
      className="workspace-drawer scenario-review"
      aria-label="Scenario review"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !event.defaultPrevented) {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="drawer-heading">
        <div>
          <p className="eyebrow">
            {acceptedBy ? 'Accepted scenario' : 'Scenario review'}
            {advisory && ` · v${advisory.version}`}
          </p>
          <h2>{title}</h2>
        </div>
        <button
          ref={close}
          className="button-quiet"
          onClick={onClose}
          aria-label="Close scenario details"
        >
          ×
        </button>
      </header>
      {acceptedBy && (
        <p className="accepted-banner" role="status">
          Accepted scenario · decision recorded by {acceptedBy}
        </p>
      )}
      <div
        className="review-tabs"
        role="tablist"
        aria-label="Scenario review sections"
      >
        {(['overview', 'evidence'] as const).map((name, index) => (
          <button
            key={name}
            ref={(element) => {
              tabs.current[index] = element;
            }}
            role="tab"
            id={`${id}-${name}-tab`}
            aria-controls={`${id}-${name}`}
            aria-selected={tab === name}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => setTab(name)}
            onKeyDown={(event) => {
              if (
                !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)
              )
                return;
              event.preventDefault();
              const next =
                event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index;
              setTab(next === 0 ? 'overview' : 'evidence');
              tabs.current[next]?.focus();
            }}
          >
            {name === 'overview' ? 'Overview' : 'Evidence'}
          </button>
        ))}
      </div>
      <div
        className="drawer-scroll"
        role="tabpanel"
        id={`${id}-${tab}`}
        aria-labelledby={`${id}-${tab}-tab`}
        tabIndex={0}
      >
        {tab === 'overview' ? (
          <>
            {advisory && (
              <div className="review-summary">
                <div className="review-state">
                  <span
                    className={`review-priority priority-${advisory.priority}`}
                  >
                    {advisory.priority.replaceAll('_', ' ')}
                  </span>
                  <span>
                    {advisory.quality} evidence ·{' '}
                    {advisory.status.replaceAll('_', ' ')}
                  </span>
                </div>
                <h3>Proposed response</h3>
                <p className="review-response">{advisory.response}</p>
                <p>{advisory.reason}</p>
                <dl className="review-facts">
                  <div>
                    <dt>Resource</dt>
                    <dd>{advisory.resource}</dd>
                  </div>
                  <div>
                    <dt>Owner</dt>
                    <dd>{advisory.ownerRole}</dd>
                  </div>
                  <div>
                    <dt>Window</dt>
                    <dd>
                      {formatTime(advisory.windowStartUtc, timezone, 'clock')} –{' '}
                      {formatTime(advisory.windowEndUtc, timezone, 'clock')}
                    </dd>
                  </div>
                  <div>
                    <dt>Expires</dt>
                    <dd>
                      {formatTime(advisory.expiresAtUtc, timezone, 'clock')}
                    </dd>
                  </div>
                </dl>
              </div>
            )}
            {!consistent ? (
              <p className="review-unavailable" role="alert">
                {projection
                  ? 'Projection snapshot does not match this advisory version. Comparison unavailable.'
                  : 'Projection unavailable. Waiting for a validated result.'}
              </p>
            ) : (
              <>
                <div className="review-section-heading">
                  <h3>15-minute outcomes</h3>
                  <span>Fixed snapshot</span>
                </div>
                <div
                  className={`outcome-grid ${outcomes?.densityOnly ? 'density-outcomes' : ''}`}
                >
                  {outcomes?.metrics.map((metric) => (
                    <article
                      className="outcome-card"
                      key={metric.key}
                      aria-label={`${metric.label} at +15 minutes`}
                    >
                      <h4>{metric.label}</h4>
                      <div className="outcome-values">
                        <div>
                          <span>Baseline</span>
                          <strong>
                            {finite(metric.baseline) ? metric.baseline : '—'}
                          </strong>
                        </div>
                        <div>
                          <span>Proposed</span>
                          <strong>
                            {finite(metric.proposed) ? metric.proposed : '—'}
                          </strong>
                        </div>
                      </div>
                      <span className="outcome-unit">{metric.unit}</span>
                      <p
                        className={`outcome-change ${metric.key === 'totalWaitingAircraftMinutes' && finite(metric.change) && metric.change > 0 ? 'adverse-change' : ''}`}
                      >
                        {finite(metric.change)
                          ? `${metric.change > 0 ? '+' : ''}${Number(metric.change.toFixed(2))} ${metric.unit}`
                          : 'Not estimable with available data'}
                      </p>
                    </article>
                  ))}
                </div>
                {outcomes?.densityOnly && (
                  <p className="capability-note">
                    Departure metrics are not estimable with available data.
                  </p>
                )}
                <details className="sample-comparison">
                  <summary>
                    Sampled comparison ·{' '}
                    {sample?.minute === 0 ? 'Now' : `+${sample?.minute} min`}
                  </summary>
                  <div className="table-scroll">
                    <table>
                      <caption>
                        Scenario comparison at{' '}
                        {sample?.minute === 0
                          ? 'now'
                          : `+${sample?.minute} minutes`}
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col">Metric</th>
                          <th scope="col">Baseline</th>
                          <th scope="col">Proposed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map(([label, before, after]) => (
                          <tr key={label}>
                            <th scope="row">{label}</th>
                            <td>{before}</td>
                            <td>{after}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <small>
                    Exact model samples are shown at 0, 5, 10 and 15 minutes.
                    Paths interpolate supplied waypoints between samples.
                  </small>
                </details>
                <div className="review-section-heading">
                  <h3>Projected aircraft</h3>
                  <span>
                    {paths.length
                      ? `${supported?.aircraftPaths?.basis === 'illustrative' ? 'Illustrative' : 'Modeled'} paths`
                      : 'Unavailable'}
                  </span>
                </div>
                {paths.length ? (
                  <div
                    className="route-list"
                    role="group"
                    aria-label="Select projected aircraft"
                  >
                    {paths.map((path) => (
                      <button
                        key={path.aircraftId}
                        aria-label={`Select ${path.label}`}
                        aria-pressed={selectedAircraftId === path.aircraftId}
                        onClick={() => onSelectAircraft?.(path.aircraftId)}
                      >
                        <svg viewBox="-15 -15 30 30" aria-hidden="true">
                          <path d="M0-12 2-3 11 3 11 5 2 2 2 8 5 10 5 12 0 10-5 12-5 10-2 8-2 2-11 5-11 3-2-3Z" />
                        </svg>
                        <span>{path.label}</span>
                        <small>
                          {selectedAircraftId === path.aircraftId
                            ? 'Selected'
                            : 'View route'}
                        </small>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="capability-note">
                    Aircraft paths unavailable for this snapshot.
                  </p>
                )}
                <p className="snapshot-footer">
                  Snapshot{' '}
                  {formatTime(supported?.snapshotAtUtc ?? null, timezone)} ·{' '}
                  {ageLabel(supported?.snapshotAtUtc ?? null, now)}
                  <br />
                  Live observations continue updating.
                </p>
              </>
            )}
          </>
        ) : (
          <div className="review-evidence">
            <h3>Advisory evidence</h3>
            {advisory?.evidence.length ? (
              <ul className="plain-list">
                {advisory.evidence.map((evidence) => (
                  <li key={evidence.id}>
                    <strong>{evidence.source}</strong>
                    <p>{evidence.summary}</p>
                    <small>
                      {formatTime(evidence.atUtc, timezone)} ·{' '}
                      {ageLabel(evidence.atUtc, now)} · {evidence.id}
                    </small>
                  </li>
                ))}
              </ul>
            ) : (
              <p>Evidence for this advisory version is unavailable.</p>
            )}
            <h3>Assumptions</h3>
            <ul>
              {[
                ...(advisory?.assumptions ?? []),
                ...(supported?.assumptions ?? []),
              ].map((value, index) => (
                <li key={index}>{value}</li>
              ))}
            </ul>
            <h3>Limitations and tradeoffs</h3>
            <ul>
              {[
                ...(advisory?.limitations ?? []),
                ...(supported?.limitations ?? []),
              ].map((value, index) => (
                <li key={index}>{value}</li>
              ))}
            </ul>
            {supported?.aircraftPaths && (
              <>
                <h3>Aircraft path basis</h3>
                <ul>
                  {supported.aircraftPaths.assumptions.map((value) => (
                    <li key={value}>{value}</li>
                  ))}
                </ul>
              </>
            )}
            <div className="model-provenance">
              <h3>Model provenance</h3>
              <p>{supported?.modelVersion ?? 'Matching model unavailable'}</p>
              <small>
                Snapshot {expectedSnapshotId}
                <br />
                {formatTime(supported?.snapshotAtUtc ?? null, timezone)} ·{' '}
                {ageLabel(supported?.snapshotAtUtc ?? null, now)}
              </small>
            </div>
          </div>
        )}
      </div>
      {footer ?? (
        <footer className="decision-footer">
          <p>This recorded version is read-only.</p>
        </footer>
      )}
    </section>
  );
}
