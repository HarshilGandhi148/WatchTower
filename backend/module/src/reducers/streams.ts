import { Identity } from 'spacetimedb';
import { t } from 'spacetimedb/server';
import { database } from '../schema';
import { requireRole } from '../auth/roles';
import { canonicalJson } from './evidence_rules';
import { text } from './validation';

export const seed_agent_identity = database.reducer(
  { name: t.string(), identityHex: t.string() },
  (ctx, { name, identityHex }) => {
    requireRole(
      ctx.db.servicePermissions.identity.find(ctx.sender)?.role,
      'admin',
    );
    if (
      !['ingestion', 'weather', 'congestion', 'validation', 'bridge'].includes(
        name,
      ) ||
      !/^[a-f0-9]{64}$/.test(identityHex)
    )
      throw new Error('Invalid service identity');
    const identity = Identity.fromString(identityHex);
    const grant = ctx.db.servicePermissions.identity.find(identity);
    if (grant && grant.role !== name)
      throw new Error('Identity already has another role');
    for (const row of ctx.db.agentIdentity.iter()) {
      if (row.identity.isEqual(identity) && row.name !== name)
        throw new Error('Identity already assigned');
    }
    const prior = ctx.db.agentIdentity.name.find(name);
    if (prior && !prior.identity.isEqual(identity)) {
      ctx.db.servicePermissions.identity.delete(prior.identity);
      ctx.db.agentIdentity.name.update({ name, identity });
    } else if (!prior) ctx.db.agentIdentity.insert({ name, identity });
    if (!grant) ctx.db.servicePermissions.insert({ identity, role: name });
  },
);

// Separate exports give every service a fixed reducer and table; no caller-selected writes.
function writer(role: 'ingestion' | 'weather' | 'congestion' | 'validation') {
  return database.reducer(
    { eventKey: t.string(), rootId: t.u64(), payloadJson: t.string() },
    (ctx, { eventKey, rootId, payloadJson }) => {
      const allowed = ctx.db.agentIdentity.name.find(role);
      if (!allowed?.identity.isEqual(ctx.sender))
        throw new Error('Unauthorized');
      text(eventKey, 256);
      if (payloadJson.length > 1024 * 1024)
        throw new Error('Payload too large');
      const value: unknown = JSON.parse(payloadJson);
      if (
        !value ||
        typeof value !== 'object' ||
        Array.isArray(value) ||
        (value as Record<string, unknown>).schema_version !== 1
      )
        throw new Error('Invalid stream schema');
      const canonical = canonicalJson(value);
      const target =
        role === 'ingestion'
          ? ctx.db.ingestEvent
          : role === 'weather'
            ? ctx.db.weatherReading
            : role === 'congestion'
              ? ctx.db.congestionReading
              : ctx.db.validationResult;
      const prior = target.eventKey.find(eventKey);
      if (prior) {
        if (prior.rootId !== rootId || prior.payloadJson !== canonical)
          throw new Error('Conflicting event key');
        return;
      }
      if (role === 'ingestion') {
        if (rootId !== 0n)
          throw new Error('Ingestion cannot reference a derived row');
      } else {
        if (eventKey !== `${role}:${rootId}`)
          throw new Error('Invalid specialist key');
        if (!ctx.db.ingestEvent.id.find(rootId))
          throw new Error('Missing ingestion reference');
        if (role === 'validation') {
          for (const table of [
            ctx.db.weatherReading,
            ctx.db.congestionReading,
          ]) {
            if (![...table.iter()].some((row) => row.rootId === rootId))
              throw new Error('Missing specialist reading');
          }
        }
      }
      target.insert({
        id: 0n,
        eventKey,
        rootId,
        createdAtUtc: ctx.timestamp,
        payloadJson: canonical,
      });
    },
  );
}
export const write_ingest_event = writer('ingestion');
export const write_weather_reading = writer('weather');
export const write_congestion_reading = writer('congestion');
export const write_validation_result = writer('validation');

// Called inside successful observation reducers: persistence and signal are atomic.
export function appendIngest(
  ctx: import('spacetimedb/server').ReducerCtx<
    import('spacetimedb/server').InferSchema<typeof database>
  >,
  eventKey: string,
  source: unknown,
) {
  const weather = ctx.db.weatherRecords.provider.find('awc');
  const latest = ctx.db.latestFeeds.provider.find('adsb_lol');
  const batch = latest
    ? ctx.db.feedBatches.batchId.find(latest.batchId)
    : undefined;
  const traffic = batch
    ? (JSON.parse(batch.payloadJson) as { snapshot: unknown }).snapshot
    : null;
  const content = canonicalJson({
    schema_version: 1,
    source,
    weather: weather
      ? {
          observed_at_utc: new Date(
            Number(weather.observedAtUtc.toMillis()),
          ).toISOString(),
          wind_speed_kt: weather.windSpeedKt ?? null,
          visibility_m: weather.visibilityM ?? null,
          ceiling_ft: weather.ceilingFt ?? null,
        }
      : null,
    traffic: traffic
      ? {
          ...(traffic as object),
          fetch_succeeded:
            ctx.db.sourceHealth.provider.find('adsb_lol')?.status === 'ok',
        }
      : null,
  });
  const previous = ctx.db.ingestEvent.eventKey.find(eventKey);
  // Original input controls idempotency; mutable latest rows must not change a retry.
  if (previous) {
    const prior = JSON.parse(previous.payloadJson) as { source: unknown };
    if (canonicalJson(prior.source) !== canonicalJson(source))
      throw new Error('Conflicting ingest signal');
    return;
  }
  ctx.db.ingestEvent.insert({
    id: 0n,
    eventKey,
    rootId: 0n,
    createdAtUtc: ctx.timestamp,
    payloadJson: content,
  });
}
