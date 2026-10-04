# Backend observation tests

validation.test.ts covers pure input, authorization-role and state transition
rules without mocking database transaction guarantees. Run npm test.

make check-spacetime exercises the actual module/server for permission grants
and revocations, unassigned/anonymous writes, private table visibility, retries,
transaction rollback and concurrent observation updates.

Immutable evidence, expired/superseded advisory decisions, material changes,
controller races and edit revalidation remain pending with those features.
