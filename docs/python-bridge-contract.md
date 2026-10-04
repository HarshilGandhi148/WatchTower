# Python / TypeScript subscription contract

Python uses the supported SpacetimeDB HTTP reducers and SQL interface; the
pinned TypeScript SDK owns WebSocket subscriptions. Exactly three application
agents remain: weather, congestion, validation. Ingestion and bridge are services.

| Durable public stream | Authorized writer | Consumers |
| --- | --- | --- |
| ingest_event | ingestion | weather, congestion, validation |
| weather_reading | weather | validation |
| congestion_reading | congestion | validation |
| validation_result | validation | dashboard/debugging only |

The private agent_identity table maps service name to a unique identity. Only
admin can seed/change it. Seeding also grants the corresponding existing reducer
role; an identity cannot acquire two service names. Bridge has no write reducer.
Existing service_permissions is retained for existing observation/evidence reducers.

These are normal append-only tables, not ephemeral event tables: restart recovery
needs retained rows. Each has an auto-increment u64 ID, unique stable event key,
server UTC timestamp, root ingestion ID and canonical versioned JSON. Exact retries
are no-ops; conflicting keys fail. Derived rows must reference an existing ingestion
row. Validation requires both specialist readings for that root. Signals carry row
ID and server timestamp; consumers read the persisted record, rather than trusting
webhook payloads. Original evidence, candidates and projections remain separate.

The bridge subscribes to configured stream tables and backfills with SQL
`id > last_seen` after reconnect. It sends rows in ascending ID order per table,
advancing its durable cursor only after HTTP acknowledgement. The Python receiver
persists per-handler receipts in SQLite after successful handling. Crash between
write and receipt safely repeats stable-key writes. A failed handler yields a retry;
completed handlers are skipped. Validation never wakes specialists (loop guard).

Stream specialist handlers currently provide conservative source-availability
readings with UTC freshness checks. They do not replace the richer fixed-snapshot
candidate flow or claim a completed live Weather Agent. Unsupported demand and
projections remain unavailable. This is an explicit integration interface proposal.
