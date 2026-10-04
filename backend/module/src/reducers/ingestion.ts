import { appendIngest } from './streams';
import { Timestamp } from 'spacetimedb';
import { t, type InferSchema, type ReducerCtx } from 'spacetimedb/server';

import { requireRole } from '../auth/roles';
import { database } from '../schema';
import {
  healthTransition,
  parseAircraftBatch,
  parseFailure,
  parseWeather,
  shouldReplace,
} from './validation';

type Context = ReducerCtx<InferSchema<typeof database>>;
const stamp = (millis: number) => new Timestamp(BigInt(millis) * 1000n);
const millis = (timestamp: Timestamp) => Number(timestamp.toMillis());

function authorize(ctx: Context) {
  requireRole(
    ctx.db.servicePermissions.identity.find(ctx.sender)?.role,
    'ingestion',
  );
}

export function health(
  ctx: Context,
  provider: string,
  fetched: number,
  status: 'ok' | 'failed',
  source?: number,
) {
  const previous = ctx.db.sourceHealth.provider.find(provider);
  const failureCount = healthTransition(
    previous
      ? {
          fetched: millis(previous.fetchedAtUtc),
          status: previous.status,
          failureCount: previous.failureCount,
        }
      : undefined,
    fetched,
    status,
  );
  if (failureCount === undefined) return;
  const row = {
    provider,
    fetchedAtUtc: stamp(fetched),
    sourceAtUtc:
      source === undefined
        ? previous?.sourceAtUtc
        : stamp(
            Math.max(
              source,
              previous?.sourceAtUtc ? millis(previous.sourceAtUtc) : 0,
            ),
          ),
    lastSuccessAtUtc:
      status === 'ok' ? stamp(fetched) : previous?.lastSuccessAtUtc,
    status,
    failureCount,
  };
  if (previous) ctx.db.sourceHealth.provider.update(row);
  else ctx.db.sourceHealth.insert(row);
}

export const ingest_weather_record = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    authorize(ctx);
    const input = parseWeather(payloadJson, millis(ctx.timestamp));
    const previous = ctx.db.weatherRecords.provider.find('awc');
    const content = JSON.stringify([input.sourceRef, input.contentJson]);
    if (
      shouldReplace(
        previous ? millis(previous.observedAtUtc) : undefined,
        previous?.contentJson,
        input.observed,
        content,
      )
    ) {
      const row = {
        provider: 'awc',
        airportIcao: 'KDTW',
        recordType: 'METAR',
        observedAtUtc: stamp(input.observed),
        fetchedAtUtc: stamp(input.fetched),
        sourceRef: input.sourceRef,
        ...input.fields,
        contentJson: content,
      };
      if (previous) ctx.db.weatherRecords.provider.update(row);
      else ctx.db.weatherRecords.insert(row);
    }
    appendIngest(
      ctx,
      `weather:${input.observed}:${input.fetched}`,
      JSON.parse(payloadJson),
    );
    health(
      ctx,
      'awc',
      input.fetched,
      'ok',
      Math.max(input.observed, previous ? millis(previous.observedAtUtc) : 0),
    );
  },
);

export const ingest_aircraft_batch = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    authorize(ctx);
    const input = parseAircraftBatch(payloadJson, millis(ctx.timestamp));
    for (const observation of input.observations) {
      const previous = ctx.db.aircraftObservations.aircraftId.find(
        observation.aircraftId,
      );
      const content = JSON.stringify([
        input.sourceRef,
        observation.contentJson,
      ]);
      if (
        !shouldReplace(
          previous ? millis(previous.observedAtUtc) : undefined,
          previous?.contentJson,
          observation.observed,
          content,
        )
      )
        continue;
      const row = {
        aircraftId: observation.aircraftId,
        provider: 'adsb_lol',
        observedAtUtc: stamp(observation.observed),
        fetchedAtUtc: stamp(input.fetched),
        sourceRef: input.sourceRef,
        ...observation.fields,
        contentJson: content,
      };
      if (previous) ctx.db.aircraftObservations.aircraftId.update(row);
      else ctx.db.aircraftObservations.insert(row);
    }
    const source = input.observations.length
      ? Math.max(...input.observations.map((row) => row.observed))
      : undefined;
    health(ctx, 'adsb_lol', input.fetched, 'ok', source);
  },
);

export const report_source_failure = database.reducer(
  { payloadJson: t.string() },
  (ctx, { payloadJson }) => {
    authorize(ctx);
    const input = parseFailure(payloadJson, millis(ctx.timestamp));
    health(ctx, input.provider, input.fetched, 'failed');
    appendIngest(
      ctx,
      `failure:${input.provider}:${input.fetched}`,
      JSON.parse(payloadJson),
    );
  },
);
