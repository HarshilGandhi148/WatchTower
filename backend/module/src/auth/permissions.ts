import { table, t } from 'spacetimedb/server';

export const servicePermissions = table(
  { name: 'service_permissions', public: false },
  { identity: t.identity().primaryKey(), role: t.string() },
);
