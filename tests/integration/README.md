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

congestion_live.py extends the same required gate with separate ingestion/weather/
congestion/validation/unassigned identities, parser-normalized TEST batches, fixed
snapshots, weather findings, the real congestion service and validation gateway,
corrections, lost commit responses, restart delivery, receipts, invalid submissions,
retention and supported TEST projection storage. The weather responder is a TEST
double; this does not implement the Weather Agent. Expiry boundaries use injected
clocks in backend reducer-body tests rather than real-time sleeps.

Pending: operational weather interpretation, advisory publication/controller authority,
provider coverage and full live three-agent rehearsal. Ordinary CI must not
need provider credentials; real smoke tests produce separate evidence.
