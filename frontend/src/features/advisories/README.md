# Frontend implementation boundary

Queue ordering, latest-version filtering, expiry/assignment/connection gating and bounded edit validation. Stable request IDs and duplicate submission handling live in the client. Server reducers remain authoritative for permissions, races and material evidence changes.

Owner: FrontEndOwner. See frontend/README.md and docs/frontend-integration.md
for verification and the pending backend handoff.
