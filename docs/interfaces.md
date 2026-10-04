# Integration contracts

full_technical_plan.md is the product authority. This document identifies
stable boundaries so the four workstreams can proceed independently.

## Agent messages

shared/contracts.py defines the domain envelope and bounded enum catalog.
shared/messages.py implements SDK wire serialization and shared/orchestration.py
defines coordination references. WeatherAssessment, FlowCandidate and
ValidatedAdvisory payload DTOs remain pending implementation.
Messages carry schema_version, message_id, case_id, sender_agent, created_at_utc,
snapshot_id, evidence_ids, airport_icao, valid_until_utc, and optional correlation_id
and reply_to. UTC-aware timestamps and KDTW are checked by the initial envelope.

Implement WeatherAssessment, FlowCandidate, and ValidatedAdvisory against plan
section 6 with a reviewed shared contract PR. Add serialization compatibility
tests. Envelopes validate structure; runtime also verifies the actual configured
sender address and case/snapshot references. An enum is not authentication.

The cycle is weather ↔ congestion → validation. Cases use fixed evidence;
there is at most one correction and two specialist exchanges. Request retries
are idempotent. Agents communicate through messages and shared records, never
by importing each other's implementation.

Validation owns case orchestration through signed CaseOpenedMessage and
CandidateReadyMessage handlers, routing AssessmentRequestMessage to congestion.
Congestion still obtains the weather assessment before submitting its combined
candidate. See docs/orchestration.md for correlation, rounds, callbacks and limits.

## Data and projections

Adapters preserve provider time, successful fetch time, source ID, units,
missing fields, and observed/forecast/conditional distinctions. All writes go
through the shared HTTP adapter into authorized reducers.

The congestion owner implements projection/aggregate_flow.py as deterministic
functions with explicit snapshots and assumptions. Validation can call those
functions. Projection code may not import the DB adapter, provider adapters, or
agents. Projections have their own storage/reducers; neither preview nor decision
writes observed coordinates/weather/demand.

Readiness/capacity assumptions stay explicit. Baseline and proposal use identical
snapshot/model/demand; only declared response parameters change. Missing live
demand gives unavailable departure metrics, not invented flights.

## SpacetimeDB and frontend

The observation foundation implements three public tables, private service
permissions and ingestion reducers. The remaining product implementation adds
the rest of the 13 planned tables and 12 reducer names from plan section 9
under backend/module/src/tables/ and reducers/. Every reducer checks the caller
and relevant version; browser button state is not authorization.

Controller decisions atomically check assignment, current version, expiry,
material evidence changes, existing decisions, and request idempotency.
Edits create a new draft/version that must be revalidated. Agents never approve.

Pin a tested 2.x CLI/module/client/host combination when access is available.
Publish only after reducer tests pass. Generate and commit TypeScript bindings
under frontend/src/module_bindings/ with the schema PR. Record the exact CLI
version and generation command in backend/module/README.md.

Frontend receives observations, advisory versions, projections, health, and
decision history through official SDK subscriptions. Disconnect disables decisions;
reconnect reloads current versions. Browser environment exposes only WS URL,
database name, and display timezone. Controller auth belongs to per-session
identity flow; no shared service token is shipped to the browser.

## Connected foundation interface proposal

This first schema implements latest METAR, latest nearby aircraft, and source
health only. Provider parsers, agent payloads, evidence and decisions remain pending.
Public tables: weather_records (AWC, KDTW), aircraft_observations (ADSB.lol),
source_health. A private service_permissions table stores identity/role only.
The publishing identity is the sole administrator; set_ingestion_permission
takes identity_hex (64 lowercase hexadecimal characters) and enabled (boolean),
and grants/revokes ingestion for other identities. No automatic browser write role.

Reducers ingest_weather_record, ingest_aircraft_batch and report_source_failure
each take one payload_json string. HTTP calls therefore send [serialized_payload].
Payloads use schema_version=1, data_mode=live, provider=awc or adsb_lol,
source_ref (nonempty public reference), fetched_at_utc and observed_at_utc where
applicable. Times are canonical YYYY-MM-DDTHH:MM:SS.sssZ, stored as native UTC
Timestamps. Optional measurements are JSON null, never implicit zero.
Weather: airport_icao=KDTW, record_type=METAR, wind_direction_deg, wind_speed_kt,
wind_gust_kt, visibility_m, ceiling_ft. Aircraft batches carry observations:
aircraft_id, observed_at_utc, latitude_deg, longitude_deg, altitude_ft,
speed_kt, track_deg, position_age_s, on_ground (nullable boolean).
Failed fetch payloads contain only the envelope and no observation fields.
Unknown keys and explicitly non-live input are rejected. The ingestion service
is the provenance trust boundary: a live label alone cannot authenticate a source.

Batches are bounded to 1000 observations; identifiers to 128 characters; source
references to 2048 characters; payloads to 1 MiB. Duplicate aircraft IDs in a batch
are rejected. Observation times cannot exceed fetch time; fetches cannot be more
than 60 seconds ahead of server time. Equal observation time and equal content
is a retry; differing content is a conflict. New fetch times may refresh health
without altering unchanged observations. Older updates never replace latest rows.
Health is ordered by fetch time; an equal-time conflicting outcome is rejected.
Empty success batches preserve positions and report no coverage guarantee.

Python exports typed WeatherRecord, AircraftObservation, AircraftBatch and
SourceFailure DTOs and asynchronous adapter methods for writes and bounded
read_observations(table) queries (maximum 1000 rows, fixed table allowlist).
SQL responses are decoded using returned column names; tagged options become
Python None or values and native timestamps become aware UTC datetimes.
Failures expose safe error categories/status, never raw bodies or headers.

Schema and generated bindings are one review surface; integration and frontend
owners review this proposal alongside the backend implementation.

## Frontend adapter handoff

The frontend display boundary and pending live verification are documented in
[frontend integration](frontend-integration.md). Display types are presentation
models, not generated database row contracts. The observation SDK adapter is
implemented; advisory and controller integration remain pending.
