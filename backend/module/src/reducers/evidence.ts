import { appendIngest } from './streams';
import { Timestamp } from 'spacetimedb';
import { t, type InferSchema, type ReducerCtx } from 'spacetimedb/server';
import { database } from '../schema';
import { requireRole } from '../auth/roles';
import { health } from './ingestion';
import {
  object,
  text,
  time,
  shouldReplace,
  type JsonObject,
} from './validation';
import {
  candidateShape,
  canonicalJson,
  deliveryExpired,
  projectionInputs,
  config,
  count,
  feed,
  message,
  parse,
  record,
  round,
  sameRefs,
  strings,
  window,
} from './evidence_rules';

type Context = ReducerCtx<InferSchema<typeof database>>;
const stamp = (millis: number) => new Timestamp(BigInt(millis) * 1000n);
const now = (ctx: Context) => Number(ctx.timestamp.toMillis());
const json = canonicalJson;
function auth(ctx: Context, role: string) {
  requireRole(ctx.db.servicePermissions.identity.find(ctx.sender)?.role, role);
}
function snapshot(ctx: Context, id: unknown, active = true) {
  const row = ctx.db.evidenceSnapshots.snapshotId.find(text(id));
  if (
    !row ||
    (active && (row.closed || Number(row.expiresAtUtc.toMillis()) <= now(ctx)))
  )
    throw new Error('Missing, closed or expired snapshot');
  return { row, value: parse(row.contentJson) };
}
function binding(ctx: Context, e: JsonObject) {
  const s = snapshot(ctx, e.snapshot_id);
  if (
    e.case_id !== s.row.caseId ||
    e.airport_icao !== s.row.airportIcao ||
    time(e.valid_until_utc) > Number(s.row.expiresAtUtc.toMillis())
  )
    throw new Error('Wrong case/airport/expiry');
  return s;
}
function refs(ctx: Context, ids: unknown, allowed: unknown) {
  const known = strings(allowed);
  for (const id of strings(ids))
    if (!known.includes(id) || !ctx.db.evidenceRecords.evidenceId.find(id))
      throw new Error('Unresolved evidence reference');
}
function evidence(
  ctx: Context,
  id: string,
  provider: string,
  contentJson: string,
) {
  const previous = ctx.db.evidenceRecords.evidenceId.find(id);
  if (previous && previous.contentJson !== contentJson)
    throw new Error('Conflicting evidence ID');
  if (!previous)
    ctx.db.evidenceRecords.insert({ evidenceId: id, provider, contentJson });
}

