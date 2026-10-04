import { useEffect, useMemo, useState } from 'react';
import {
  SpacetimeDBProvider,
  useSpacetimeDB,
  useTable,
} from 'spacetimedb/react';

import { frontendConfig } from './lib/config';
import { createConnectionBuilder } from './lib/connection';
import { aircraftFreshness, displayTime, metarFreshness } from './lib/time';
import { tables } from './module_bindings';

function Observations() {
  const { isActive, connectionError } = useSpacetimeDB();
  const [weather, weatherReady] = useTable(tables.weatherRecords);
  const [aircraft, aircraftReady] = useTable(tables.aircraftObservations);
  const [health, healthReady] = useTable(tables.sourceHealth);
  const [now, setNow] = useState(Date.now);
  const [hasConnected, setHasConnected] = useState(false);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (isActive && !hasConnected) setHasConnected(true);
  const ready = isActive && weatherReady && aircraftReady && healthReady;
  const status = connectionError
    ? 'Connection error'
    : ready
      ? 'Connected'
      : isActive
        ? 'Loading observations'
        : hasConnected
          ? 'Disconnected'
          : 'Connecting';
  const knownPositions = aircraft.filter(
    (row) =>
      row.latitudeDeg !== undefined &&
      row.longitudeDeg !== undefined &&
      ['fresh', 'aging', 'stale'].includes(
        aircraftFreshness(row.observedAtUtc, row.positionAgeS, now),
      ),
  ).length;
  return (
    <section aria-labelledby="connection-title">
      <h2 id="connection-title">{status}</h2>
      <p role="status">
        {!ready
          ? 'Current observations unavailable. Waiting for a synchronized connection.'
          : weather.length || aircraft.length
            ? 'Provider observations received.'
            : 'No live observations have been received.'}
      </p>
      {!ready && (weather.length > 0 || aircraft.length > 0) && (
        <p>
          Cached observations below are stale; current state is unavailable.
        </p>
      )}
      <p>Advisories and controller decisions are unavailable.</p>
      <h3>Source health</h3>
      {health.length === 0 ? (
        <p>Source health unknown.</p>
      ) : (
        <ul>
          {health.map((row) => (
            <li key={row.provider}>
              {row.provider}:{' '}
              {row.status === 'ok' ? 'last fetch succeeded' : 'fetch failed'};
              fetched{' '}
              {displayTime(row.fetchedAtUtc, frontendConfig.displayTimezone)};
              last success{' '}
              {row.lastSuccessAtUtc
                ? displayTime(
                    row.lastSuccessAtUtc,
                    frontendConfig.displayTimezone,
                  )
                : 'unknown'}
            </li>
          ))}
        </ul>
      )}
      <h3>KDTW weather observations</h3>
      {weather.length === 0 ? (
        <p>Weather unknown.</p>
      ) : (
        weather.map((row) => (
          <p key={row.provider}>
            METAR:{' '}
            {ready
              ? metarFreshness(row.observedAtUtc, now)
              : 'stale cached observation'}
            ; observed{' '}
            {displayTime(row.observedAtUtc, frontendConfig.displayTimezone)}.
            Wind{' '}
            {row.windSpeedKt === undefined
              ? 'unknown'
              : `${row.windSpeedKt} kt`}
            ; visibility{' '}
            {row.visibilityM === undefined ? 'unknown' : `${row.visibilityM} m`}
            ; ceiling{' '}
            {row.ceilingFt === undefined ? 'unknown' : `${row.ceilingFt} ft`}.
          </p>
        ))
      )}
      <h3>Nearby aircraft observations</h3>
      <p>
        {ready && aircraft.length > 0
          ? `${knownPositions} observations with positions aged at most 120 seconds; ${aircraft.length} stored aircraft observations.`
          : 'Current aircraft coverage unknown.'}{' '}
        Airport demand and DTW flight association are unknown.
      </p>
      {aircraft.length > 0 && (
        <ul>
          {aircraft.map((row) => (
            <li key={row.aircraftId}>
              {row.aircraftId}:{' '}
              {ready
                ? aircraftFreshness(row.observedAtUtc, row.positionAgeS, now)
                : 'stale cached observation'}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function App() {
  const builder = useMemo(() => createConnectionBuilder(), []);
  return (
    <main className="dashboard">
      <header>
        <p className="eyebrow">WatchTower · KDTW</p>
        <h1>DTW flow advisory</h1>
      </header>
      {builder ? (
        <SpacetimeDBProvider connectionBuilder={builder}>
          <Observations />
        </SpacetimeDBProvider>
      ) : (
        <section aria-labelledby="connection-title">
          <h2 id="connection-title">Connection unavailable</h2>
          <p role="status">No live observations have been received.</p>
          <p>Advisories and controller decisions are unavailable.</p>
        </section>
      )}
    </main>
  );
}
