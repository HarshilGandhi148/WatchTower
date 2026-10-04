import {
  exchangeData,
  object,
  sanitize,
  strings,
  text,
  time,
} from '../features/exchange/adapter';
import type { EvidenceDetail } from '../features/exchange/types';
import { reviewFromJson } from '../features/exchange/reviews';
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
        observedAtUtc: new Date(
          Number(row.observedAtUtc.toMillis()),
        ).toISOString(),
        dtwAssociated: false,
      },
    ];
  });
  const nearby = currentAircraft(positions, now);
  const metar = weather.find((row) => row.airportIcao === 'KDTW');
  const aircraftHealth = health.find((row) => row.provider === 'adsb_lol');
  const batchIds = new Set(
    aircraft
      .filter(
        (row) =>
          row.provider === 'adsb_lol' &&
          aircraftHealth &&
          row.fetchedAtUtc.toMillis() ===
            aircraftHealth.fetchedAtUtc.toMillis(),
      )
      .map((row) => row.aircraftId),
  );
  const batchNearby = nearby.filter((row) => batchIds.has(row.id));
  const aircraftAvailable =
    aircraftHealth?.status === 'ok' &&
    now >= Number(aircraftHealth.fetchedAtUtc.toMillis()) &&
    now - Number(aircraftHealth.fetchedAtUtc.toMillis()) <= 60_000 &&
    batchNearby.length > 0;
  return {
    ...emptyData,
    refreshedAtUtc: health.length
      ? new Date(
          Math.max(...health.map((row) => Number(row.fetchedAtUtc.toMillis()))),
        ).toISOString()
      : null,
    aircraft: nearby,
    // Retained rows are not a complete provider snapshot; never infer zero demand.
    nearbyCount: aircraftAvailable ? batchNearby.length : null,
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
        status:
          !row || Number(row.fetchedAtUtc.toMillis()) > now
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
            events.ready({
              ...observationData(
                [...connection.db.weatherRecords.iter()],
                [...connection.db.aircraftObservations.iter()],
                [...connection.db.sourceHealth.iter()],
              ),
              exchange: {
                ...exchangeData(
                  {
                    requests: [...connection.db.assessmentRequests.iter()],
                    findings: [...connection.db.agentFindings.iter()],
                    candidates: [...connection.db.flowCandidates.iter()],
                    deliveries: [...connection.db.pendingDeliveries.iter()],
                    reports: [...connection.db.validationReports.iter()],
                  },
                  Date.now(),
                ),
                reviews: [
                  ...connection.db.recommendationReviews.iter(),
                ].flatMap((row) => {
                  try {
                    return [reviewFromJson(JSON.parse(row.contentJson))];
                  } catch {
                    return [];
                  }
                }),
              },
            });
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
                db.db.assessmentRequests,
                db.db.agentFindings,
                db.db.flowCandidates,
                db.db.pendingDeliveries,
                db.db.validationReports,
                db.db.recommendationReviews,
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
                  tables.assessmentRequests,
                  tables.agentFindings,
                  tables.flowCandidates,
                  tables.pendingDeliveries,
                  tables.validationReports,
                  tables.recommendationReviews,
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

export function loadEvidence(snapshotId: string): Promise<EvidenceDetail> {
  return new Promise((resolve, reject) => {
    const builder = createConnectionBuilder();
    if (!builder) {
      reject(new Error('Evidence connection unavailable.'));
      return;
    }
    let db: DbConnection | undefined;
    let finished = false;
    const finish = (result?: EvidenceDetail) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      db?.disconnect();
      if (result) resolve(result);
      else
        reject(new Error('Stored evidence unavailable. Retry when connected.'));
    };
    const timer = setTimeout(() => finish(), 10000);
    try {
      db = builder
        .onConnect((connection) => {
          if (finished) {
            connection.disconnect();
            return;
          }
          db = connection;
          connection
            .subscriptionBuilder()
            .onApplied(() => {
              try {
                const row =
                  connection.db.evidenceSnapshots.snapshotId.find(snapshotId);
                if (!row) {
                  finish();
                  return;
                }
                const v = object(JSON.parse(row.contentJson));
                finish({
                  snapshotId: text(v.snapshot_id),
                  windowStartUtc: time(v.window_start_utc),
                  windowEndUtc: time(v.window_end_utc),
                  evidenceIds: strings(v.evidence_ids),
                  content: sanitize(v),
                });
              } catch {
                finish();
              }
            })
            .onError(() => finish())
            .subscribe(
              `SELECT * FROM evidence_snapshots WHERE snapshot_id = '${snapshotId.replaceAll("'", "''")}'`,
            );
        })
        .onConnectError(() => finish())
        .onDisconnect(() => finish())
        .build();
    } catch {
      finish();
    }
  });
}