export const ingest_feed_batch = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'ingestion');
    const input = feed(payloadJson, now(ctx)),
      content = json(input.value);
    const previous = ctx.db.feedBatches.batchId.find(input.batchId);
    if (previous) {
      if (previous.payloadJson !== content)
        throw new Error('Conflicting batch ID');
      return;
    }
    ctx.db.feedBatches.insert({
      batchId: input.batchId,
      provider: input.provider,
      fetchedAtUtc: stamp(input.fetched),
      sourceAtUtc: input.source === undefined ? undefined : stamp(input.source),
      payloadJson: content,
    });
    const latest = ctx.db.latestFeeds.provider.find(input.provider);
    const prior = latest
      ? ctx.db.feedBatches.batchId.find(latest.batchId)
      : undefined;
    // Provider publication time and fetch time remain separate. Older fetches cannot
    // replace a newer batch; individual latest positions also reject older observations.
    const newer = shouldReplace(
      prior ? Number(prior.fetchedAtUtc.toMillis()) : undefined,
      prior?.payloadJson,
      input.fetched,
      content,
    );
    if (newer) {
      const row = { provider: input.provider, batchId: input.batchId };
      if (latest) ctx.db.latestFeeds.provider.update(row);
      else ctx.db.latestFeeds.insert(row);
    }
    if (input.provider === 'adsb_lol') {
      for (const item of input.snapshot.observations as JsonObject[]) {
        if (item.observed_at_utc === null) continue; // missing source age is unknown
        const aircraftId = text(item.aircraft_id),
          observed = time(item.observed_at_utc);
        const previous =
          ctx.db.aircraftObservations.aircraftId.find(aircraftId);
        const fields = {
          latitudeDeg: item.latitude_deg as number | undefined,
          longitudeDeg: item.longitude_deg as number | undefined,
          altitudeFt: item.altitude_ft as number | undefined,
          speedKt: item.ground_speed_kt as number | undefined,
          trackDeg: item.track_deg as number | undefined,
          positionAgeS: item.position_age_s as number | undefined,
          onGround: item.on_ground as boolean | undefined,
        };
        // SATS options use undefined, rather than JSON null.
        for (const key of Object.keys(fields) as (keyof typeof fields)[])
          if (fields[key] === null) delete fields[key];
        const contentJson = json([input.value.source_ref, fields]);
        // Repeated polls can have the same position time but a newer seen_pos,
        // fetch time or other telemetry. Order those refreshes by fetch time.
        const samePositionTime =
          previous && observed === Number(previous.observedAtUtc.toMillis());
        if (
          !shouldReplace(
            previous
              ? Number(
                  (samePositionTime
                    ? previous.fetchedAtUtc
                    : previous.observedAtUtc
                  ).toMillis(),
                )
              : undefined,
            previous?.contentJson,
            samePositionTime ? input.fetched : observed,
            contentJson,
          )
        )
          continue;
        const row = {
          aircraftId,
          provider: 'adsb_lol',
          observedAtUtc: stamp(observed),
          fetchedAtUtc: stamp(input.fetched),
          sourceRef: text(input.value.source_ref, 2048),
          ...fields,
          contentJson,
        };
        if (previous) ctx.db.aircraftObservations.aircraftId.update(row);
        else ctx.db.aircraftObservations.insert(row);
      }
    } else if (newer) {
      for (const row of ctx.db.airportEvents.iter())
        ctx.db.airportEvents.eventId.delete(row.eventId);
      for (const item of input.snapshot.events as JsonObject[]) {
        const airport = text(item.airport_code);
        ctx.db.airportEvents.insert({
          eventId: `${input.batchId}:${text(item.event_key)}`,
          batchId: input.batchId,
          airportIcao: airport === 'DTW' ? 'KDTW' : airport,
          contentJson: json(item),
        });
      }
    }
    health(ctx, input.provider, input.fetched, 'ok', input.source);
    appendIngest(ctx, `feed:${input.batchId}`, input.value);
  },
);

export const ingest_flight_records = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'ingestion');
    const v = object(parse(payloadJson), [
      'batch_id',
      'schema_version',
      'data_mode',
      'provider',
      'source_ref',
      'configuration',
      'fetched_at_utc',
      'records',
    ]);
    if (
      v.schema_version !== 1 ||
      v.data_mode !== 'live' ||
      v.provider !== 'flightaware'
    )
      throw new Error('Unsupported flight records');
    config(v.configuration);
    text(v.source_ref, 2048);
    const fetched = time(v.fetched_at_utc);
    if (
      fetched > now(ctx) + 60000 ||
      !Array.isArray(v.records) ||
      v.records.length > 1000
    )
      throw new Error('Invalid flight batch');
    const ids = new Set();
    for (const item of v.records) {
      const r = object(item, [
        'flight_id',
        'direction',
        'time_utc',
        'time_kind',
        'association',
        'cancelled',
      ]);
      const id = text(r.flight_id);
      if (ids.has(id)) throw new Error('Duplicate flight');
      ids.add(id);
      if (
        !['arrival', 'departure'].includes(text(r.direction)) ||
        !['confirmed_record', 'inferred'].includes(text(r.association)) ||
        typeof r.cancelled !== 'boolean'
      )
        throw new Error('Invalid flight association');
      if ((r.time_utc === null) !== (r.time_kind === null))
        throw new Error('Incomplete flight time');
      if (r.time_utc !== null) {
        time(r.time_utc);
        if (
          !['scheduled_gate', 'estimated_runway', 'actual_runway'].includes(
            text(r.time_kind),
          )
        )
          throw new Error('Invalid time kind');
      }
    }
    const batchId = text(v.batch_id),
      contentJson = json(v),
      previous = ctx.db.flightRecords.batchId.find(batchId);
    if (previous) {
      if (previous.contentJson !== contentJson)
        throw new Error('Conflicting flight batch');
      return;
    }
    ctx.db.flightRecords.insert({
      batchId,
      fetchedAtUtc: stamp(fetched),
      contentJson,
    });
    const latest = ctx.db.latestFeeds.provider.find('flightaware');
    const prior = latest
      ? ctx.db.flightRecords.batchId.find(latest.batchId)
      : undefined;
    if (
      shouldReplace(
        prior ? Number(prior.fetchedAtUtc.toMillis()) : undefined,
        prior?.contentJson,
        fetched,
        contentJson,
      )
    ) {
      const row = { provider: 'flightaware', batchId };
      if (latest) ctx.db.latestFeeds.provider.update(row);
      else ctx.db.latestFeeds.insert(row);
    }
    health(ctx, 'flightaware', fetched, 'ok', fetched);
  },
);

