# Frontend implementation boundary

Projection selection is owned by App and the projection drawer. Baseline and
proposal stay separate from live observations and retain their original snapshot.
TEST scenarios and TEST revalidation cannot reach the real database.

Owner: FrontEndOwner. See frontend/README.md and docs/frontend-integration.md
for verification and the pending backend handoff.
