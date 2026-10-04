// Pure input/state rules: usable in unit tests without the database runtime.
export type JsonObject = Record<string, unknown>;
const envelopeKeys = [
  'schema_version',
  'data_mode',
  'provider',
  'source_ref',
  'fetched_at_utc',
];
export const weatherFields = [
  'wind_direction_deg',
  'wind_speed_kt',
  'wind_gust_kt',
  'visibility_m',
  'ceiling_ft',
] as const;
export const aircraftFields = [
  'latitude_deg',
  'longitude_deg',
  'altitude_ft',
  'speed_kt',
  'track_deg',
  'position_age_s',
] as const;

export function object(value: unknown, keys: readonly string[]): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Expected object');
  const result = value as JsonObject;
  if (
    Object.keys(result).length !== keys.length ||
    keys.some((key) => !(key in result)) ||
    Object.keys(result).some((key) => !keys.includes(key))
  )
    throw new Error('Unexpected or missing fields');
  return result;
}

export function text(value: unknown, limit = 128): string {
  if (
    typeof value !== 'string' ||
    !value.trim() ||
    value.length > limit ||
    Array.from(value).some((char) => char.charCodeAt(0) < 32)
  )
    throw new Error('Invalid text');
  return value;
}

export function time(value: unknown): number {
  if (
    typeof value !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
  )
    throw new Error('Expected UTC millisecond timestamp');
  const millis = Date.parse(value);
  if (
    !Number.isFinite(millis) ||
    millis < 0 ||
    new Date(millis).toISOString() !== value
  )
    throw new Error('Invalid timestamp');
  return millis;
}

export function numberOrNull(
  value: unknown,
  min: number,
  max: number,
): number | undefined {
  if (value === null) return undefined;
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    value < min ||
    value > max
  )
    throw new Error('Invalid measurement');
  return value;
}

export function envelope(
  json: string,
  keys: readonly string[],
  now: number,
): JsonObject {
  if (json.length > 1024 * 1024) throw new Error('Payload too large');
  const value = object(JSON.parse(json) as unknown, [...envelopeKeys, ...keys]);
  if (
    value.schema_version !== 1 ||
    value.data_mode !== 'live' ||
    !['awc', 'adsb_lol'].includes(text(value.provider))
  )
    throw new Error('Unsupported source/schema/mode');
  text(value.source_ref, 2048);
  if (time(value.fetched_at_utc) > now + 60_000)
    throw new Error('Future fetch');
  return value;
}

export function parseWeather(json: string, now: number) {
  const value = envelope(
    json,
    ['airport_icao', 'record_type', 'observed_at_utc', ...weatherFields],
    now,
  );
  if (
    value.provider !== 'awc' ||
    value.airport_icao !== 'KDTW' ||
    value.record_type !== 'METAR'
  )
    throw new Error('Unsupported weather record');
  const observed = time(value.observed_at_utc);
  const fetched = time(value.fetched_at_utc);
  if (observed > fetched) throw new Error('Observation after fetch');
  const fields = {
    windDirectionDeg: numberOrNull(value.wind_direction_deg, 0, 360),
    windSpeedKt: numberOrNull(value.wind_speed_kt, 0, 300),
    windGustKt: numberOrNull(value.wind_gust_kt, 0, 300),
    visibilityM: numberOrNull(value.visibility_m, 0, 200_000),
    ceilingFt: numberOrNull(value.ceiling_ft, 0, 100_000),
  };
  return {
    observed,
    fetched,
    sourceRef: text(value.source_ref, 2048),
    fields,
    contentJson: JSON.stringify(fields),
  };
}

export function parseAircraftBatch(json: string, now: number) {
  const value = envelope(json, ['observations'], now);
  if (
    value.provider !== 'adsb_lol' ||
    !Array.isArray(value.observations) ||
    value.observations.length > 1000
  )
    throw new Error('Invalid aircraft batch');
  const fetched = time(value.fetched_at_utc);
  const seen = new Set<string>();
  const observations = value.observations.map((entry: unknown) => {
    const row = object(entry, [
      'aircraft_id',
      'observed_at_utc',
      'on_ground',
      ...aircraftFields,
    ]);
    const aircraftId = text(row.aircraft_id);
    if (seen.has(aircraftId)) throw new Error('Duplicate aircraft');
    seen.add(aircraftId);
    const observed = time(row.observed_at_utc);
    if (observed > fetched) throw new Error('Observation after fetch');
    if (row.on_ground !== null && typeof row.on_ground !== 'boolean')
      throw new Error('Invalid on_ground');
    const fields = {
      latitudeDeg: numberOrNull(row.latitude_deg, -90, 90),
      longitudeDeg: numberOrNull(row.longitude_deg, -180, 180),
      altitudeFt: numberOrNull(row.altitude_ft, -2000, 100_000),
      speedKt: numberOrNull(row.speed_kt, 0, 2000),
      trackDeg: numberOrNull(row.track_deg, 0, 360),
      positionAgeS: numberOrNull(row.position_age_s, 0, 86400),
      onGround: row.on_ground === null ? undefined : (row.on_ground as boolean),
    };
    if (
      (fields.latitudeDeg === undefined) !==
      (fields.longitudeDeg === undefined)
    )
      throw new Error('Coordinates must be a pair');
    return {
      aircraftId,
      observed,
      fields,
      contentJson: JSON.stringify(fields),
    };
  });
  return { fetched, sourceRef: text(value.source_ref, 2048), observations };
}

export function parseFailure(json: string, now: number) {
  const value = envelope(json, [], now);
  return {
    provider: text(value.provider),
    fetched: time(value.fetched_at_utc),
  };
}

export function shouldReplace(
  previousTime: number | undefined,
  previousContent: string | undefined,
  nextTime: number,
  nextContent: string,
): boolean {
  if (previousTime === undefined) return true;
  if (nextTime === previousTime && previousContent !== nextContent)
    throw new Error('Conflicting equal-time observation');
  return nextTime > previousTime;
}

export function healthTransition(
  previous:
    { fetched: number; status: string; failureCount: number } | undefined,
  fetched: number,
  status: 'ok' | 'failed',
) {
  if (previous && fetched < previous.fetched) return undefined;
  if (previous && fetched === previous.fetched) {
    if (previous.status !== status)
      throw new Error('Conflicting fetch outcome');
    return undefined;
  }
  return status === 'ok' ? 0 : (previous?.failureCount ?? 0) + 1;
}
