import { describe, expect, it } from 'vitest';
import { requireRole } from '../src/auth/roles';
import {
  healthTransition,
  parseAircraftBatch,
  parseFailure,
  parseWeather,
  shouldReplace,
} from '../src/reducers/validation';

const observed = '2026-10-04T00:00:00.000Z';
const now = Date.parse(observed);
const envelope = {
  schema_version: 1,
  data_mode: 'live',
  provider: 'awc',
  source_ref: 'https://aviationweather.gov/',
  fetched_at_utc: observed,
};
const weather = {
  ...envelope,
  airport_icao: 'KDTW',
  record_type: 'METAR',
  observed_at_utc: observed,
  wind_direction_deg: null,
  wind_speed_kt: null,
  wind_gust_kt: null,
  visibility_m: null,
  ceiling_ft: null,
};
const aircraft = {
  aircraft_id: 'test-plane',
  observed_at_utc: observed,
  latitude_deg: null,
  longitude_deg: null,
  altitude_ft: null,
  speed_kt: null,
  track_deg: null,
  position_age_s: null,
  on_ground: null,
};
const batch = { ...envelope, provider: 'adsb_lol', observations: [aircraft] };

describe('observation validation', () => {
  it('accepts optional retained weather and rejects mismatched or future source evidence', () => {
    const full = {
      ...weather,
      metar_evidence: {
        evidence_id: 'TEST-metar',
        airport_icao: 'KDTW',
        observed_at_utc: observed,
        fetched_at_utc: observed,
        source_ref: weather.source_ref,
        raw_text: 'TEST METAR',
        conditions: {
          wind_direction_deg: null,
          wind_speed_kt: null,
          wind_gust_kt: null,
          visibility_m: null,
          visibility_bound: 'exact',
          ceiling_ft: null,
        },
      },
      taf_evidence: [],
    };
    expect(
      parseWeather(JSON.stringify(full), now).retained?.metar_evidence,
    ).toEqual(full.metar_evidence);
    for (const change of [
      { airport_icao: 'KJFK' },
      { source_ref: 'https://wrong.example' },
      { fetched_at_utc: '2026-10-04T00:00:01.000Z' },
      { conditions: { ...full.metar_evidence.conditions, wind_speed_kt: 20 } },
    ])
      expect(() =>
        parseWeather(
          JSON.stringify({
            ...full,
            metar_evidence: { ...full.metar_evidence, ...change },
          }),
          now,
        ),
      ).toThrow();
    expect(() =>
      parseWeather(
        JSON.stringify({
          ...full,
          taf_evidence: [
            {
              evidence_id: 'TEST-taf',
              airport_icao: 'KDTW',
              issued_at_utc: observed,
              fetched_at_utc: '2026-10-04T00:00:01.000Z',
              source_ref: weather.source_ref,
              raw_text: 'TEST TAF',
              start_utc: observed,
              end_utc: '2026-10-05T00:00:00.000Z',
              groups: [],
              amendment: 'original',
              temperatures: [],
            },
          ],
        }),
        now,
      ),
    ).toThrow();
  });
  it('preserves unknowns and zero measurements independently', () => {
    expect(
      parseWeather(JSON.stringify(weather), now).fields.windSpeedKt,
    ).toBeUndefined();
    expect(
      parseWeather(JSON.stringify({ ...weather, wind_speed_kt: 0 }), now).fields
        .windSpeedKt,
    ).toBe(0);
    expect(
      parseAircraftBatch(JSON.stringify(batch), now).observations[0].fields
        .onGround,
    ).toBeUndefined();
  });
  it.each([
    { data_mode: 'test' },
    { data_mode: 'projection' },
    { schema_version: 2 },
    { provider: 'invented' },
    { airport_icao: 'KJFK' },
    { record_type: 'TAF' },
    { source_ref: '' },
    { source_ref: 'bad\nreference' },
    { wind_speed_kt: -1 },
    { wind_speed_kt: '20 kt' },
    { wind_speed_kt: true },
    { wind_direction_deg: 361 },
    { observed_at_utc: '2026-10-04T00:00:00-04:00' },
    { observed_at_utc: '2026-02-30T00:00:00.000Z' },
    { observed_at_utc: '2026-10-04T00:01:00.000Z' },
    { fetched_at_utc: '2026-10-04T00:02:00.000Z' },
    { scenario_parameters: {} },
  ])('rejects malformed or unsupported weather: %j', (change) => {
    expect(() =>
      parseWeather(JSON.stringify({ ...weather, ...change }), now),
    ).toThrow();
  });
  it('rejects missing fields and oversized input', () => {
    const { ceiling_ft, ...missing } = weather;
    expect(ceiling_ft).toBeNull();
    expect(() => parseWeather(JSON.stringify(missing), now)).toThrow();
    expect(() => parseFailure(' '.repeat(1024 * 1024 + 1), now)).toThrow();
  });
  it.each([
    { latitude_deg: 91, longitude_deg: 0 },
    { latitude_deg: 1 },
    { speed_kt: -1 },
    { altitude_ft: 100001 },
    { track_deg: 361 },
    { on_ground: 'ground' },
    { position_age_s: -1 },
    { aircraft_id: '' },
  ])('rejects invalid aircraft: %j', (change) => {
    expect(() =>
      parseAircraftBatch(
        JSON.stringify({
          ...batch,
          observations: [{ ...aircraft, ...change }],
        }),
        now,
      ),
    ).toThrow();
  });
  it('accepts empty success but rejects duplicate IDs and excessive batches', () => {
    expect(
      parseAircraftBatch(JSON.stringify({ ...batch, observations: [] }), now)
        .observations,
    ).toEqual([]);
    expect(() =>
      parseAircraftBatch(
        JSON.stringify({ ...batch, observations: [aircraft, aircraft] }),
        now,
      ),
    ).toThrow();
    expect(() =>
      parseAircraftBatch(
        JSON.stringify({ ...batch, observations: Array(1001).fill(aircraft) }),
        now,
      ),
    ).toThrow();
  });
});

describe('state rules', () => {
  it('requires the exact role', () => {
    expect(() => requireRole('ingestion', 'ingestion')).not.toThrow();
    for (const role of [undefined, 'admin', 'weather', 'controller'])
      expect(() => requireRole(role, 'ingestion')).toThrow();
  });
  it('orders updates and rejects equal-time conflicts', () => {
    expect(shouldReplace(undefined, undefined, 10, 'a')).toBe(true);
    expect(shouldReplace(10, 'a', 9, 'b')).toBe(false);
    expect(shouldReplace(10, 'a', 10, 'a')).toBe(false);
    expect(shouldReplace(10, 'a', 11, 'b')).toBe(true);
    expect(() => shouldReplace(10, 'a', 10, 'b')).toThrow();
  });
  it('orders health outcomes and counts failures once', () => {
    const previous = { fetched: 10, status: 'failed', failureCount: 2 };
    expect(healthTransition(previous, 9, 'ok')).toBeUndefined();
    expect(healthTransition(previous, 10, 'failed')).toBeUndefined();
    expect(() => healthTransition(previous, 10, 'ok')).toThrow();
    expect(healthTransition(previous, 11, 'failed')).toBe(3);
    expect(healthTransition(previous, 11, 'ok')).toBe(0);
  });
});
