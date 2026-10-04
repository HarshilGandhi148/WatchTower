import type { DashboardData } from '../features/live/types';
import { tables, type DbConnection } from '../module_bindings';
import type {
  AircraftObservations,
  SourceHealth,
  WeatherRecords,
} from '../module_bindings/types';
import { currentAircraft } from '../components/dashboard/geography';
import {
  createConnectionBuilder,
  emptyData,
  type DashboardTransport,
} from './connection';
import { aircraftFreshness, metarFreshness } from './time';

export function observationData(
  weather: readonly WeatherRecords[],
  aircraft: readonly AircraftObservations[],
  health: readonly SourceHealth[],
  now = Date.now(),
): DashboardData {
  const positions = aircraft.flatMap((row) => {
    if (
      row.latitudeDeg === undefined ||
      row.longitudeDeg === undefined ||
      row.positionAgeS === undefined ||
      ['unknown', 'excluded'].includes(
        aircraftFreshness(row.observedAtUtc, row.positionAgeS, now),
      )
    )
      return [];
    return [
      {
        id: row.aircraftId,
        label: row.aircraftId,
        latitudeDeg: row.latitudeDeg,
        longitudeDeg: row.longitudeDeg,
        trackDeg: row.trackDeg === undefined ? null : row.trackDeg % 360,
        // Position time accounts for the provider's reported position age.
        observedAtUtc: new Date(
          Number(row.observedAtUtc.toMillis()) - row.positionAgeS * 1000,
        ).toISOString(),
        dtwAssociated: false,
      },
    ];
  });
  const nearby = currentAircraft(positions, now);
  const metar = weather.find((row) => row.airportIcao === 'KDTW');
  const aircraftHealth = health.find((row) => row.provider === 'adsb_lol');
  const aircraftAvailable =
    aircraftHealth?.status === 'ok' &&
    now - Number(aircraftHealth.fetchedAtUtc.toMillis()) <= 60_000 &&
    aircraft.length > 0 &&
    nearby.length > 0;
  return {
    ...emptyData,
    refreshedAtUtc: health.length
      ? new Date(
          Math.max(...health.map((row) => Number(row.fetchedAtUtc.toMillis()))),
        ).toISOString()
      : null,
    aircraft: nearby,
    // Retained rows are not a complete provider snapshot; never infer zero demand.
    nearbyCount: aircraftAvailable ? nearby.length : null,
    weather: metar
      ? {
          summary: `KDTW METAR · ${metarFreshness(metar.observedAtUtc, now)}`,
          basis: 'Observed',
          atUtc: metar.observedAtUtc.toISOString(),
          facts: [
            {
              label: 'Wind',
              value:
                metar.windSpeedKt === undefined
                  ? 'Unknown'
                  : `${metar.windSpeedKt} kt`,
            },
            {
              label: 'Visibility',
              value:
                metar.visibilityM === undefined
                  ? 'Unknown'
                  : `${metar.visibilityM} m`,
            },
            {
              label: 'Ceiling',
              value:
                metar.ceilingFt === undefined
                  ? 'Unknown'
                  : `${metar.ceilingFt} ft`,
            },
          ],
        }
      : null,
    sources: ['awc', 'adsb_lol'].map((provider) => {
      const row = health.find((item) => item.provider === provider);
      const stale =
        row &&
        now - Number(row.fetchedAtUtc.toMillis()) >
          (provider === 'awc' ? 90 * 60_000 : 60_000);
      return {
        id: provider,
        name: provider === 'awc' ? 'AWC METAR' : 'ADSB.lol',
        status: !row
          ? 'unknown'
          : row.status !== 'ok'
            ? 'unavailable'
            : stale
              ? 'stale'
              : provider === 'awc' &&
                  metar &&
                  metarFreshness(metar.observedAtUtc, now) === 'limited'
                ? 'limited'
                : 'fresh',
        sourceAtUtc: row?.sourceAtUtc?.toISOString() ?? null,
        fetchedAtUtc: row?.fetchedAtUtc.toISOString() ?? null,
        detail: !row
          ? 'No provider observations received.'
          : row.status !== 'ok'
            ? 'Last fetch failed; retained observations may be stale.'
            : 'Last fetch succeeded. Coverage and airport demand are not established.',
        attribution:
          provider === 'awc' ? 'NOAA Aviation Weather Center' : 'ADSB.lol',
        attributionUrl:
          provider === 'awc'
            ? 'https://aviationweather.gov/'
            : 'https://adsb.lol/',
      };
    }),
  };
}

export function createSpacetimeTransport(): DashboardTransport | undefined {
  if (!createConnectionBuilder()) return undefined;
  return {
    connect(events) {
      let stopped = false;
      let generation = 0;
      let connection: DbConnection | undefined;
      let retry: ReturnType<typeof setTimeout> | undefined;
      let refresh: ReturnType<typeof setInterval> | undefined;
      let delay = 1000;
      const connect = () => {
        const attempt = ++generation;
        let applied = false;
        const alive = () => !stopped && generation === attempt;
        const emit = () => {
          if (alive() && applied && connection)
            events.ready(
              observationData(
                [...connection.db.weatherRecords.iter()],
                [...connection.db.aircraftObservations.iter()],
                [...connection.db.sourceHealth.iter()],
              ),
            );
        };
        const failed = (message: string, isError: boolean) => {
          if (!alive()) return;
          ++generation;
          if (refresh) clearInterval(refresh);
          if (isError) events.error(message);
          else events.disconnected(message);
          connection?.disconnect();
          retry = setTimeout(connect, delay);
          delay = Math.min(delay * 2, 30_000);
        };
        events.synchronizing();
        try {
          connection = createConnectionBuilder()!
            .onConnect((db) => {
              if (!alive()) {
                db.disconnect();
                return;
              }
              connection = db;
              for (const table of [
                db.db.weatherRecords,
                db.db.aircraftObservations,
                db.db.sourceHealth,
              ]) {
                table.onInsert(emit);
                table.onUpdate(emit);
                table.onDelete(emit);
              }
              db.subscriptionBuilder()
                .onApplied(() => {
                  if (!alive()) return;
                  applied = true;
                  delay = 1000;
                  emit();
                  refresh = setInterval(emit, 1000);
                })
                .onError(() =>
                  failed(
                    'Observation subscription failed. Reconnecting.',
                    true,
                  ),
                )
                .subscribe([
                  tables.weatherRecords,
                  tables.aircraftObservations,
                  tables.sourceHealth,
                ]);
            })
            .onDisconnect(() =>
              failed(
                'Connection lost. Cached observations are unavailable as current data.',
                false,
              ),
            )
            .onConnectError(() =>
              failed('Unable to connect to SpacetimeDB. Retrying.', true),
            )
            .build();
        } catch {
          failed('Unable to connect to SpacetimeDB. Retrying.', true);
        }
      };
      connect();
      return () => {
        stopped = true;
        ++generation;
        if (retry) clearTimeout(retry);
        if (refresh) clearInterval(refresh);
        connection?.disconnect();
      };
    },
    command: async () => {
      throw new Error('Advisory and controller reducers are not implemented.');
    },
  };
}
