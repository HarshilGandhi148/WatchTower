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
    if (enabled && !previous)
      ctx.db.servicePermissions.insert({ identity, role: 'ingestion' });
    if (!enabled && previous)
      ctx.db.servicePermissions.identity.delete(identity);
  },
);