export const create_evidence_snapshot = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'congestion');
    const input = parse(payloadJson);
    const v = object(input, [
      'snapshot_id',
      'case_id',
      'configuration',
      'window_start_utc',
      'window_end_utc',
      'expires_at_utc',
      ...(Object.hasOwn(input, 'align_window_to_cutoff')
        ? ['align_window_to_cutoff']
        : []),
    ]);
    const id = text(v.snapshot_id),
      caseId = text(v.case_id),
      configuration = config(v.configuration);
    const start = time(v.window_start_utc),
      end = time(v.window_end_utc),
      expires = time(v.expires_at_utc);
    if (
      start > now(ctx) + 60000 ||
      end <= start ||
      end - start > 1800000 ||
      expires <= now(ctx) ||
      expires <= start ||
      expires - start > 600000
    )
      throw new Error('Invalid snapshot window/expiry');
    const requestJson = json(v),
      previous = ctx.db.evidenceSnapshots.snapshotId.find(id);
    if (previous) {
      if (previous.requestJson !== requestJson)
        throw new Error('Conflicting snapshot ID');
      return;
    }
    if (ctx.db.evidenceSnapshots.caseId.find(caseId))
      throw new Error('Case already has a snapshot');
    if (
      v.align_window_to_cutoff !== undefined &&
      typeof v.align_window_to_cutoff !== 'boolean'
    )
      throw new Error('Invalid window alignment');
    const cutoff = now(ctx);
    const actualStart = v.align_window_to_cutoff ? cutoff : start;
    const actualEnd = actualStart + end - start;
    const actualExpiry = actualStart + expires - start;
    if (
      v.align_window_to_cutoff &&
      Array.from(ctx.db.weatherRecords.iter()).some(
        (row) => Number(row.fetchedAtUtc.toMillis()) > cutoff,
      )
    )
      throw new Error(
        'Weather fetch is ahead of the snapshot clock; retry shortly',
      );
    const batches: JsonObject[] = [],
      evidenceIds: string[] = [],
      limitations: string[] = [];
    for (const provider of ['adsb_lol', 'faa']) {
      const latest = ctx.db.latestFeeds.provider.find(provider);
      const batch = latest
        ? ctx.db.feedBatches.batchId.find(latest.batchId)
        : undefined;
      if (!batch) {
        limitations.push(`${provider}: missing batch`);
        continue;
      }
      const payload = parse(batch.payloadJson);
      if (json(payload.configuration) !== json(configuration)) {
        limitations.push(`${provider}: configuration mismatch`);
        continue;
      }
      batches.push({ batch_id: batch.batchId, payload });
      evidence(ctx, batch.batchId, provider, batch.payloadJson);
      evidenceIds.push(batch.batchId);
      const source = record(payload.snapshot);
      if (provider === 'faa')
        for (const event of source.events as JsonObject[]) {
          if (!['DTW', 'KDTW'].includes(text(event.airport_code))) continue;
          const eventId = `${batch.batchId}:${text(event.event_key)}`;
          evidence(ctx, eventId, provider, json(event));
          evidenceIds.push(eventId);
        }
      const fetched = Number(batch.fetchedAtUtc.toMillis());
      if (v.align_window_to_cutoff && fetched > cutoff)
        throw new Error(
          'Feed fetch is ahead of the snapshot clock; retry shortly',
        );
      if (now(ctx) - fetched > (provider === 'adsb_lol' ? 60000 : 300000))
        limitations.push(`${provider}: stale batch`);
      if (
        strings(payload.coverage_flags).some((flag) =>
          [
            'rejected_records',
            'unknown_sections',
            'unparsed_source_time',
            'reported_count_mismatch',
          ].includes(flag),
        )
      )
        limitations.push(`${provider}: incomplete response`);
    }
    const flight = ctx.db.latestFeeds.provider.find('flightaware');
    const flightBatch = flight
      ? ctx.db.flightRecords.batchId.find(flight.batchId)
      : undefined;
    if (flightBatch) {
      const payload = parse(flightBatch.contentJson);
      if (json(payload.configuration) === json(configuration)) {
        batches.push({ batch_id: flightBatch.batchId, payload });
        evidence(
          ctx,
          flightBatch.batchId,
          'flightaware',
          flightBatch.contentJson,
        );
        evidenceIds.push(flightBatch.batchId);
        if (now(ctx) - Number(flightBatch.fetchedAtUtc.toMillis()) > 300000)
          limitations.push('flightaware: stale batch');
      } else limitations.push('flightaware: configuration mismatch');
    } else limitations.push('airport-associated demand unavailable');
    const weather = [];
    for (const row of ctx.db.weatherRecords.iter()) {
      const evidenceId = `${id}:weather:${row.provider}`;
      const content = {
        provider: row.provider,
        airport_icao: row.airportIcao,
        observed_at_utc: row.observedAtUtc.toDate().toISOString(),
        fetched_at_utc: row.fetchedAtUtc.toDate().toISOString(),
        source_ref: row.sourceRef,
        content_json: row.contentJson,
      };
      weather.push(content);
      evidence(ctx, evidenceId, row.provider, json(content));
      evidenceIds.push(evidenceId);
      const retained = JSON.parse(row.contentJson) as unknown[];
      if (retained.length === 3) {
        const bundle = record(retained[2]);
        const tafs = bundle.taf_evidence as unknown[];
        for (const [index, taf] of tafs.entries()) {
          const tafId = `${evidenceId}:taf:${index}`;
          evidence(ctx, tafId, row.provider, json(taf));
          evidenceIds.push(tafId);
        }
      }
    }
    const healthRows = Array.from(ctx.db.sourceHealth.iter()).map((row) => ({
      provider: row.provider,
      status: row.status,
      fetched_at_utc: row.fetchedAtUtc.toDate().toISOString(),
      source_at_utc: row.sourceAtUtc?.toDate().toISOString() ?? null,
      last_success_at_utc: row.lastSuccessAtUtc?.toDate().toISOString() ?? null,
      failure_count: row.failureCount,
    }));
    for (const provider of ['adsb_lol', 'faa'])
      if (healthRows.find((row) => row.provider === provider)?.status !== 'ok')
        limitations.push(`${provider}: unavailable health`);
    const contentJson = json({
      ...v,
      window_start_utc: new Date(actualStart).toISOString(),
      window_end_utc: new Date(actualEnd).toISOString(),
      expires_at_utc: new Date(actualExpiry).toISOString(),
      cutoff_at_utc: ctx.timestamp.toDate().toISOString(),
      evidence_ids: evidenceIds,
      batches,
      weather,
      health: healthRows,
      complete: !limitations.length,
      limitations,
    });
    if (contentJson.length > 2 * 1024 * 1024)
      throw new Error('Snapshot oversized: incomplete input');
    ctx.db.evidenceSnapshots.insert({
      snapshotId: id,
      caseId,
      airportIcao: 'KDTW',
      cutoffAtUtc: ctx.timestamp,
      expiresAtUtc: stamp(actualExpiry),
      requestJson,
      contentJson,
      closed: false,
    });
  },
);

