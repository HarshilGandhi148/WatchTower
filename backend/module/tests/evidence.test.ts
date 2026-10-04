import { describe, expect, it, vi } from 'vitest';
import { Timestamp } from 'spacetimedb';
import {
  canonicalJson,
  config,
  deliveryExpired,
  feed,
  message,
  projectionInputs,
} from '../src/reducers/evidence_rules';

// Execute the actual reducer bodies with deterministic clocks for expiry/retention.
// Real transaction rollback, transport and schema are separately covered by the
// mandatory local-server harness; this mock never claims to provide transactions.
vi.mock('spacetimedb/server', () => ({
  t: { string: () => ({}), u64: () => ({}) },
}));
vi.mock('../src/schema', () => ({
  database: { reducer: (args: unknown, body?: unknown) => body ?? args },
}));
import {
  close_unfinished_cases,
  expire_deliveries,
  record_candidate_receipt,
  prune_live_observations,
  ingest_feed_batch,
  create_evidence_snapshot,
} from '../src/reducers/evidence';
import {
  ingest_aircraft_batch,
  ingest_weather_record,
} from '../src/reducers/ingestion';

const at = '2026-10-04T12:00:00.000Z';
const now = Date.parse(at);
const configuration = {
  airport_icao: 'KDTW',
  latitude_deg: 42.2122,
  longitude_deg: -83.3533,
  radius_nm: 40,
  version: 'airport-v1',
};
const adsb = {
  schema_version: 1,
  data_mode: 'live',
  batch_id: 'TEST-batch',
  provider: 'adsb_lol',
  source_ref: 'https://example.test',
  configuration,
  coverage_flags: [],
  snapshot: {
    source_id: 'adsb.lol',
    provider_time_utc: at,
    fetched_at_utc: at,
    observations: [],
    reported_total: 0,
    rejected_records: 0,
    duplicates_dropped: 0,
  },
};
const env = {
  schema_version: 1,
  message_id: 'TEST-message',
  case_id: 'TEST-case',
  sender_agent: 'congestion',
  created_at_utc: at,
  snapshot_id: 'TEST-snapshot',
  evidence_ids: ['TEST-batch'],
  airport_icao: 'KDTW',
  valid_until_utc: '2026-10-04T12:01:00.000Z',
  correlation_id: null,
  reply_to: null,
};

type Row = Record<string, unknown>;
function table(key: string, entries: Row[] = []) {
  const rows = new Map(entries.map((row) => [row[key], row]));
  const index = {
    find: (id: unknown) => rows.get(id),
    update: (row: Row) => rows.set(row[key], row),
    delete: (id: unknown) => rows.delete(id),
  };
  return {
    [key]: index,
    iter: () => rows.values(),
    insert: (row: Row) => {
      rows.set(row[key], row);
      return row;
    },
    rows,
    status: {
      filter: (status: unknown) =>
        [...rows.values()].filter((row) => row.status === status),
    },
    closed: {
      filter: (closed: unknown) =>
        [...rows.values()].filter((row) => row.closed === closed),
    },
  };
}
function invoke(reducer: unknown, ctx: unknown, args: object = {}) {
  (reducer as (ctx: unknown, args: object) => void)(ctx, args);
}
const stamp = (millis: number) => new Timestamp(BigInt(millis) * 1000n);
function context(role: string, millis = now) {
  return {
    sender: 'TEST-identity',
    timestamp: stamp(millis),
    db: {
      servicePermissions: table('identity', [
        { identity: 'TEST-identity', role },
      ]),
      pendingDeliveries: table('candidateId'),
      evidenceSnapshots: table('snapshotId'),
      aircraftObservations: table('aircraftId'),
      feedBatches: table('batchId'),
      latestFeeds: table('provider'),
      sourceHealth: table('provider'),
      weatherRecords: table('provider'),
      evidenceRecords: table('evidenceId'),
      flightRecords: table('batchId'),
      ingestEvent: table('eventKey'),
    },
  };
}

