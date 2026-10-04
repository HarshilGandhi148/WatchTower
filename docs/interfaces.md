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

### AssessmentRequest (proposal, congestion owner)

`AssessmentRequest` in shared/contracts.py is congestion's request for a weather
assessment: a `MessageEnvelope` (sender must be congestion, snapshot and evidence
fixed for the case), an aware-UTC window, and `attempt` (1 or 2; at most two
specialist exchanges per case). A retry reuses the same `message_id`, so the weather
agent can deduplicate. Replies must set `reply_to` to that `message_id`;
agents/congestion/coordination.py accepts a reply only from the weather role for the
same case and snapshot, answering the outstanding request, before its expiry. A timeout
proceeds with an unknown weather signal; a late reply is rejected.

Still to be defined by their owners: the weather reply (`WeatherAssessment`, plan 6.2)
and validation's correction request. Coordination currently reads only their envelope
fields, a reduced weather signal and an assessment ID, so neither needs congestion types.
A correction request must set `reply_to` to the candidate's `message_id`; one correction
per case is honored.

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

The backend owner adds the 13 tables and 12 reducer names from plan section 9
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
