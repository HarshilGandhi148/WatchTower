# Integration acceptance tests

test_bureau_wiring.py checks real SDK identity construction, three-agent Bureau
composition and signed protocol registration, with external ledger services
mocked. It does not start a live system or satisfy the acceptance gates below.

Pending: fixed-snapshot three-agent cycle, evidence persistence/pruning, actual
database HTTP calls and frontend subscriptions, stale/failed-feed capability
degradation, and live/projection storage/query isolation. Ordinary CI must not
need provider credentials; real smoke tests produce separate evidence.