describe('stored feed validation', () => {
  it('refreshes retained weather and freezes forecast evidence with an aligned live window', () => {
    const ctx = context('ingestion', now + 2000);
    const payload = {
      schema_version: 1,
      data_mode: 'live',
      provider: 'awc',
      airport_icao: 'KDTW',
      record_type: 'METAR',
      source_ref: 'https://example.test/metar',
      observed_at_utc: at,
      fetched_at_utc: at,
      wind_direction_deg: 180,
      wind_speed_kt: 5,
      wind_gust_kt: null,
      visibility_m: null,
      ceiling_ft: null,
    };
    invoke(ingest_weather_record, ctx, {
      payloadJson: JSON.stringify(payload),
    });
    const fetched = new Date(now + 1000).toISOString();
    const taf = {
      evidence_id: 'TEST-taf',
      airport_icao: 'KDTW',
      issued_at_utc: at,
      fetched_at_utc: at,
      source_ref: 'https://example.test/taf',
      raw_text: 'TEST TAF',
      start_utc: at,
      end_utc: new Date(now + 86400000).toISOString(),
      groups: [],
      amendment: 'original',
      temperatures: [],
    };
    const full = {
      ...payload,
      fetched_at_utc: fetched,
      metar_evidence: {
        evidence_id: 'TEST-metar',
        airport_icao: 'KDTW',
        observed_at_utc: at,
        fetched_at_utc: fetched,
        source_ref: payload.source_ref,
        raw_text: 'TEST METAR',
        conditions: {
          wind_direction_deg: 180,
          wind_speed_kt: 5,
          wind_gust_kt: null,
          visibility_m: 16093.44,
          visibility_bound: 'greater_than',
          ceiling_ft: null,
        },
      },
      taf_evidence: [taf],
    };
    invoke(ingest_weather_record, ctx, { payloadJson: JSON.stringify(full) });
    const weather = ctx.db.weatherRecords.provider.find('awc')!;
    expect(weather.fetchedAtUtc).toEqual(stamp(now + 1000));
    const db = {
      ...ctx.db,
      evidenceSnapshots: {
        ...ctx.db.evidenceSnapshots,
        caseId: { find: () => undefined },
      },
    };
    const request = {
      snapshot_id: 'TEST-live-window',
      case_id: 'TEST-case',
      configuration,
      window_start_utc: at,
      window_end_utc: new Date(now + 900000).toISOString(),
      expires_at_utc: new Date(now + 120000).toISOString(),
      align_window_to_cutoff: true,
    };
    const frozen = { ...ctx, db, sender: 'TEST-identity' };
    db.servicePermissions.identity.update({
      identity: 'TEST-identity',
      role: 'congestion',
    });
    expect(() =>
      invoke(
        create_evidence_snapshot,
        { ...frozen, timestamp: stamp(now + 500) },
        { payloadJson: JSON.stringify(request) },
      ),
    ).toThrow('snapshot clock');
    expect(db.evidenceSnapshots.rows.size).toBe(0);
    invoke(create_evidence_snapshot, frozen, {
      payloadJson: JSON.stringify(request),
    });
    const saved = db.evidenceSnapshots.snapshotId.find(request.snapshot_id)!;
    const content = JSON.parse(saved.contentJson as string);
    expect(content.window_start_utc).toEqual(content.cutoff_at_utc);
    expect(
      Date.parse(content.window_end_utc) - Date.parse(content.window_start_utc),
    ).toBe(900000);
    expect(content.evidence_ids).toContain(
      'TEST-live-window:weather:awc:taf:0',
    );
    expect(
      JSON.parse(
        db.evidenceRecords.evidenceId.find(
          'TEST-live-window:weather:awc:taf:0',
        )!.contentJson as string,
      ),
    ).toEqual(taf);
    invoke(create_evidence_snapshot, frozen, {
      payloadJson: JSON.stringify(request),
    });
    expect(db.evidenceSnapshots.rows.size).toBe(1);
  });
  it('refreshes the observation endpoint when position time is unchanged', () => {
    const ctx = context('ingestion', now + 10_000);
    const observation = {
      aircraft_id: 'abc123',
      observed_at_utc: new Date(now - 10_000).toISOString(),
      latitude_deg: 42.2,
      longitude_deg: -83.3,
      position_age_s: 10,
      altitude_ft: null,
      speed_kt: null,
      track_deg: null,
      on_ground: false,
    };
    const first = {
      schema_version: 1,
      data_mode: 'live',
      provider: 'adsb_lol',
      fetched_at_utc: at,
      source_ref: 'https://example.test',
      observations: [observation],
    };
    invoke(ingest_aircraft_batch, ctx, { payloadJson: JSON.stringify(first) });
    const next = {
      ...first,
      fetched_at_utc: new Date(now + 10_000).toISOString(),
      observations: [{ ...observation, position_age_s: 20 }],
    };
    invoke(ingest_aircraft_batch, ctx, { payloadJson: JSON.stringify(next) });
    const row = ctx.db.aircraftObservations.rows.get('abc123')!;
    expect((row.observedAtUtc as Timestamp).toMillis()).toBe(
      BigInt(now - 10_000),
    );
    expect((row.fetchedAtUtc as Timestamp).toMillis()).toBe(
      BigInt(now + 10_000),
    );
    expect(row.positionAgeS).toBe(20);
    expect(() =>
      invoke(ingest_aircraft_batch, ctx, {
        payloadJson: JSON.stringify({
          ...next,
          observations: [{ ...observation, position_age_s: 21 }],
        }),
      }),
    ).toThrow('Conflicting equal-time observation');
  });

  it('refreshes a repeated position without treating increasing provider age as a conflict', () => {
    const ctx = context('ingestion', now + 10_000);
    const positionAt = new Date(now - 10_000).toISOString();
    const observation = {
      aircraft_id: 'abc123',
      callsign: 'TEST',
      latitude_deg: 42.2,
      longitude_deg: -83.3,
      altitude_ft: 1000,
      on_ground: false,
      ground_speed_kt: 100,
      track_deg: 90,
      position_age_s: 10,
      observed_at_utc: positionAt,
      provider_time_utc: at,
      fetched_at_utc: at,
      missing_fields: [],
      quality_flags: [],
    };
    const first = {
      ...adsb,
      snapshot: {
        ...adsb.snapshot,
        reported_total: 1,
        observations: [observation],
      },
    };
    invoke(ingest_feed_batch, ctx, { payloadJson: JSON.stringify(first) });
    const nextAt = new Date(now + 10_000).toISOString();
    const next = {
      ...first,
      batch_id: 'TEST-next-batch',
      snapshot: {
        ...first.snapshot,
        provider_time_utc: nextAt,
        fetched_at_utc: nextAt,
        observations: [
          {
            ...observation,
            position_age_s: 20,
            provider_time_utc: nextAt,
            fetched_at_utc: nextAt,
          },
        ],
      },
    };
    invoke(ingest_feed_batch, ctx, { payloadJson: JSON.stringify(next) });
    const row = ctx.db.aircraftObservations.rows.get('abc123')!;
    expect((row.observedAtUtc as Timestamp).toMillis()).toBe(
      BigInt(now - 10_000),
    );
    expect((row.fetchedAtUtc as Timestamp).toMillis()).toBe(
      BigInt(now + 10_000),
    );
    expect(row.positionAgeS).toBe(20);
    expect(ctx.db.feedBatches.rows.size).toBe(2);
  });
  it('keeps empty success and unparsed source time/rejections distinct', () => {
    expect(feed(JSON.stringify(adsb), now).snapshot.observations).toEqual([]);
    expect(
      feed(
        JSON.stringify({
          ...adsb,
          snapshot: {
            ...adsb.snapshot,
            provider_time_utc: null,
            rejected_records: 2,
          },
        }),
        now,
      ).snapshot.rejected_records,
    ).toBe(2);
  });
  it.each([
    { data_mode: 'test' },
    { data_mode: 'projection' },
    { schema_version: 2 },
    { provider: 'invented' },
    { batch_id: '' },
    { configuration: { ...configuration, airport_icao: 'KJFK' } },
    { configuration: { ...configuration, radius_nm: 0 } },
    { configuration: { ...configuration, latitude_deg: null } },
    {
      snapshot: {
        ...adsb.snapshot,
        fetched_at_utc: '2026-10-04T12:02:00.000Z',
      },
    },
    {
      snapshot: {
        ...adsb.snapshot,
        provider_time_utc: '2026-10-04T12:02:00.000Z',
      },
    },
    { snapshot: { ...adsb.snapshot, rejected_records: -1 } },
    { snapshot: { ...adsb.snapshot, rejected_records: true } },
    { snapshot: { ...adsb.snapshot, observations: Array(1001).fill({}) } },
    { response_headers: { authorization: 'TEST forbidden' } },
  ])('rejects invalid or oversized feed: %j', (change) => {
    expect(() => feed(JSON.stringify({ ...adsb, ...change }), now)).toThrow();
  });
  it('accepts only matching configuration and explicit UTC envelope units', () => {
    expect(config(configuration).radius_nm).toBe(40);
    expect(message(env, 'congestion', now).snapshot_id).toBe('TEST-snapshot');
    for (const change of [
      { sender_agent: 'weather' },
      { airport_icao: 'KJFK' },
      { evidence_ids: ['TEST-batch', 'TEST-batch'] },
      { valid_until_utc: at },
      { created_at_utc: '2026-10-04T08:00:00.000-04:00' },
    ]) {
      expect(() => message({ ...env, ...change }, 'congestion', now)).toThrow();
    }
  });
  it('canonicalizes object key order for identical retries', () => {
    expect(canonicalJson({ b: 1, a: { d: 2, c: 3 } })).toBe(
      canonicalJson({ a: { c: 3, d: 2 }, b: 1 }),
    );
  });
  it('rejects unsupported demand before projection can be stored', () => {
    expect(() =>
      projectionInputs({ input: {}, assumptions: {}, response: {} }, {}, {}),
    ).toThrow();
  });
});

