import { table, t } from 'spacetimedb/server';

export const agentIdentity = table(
  { name: 'agent_identity', public: false },
  { name: t.string().primaryKey(), identity: t.identity().unique() },
);
const fields = () => ({
  id: t.u64().primaryKey().autoInc(),
  eventKey: t.string().unique(),
  rootId: t.u64(),
  createdAtUtc: t.timestamp(),
  payloadJson: t.string(),
});
export const ingestEvent = table(
  { name: 'ingest_event', public: true },
  fields(),
);
export const weatherReading = table(
  { name: 'weather_reading', public: true },
  fields(),
);
export const congestionReading = table(
  { name: 'congestion_reading', public: true },
  fields(),
);
export const validationResult = table(
  { name: 'validation_result', public: true },
  fields(),
);
