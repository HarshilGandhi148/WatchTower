# Repository review — October 4, 2026

## Assessment

The repository is a working observation foundation and frontend demonstration,
with a scaffold for the planned three-agent system. **None of the Weather,
Congestion, or Validation agents is implemented.** Their `agent.py` files contain
only TODO docstrings. Validation has tested coordination helpers, but its actual
evidence checks, advisory rules, wording, and agent factory are placeholders.

The structure follows the [technical plan](../full_technical_plan.md) and
[ownership boundaries](ownership.md). Passing tests establish the behavior of
the implemented foundation, not readiness for live air traffic control support.

## Findings to resolve before live integration

### 1. Aircraft retention and bounded reads can produce incomplete evidence

**Priority: high before sustained ingestion. Owner: validation/integration.**

`backend/module/src/reducers/ingestion.ts` inserts or updates aircraft by ID but
never removes old IDs. `shared/spacetimedb_adapter.py::read_observations` reads
`SELECT * FROM aircraft_observations LIMIT 1000` without a freshness filter,
pagination, or a truncation indicator. Once the table exceeds 1000 distinct IDs,
the caller receives an incomplete set without knowing it. Fresh aircraft are not
guaranteed to be in that set. Meanwhile, the frontend subscribes to all retained
rows and revisits them every second, even though it hides stale positions.

Plan sections 9–10 require bounded retention and consistent evidence. Define
retention and complete snapshot retrieval together: preserve referenced evidence,
prune transient observations in bounded batches, and detect or handle overflow.
Test more than 1000 distinct aircraft, stale/fresh mixtures, and evidence surviving
pruning before connecting congestion calculations. This review identifies the
gap by code inspection; it did not perform a large-table load test.

### 2. Frontend demo isolation is correct today but incompletely guarded

**Priority: medium. Owners: frontend and integration.**

Production `frontend/src/main.tsx` uses the real observation transport. Demo
helpers live under `frontend/src/test/` and are used by the separate
`frontend/tests/preview.tsx` entrypoint. No production import of those helpers
was found, and the production build contained none of the checked unique sample
markers (`test-projection-1`, `DEMO101`, or the hand-authored path description).

However, `frontend/eslint.config.js` blocks `tests/fixtures` imports but does not
block the actual `src/test` helpers from production code. A production-path
ESLint probe importing `./test/scenarios` passed. The Python repository guard
checks browser configuration but does not check JavaScript fixture imports.

Add a production import/re-export boundary for TEST helpers with regression
checks, while allowing unit tests and the separate demo entrypoint to use them.
This is a missing guard, not evidence that synthetic observations currently
enter the production app. It relates to plan sections 8.5 and 14.1.

### 3. Other airports require implementation work beyond environment settings

**Priority: medium before offering another airport demo. Owners: all affected
workstreams, coordinated through shared interfaces.**

The broader product goal is airport-independent, with Detroit as the demo.
The current implementation follows the original DTW scope:

- `shared/contracts.py` rejects non-KDTW envelopes and weather records.
- Backend weather validation and ingestion accept/write only KDTW METAR.
- Weather rows are keyed by provider alone, so multiple airports or report types
  cannot coexist under the same provider in the current schema.
- The frontend selects KDTW weather, uses a fixed DTW map center and 40 NM
  boundary, and carries DTW-specific display fields and labels.
- Root airport/radius settings are placeholders; the implemented Python settings
  loader only reads database connection settings.

Agree on an airport configuration and data contract before generalizing these
paths. Generate bindings with the schema change and test a second airport. Keep
browser configuration within its existing three-public-value restriction.
The README and technical plan now distinguish the product goal from the current
implementation; this review does not claim multi-airport support is delivered.

## Plan-to-code inventory