export const register_assessment_request = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'validation');
    const v = object(parse(payloadJson), ['envelope', 'assessment_round']);
    const e = message(v.envelope, 'validation', now(ctx)),
      s = binding(ctx, e),
      r = round(v.assessment_round);
    sameRefs(e.evidence_ids, s.value.evidence_ids);
    const caseRound = `${s.row.caseId}:${r}`,
      contentJson = json(v),
      previous = ctx.db.assessmentRequests.caseRound.find(caseRound);
    if (previous) {
      if (previous.contentJson !== contentJson)
        throw new Error('Conflicting assessment round');
      return;
    }
    if (r === 2) {
      const first = ctx.db.flowCandidates.caseRound.find(`${s.row.caseId}:1`);
      if (!first || e.reply_to !== first.candidateId)
        throw new Error('Correction before first candidate');
    }
    ctx.db.assessmentRequests.insert({
      caseRound,
      snapshotId: s.row.snapshotId,
      contentJson,
    });
  },
);

export const submit_weather_assessment = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'weather');
    const v = object(parse(payloadJson), [
      'finding_id',
      'envelope',
      'assessment_round',
      'window_start_utc',
      'window_end_utc',
      'status',
      'quality',
      'summary',
      'missing_inputs',
      'assumptions',
    ]);
    const e = message(v.envelope, 'weather', now(ctx)),
      s = binding(ctx, e),
      r = round(v.assessment_round);
    window(v, s.value);
    refs(ctx, e.evidence_ids, s.value.evidence_ids);
    if (
      !['impact', 'no_material_impact', 'insufficient_data'].includes(
        text(v.status),
      ) ||
      !['adequate', 'limited', 'unavailable'].includes(text(v.quality))
    )
      throw new Error('Invalid weather status');
    text(v.summary, 2000);
    strings(v.missing_inputs);
    strings(v.assumptions);
    const findingId = text(v.finding_id),
      caseRound = `${s.row.caseId}:${r}`,
      contentJson = json(v);
    const previous = ctx.db.agentFindings.findingId.find(findingId);
    if (previous) {
      if (previous.contentJson !== contentJson)
        throw new Error('Conflicting finding ID');
      return;
    }
    if (!ctx.db.assessmentRequests.caseRound.find(caseRound))
      throw new Error('Unrequested weather round');
    if (ctx.db.agentFindings.caseRound.find(caseRound))
      throw new Error('Weather round already stored');
    ctx.db.agentFindings.insert({
      findingId,
      snapshotId: s.row.snapshotId,
      caseRound,
      expiresAtUtc: stamp(time(e.valid_until_utc)),
      contentJson,
    });
  },
);

