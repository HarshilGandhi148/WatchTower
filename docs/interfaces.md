# Integration contracts

full_technical_plan.md is the product authority. This document identifies
stable boundaries so the four workstreams can proceed independently.

## Congestion CI compatibility repair

The congestion contracts extend, rather than replace, the observation foundation.
`WeatherRecord`, `AircraftObservation`, `AircraftBatch`, `SourceFailure`, and their
UTC/measurement helpers remain available in `shared/contracts.py` alongside
`FlowCandidate` and `WeatherAssessmentRequest`. The observation payload schema and
generated database bindings are unchanged. Existing observation, congestion,
serialization, and real SpacetimeDB tests verify this compatibility boundary.

## Agent messages

shared/contracts.py defines the domain envelope and bounded enum catalog.
shared/messages.py implements SDK wire serialization and shared/orchestration.py
defines coordination references. FlowCandidate and ValidatedAdvisory payload
DTOs are proposed below; the WeatherAssessment payload remains pending.
Messages carry schema_version, message_id, case_id, sender_agent, created_at_utc,
snapshot_id, evidence_ids, airport_icao, valid_until_utc, and optional correlation_id
and reply_to. UTC-aware timestamps and KDTW are checked by the initial envelope.

Implement WeatherAssessment, FlowCandidate, and ValidatedAdvisory against plan
section 6 with a reviewed shared contract PR. Add serialization compatibility
tests. Envelopes validate structure; runtime also verifies the actual configured
sender address and case/snapshot references. An enum is not authentication.

### FlowCandidate (proposal, congestion owner)

`FlowCandidate` in shared/contracts.py wraps a `MessageEnvelope` and follows plan
6.3. Structural rules, all enforced at construction:

- Sender must be congestion; the window is aware UTC and ends after it starts.
- Every `TrafficMetric` has a unit, a `MetricBasis` (observed, confirmed_record,
  inferred, estimated, projected) and evidence IDs; those and `faa_event_ids` must
  already be listed in the envelope's `evidence_ids`.
- `proposed_action_type` comes from the bounded `ActionType` catalog (no aircraft
  control). Only `change_worth_reviewing` may carry action parameters;
  `no_change`/`insufficient_data` propose nothing; `insufficient_data` must list
  `required_missing_inputs`; an actionable candidate cannot have `unavailable` quality.
- A downstream restriction needs an FAA event and reliably associated flights.
- `weather_assessment_id` is optional, so clear weather never blocks a candidate.

Not enforced here: parameter limits (validator checks them against project bounds),
that evidence IDs exist in storage, and uAgents serialization. Review questions:
weather owner confirms `weather_assessment_id` semantics; validation owner confirms
the validator can check the action/parameter names and wants no further fields.

### ValidatedAdvisory (proposal, validation owner)

`ValidatedAdvisory` in shared/contracts.py follows plan 6.4 and maps onto the
frontend `Advisory` display model. Structural rules, enforced at construction:

- Status is always `pending_review`. `AdvisoryStatus` lists the full lifecycle, but
  approved/ignored/expired/superseded are written only by controller reducers.
- KDTW; aware-UTC window, creation and expiry; window end > start; expiry > creation;
  version ≥ 1. `quality` is adequate or limited, because unavailable evidence stays an
  internal insufficient-data result.
- At least one evidence ID, with no duplicates. A `projection_id` must also be listed
  in `evidence_ids`.
- Parameters only with a flow-review action, never with a notice; unique names.
- Display text (title, response, reason, owner role, resource, tradeoffs, assumptions,
  limitations) is non-empty, printable and at most 500 characters.
- `material_fingerprint` identifies an unchanged advisory across cycles. Storage
  keeps it so a later run can suppress duplicates or create the next version.

Frontend mapping: `id`←`advisory_id`, `response`←`suggested_response`,
`resource`←`affected_resource`, `parameters`←`action_parameters` (bounds from the
validator's catalog), `projectionId`←`projection_id`. `evidence` display rows
come from stored evidence records, not from the advisory itself.

Review questions:

- **Frontend owner:** does the field mapping cover the card, and should
  `limitations` stay separate from `assumptions`?
- **Congestion owner:** the validator excludes the candidate's own derived
  `projection_id` when it checks that cited evidence belongs to the case opening.
  The opening lists inputs only. Please persist the `FlowSnapshot`,
  `ProjectionAssumptions` and `ProjectionComparison` behind each `projection_id`
  so the validator can recompute it.

The validator maps `no_change` to an internal REJECT with finding `no_change`.
Adding a separate `ValidationVerdict.NO_CHANGE` is an open option.

### WeatherAssessmentRequest (proposal, congestion owner)

`WeatherAssessmentRequest` in shared/contracts.py is congestion's request for a weather
assessment within one validation round: a `MessageEnvelope` (sender must be congestion,
snapshot and evidence fixed for the case), an aware-UTC window, and `attempt` (1 or 2,
matching the validation `assessment_round`). It is distinct from shared/orchestration.py's
`AssessmentRequest`, which is validation asking congestion. A retry reuses the same
`message_id`, so the weather agent can deduplicate.

On the wire (shared/messages.py) it travels as `WeatherAssessmentRequestMessage`; the
weather agent answers with `WeatherAssessmentReplyMessage`: a weather-role envelope whose
`reply_to` names the request, a status from plan 6.2 (impact | no_material_impact |
insufficient_data) and the stored `assessment_id`. The full WeatherAssessment payload
stays with the weather owner.

Congestion accepts a reply only from the configured weather address, for the same case
and snapshot, answering the outstanding request, before expiry; late replies are
rejected. Validation's `CandidateReady` requires a stored weather assessment, so a case
whose weather exchange times out or returns no assessment id is not submitted and
validation closes it as insufficient data at its deadline (to be confirmed with the
validation and weather owners; the plan also allows a limited result instead).

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
