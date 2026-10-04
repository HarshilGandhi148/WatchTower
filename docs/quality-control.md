# Quality control

There are two distinct gates: repository checks that exist now, and product
acceptance checks that must be implemented as behavior arrives.

## Scaffold checks in CI

| Check | Enforced now |
| --- | --- |
| Ownership boundaries | Python AST checks block cross-agent implementation imports and reverse imports from shared/data/projection |
| Fixture isolation | Runtime imports of tests/fixtures are rejected |
| Three-agent limit | Agent directory names must be exactly weather, congestion, validation |
| Public frontend config | Only the three allowlisted VITE settings; browser env access is centralized |
| Python quality | Ruff lint/format and strict mypy |
| Shared envelope | UTC, validity, schema, airport, ID, and role regression checks |
| Frontend/backend quality | ESLint, Prettier, TypeScript, frontend test and production build |
| Secrets | Gitleaks history scan plus environment filenames ignored by Git |

The import policy covers static Python imports, not dynamic imports, HTTP/database
writes, or reducer permissions. Review and product tests must cover those cases.
CODEOWNERS is a template until usernames are assigned. GitHub protection must be
enabled; configuration files alone do not block merges.

## Product acceptance checklist — all pending

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