| Plan area | Present today | Remaining work |
| --- | --- | --- |
| Product and architecture (§1–3) | Exactly three agent directories, separate frontend, shared contracts and infrastructure layers | Runnable end-to-end workflow |
| Providers (§4) | Normalized METAR/aircraft DTOs and authorized database writes | All provider adapters, polling, error/backoff handling, FAA events, schedules, and live coverage checks |
| Configuration (§5) | Secret-free templates, database settings loader, three public browser settings | Agent/provider configuration loading and identity provisioning for the remaining roles |
| Weather (§6.2) | Owned directory and TODO modules | Agent factory, parsing, weather assessment, prompts, and unit tests |
| Congestion (§6.3) | Owned directory and TODO modules | Agent factory, metrics, weather exchange, candidates, demand association, and unit tests |
| Validation (§6.4) | Case coordinator and signed message handlers | Agent factory, evidence checks, action rules, numerical checks, wording, and domain tests |
| Cooperation (§6.1, §6.5) | Envelope serialization, configured sender checks, case/snapshot correlation, one correction, deadlines, duplicate handling | Actual assessment/candidate/advisory payload contracts, weather exchange, issue/resource/window deduplication, and durable persistence/restart recovery |
| Controller authority (§7) | Frontend review controls tested with a TEST server; production is read-only | Assignments, decision/edit reducers, version/expiry checks, atomic races, and real acknowledgements |
| Projections (§8) | Comparison and illustrative playback UI using TEST data | Deterministic model, assumptions, conservation tests, storage, and validation; no working calculation exists |
| Database (§9) | Three public observation/health tables, private service permissions, ingestion reducers | Events, flights, immutable evidence, findings, projections, advisories, assignments, decisions, audit history, and retention |
| Reliability (§10) | HTTP timeout/redaction, stale/out-of-order write handling, frontend reconnect and aging | Provider scheduling/retries, budgets/counters, bounded retention, and measured latency |
| LLM behavior (§11) | Placeholder interface | Model client, structured output checks, evidence-bound wording, repair/fallback, and source-injection tests |
| Dashboard (§12) | Observation subscriptions, map, review/history components, and isolated demo | Agent/advisory/controller backend integration and real browser rehearsal |
| Demo and quality (§13–14) | Local demo and automated foundation checks | Live smoke tests, operational scenarios, real two-controller rehearsal, attribution/budget and performance evidence |
| Delivery (§15–18) | Directory ownership, version pins, locks, CI configuration, setup documentation | Owner usernames, verified branch protection, coordinated interface handoffs, and operational acceptance |

The current weather DTO only represents selected METAR measurements. The Weather
Agent will also need forecast validity, conditional groups, hazards, and retained
source evidence. The existing coordination messages carry record IDs; they do
not replace `WeatherAssessment`, `FlowCandidate`, or `ValidatedAdvisory` payloads.
Resolve those shared contracts before independent agent implementations diverge.

## Verification performed

- `make check`: passed — 56 Python tests, 31 backend tests, 78 frontend tests,
  repository policy, lint, formatting, type checks, and production build.
- `make check-spacetime`: passed with CLI/server/SDK 2.10.2 — real module build and
  publication, regenerated-binding comparison, Python HTTP writes/reads, two SDK
  clients, permission denial/revocation, rollback, retries, and concurrent updates.
  The sandbox initially blocked a local port; the isolated test passed after
  running with permission outside that sandbox. It used a temporary TEST database.
- Production-path ESLint probe: confirmed the missing TEST-helper import guard.
- Inspected runtime imports, the production entrypoint, observation write
  permissions, UTC/unit/null handling, generated-binding workflow, and TEST data
  separation. Only the two `.env.example` files were tracked among the checked
  environment/identity-file patterns.
- Build warning: the MapLibre chunk exceeds Vite's 500 kB warning threshold.
  This does not fail the build and is not evidence of a correctness failure.

No live providers, real agent exchange, deployed controller decisions, or browser
visual rehearsal were tested. Gitleaks is configured in CI but was unavailable
locally; a full secret-history scan and remote branch-protection verification
were not performed. The built-bundle marker check is not a comprehensive secret
or fixture-isolation proof.

## Recommended implementation order

1. Agree on shared assessment/candidate/advisory and immutable evidence contracts,
   including forecast data and airport scope. Use an interface proposal reviewed
   by the affected owners.
2. Complete the initial provider path and retention/read behavior. Verify one real
   weather report and aircraft update reach the observation dashboard.
3. Implement Weather parsing/assessment and Congestion metrics/projections with
   their own tests. Connect their exchange through shared messages.
4. Implement Validation's evidence/action/calculation checks and connect its
   existing orchestration helpers to durable backend callbacks.
5. Add advisory storage and authorized controller reducers, then integrate the
   frontend. Test edits, expiry, concurrent decisions, and observation isolation.
6. Complete live smoke tests and the five manual scenarios in the plan. Keep the
   operational checklist unchecked until each behavior has evidence.

Close the frontend isolation guard before adding further production imports.
Keep extra-airport support as an explicit shared change; the current DTW demo can
be completed first. No fourth agent is needed for any of this work.
