# Quality control

There are two distinct gates: repository checks that exist now, and product
acceptance checks that must be implemented as behavior arrives.

The [October 4 repository review](repository-review.md) maps the current code to
the technical plan. The review predates the congestion persistence integration documented below.
Weather interpretation, advisory publication and controller authority remain incomplete;
tested coordination and storage are not a completed operational agent system.

## Scaffold checks in CI

| Check | Enforced now |
| --- | --- |
| Ownership boundaries | Python AST checks block cross-agent implementation imports and reverse imports from shared/data/projection |
| Fixture isolation | Runtime imports of tests/fixtures are rejected |
| Three-agent limit | Agent directory names must be exactly weather, congestion, validation |
| Public frontend config | Only the three allowlisted VITE settings; browser env access is centralized |
| Python quality | Ruff lint/format and strict mypy |
| Shared envelope | UTC, validity, schema, airport, ID, and role regression checks |
| Fetch.ai wiring | Real SDK JSON round trips, signed protocol registration, exactly three Bureau members; external services mocked |
| Orchestration | Sender allowlist, fixed snapshots, request correlation, duplicates, one correction, expiry and retryable outcome persistence |
| Frontend/backend quality | ESLint, Prettier, TypeScript, frontend test and production build |
| Secrets | Gitleaks history scan plus environment filenames ignored by Git |

The import policy covers static Python imports, not dynamic imports, HTTP/database
writes, or reducer permissions. Review and product tests must cover those cases.
CODEOWNERS is a template until usernames are assigned. GitHub protection must be
enabled; configuration files alone do not block merges.

## Observation foundation verification

Verified locally with CLI/server/module/client 2.10.2 on October 4, 2026:

- [x] Normalized observation contracts, UTC/null encoding and HTTP error redaction.
- [x] Real module publication, Python HTTP writes/reads and two SDK subscriptions.
- [x] Ingestion grants/revocation, anonymous/wrong-role denial, private table
  visibility, atomic rollback, retries and concurrent/out-of-order observations.
- [x] React empty/error/disconnected states, subscription readiness, aging and
  Detroit DST formatting; generated bindings checked against the schema.

Evidence: backend/module/tests/validation.test.ts,
tests/contracts/test_observations.py, frontend/src/App.test.tsx,
frontend/src/lib/spacetimeTransport.test.ts, frontend/src/lib/freshness.test.ts and
`make check-spacetime` (tests/integration/spacetimedb_live.py).
The integration gate uses a synthetic TEST database, not real provider coverage.
CI is configured to run it; remote CI results require pushing the change.

## Congestion persistence verification

Verified against the pinned local 2.10.2 module/server with a synthetic TEST database:

- [x] Independent ADS-B/FAA polling, bounded requests/backoff and redacted failures;
  successful empty batches, rejections, coverage flags and publication/fetch times retained.
- [x] Transactional fixed snapshots with copied inputs/health/configuration and resolvable
  stored evidence IDs; latest retained aircraft cannot inflate selected-batch metrics.
- [x] Separate ingestion/weather/congestion/validation permissions; missing references,
  wrong snapshots/windows/rounds, expiry, units and conflicting ID reuse rejected.
- [x] Atomic candidate/outbox save, identical lost-response retries, notification/receipt
  retries, duplicate suppression, restart delivery and closed-case reopen prevention.
- [x] Correction rounds preserve snapshot/window; supported TEST projections stay in
  derived storage and are retrievable/recomputed without changing live observations.
- [x] Explicit oversized read errors and bounded live retention preserving copied evidence.

Evidence: agents/congestion/tests/test_persistence.py, tests/adapters/test_ingestion.py,
tests/contracts/test_persistence_adapter.py, backend/module/tests/evidence.test.ts,
agents/validation/tests/test_protocol.py and make check-spacetime
(tests/integration/congestion_live.py). Weather assessment is a **TEST double**.
Expiry/retention reducer tests inject clocks; real-server recovery tests require no
provider credentials, paid polling or timing-dependent sleeps.
These items describe implementation verification, not operational aviation readiness,
provider coverage, a completed Weather Agent, advisory publication or controller authority.
The broader operational checklist below remains pending.

## Product acceptance checklist — remaining release gates

Track implementation evidence in PRs and check items only after the relevant
behavior exists and has been verified. A scaffold test is not operational evidence.

- [ ] Enabled provider adapters: success/empty/malformed/unavailable, timeout,
  backoff/429, field units, missing values, stale/out-of-order records.
- [ ] METAR variable wind/visibility/ceiling and TAF amendments, conditional
  groups, midnight validity and Detroit DST display.