export const submit_flow_candidate = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'congestion');
    const v = object(parse(payloadJson), ['candidate', 'ready']);
    const c = record(v.candidate),
      ready = object(v.ready, [
        'envelope',
        'candidate_id',
        'weather_assessment_id',
        'assessment_round',
        'projection_id',
      ]);
    candidateShape(c);
    const contentJson = json(c),
      readyJson = json(ready),
      candidateId = text(ready.candidate_id);
    // Lost-response retries remain idempotent even after expiry or receipt.
    const previous = ctx.db.flowCandidates.candidateId.find(candidateId);
    if (previous) {
      if (
        previous.contentJson !== contentJson ||
        ctx.db.pendingDeliveries.candidateId.find(candidateId)?.contentJson !==
          readyJson
      )
        throw new Error('Conflicting candidate ID');
      return;
    }
    const e = message(c.envelope, 'congestion', now(ctx)),
      re = message(ready.envelope, 'congestion', now(ctx)),
      s = binding(ctx, e),
      r = round(ready.assessment_round);
    if (
      e.valid_until_utc !== re.valid_until_utc ||
      re.message_id !== candidateId ||
      e.message_id !== candidateId ||
      re.snapshot_id !== e.snapshot_id ||
      re.case_id !== e.case_id
    )
      throw new Error('Notification/candidate mismatch');
    binding(ctx, re);
    sameRefs(re.evidence_ids, s.value.evidence_ids);
    window(c, s.value);
    const caseRound = `${s.row.caseId}:${r}`,
      request = ctx.db.assessmentRequests.caseRound.find(caseRound);
    if (!request) throw new Error('Unrequested round');
    const requestEnvelope = record(parse(request.contentJson).envelope);
    if (
      re.reply_to !== requestEnvelope.message_id ||
      re.correlation_id !== requestEnvelope.correlation_id ||
      time(re.valid_until_utc) > time(requestEnvelope.valid_until_utc)
    )
      throw new Error('Wrong request/expiry');
    if (ctx.db.flowCandidates.caseRound.find(caseRound))
      throw new Error('Round already submitted');
    const finding = ctx.db.agentFindings.findingId.find(
      text(ready.weather_assessment_id),
    );
    if (
      !finding ||
      finding.snapshotId !== s.row.snapshotId ||
      finding.caseRound !== caseRound ||
      Number(finding.expiresAtUtc.toMillis()) <= now(ctx) ||
      c.weather_assessment_id !== finding.findingId
    )
      throw new Error('Wrong weather assessment');
    if (time(re.valid_until_utc) > Number(finding.expiresAtUtc.toMillis()))
      throw new Error('Candidate outlives weather');
    const allowed = [...strings(s.value.evidence_ids)];
    if (c.projection_id !== null) {
      const p = ctx.db.projectionResults.projectionId.find(
        text(c.projection_id),
      );
      if (
        !p ||
        p.snapshotId !== s.row.snapshotId ||
        ready.projection_id !== c.projection_id
      )
        throw new Error('Wrong projection');
      allowed.push(p.projectionId);
    } else if (ready.projection_id !== null)
      throw new Error('Unexpected projection');
    refs(ctx, e.evidence_ids, allowed);
    for (const id of strings(c.faa_event_ids))
      if (ctx.db.evidenceRecords.evidenceId.find(id)?.provider !== 'faa')
        throw new Error('Wrong FAA evidence');
    if (r === 2 && !ctx.db.flowCandidates.caseRound.find(`${s.row.caseId}:1`))
      throw new Error('Missing first round');
    const expiresAtUtc = stamp(time(re.valid_until_utc));
    ctx.db.flowCandidates.insert({
      candidateId,
      snapshotId: s.row.snapshotId,
      caseRound,
      expiresAtUtc,
      contentJson,
    });
    ctx.db.pendingDeliveries.insert({
      candidateId,
      expiresAtUtc,
      contentJson: readyJson,
      status: 'pending',
      updatedAtUtc: ctx.timestamp,
    });
  },
);

