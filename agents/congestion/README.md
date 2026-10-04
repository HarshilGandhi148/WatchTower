# Congestion and projection workstream

Owner: TODO_CONGESTION_OWNER. Own this folder, data/adsb.py, data/faa_status.py,
data/flightaware.py and projection/. See plan 4.3–4.5, 6.3, 8.

Nearby traffic is not automatically DTW traffic. Optional schedule access must
have credentials and a budget. Missing readiness/capacity stay explicit. Use
shared messages for weather and validation; never import their implementations.

First handoff: fixed-snapshot metrics, combined FlowCandidate, optional projection
and declared limitations. Clear weather must not block supported congestion.
Keep projection functions deterministic and free of provider/database access.
