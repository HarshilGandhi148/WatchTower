import {
  numberOrNull,
  object,
  text,
  time,
  type JsonObject,
} from './validation';

export function record(value: unknown): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Expected object');
  return value as JsonObject;
}
export function parse(json: string): JsonObject {
  if (json.length > 2 * 1024 * 1024) throw new Error('Payload too large');
  return record(JSON.parse(json));
}
export function strings(value: unknown, limit = 2000): string[] {
  if (!Array.isArray(value) || value.length > limit)
    throw new Error('Invalid references');
  return value.map((v: unknown) => text(v, 2048));
}
export function count(value: unknown, max = 100000): number {
  if (
    typeof value !== 'number' ||
    !Number.isSafeInteger(value) ||
    value < 0 ||
    value > max
  )
    throw new Error('Invalid count');
  return value;
}
export function config(value: unknown) {
  const c = object(value, [
    'airport_icao',
    'latitude_deg',
    'longitude_deg',
    'radius_nm',
    'version',
  ]);
  if (c.airport_icao !== 'KDTW') throw new Error('Unsupported airport');
  numberOrNull(c.latitude_deg, -90, 90);
  numberOrNull(c.longitude_deg, -180, 180);
  if (c.latitude_deg === null || c.longitude_deg === null)
    throw new Error('Missing center');
  if (count(c.radius_nm, 250) < 1) throw new Error('Invalid radius');
  text(c.version);
  return c;
}
function optionalTime(value: unknown, fetched: number) {
  if (value === null) return undefined;
  const at = time(value);
  if (at > fetched + 60000) throw new Error('Future source timestamp');
  return at;
}
export function feed(json: string, now: number) {
  const v = object(parse(json), [
    'schema_version',
    'data_mode',
    'batch_id',
    'provider',
    'source_ref',
    'configuration',
    'snapshot',
    'coverage_flags',
  ]);
  if (
    v.schema_version !== 1 ||
    v.data_mode !== 'live' ||
    !['adsb_lol', 'faa'].includes(text(v.provider))
  )
    throw new Error('Invalid feed');
  text(v.batch_id);
  text(v.source_ref, 2048);
  config(v.configuration);
  strings(v.coverage_flags);
  const s = record(v.snapshot);
  const fetched = time(s.fetched_at_utc);
  if (fetched > now + 60000) throw new Error('Future fetch');
  let source: number | undefined;
  if (v.provider === 'adsb_lol') {
    object(s, [
      'source_id',
      'provider_time_utc',
      'fetched_at_utc',
      'observations',
      'reported_total',
      'rejected_records',
      'duplicates_dropped',
    ]);
    if (
      s.source_id !== 'adsb.lol' ||
      !Array.isArray(s.observations) ||
      s.observations.length > 1000
    )
      throw new Error('Oversized or invalid ADS-B');
    source = optionalTime(s.provider_time_utc, fetched);
    count(s.rejected_records);
    count(s.duplicates_dropped);
    if (s.reported_total !== null) count(s.reported_total);
    const ids = new Set();
    for (const item of s.observations) {
      const r = object(item, [
        'aircraft_id',
        'callsign',
        'latitude_deg',
        'longitude_deg',
        'altitude_ft',
        'on_ground',
        'ground_speed_kt',
        'track_deg',
        'position_age_s',
        'observed_at_utc',
        'provider_time_utc',
        'fetched_at_utc',
        'missing_fields',
        'quality_flags',
      ]);
      const id = text(r.aircraft_id);
      if (!/^~?[a-f0-9]{6}$/.test(id) || ids.has(id))
        throw new Error('Invalid/duplicate aircraft');
      ids.add(id);
      if (r.callsign !== null) text(r.callsign);
      if (time(r.fetched_at_utc) !== fetched) throw new Error('Mixed fetch');
      optionalTime(r.observed_at_utc, fetched);
      optionalTime(r.provider_time_utc, fetched);
      numberOrNull(r.latitude_deg, -90, 90);
      numberOrNull(r.longitude_deg, -180, 180);
      if ((r.latitude_deg === null) !== (r.longitude_deg === null))
        throw new Error('Incomplete coordinates');
      numberOrNull(r.altitude_ft, -2000, 100000);
      numberOrNull(r.ground_speed_kt, 0, 2000);
      numberOrNull(r.track_deg, 0, 360);
      numberOrNull(r.position_age_s, 0, 86400);
      if (r.on_ground !== null && typeof r.on_ground !== 'boolean')
        throw new Error('Invalid ground state');
      strings(r.missing_fields);
      strings(r.quality_flags);
    }
  } else {
    object(s, [
      'source_id',
      'source_update_time_utc',
      'fetched_at_utc',
      'events',
      'unrecognized_sections',
      'malformed_entries',
    ]);
    if (
      s.source_id !== 'faa-nas-status' ||
      !Array.isArray(s.events) ||
      s.events.length > 1000
    )
      throw new Error('Oversized or invalid FAA');
    source = optionalTime(s.source_update_time_utc, fetched);
    count(s.malformed_entries);
    strings(s.unrecognized_sections);
    const ids = new Set();
    for (const item of s.events) {
      const r = object(item, [
        'event_key',
        'event_type',
        'airport_code',
        'reason',
        'direction',
        'start_utc',
        'end_utc',
        'min_delay_minutes',
        'avg_delay_minutes',
        'max_delay_minutes',
        'trend',
        'raw_times',
        'quality_flags',
      ]);
      const id = text(r.event_key);
      if (ids.has(id)) throw new Error('Duplicate FAA event');
      ids.add(id);
      if (
        ![
          'ground_delay_program',
          'ground_stop',
          'airport_closure',
          'arrival_departure_delay',
        ].includes(text(r.event_type)) ||
        !/^[A-Z0-9]{3,4}$/.test(text(r.airport_code))
      )
        throw new Error('Invalid event');
      if (typeof r.reason !== 'string' || r.reason.length > 500)
        throw new Error('Invalid reason');
      if (
        r.direction !== null &&
        !['arrival', 'departure'].includes(text(r.direction))
      )
        throw new Error('Invalid direction');
      for (const key of ['start_utc', 'end_utc'])
        if (r[key] !== null) time(r[key]);
      if (
        r.start_utc !== null &&
        r.end_utc !== null &&
        time(r.end_utc) < time(r.start_utc)
      )
        throw new Error('Invalid event window');
      for (const key of [
        'min_delay_minutes',
        'avg_delay_minutes',
        'max_delay_minutes',
      ])
        if (r[key] !== null) count(r[key]);
      strings(r.quality_flags);
      if (!Array.isArray(r.raw_times) || r.raw_times.length > 10)
        throw new Error('Invalid raw times');
      for (const pair of r.raw_times)
        if (
          !Array.isArray(pair) ||
          pair.length !== 2 ||
          strings(pair).length !== 2
        )
          throw new Error('Invalid raw time');
      if (r.trend !== null) text(r.trend, 500);
    }
  }
  return {
    value: v,
    snapshot: s,
    fetched,
    source,
    provider: text(v.provider),
    batchId: text(v.batch_id),
  };
}
export function message(value: unknown, role: string, now: number) {
  const e = object(value, [
    'schema_version',
    'message_id',
    'case_id',
    'sender_agent',
    'created_at_utc',
    'snapshot_id',
    'evidence_ids',
    'airport_icao',
    'valid_until_utc',
    'correlation_id',
    'reply_to',
  ]);
  if (
    e.schema_version !== 1 ||
    e.sender_agent !== role ||
    e.airport_icao !== 'KDTW'
  )
    throw new Error('Invalid message envelope');
  for (const k of ['message_id', 'case_id', 'snapshot_id']) text(e[k]);
  for (const k of ['correlation_id', 'reply_to']) if (e[k] !== null) text(e[k]);
  const refs = strings(e.evidence_ids);
  if (new Set(refs).size !== refs.length)
    throw new Error('Duplicate references');
  const created = time(e.created_at_utc),
    expires = time(e.valid_until_utc);
  if (created > now + 60000 || expires <= created || expires <= now)
    throw new Error('Expired or invalid envelope');
  return e;
}
export function window(v: JsonObject, snapshot: JsonObject) {
  if (
    v.window_start_utc !== snapshot.window_start_utc ||
    v.window_end_utc !== snapshot.window_end_utc
  )
    throw new Error('Wrong analysis window');
}
export function sameRefs(a: unknown, b: unknown) {
  const first = strings(a),
    second = strings(b);
  if (first.length !== second.length || first.some((v) => !second.includes(v)))
    throw new Error('Wrong evidence set');
}
export function round(value: unknown) {
  if (value !== 1 && value !== 2) throw new Error('Invalid assessment round');
  return value;
}
export function candidateShape(v: JsonObject) {
  object(v, [
    'envelope',
    'status',
    'issue_type',
    'resource',
    'window_start_utc',
    'window_end_utc',
    'quality',
    'affected_flight_ids',
    'traffic_metrics',
    'weather_assessment_id',
    'faa_event_ids',
    'proposed_action_type',
    'proposed_action_parameters',
    'projection_id',
    'tradeoffs',
    'assumptions',
    'required_missing_inputs',
  ]);
  if (
    ![
      'change_worth_reviewing',
      'information_only',
      'no_change',
      'insufficient_data',
    ].includes(text(v.status)) ||
    ![
      'WEATHER_FLOW_REVIEW',
      'DEPARTURE_RELEASE_REVIEW',
      'DOWNSTREAM_RESTRICTION',
      'TRAFFIC_PRESSURE_NOTICE',
    ].includes(text(v.issue_type)) ||
    !['adequate', 'limited', 'unavailable'].includes(text(v.quality))
  )
    throw new Error('Invalid candidate vocabulary');
  text(v.resource);
  for (const k of [
    'affected_flight_ids',
    'faa_event_ids',
    'tradeoffs',
    'assumptions',
    'required_missing_inputs',
  ])
    strings(v[k]);
  const env = record(v.envelope),
    refs = strings(env.evidence_ids);
  for (const id of strings(v.faa_event_ids))
    if (!refs.includes(id)) throw new Error('Missing FAA evidence');
  if (
    !Array.isArray(v.traffic_metrics) ||
    v.traffic_metrics.length > 100 ||
    !Array.isArray(v.proposed_action_parameters) ||
    v.proposed_action_parameters.length > 3
  )
    throw new Error('Invalid metrics/parameters');
  for (const metric of v.traffic_metrics) {
    const m = object(metric, [
      'name',
      'value',
      'unit',
      'basis',
      'evidence_ids',
    ]);
    text(m.name);
    numberOrNull(m.value, -10000000, 10000000);
    if (
      m.value === null ||
      !['aircraft', 'aircraft-minutes'].includes(text(m.unit)) ||
      ![
        'observed',
        'confirmed_record',
        'inferred',
        'estimated',
        'projected',
      ].includes(text(m.basis))
    )
      throw new Error('Invalid metric units/basis');
    const ids = strings(m.evidence_ids);
    if (!ids.length || ids.some((id) => !refs.includes(id)))
      throw new Error('Missing metric evidence');
  }
  const names = new Set();
  for (const param of v.proposed_action_parameters) {
    const p = object(param, ['name', 'value', 'unit']);
    if (names.has(p.name)) throw new Error('Duplicate parameter');
    names.add(p.name);
    if (p.name === 'release_cap') {
      count(p.value, 120);
      if (p.unit !== 'aircraft/hour') throw new Error('Invalid rate units');
    } else if (
      p.name === 'window_start_minute' ||
      p.name === 'window_end_minute'
    ) {
      count(p.value, 30);
      if (p.unit !== 'minutes after snapshot')
        throw new Error('Invalid window units');
    } else throw new Error('Unknown parameter');
  }
  const action = v.proposed_action_type;
  if (
    action !== null &&
    ![
      'review_flow_window',
      'review_release_rate',
      'coordinate_downstream_restriction',
      'information_notice',
    ].includes(text(action))
  )
    throw new Error('Invalid action');
  if (v.status === 'change_worth_reviewing') {
    if (
      action === null ||
      action === 'information_notice' ||
      v.quality === 'unavailable'
    )
      throw new Error('Unsupported action');
    if (
      v.issue_type === 'DOWNSTREAM_RESTRICTION' &&
      (!strings(v.faa_event_ids).length ||
        !strings(v.affected_flight_ids).length)
    )
      throw new Error('Missing downstream association');
  } else {
    if (
      v.proposed_action_parameters.length ||
      (v.status === 'information_only'
        ? action !== null && action !== 'information_notice'
        : action !== null)
    )
      throw new Error('Unsupported parameters/action');
    if (
      v.status === 'insufficient_data' &&
      !strings(v.required_missing_inputs).length
    )
      throw new Error('Missing limitations');
  }
}

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    const v = value as JsonObject;
    return `{${Object.keys(v)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(v[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

export function projectionInputs(
  v: JsonObject,
  s: JsonObject,
  demand: JsonObject,
) {
  const input = object(v.input, [
    'snapshot_id',
    'as_of_utc',
    'initial_upstream_waiting',
    'initial_runway_queue',
    'demand_per_minute',
    'demand_basis',
  ]);
  const a = object(v.assumptions, [
    'capacity',
    'horizon_minutes',
    'transit_delay_weights',
    'bounds',
    'model_version',
  ]);
  const capacity = object(a.capacity, [
    'name',
    'effective_capacity_per_hour',
    'basis',
  ]);
  const response = object(v.response, [
    'release_cap_per_hour',
    'window_start_minute',
    'window_end_minute',
  ]);
  const bounds = object(a.bounds, [
    'min_release_cap_per_hour',
    'max_window_minutes',
  ]);
  if (
    v.model_version !== 'aggregate-flow-1' ||
    a.model_version !== v.model_version ||
    ![15, 30].includes(count(a.horizon_minutes))
  )
    throw new Error('Unsupported projection model');
  text(capacity.name);
  count(capacity.effective_capacity_per_hour, 120);
  if (
    !['hypothetical', 'operator_configured', 'source_reported'].includes(
      text(capacity.basis),
    )
  )
    throw new Error('Invalid capacity basis');
  text(input.demand_basis, 2048);
  count(bounds.min_release_cap_per_hour, 120);
  count(bounds.max_window_minutes, 30);
  if (response.release_cap_per_hour !== null) {
    const cap = count(response.release_cap_per_hour, 120),
      start = count(response.window_start_minute, 30),
      end = count(response.window_end_minute, 30);
    if (
      cap < count(bounds.min_release_cap_per_hour) ||
      start >= end ||
      end > count(a.horizon_minutes) ||
      end - start > count(bounds.max_window_minutes)
    )
      throw new Error('Invalid response bounds');
  }
  if (
    !Array.isArray(a.transit_delay_weights) ||
    a.transit_delay_weights.length < 1 ||
    a.transit_delay_weights.length > 31 ||
    a.transit_delay_weights.reduce(
      (sum: number, x: unknown) => sum + count(x),
      0,
    ) < 1
  )
    throw new Error('Invalid transit assumptions');
  if (
    !Array.isArray(input.demand_per_minute) ||
    input.demand_per_minute.length !== a.horizon_minutes
  )
    throw new Error('Invalid demand horizon');
  const start = time(s.window_start_utc),
    buckets = Array(count(a.horizon_minutes) / 5).fill(0) as number[];
  if (!Array.isArray(demand.records))
    throw new Error('Missing departure records');
  for (const item of demand.records) {
    const r = record(item);
    if (
      r.direction !== 'departure' ||
      r.association !== 'confirmed_record' ||
      r.cancelled === true ||
      r.time_utc === null ||
      r.time_kind === 'scheduled_gate'
    )
      continue;
    const bucket = Math.floor((time(r.time_utc) - start) / 300000);
    if (bucket >= 0 && bucket < buckets.length) buckets[bucket]++;
  }
  // Same deterministic five-minute eligibility estimate used by congestion.
  const expected = buckets.flatMap((n) =>
    Array.from(
      { length: 5 },
      (_, i) => Math.floor(n / 5) + (i < n % 5 ? 1 : 0),
    ),
  );
  if (input.demand_per_minute.some((n, i) => count(n) !== expected[i]))
    throw new Error('Projection demand not supported by stored records');
  const result = record(v.result);
  if (
    canonicalJson(result.capacity) !== canonicalJson(capacity) ||
    result.model_version !== v.model_version
  )
    throw new Error('Changed projection assumptions');
  if (
    canonicalJson(record(result.proposal).response) !==
      canonicalJson(response) ||
    canonicalJson(record(result.baseline).response) !==
      canonicalJson({
        release_cap_per_hour: null,
        window_start_minute: 0,
        window_end_minute: 0,
      })
  )
    throw new Error('Invalid projection branches');
  for (const name of ['baseline', 'proposal']) {
    const series = record(record(result[name]).series);
    if (!Array.isArray(series.served))
      throw new Error('Missing service series');
    const rate = count(capacity.effective_capacity_per_hour, 120);
    series.served.forEach((n, i) => {
      if (
        count(n) >
        Math.floor(((i + 1) * rate) / 60) - Math.floor((i * rate) / 60)
      )
        throw new Error('Service exceeds capacity');
    });
  }
}

export function deliveryExpired(
  status: string,
  expires: number,
  now: number,
): boolean {
  return status === 'pending' && expires <= now;
}