export const store_projection = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'congestion');
    const v = object(parse(payloadJson), [
      'projection_id',
      'snapshot_id',
      'model_version',
      'evidence_ids',
      'input',
      'assumptions',
      'response',
      'result',
    ]);
    const s = snapshot(ctx, v.snapshot_id);
    text(v.model_version);
    refs(ctx, v.evidence_ids, s.value.evidence_ids);
    const input = record(v.input),
      result = record(v.result);
    if (
      input.snapshot_id !== s.row.snapshotId ||
      result.snapshot_id !== s.row.snapshotId ||
      result.model_version !== v.model_version ||
      result.status !== 'computed' ||
      input.demand_per_minute === null ||
      input.initial_upstream_waiting === null ||
      input.initial_runway_queue === null ||
      !strings(v.evidence_ids).length
    )
      throw new Error('Unsupported projection inputs');
    const demandEvidence = strings(v.evidence_ids)
      .map((id) => ctx.db.evidenceRecords.evidenceId.find(id))
      .find((row) => row?.provider === 'flightaware');
    if (!demandEvidence) throw new Error('Missing stored departure demand');
    const demandBatch = parse(demandEvidence.contentJson);
    projectionInputs(v, s.value, demandBatch);
    if (
      time(s.value.window_start_utc) - time(demandBatch.fetched_at_utc) >
      300000
    )
      throw new Error('Stale departure demand');
    if (
      !Array.isArray(input.demand_per_minute) ||
      input.demand_per_minute.length < 1 ||
      input.demand_per_minute.length > 30
    )
      throw new Error('Invalid demand units/horizon');
    input.demand_per_minute.forEach((x) => count(x));
    count(input.initial_upstream_waiting);
    count(input.initial_runway_queue);
    if (
      time(input.as_of_utc) !== time(s.value.window_start_utc) ||
      input.demand_per_minute.length * 60000 !==
        time(s.value.window_end_utc) - time(s.value.window_start_utc)
    )
      throw new Error('Wrong projection window');
    for (const name of ['baseline', 'proposal']) {
      const branch = record(result[name]),
        series = record(branch.series);
      const keys = [
        'demand',
        'released',
        'served',
        'upstream_waiting',
        'runway_queue',
        'in_transit',
      ];
      for (const key of keys) {
        if (
          !Array.isArray(series[key]) ||
          series[key].length !== input.demand_per_minute.length
        )
          throw new Error('Invalid projection series');
        (series[key] as unknown[]).forEach((x) => count(x));
      }
      let arrivals =
          count(input.initial_upstream_waiting) +
          count(input.initial_runway_queue),
        served = 0;
      for (let i = 0; i < input.demand_per_minute.length; i++) {
        if ((series.demand as number[])[i] !== input.demand_per_minute[i])
          throw new Error('Changed projection demand');
        arrivals += count(input.demand_per_minute[i]);
        served += (series.served as number[])[i];
        if (
          arrivals !==
          served +
            (series.upstream_waiting as number[])[i] +
            (series.runway_queue as number[])[i] +
            (series.in_transit as number[])[i]
        )
          throw new Error('Projection violates conservation');
      }
    }
    const projectionId = text(v.projection_id),
      contentJson = json(v),
      previous = ctx.db.projectionResults.projectionId.find(projectionId);
    if (previous) {
      if (previous.contentJson !== contentJson)
        throw new Error('Conflicting projection ID');
      return;
    }
    ctx.db.projectionResults.insert({
      projectionId,
      snapshotId: s.row.snapshotId,
      contentJson,
    });
    evidence(ctx, projectionId, 'projection', contentJson);
  },
);