- [ ] ADS-B on-ground/missing coordinates, duplicates and position ages;
  nearby aircraft stay distinct from verified KDTW flights.
- [ ] FAA XML: secure parser with external entities disabled, no events,
  multiple events, malformed XML, DTW normalization, fetch vs publication time.
- [ ] AeroAPI, if enabled: cap/pagination, null times/cancellations, association,
  scheduled gate vs estimated/actual runway timing.
- [ ] Agent cycle: clear-weather congestion, weather without enough demand,
  contradictory airport/window/snapshot, one correction/timeout, duplicate messages.
- [ ] Factual provenance: evidence for every factual claim, deterministic numbers,
  invalid structured output and source prompt injection handled.
- [ ] Projection: reproducibility, conservation including transit/upstream/queue,
  nonnegative integer counts, service ≤ capacity/demand, same baseline inputs,
  unavailable metrics stay unavailable, no invented savings.
- [ ] Live isolation: scenario parameters and projected entities cannot write or
  appear in provider observations or the agents' live evidence queries.
- [ ] Frontend fixture isolation: production imports/re-exports of frontend TEST
  helpers are rejected by automated checks, while test and demo entrypoints work.
- [ ] Bounded observation retention and complete evidence reads: more than 1000
  aircraft IDs cannot silently truncate the live evidence set; pruning preserves
  referenced snapshots and frontend subscriptions remain bounded.
- [ ] Reducers: wrong-role/unassigned callers denied, expired/superseded approval
  denied, two-controller races atomic, repeat request idempotent, edit revalidated.
- [ ] Lifecycle: material changes invalidate pending versions; minor refreshes do
  not create duplicates; history preserves exact accepted parameters/snapshot.
- [ ] Frontend: disabled decisions offline, current state reload after reconnect,
  readable/keyboard-operable controls, observed/projected labels and assumptions.
- [ ] End-to-end: two separate controller identities synchronize approve/edit/ignore.
- [ ] Failure modes: outages degrade capabilities visibly; no TEST input under LIVE.
- [ ] Secrets: repository, built bundle, logs and exports scanned; roles use
  separate service identities and controllers use separate session identities.
- [ ] Live smoke tests: credentialed coverage/response check for every enabled
  provider and tested database/SDK/bindings combination.
- [ ] Manual review: clear/no-change, congestion-only, weather+demand, downstream
  restriction, insufficient data; fixture cases shown in TEST.
- [ ] Attribution, request counters, paid-provider cap, measured latency and demo
  rehearsal documented.

Use tests/integration/, tests/decisions/, tests/e2e/, provider/agent unit test
folders, and backend/module/tests/ for this evidence. Never mark a pending gate
passed because a directory exists or the scaffold builds.

## Validation logic evidence

The validator's pure domain checks (agents/validation/service.py) have unit tests
using TEST packets:

- cross-case/snapshot/airport references; missing, stale and non-live evidence
- projections cited as observations
- recomputed projection invariants and numbers
- action catalog and parameter bounds
- weather contradictions leading to one reassessment
- source prompt-injection text
- duplicate suppression and versioning

tests/integration/test_congestion_validation.py drives a real congestion candidate
through the orchestrator and validator. That test rebuilds the evidence records and
projection inputs because congestion does not store them yet. The agent-cycle and
provenance gates above stay unchecked until stored evidence, a weather agent and
persistence exist and run together.

## Frontend implementation evidence

The frontend has automated coverage for version/assignment/expiry gating,
bounded edits, rejected requests, stable retry IDs, subscription cleanup,
disconnect/resynchronization, fixed snapshots, unavailable metrics, stale
positions, source-text escaping, Detroit DST, focus restoration, accepted
projection selection and synchronization across two isolated TEST identities.
Review coverage includes keyboard tabs, +15 outcome tradeoffs, missing horizon
samples, branch switching, aircraft selection, version retention, read-only
history and delayed acknowledgements. Animation checks cover frame cancellation,
reduced-motion changes during playback, and fixed route geometry across playback
and LIVE/TEST transitions.
The separate browser harness is documented in tests/e2e/README.md.

These checks use TEST display data and a local TEST transport. They do not prove
real reducer authorization, live isolation, deterministic model invariants or
provider integration. The operational checklist above remains pending until
credentialed evidence exists. Browser visual review was unavailable because the
browser tool could not verify the administrator-enforced policy.

### Mission-control presentation

The map/exchange presentation includes a separate generated TEST replay, immutable
evidence inspection, reduced-motion support and backend informational validation
reports. Tests verify these mechanisms. The Weather Agent, advisory publication,
controller authorization and operational aviation release gates remain open.
A supported informational report does not satisfy those gates.
