import { Identity } from 'spacetimedb';
import { t } from 'spacetimedb/server';

import { requireRole } from './auth/roles';
import { database } from './schema';

export default database;
export {
  ingest_aircraft_batch,
  ingest_weather_record,
  report_source_failure,
} from './reducers/ingestion';

export const init = database.init((ctx) => {
  ctx.db.servicePermissions.insert({ identity: ctx.sender, role: 'admin' });
});

export const set_ingestion_permission = database.reducer(
  { identityHex: t.string(), enabled: t.bool() },
  (ctx, { identityHex, enabled }) => {
    if (!/^[a-f0-9]{64}$/.test(identityHex))
      throw new Error('Invalid identity');
    const identity = Identity.fromString(identityHex);
    requireRole(
      ctx.db.servicePermissions.identity.find(ctx.sender)?.role,
      'admin',
    );
    const previous = ctx.db.servicePermissions.identity.find(identity);
    if (previous?.role === 'admin')
      throw new Error('Administrator cannot be changed');
    if (enabled && previous && previous.role !== 'ingestion')
      throw new Error('Identity already has another role');
    if (enabled && !previous)
      ctx.db.servicePermissions.insert({ identity, role: 'ingestion' });
    if (!enabled && previous)
      ctx.db.servicePermissions.identity.delete(identity);
  },
);

export {
  register_assessment_request,
  ingest_flight_records,
  ingest_feed_batch,
  create_evidence_snapshot,
  submit_weather_assessment,
  submit_flow_candidate,
  store_projection,
  record_candidate_receipt,
  expire_deliveries,
  close_unfinished_cases,
  prune_live_observations,
} from './reducers/evidence';

export const set_service_permission = database.reducer(
  { identityHex: t.string(), role: t.string(), enabled: t.bool() },
  (ctx, { identityHex, role, enabled }) => {
    requireRole(
      ctx.db.servicePermissions.identity.find(ctx.sender)?.role,
      'admin',
    );
    if (
      !/^[a-f0-9]{64}$/.test(identityHex) ||
      !['ingestion', 'weather', 'congestion', 'validation'].includes(role)
    )
      throw new Error('Invalid service grant');
    const identity = Identity.fromString(identityHex);
    const previous = ctx.db.servicePermissions.identity.find(identity);
    if (previous?.role === 'admin')
      throw new Error('Administrator cannot be changed');
    if (enabled) {
      if (previous)
        ctx.db.servicePermissions.identity.update({ identity, role });
      else ctx.db.servicePermissions.insert({ identity, role });
    } else if (previous) ctx.db.servicePermissions.identity.delete(identity);
  },
);

export { store_validation_report } from './reducers/reports';

export {
  seed_agent_identity,
  write_ingest_event,
  write_weather_reading,
  write_congestion_reading,
  write_validation_result,
} from './reducers/streams';
