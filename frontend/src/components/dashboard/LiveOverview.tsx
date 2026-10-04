import { useId } from 'react';
import type { DashboardSnapshot, Projection } from '../../features/live/types';
import { ScenarioPlayback } from '../projections/ScenarioPlayback';
import { ageLabel, formatTime } from '../../lib/time';
import { Popover } from '../workspace/Popover';
import { currentAircraft } from './geography';
import { TrafficMap } from './TrafficMap';

export function LiveOverview({
  id,
  state,
  now,
  timezone,
  scenario = null,
  playbackVisible = true,
  onMinuteChange,
  onCloseProjection,
  onScenarioDetails,
}: {
  id?: string;
  state: DashboardSnapshot;
  now: number;
  timezone: string;
  scenario?: {
    projection: Projection | undefined;
    snapshotId: string;
    title: string;
    acceptedBy?: string;
    minute: number;
  } | null;
  playbackVisible?: boolean;
  onMinuteChange: (minute: number) => void;
  onCloseProjection: () => void;
  onScenarioDetails: () => void;
}) {
  const titleId = useId();
  const popoverGroup = useId();
  const positions = currentAircraft(state.aircraft, now);
  return (
    <section className="panel live-panel" aria-labelledby={id ?? titleId}>
      <div className="live-topbar">
        <div className="live-heading">
          <h2 id={id ?? titleId} tabIndex={-1}>
            Traffic overview
          </h2>
          <span className="tag">
            {state.mode === 'TEST' ? 'Sample data' : 'Observed'}
          </span>
        </div>
        <div className="traffic-summary">
          <div>
            <strong>{state.nearbyCount ?? '—'}</strong>
            <span>
              {state.connection === 'ready'
                ? 'Nearby aircraft'
                : 'Last known nearby aircraft'}
            </span>
          </div>
          <div>
            <strong>
              40 <small>NM</small>
            </strong>
            <span>Monitoring radius</span>
          </div>
          <div>
            <strong>
              {state.connection === 'ready' && state.nearbyCount !== null
                ? 'Available'
                : 'Unknown'}
            </strong>
            <span>Position coverage</span>
          </div>
          {state.nearbyCount === null && (
            <small className="coverage-note">
              Not estimable with available data
            </small>
          )}
        </div>
      </div>
      <TrafficMap
        aircraft={positions}
        now={now}
        disconnected={state.connection !== 'ready'}
        timezone={timezone}
        mode={state.mode}
        mapProjection={
          scenario
            ? {
                projection: scenario.projection,
                expectedSnapshotId: scenario.snapshotId,
                minute: scenario.minute,
                mode: state.mode,
              }
            : null
        }
      >
        {scenario && playbackVisible && (
          <ScenarioPlayback
            key={`${scenario.projection?.id}-${scenario.snapshotId}-${scenario.acceptedBy ?? 'preview'}`}
            projection={scenario.projection}
            expectedSnapshotId={scenario.snapshotId}
            title={scenario.title}
            acceptedBy={scenario.acceptedBy}
            minute={scenario.minute}
            mode={state.mode}
            timezone={timezone}
            onMinuteChange={onMinuteChange}
            onClose={onCloseProjection}
            onDetails={onScenarioDetails}
          />
        )}
      </TrafficMap>
      <div className="overview-tools">
        <Popover
          className="overview-popover"
          group={popoverGroup}
          summary={
            <>
              <span>Weather</span>
              <strong>{state.weather?.summary ?? 'Weather unavailable'}</strong>
            </>
          }
        >
          <h3>KDTW weather report</h3>
          {state.weather ? (
            <>
              <p>{state.weather.summary}</p>
              <p>
                {state.weather.basis} ·{' '}
                {formatTime(state.weather.atUtc, timezone)} ·{' '}
                {ageLabel(state.weather.atUtc, now)}
              </p>
              <dl className="facts">
                {state.weather.facts.map((fact) => (
                  <div key={fact.label}>
                    <dt>{fact.label}</dt>
                    <dd>{fact.value}</dd>
                  </div>
                ))}
              </dl>
            </>
          ) : (
            <p>No supported weather report received.</p>
          )}
        </Popover>
        <Popover
          className="overview-popover"
          group={popoverGroup}
          summary={
            <>
              <span>Restrictions</span>
              <strong>
                {state.events.length
                  ? `${state.events.length} published`
                  : state.eventsStatus === 'none_published'
                    ? 'None published'
                    : 'Unavailable'}
              </strong>
            </>
          }
        >
          <h3>Published restrictions</h3>
          {state.events.length ? (
            state.events.map((event) => (
              <div key={event.id}>
                <h4>{event.title}</h4>
                <p>{event.detail}</p>
                <small>{formatTime(event.atUtc, timezone)}</small>
              </div>
            ))
          ) : (
            <p>
              {state.eventsStatus === 'none_published'
                ? 'No DTW restrictions published in this feed. Local flow conditions may differ.'
                : 'Waiting for source data.'}
            </p>
          )}
        </Popover>
        <Popover
          className="overview-popover"
          group={popoverGroup}
          summary={
            <>
              <span>Aircraft</span>
              <strong>{positions.length} positions</strong>
            </>
          }
        >
          <h3>Observed aircraft and timestamps</h3>
          <p>Nearby aircraft are not necessarily DTW-bound.</p>
          {positions.length ? (
            <ul className="plain-list">
              {positions.map((aircraft) => (
                <li key={aircraft.id}>
                  <span>
                    {aircraft.label}
                    {state.connection !== 'ready' ||
                    now - Date.parse(aircraft.observedAtUtc) > 60_000
                      ? ' · stale'
                      : ''}
                  </span>{' '}
                  ·{' '}
                  {aircraft.dtwAssociated
                    ? 'DTW-associated'
                    : 'association unknown'}{' '}
                  · {formatTime(aircraft.observedAtUtc, timezone)} ·{' '}
                  {ageLabel(aircraft.observedAtUtc, now)}
                </li>
              ))}
            </ul>
          ) : (
            <p>No supported current positions.</p>
          )}
        </Popover>
        <Popover
          className="overview-popover"
          group={popoverGroup}
          summary={<span>Specialist findings</span>}
        >
          <h3>Latest specialist findings</h3>
          {state.findings.length ? (
            <ul className="plain-list">
              {state.findings.map((finding) => (
                <li key={finding.agent}>
                  <strong>{finding.agent}</strong> · {finding.summary}
                  <br />
                  <small>{formatTime(finding.atUtc, timezone)}</small>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              No supported findings received from weather, congestion, or
              validation.
            </p>
          )}
        </Popover>
      </div>
      <div className="agent-pipeline" aria-label="Agent workflow">
        {['Weather', 'Congestion', 'Validation'].map((agent, index) => {
          const finding = state.findings.find(
            (f) => f.agent.toLowerCase() === agent.toLowerCase(),
          );
          return (
            <article key={agent}>
              <span className="agent-step">0{index + 1}</span>
              <div>
                <h3>{agent}</h3>
                <p>{finding?.summary ?? 'Awaiting assessment'}</p>
                {finding && <small>{ageLabel(finding.atUtc, now)}</small>}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
