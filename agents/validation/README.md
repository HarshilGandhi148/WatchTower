# Validation and integration workstream

Owner: TODO_VALIDATION_OWNER. Own this agent, backend/, runtime/ and integration
infrastructure identified in docs/ownership.md. Coordinate shared contract changes.

orchestrator.py implements bounded case routing; protocol.py attaches signed
Fetch.ai handlers to this same agent. runtime/bureau.py assembles the three
prepared identities. See docs/orchestration.md for callbacks and integration.
Evidence/action checks, agent factory and backend reducers remain pending.

Check evidence, UTC/units, airport/window/snapshot, freshness, bounded actions,
projection calculations and factual wording. Allow one reassessment; do not
make agent agreement count as independent source corroboration. See plan 6.4–10.

First handoff: supported versioned advisory or truthful rejected/insufficient-data
result. The agent never approves. Server reducers enforce controller assignment,
version/expiry, material changes and idempotent atomic decisions.
