import { useEffect, useId, useRef } from 'react';
import type { FlowPoint, Projection } from '../../features/live/types';
import { ageLabel, formatTime } from '../../lib/time';

function available(value: number | null | undefined, unit: string) {
  return value === null || value === undefined || !Number.isFinite(value)
    ? 'Not estimable with available data'
    : `${value} ${unit}`;
}
export function ProjectionDrawer({
  projection,
  expectedSnapshotId,
  title,
  acceptedBy,
  now,
  timezone,
  minute,
  onMinuteChange,
  onClose,
}: {
  projection: Projection | undefined;
  expectedSnapshotId: string;
  title: string;
  acceptedBy?: string;
  now: number;
  timezone: string;
  minute: number;
  onMinuteChange: (minute: number) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
  }, []);
  const consistent = projection && projection.snapshotId === expectedSnapshotId;
  const sampleMinute =
    projection?.baseline
      .filter((p) => p.minute >= 0 && p.minute <= Math.min(15, minute))
      .sort((a, b) => b.minute - a.minute)[0]?.minute ?? 0;
  const baseline = projection?.baseline.find((p) => p.minute === sampleMinute);
  const proposed = projection?.proposed.find((p) => p.minute === sampleMinute);
  const peak = (series: readonly FlowPoint[]) => {
    const values = series
      .filter((p) => p.minute >= 0 && p.minute <= 15)
      .map((p) => p.pressure);
    return values.length &&
      values.every((v) => v !== null && Number.isFinite(v))
      ? Math.max(...(values as number[]))
      : null;
  };
  const rows = [
    [
      'Modeled runway queue',
      available(baseline?.runwayQueue, 'aircraft'),
      available(proposed?.runwayQueue, 'aircraft'),
    ],
    [
      'Upstream waiting',
      available(baseline?.upstreamWaiting, 'aircraft'),
      available(proposed?.upstreamWaiting, 'aircraft'),
    ],
    [
      'Total waiting',
      available(baseline?.totalWaitingAircraftMinutes, 'aircraft-min'),
      available(proposed?.totalWaitingAircraftMinutes, 'aircraft-min'),
    ],
    [
      'Cumulative departures',
      available(baseline?.departures, 'aircraft'),
      available(proposed?.departures, 'aircraft'),
    ],
    [
      'Traffic pressure',
      available(baseline?.pressure, projection?.pressureUnit ?? ''),
      available(proposed?.pressure, projection?.pressureUnit ?? ''),
    ],
    [
      'Sampled pressure peak (0–15 min)',
      available(
        projection && peak(projection.baseline),
        projection?.pressureUnit ?? '',
      ),
      available(
        projection && peak(projection.proposed),
        projection?.pressureUnit ?? '',
      ),
    ],
  ];
  return (
    <section
      className="workspace-drawer scenario-review"
      aria-labelledby={titleId}
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
            {acceptedBy ? `Accepted by ${acceptedBy}` : 'Hypothetical scenario'}
          </p>
          <h2 id={titleId}>Scenario details</h2>
          <p className="drawer-subtitle">{title}</p>
        </div>
        <button
          ref={close}
          onClick={onClose}
          aria-label="Close scenario details"
        >
          Close
        </button>
      </header>
      <div
        className="drawer-scroll"
        role="region"
        aria-label="Scenario details content"
        tabIndex={0}
      >
        {!consistent ? (
          <p role="alert">
            {projection
              ? 'Projection snapshot does not match this advisory version. Comparison unavailable.'
              : 'Projection unavailable. Waiting for a validated result.'}
          </p>
        ) : (
          <>
            <p className="projection-caption">
              Projected scenario from snapshot{' '}
              {formatTime(projection.snapshotAtUtc, timezone)}; observations
              continue updating.
            </p>
            <p className="scenario-model">
              {ageLabel(projection.snapshotAtUtc, now)} · model{' '}
              {projection.modelVersion}
            </p>
            <div className="scrubber">
              <span>Compare model values</span>
              <div role="group" aria-label="Projection time">
                {[0, 5, 10, 15].map((t) => (
                  <button
                    key={t}
                    aria-pressed={sampleMinute === t}
                    onClick={() => onMinuteChange(t)}
                  >
                    {t === 0 ? 'Now' : `+${t} min`}
                  </button>
                ))}
              </div>
            </div>
            <div className="table-scroll">
              <table>
                <caption>
                  Scenario comparison at{' '}
                  {sampleMinute === 0 ? 'now' : `+${sampleMinute} minutes`}
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
            <small className="model-sample-note">
              Exact model samples are shown at 0, 5, 10 and 15 minutes. The map
              interpolates supplied path waypoints between samples.
            </small>
            <details className="projection-notes" open>
              <summary>Assumptions and limitations</summary>
              <h3>Declared assumptions</h3>
              <ul>
                {projection.assumptions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
              <h3>Limitations and tradeoffs</h3>
              <ul>
                {projection.limitations.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
              {projection.aircraftPaths && (
                <>
                  <h3>Aircraft path basis</h3>
                  <ul>
                    {projection.aircraftPaths.assumptions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
              <small>
                Snapshot {projection.snapshotId}. Comparison changes only the
                declared response parameters. Moving waiting upstream does not
                eliminate total waiting.
              </small>
            </details>
          </>
        )}
      </div>
    </section>
  );
}
