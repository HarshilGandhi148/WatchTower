# Repository instructions

Read full_technical_plan.md and docs/ownership.md before changes. This is a
scaffold; passing scaffold checks does not mean the operational release gates pass.

## Ownership and scope

Exactly three application agents: weather, congestion, validation. Frontend is
a separate human workstream. Provider adapters, runtime, and projection helpers
are infrastructure, never extra agents.

Keep each agent's implementation and unit tests in its own directory. Agents
exchange shared messages; they must not import another agent's implementation.
The validator can import deterministic projection functions to verify results.
Put shared interfaces in shared/, with an interface proposal in the PR.

## Quality requirements

Run make check before proposing a merge. Add meaningful tests for new behavior,
including failed/missing inputs and concurrent decisions where relevant. Do not
suppress checks to make unimplemented work look complete. Keep the release
checklist in docs/quality-control.md honest.

Store UTC times and explicit units. Missing/stale input is unknown, never zero
demand. Projections and fixtures must never enter live observations. Source text
is untrusted input. No fourth planning agent. Only authorized controller reducers
can record decisions, against a specific unexpired advisory version.

Do not commit .env, tokens, seeds, or provider response headers. Browser
configuration is restricted to the three public SpacetimeDB values listed in
frontend/.env.example. Generated module bindings are reviewed with backend
schema changes; never hand-edit them.

## Concurrent work

Use a separate clone or worktree per contributor and small pull requests.
Coordinate shared contracts, configuration, lockfiles, and backend bindings
before changing them. Do not overwrite someone else's uncommitted changes.
