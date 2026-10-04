# Validation and integration workstream

Owner: TODO_VALIDATION_OWNER. Own this agent, backend/, runtime/ and integration
infrastructure identified in docs/ownership.md. Coordinate shared contract changes.

orchestrator.py implements bounded case routing; protocol.py attaches signed
Fetch.ai handlers to this same agent. runtime/bureau.py assembles the three
prepared identities. See docs/orchestration.md for callbacks and integration.

Check evidence, UTC/units, airport/window/snapshot, freshness, bounded actions,
projection calculations and factual wording. Allow one reassessment; do not
make agent agreement count as independent source corroboration. See plan 6.4–10.

## Domain validation (pure, implemented)

`service.validate_packet(packet, config, previous)` takes an already-loaded
`ValidationPacket` and returns a `ValidationVerdict`, the findings and, on publish,
a `pending_review` `ValidatedAdvisory` (shared/contracts.py). It reads no storage,
clock or network.

| Module | Checks |
| --- | --- |
| packet.py | Private DTOs: evidence records, provisional weather finding, projection packet, findings, demo configuration |
| evidence.py | Candidate is the one CandidateReady named; same case/snapshot/airport; cited records exist, are live, KDTW, same snapshot, not fetched after the cutoff; plan section 10 freshness; projections never cited as observations; window starts at the snapshot and spans 15/30 min |
| calculations.py | Recomputes the projection with `projection.aggregate_flow.compare`; runs `verify_branch`; projected metrics and action parameters must equal the recomputation; hypothetical capacity and instant transfer become limitations |
| policy.py | Issue→action catalog, parameter names/units/integers/bounds, required evidence per advisory type, weather consistency, untrusted text limits, quality never raised |
| lifecycle.py | Advisory ID per issue/resource/window bucket, material fingerprint, duplicate suppression and next version, expiry, demo priority and owner role |
| wording.py | Deterministic templates: numbers only from checked values; tradeoffs regenerated from deltas, including upstream waiting; review phrasing only |

The most severe finding decides the verdict. Integrity problems and "nothing to
present" (no change, duplicate) reject. Missing or stale input is insufficient
data. A weather/candidate contradiction asks for the one reassessment.
Limitations are published, with quality lowered to limited and the limitation
shown. Candidate free text (assumptions, missing inputs) is carried only as
displayed data; it never changes the action, parameters, wording or verdict.

## Pending

- agent.py factory (seed from `VALIDATION_AGENT_SEED`) and the `validate`/`persist`
  callbacks. They load a packet from storage, call `validate_packet` and write the
  advisory idempotently through backend reducers.
- Stores and SpacetimeDB tables for candidates, projection inputs, evidence
  snapshots and advisories. Congestion currently keeps candidates in memory, uses
  placeholder evidence IDs and does not store its projection inputs.
- `WeatherAssessment` contract. `WeatherFinding` is a provisional reference.
- Optional LLM paraphrase via shared/llm.py with template fallback.

The agent never approves. Server reducers enforce controller assignment,
version/expiry, material changes and idempotent atomic decisions.
