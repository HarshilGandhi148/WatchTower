# Congestion persistence integration

The implemented path is public feeds → stored batches → transactional fixed snapshot
→ weather finding and congestion candidate → durable notification → validation receipt.
A candidate and its receipt are suggestions/delivery records. They do not create an
advisory or authorize a controller decision.

## Setup

Use Python 3.13, Node 24 and CLI/server/module/client SpacetimeDB 2.10.2. Install the
repository's pinned dependencies, start the local server and publish the module as
in docs/development.md. Generate bindings using the command in backend/module/README.md.

Create **five separate database identities** using local `POST /v1/identity`: ingestion,
weather, congestion, validation and an unassigned viewer. The database publisher is
administrator. With that administrator, grant each service its own role:

```sh
spacetime call watchtower-local set_service_permission '"<identity-hex>"' '"ingestion"' true
spacetime call watchtower-local set_service_permission '"<weather-identity-hex>"' '"weather"' true
spacetime call watchtower-local set_service_permission '"<congestion-identity-hex>"' '"congestion"' true
spacetime call watchtower-local set_service_permission '"<validation-identity-hex>"' '"validation"' true
```

Keep the tokens in ignored `.env` or a secret manager and export the variables to the
process. The module never grants observation writes to the publishing administrator.
`SpacetimeSettings.from_env(role="weather" | "congestion" | "validation")` selects that
role's token; the ingestion default selects `SPACETIMEDB_INGEST_TOKEN`.

Set `DATA_USER_AGENT` to identify the team. `AIRPORT_LATITUDE_DEG`,
`AIRPORT_LONGITUDE_DEG` and `TRAFFIC_RADIUS_NM` are used by ingestion, snapshots and
congestion calculations. The default FAA KDTW reference point matches the frontend
handoff. Current shared message/weather contracts explicitly support KDTW only;
changing ICAO to another airport fails early instead of mixing airports.

Start public feed ingestion:

```sh
python -m data.ingestion
```

ADS-B polls every 10 seconds and FAA every 60 seconds independently. Requests have a
10-second timeout, a 2 MB response limit, disabled redirects and capped exponential
backoff with jitter. Numeric and HTTP-date Retry-After delays are bounded. Errors expose
only safe categories; provider bodies, tokens and response headers are not logged.
Successful empty batches, parser rejections/deduplication, source timestamps and coverage
flags are retained. Database commit retries reuse the same batch ID and payload.
A failed fetch updates health, and cannot be mistaken for a successful empty response.

AWC polling and the Weather Agent's assessment logic remain the weather owner's work.
The existing METAR write adapter is available. FlightAware polling stays disabled; the
normalized flight-record reducer is a storage contract for future authorized ingestion
and isolated TEST projections. It introduces no paid requests.

## Specialist and validation hooks

Build congestion with `AgentConfig.from_env` / `build_agent`. It reads the database and
never fetches feeds. Its async service fixes an immutable snapshot before opening a
case. Both weather rounds and both candidates retain its original analysis window.
Concurrent cycles reuse the open case; concurrent advance calls save one candidate
per round. A prepared candidate remains retryable until persistence succeeds.

The Weather owner must read `adapter.load_snapshot(request.envelope.snapshot_id)` and
save `shared.persistence.WeatherFinding` using `adapter.save_weather` before sending a
reply with the finding ID. Use the requested window, attempt, case, evidence and expiry.
Findings cannot outlive the fixed snapshot; missing/stale inputs require limitations.
The integration test's clear-weather responder is explicitly a **TEST double**, not an
implementation of the Weather Agent.

Pass `ValidationStorage(validation_adapter)` to `register_orchestration` or
`build_coordinated_bureau`. The gateway saves assessment requests before dispatch,
retrieves stored candidates/evidence, and records exact notification receipts. Duplicate
notifications do not repeat domain validation in an active process. Domain `validate`
and `persist` callbacks remain required; receipt is not approval. The projection hook
recomputes the deterministic model and rejects a stored result that differs.

Registered validation gateways run `storage.restart()` at startup before accepting
messages and retry recovery if the database is unavailable. A custom gateway composition
must call this hook before accepting messages. The congestion service also closes unfinished snapshots during its first
startup operation and resumes saved outbox records. Late openings cannot reopen a
closed snapshot. A specialist restart can still deliver a committed candidate to the
running validator. An entire bureau restart closes volatile cases; saved notifications
can be acknowledged as delivery without reviving them or publishing an advisory.

## Retention, references and bounds

A snapshot transaction selects complete batches through latest-feed pointers and copies
batch, weather, health and configuration contents. Its stored IDs resolve through
`evidence_records`. Counts never read the aircraft latest-state table: retained aircraft
outside the selected successful batch cannot inflate them. Missing/stale ADS-B is unknown;
a fresh empty successful batch is a limited observed count, never zero airport demand.
Rejected-only responses are unknown. FAA retrieval health and publication time remain
separate. Source text is retained as data and never interpreted as instructions.

Ingestion can call `prune_live_observations` periodically. It removes at most 1000 old
position rows and 1000 old noncurrent batches per call after 30 minutes. It does not
remove copied evidence, findings, candidates or projections. Historical derived records
are retained for this prototype; a production retention policy remains future work.
All batch inputs are limited to 1000 records and 2 MB. Reads fetch 1001 rows and fail
explicitly if more than 1000 exist, instead of silently returning a truncated set.
Pending-delivery queries exclude receipts/history. Expiry sweeps process bounded pending
records. Missing airport demand/capacity/backlog never invokes an unsupported projection.

## Verification and remaining release work

Required commands: `make check`, `make check-spacetime`, and Gitleaks over changes/history
and built artifacts. Ordinary tests inject clocks and provider/database failures. The
real gate starts an isolated TEST database and uses distinct database roles, generated
bindings, real HTTP reducers, the real congestion service and validation gateway. No
live provider credentials or paid API calls are needed.

Tests cover frozen membership/window during concurrent updates, unavailable/empty feeds,
wrong roles and references, duplicate/conflicting retries, save failure, successful commit
with lost response, crash before notification, notification/receipt retries, correction,
restart and stale reopen prevention, pruning and separate supported TEST projections.
Clock-controlled reducer tests cover expiry boundaries without timing-dependent sleeps.
These results do not establish provider coverage, operational weather interpretation,
advisory publication, controller authorization, or aviation readiness. Those checklist
items remain open in docs/quality-control.md.

## AeroAPI merge compatibility

The AeroAPI parser, budget guard, injected-transport fetch helper and deterministic
backlog calculations are retained. The fetch helper lives in `data/flightaware_fetch.py`
and is not scheduled by the runtime. `ENABLE_FLIGHTAWARE` and
`DEPARTURE_CAPACITY_PER_HOUR` do not enable paid polling or automatic projections in
the stored-snapshot agent. Its candidates reference only persisted evidence; an offline
projection calculation cannot create a candidate projection reference. Connecting the
expanded AeroAPI records/backlog model to ingestion and durable projections remains a
separate integration step. The existing TEST projection storage/retrieval gate is unchanged.
