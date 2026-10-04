# SpacetimeDB module placeholder

Status: empty schema, tables/reducers/auth pending. This workspace can be linted
and typechecked without a host or credentials; it is not a working database.
No publish/start script is provided for the empty module.

Run npm ci and npm run check. The npm lockfile captures the locally checked SDK
version; host and CLI compatibility remain unverified. When access arrives,
pin the module and frontend client to a tested, identical 2.x release.

## Schema layout

tables/live.ts reserves five provider/health tables.
tables/evidence.ts reserves snapshots, findings and flow candidates.
tables/projections.ts, advisories.ts, controllers.ts and audit.ts reserve the
remaining five tables. Reducer files reserve the 12 operations from plan 9.2.
src/auth/permissions.ts owns server-side identity and scope checks.

Only live ingestion writes observation tables. Projection, validator and
controller identities must not get that permission. Decisions atomically check
role/scope, version, expiry, material evidence and request idempotency.

## Compatibility record — pending

- Tested host release: TODO
- Tested CLI release: TODO
- Tested module/frontend client SDK release: TODO
- Database/module identity: configured locally, never a service token here
- Binding generation command: TODO, recorded after checking selected CLI help
- Generated schema/module version or hash: TODO
- Credentialed reducer/authorization/race test evidence: TODO

Generate bindings into frontend/src/module_bindings/ only after implementing and
testing the schema. Commit them in the schema PR. See the official references:
https://spacetimedb.com/docs/quickstarts/react/
https://spacetimedb.com/docs/clients/typescript/