export const record_candidate_receipt = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    auth(ctx, 'validation');
    const v = object(parse(payloadJson), ['candidate_id', 'ready']);
    const row = ctx.db.pendingDeliveries.candidateId.find(text(v.candidate_id));
    if (!row || row.contentJson !== json(v.ready))
      throw new Error('Missing or conflicting delivery');
    if (row.status === 'received') return;
    if (
      row.status !== 'pending' ||
      Number(row.expiresAtUtc.toMillis()) <= now(ctx)
    )
      throw new Error('Expired delivery');
    ctx.db.pendingDeliveries.candidateId.update({
      ...row,
      status: 'received',
      updatedAtUtc: ctx.timestamp,
    });
  },
);
export const expire_deliveries = database.reducer((ctx) => {
  auth(ctx, 'congestion');
  let count = 0;
  for (const row of ctx.db.pendingDeliveries.status.filter('pending')) {
    if (
      deliveryExpired(row.status, Number(row.expiresAtUtc.toMillis()), now(ctx))
    )
      ctx.db.pendingDeliveries.candidateId.update({
        ...row,
        status: 'expired',
        updatedAtUtc: ctx.timestamp,
      });
    if (++count >= 1000) break;
  }
});
export const close_unfinished_cases = database.reducer((ctx) => {
  const role = ctx.db.servicePermissions.identity.find(ctx.sender)?.role;
  if (role !== 'validation' && role !== 'congestion')
    throw new Error('Unauthorized');
  for (const row of ctx.db.evidenceSnapshots.closed.filter(false))
    if (!row.closed)
      ctx.db.evidenceSnapshots.snapshotId.update({ ...row, closed: true });
});
export const prune_live_observations = database.reducer((ctx) => {
  auth(ctx, 'ingestion');
  let count = 0;
  for (const row of ctx.db.aircraftObservations.iter()) {
    if (now(ctx) - Number(row.fetchedAtUtc.toMillis()) > 1800000)
      ctx.db.aircraftObservations.aircraftId.delete(row.aircraftId);
    if (++count >= 1000) break;
  }
  // Snapshots own immutable copies; no evidence/findings/candidates are pruned.
  count = 0;
  for (const row of ctx.db.feedBatches.iter()) {
    if (
      now(ctx) - Number(row.fetchedAtUtc.toMillis()) > 1800000 &&
      ctx.db.latestFeeds.provider.find(row.provider)?.batchId !== row.batchId
    )
      ctx.db.feedBatches.batchId.delete(row.batchId);
    if (++count >= 1000) break;
  }
});
