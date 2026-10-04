# Integration acceptance tests

test_bureau_wiring.py checks real SDK identity construction, three-agent Bureau
composition and signed protocol registration, with external ledger services
mocked. It does not start a live system or satisfy the acceptance gates below.

Run `make check-spacetime` for the real isolated SpacetimeDB gate. It builds and
publishes the module, verifies generated bindings, writes/reads through Python,
and checks two SDK subscriptions plus authorization, rollback and update races.
It creates a fresh local TEST server, credentials and database, then terminates
the server and removes temporary state. It reads no application .env or CLI token.
Missing CLI/server dependencies fail the test.

Pending: fixed-snapshot three-agent cycle, evidence persistence/pruning,
stale/failed-feed capability
degradation, and live/projection storage/query isolation. Ordinary CI must not
need provider credentials; real smoke tests produce separate evidence.
