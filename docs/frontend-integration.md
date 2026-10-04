# Frontend integration handoff

Frontend owner: FrontEndOwner. Backend/integration owner supplies the tested
SpacetimeDB release, schema, generated TypeScript bindings, per-session identity
flow, assignments, edit bounds, and reducer outcome semantics.

## Adapter boundary

`frontend/src/features/live/types.ts` describes only the information required
for display. These types do not claim to match database tables or reducer wire
arguments. Map generated SDK types into them inside the integration adapter;
never hand-copy database rows or edit generated bindings.

Implement `DashboardTransport` from `frontend/src/lib/connection.ts`, then create
a client with `createDashboardClient(transport)` and supply it to `App`. Preserve
the unconfigured default until this adapter is implemented and tested.

- `connect(events)` opens the official SDK session and subscriptions and returns
  an unsubscribe/disconnect cleanup function. It must recover dropped connections
  and discard callbacks from obsolete connection attempts.
- Call `synchronizing()` immediately on reconnect. Call `ready(data)` only after
  observations, current advisory versions, projections, health, history, and
  current controller assignments have been loaded into one coherent display state.
- Clear decision capability during disconnect/errors. Subscription updates must
  replace collections rather than append duplicate versions. Preserve immutable
  referenced projections and evidence so history remains reviewable.
- `command(command)` maps decisions to `decide_advisory` and edits to
  `create_advisory_edit`, using generated reducer signatures. Resolve only after
  a correlated authoritative acknowledgement; reducer dispatch alone is not
  acceptance. A successful approval acknowledgement opens the accepted projection.
  Return the same request ID and a readable confirmation, or reject
  with a safe, actionable error. Document how acknowledgements are correlated.
- Server authorization, current version, expiry, snapshot/evidence changes,
  atomic conflict handling and idempotency are required. UI gating is advisory.

## Data semantics

- All times are UTC strings in display models; format in the configured timezone,
  defaulting to America/Detroit. Keep source publication time distinct from fetch
  success. Apply plan section 10 freshness rules per source in the adapter.
- `decidableAdvisoryIds` comes from authenticated server assignments and scope,
  never from a browser role selector. Unassigned sessions are read-only.
- `nearbyCount` is null for unavailable or unsupported current counts. Nearby
  aircraft are not automatically DTW flights. Map normalized WGS84 observations
  directly to `latitudeDeg` and `longitudeDeg`; keep `trackDeg` null when unknown
  and supply only observed true track (0–359.999°). Exclude absent coordinates.
  Label confirmed association explicitly. The map uses the FAA KDTW reference
  point (-83.3533933°, 42.2124311°) and a geodesic 40 NM observation boundary.
- `eventsStatus` distinguishes an unavailable FAA feed from a successful feed
  with no published KDTW events. Neither proves that the airport is uncongested.
- Projections carry snapshot/model identifiers, declared assumptions, pressure
  units and separate baseline/proposed series. Use null for unavailable metrics;
  include minute 0/5/10/15 samples when supported. The browser does not compute
  departure optimization or invent projection values.
- Parameter definitions and bounds come from the validated action catalog.
  Edits create a revised draft that requires revalidation and a newly checked
  projection. Preserve old version history and accepted parameters.
- Include provider attribution and safe attribution links in source-health data.
  Render source descriptions as plain text.

## Required live verification

1. Pin the exact tested SDK and generate bindings with the deployed schema.
2. Confirm a real KDTW observation and source-health update reaches the dashboard.
3. Provision two distinct controller identities and an unassigned viewer.
4. Rehearse approve/edit/revalidation/ignore, expiry, simultaneous requests,
   duplicate retries and rejection after a material evidence change.
5. Disconnect, change versions/assignments, reconnect and verify decisions remain
   disabled until full resynchronization. Check subscription cleanup.
6. Verify preview/approval cannot change live observations; review quiet-data and
   provider-outage behavior. Scan the production bundle for credentials/fixtures.

The TEST browser preview and frontend transport tests demonstrate UI behavior
only. They do not satisfy these live release gates. Backend schema/reducer changes
and generated bindings stay with the integration owner and affected-owner review.

## Optional aircraft path display — proposal for integration review

The frontend now supports `Projection.aircraftPaths` as an optional presentation
field. It carries a basis (`modeled` or TEST-only `illustrative`), path assumptions,
and per-aircraft baseline/proposed waypoint series. Every waypoint has a minute
offset (0–15), latitude in degrees and longitude in degrees. The parent projection
snapshot must match the advisory version. Paths are rendered as separate dashed
geometry and hollow projected symbols; they never update observation records.

This is a frontend adapter proposal, not an implemented database contract.
The integration owner must approve and provide modeled trajectories from the
same validated snapshot before enabling them in live mode. Missing routes or
invalid coordinates leave path playback unavailable; aggregate model values can
still be inspected. The MHacks sample routes are hand-authored TEST fixtures,
not computed by the agents or aggregate projection, including after demo edits.


## Frontend-only review controls

Branch selection (`baseline` / `proposed`) and selected aircraft ID are local
presentation state, not reducer arguments or database fields. They preserve the
fixed snapshot and do not recalculate routes. The review pane compares exact
+15 samples and displays no horizon delta unless both values are supported.
Playback uses ordered supplied waypoints only. Queue decisions have moved into
the versioned review footer; acknowledgement and authorization contracts above
remain unchanged. These controls require no backend schema migration.
