# Congestion and projection workstream

Owner: TODO_CONGESTION_OWNER. Own this folder, data/adsb.py, data/faa_status.py,
data/flightaware.py and projection/. See plan 4.3–4.5, 6.3, 8.

Nearby traffic is not automatically DTW traffic. Optional schedule access must
have credentials and a budget. Missing readiness/capacity stay explicit. Use
shared messages for weather and validation; never import their implementations.

First handoff: fixed-snapshot metrics, combined FlowCandidate, optional projection
and declared limitations. Clear weather must not block supported congestion.
Keep projection functions deterministic and free of provider/database access.

## Agent runtime

agent.py is a factory for runtime/bureau.py (see docs/orchestration.md); it never starts a
server or registers an endpoint. service.py implements congestion's side of the protocol:
open a case with validation, answer round 1/2 by asking weather, then send CandidateReady
references. feeds.py is an interim live fetch of the public ADS-B and FAA feeds until
ingestion and the DB adapter exist. Configuration names: CONGESTION_AGENT_SEED,
WEATHER_AGENT_ADDRESS, VALIDATION_AGENT_ADDRESS, DATA_USER_AGENT (never hardcode a seed).

Not done yet: candidates are kept in memory (validation cannot load them across processes
until the DB adapter exists), no AeroAPI demand, and weather timeouts are not submitted.