describe('delivery receipt and restart reducers', () => {
  it('expires pending deliveries exactly at the boundary and keeps received records', () => {
    const ctx = context('congestion');
    ctx.db.pendingDeliveries.rows.set('TEST-expired', {
      candidateId: 'TEST-expired',
      expiresAtUtc: stamp(now),
      status: 'pending',
    });
    ctx.db.pendingDeliveries.rows.set('TEST-future', {
      candidateId: 'TEST-future',
      expiresAtUtc: stamp(now + 1),
      status: 'pending',
    });
    ctx.db.pendingDeliveries.rows.set('TEST-received', {
      candidateId: 'TEST-received',
      expiresAtUtc: stamp(now - 1),
      status: 'received',
    });
    invoke(expire_deliveries, ctx);
    expect(ctx.db.pendingDeliveries.rows.get('TEST-expired')?.status).toBe(
      'expired',
    );
    expect(ctx.db.pendingDeliveries.rows.get('TEST-future')?.status).toBe(
      'pending',
    );
    expect(ctx.db.pendingDeliveries.rows.get('TEST-received')?.status).toBe(
      'received',
    );
    expect(deliveryExpired('pending', now, now)).toBe(true);
  });
  it('records one receipt, allows identical retries and rejects conflicting/expired deliveries', () => {
    const ctx = context('validation'),
      ready = { candidate_id: 'TEST-ready' };
    ctx.db.pendingDeliveries.rows.set('TEST-ready', {
      candidateId: 'TEST-ready',
      expiresAtUtc: stamp(now + 1),
      contentJson: canonicalJson(ready),
      status: 'pending',
    });
    const args = {
      payloadJson: JSON.stringify({ candidate_id: 'TEST-ready', ready }),
    };
    invoke(record_candidate_receipt, ctx, args);
    invoke(record_candidate_receipt, ctx, args);
    expect(ctx.db.pendingDeliveries.rows.size).toBe(1);
    expect(ctx.db.pendingDeliveries.rows.get('TEST-ready')?.status).toBe(
      'received',
    );
    expect(() =>
      invoke(record_candidate_receipt, ctx, {
        payloadJson: JSON.stringify({
          candidate_id: 'TEST-ready',
          ready: { candidate_id: 'conflict' },
        }),
      }),
    ).toThrow();
    ctx.db.pendingDeliveries.rows.set('TEST-ready', {
      candidateId: 'TEST-ready',
      expiresAtUtc: stamp(now),
      contentJson: canonicalJson(ready),
      status: 'pending',
    });
    expect(() => invoke(record_candidate_receipt, ctx, args)).toThrow();
  });
  it.each(['ingestion', 'weather', 'validation', 'admin', 'unknown'])(
    'denies outbox expiration for %s',
    (role) => {
      expect(() => invoke(expire_deliveries, context(role))).toThrow(
        'Unauthorized',
      );
    },
  );
  it.each(['ingestion', 'weather', 'congestion', 'admin', 'unknown'])(
    'denies receipt for %s',
    (role) => {
      expect(() =>
        invoke(record_candidate_receipt, context(role), { payloadJson: '{}' }),
      ).toThrow('Unauthorized');
    },
  );
  it('closes unfinished snapshots while retaining saved deliveries for a specialist restart', () => {
    const ctx = context('congestion');
    ctx.db.evidenceSnapshots.rows.set('TEST-snapshot', {
      snapshotId: 'TEST-snapshot',
      closed: false,
    });
    ctx.db.pendingDeliveries.rows.set('TEST-delivery', {
      candidateId: 'TEST-delivery',
      status: 'pending',
    });
    invoke(close_unfinished_cases, ctx);
    expect(ctx.db.evidenceSnapshots.rows.get('TEST-snapshot')?.closed).toBe(
      true,
    );
    expect(ctx.db.pendingDeliveries.rows.get('TEST-delivery')?.status).toBe(
      'pending',
    );
  });
  it('prunes stale live positions without touching snapshots or deliveries', () => {
    const ctx = context('ingestion');
    ctx.db.aircraftObservations.rows.set('old', {
      aircraftId: 'old',
      fetchedAtUtc: stamp(now - 1800001),
    });
    ctx.db.aircraftObservations.rows.set('fresh', {
      aircraftId: 'fresh',
      fetchedAtUtc: stamp(now),
    });
    ctx.db.evidenceSnapshots.rows.set('TEST-snapshot', {
      snapshotId: 'TEST-snapshot',
      closed: false,
    });
    invoke(prune_live_observations, ctx);
    expect([...ctx.db.aircraftObservations.rows.keys()]).toEqual(['fresh']);
    expect(ctx.db.evidenceSnapshots.rows.size).toBe(1);
  });
});
