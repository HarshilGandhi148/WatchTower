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
references. feeds.py reconstructs parser inputs from copied database batches; it does
no network I/O. persistence.py creates snapshots, serializes concurrent transitions,
saves each candidate and delivery atomically, and retries pending notifications until
receipt or expiry. A failed save remains retryable with the same payload and ID.

Configuration: CONGESTION_AGENT_SEED, WEATHER_AGENT_ADDRESS,
VALIDATION_AGENT_ADDRESS, SPACETIMEDB_HTTP_URL, SPACETIMEDB_DATABASE,
SPACETIMEDB_CONGESTION_TOKEN and the shared airport center/radius. Public feeds are
polled independently by data/ingestion.py with the ingestion identity. No service
token belongs in browser configuration.

Both assessment rounds use the snapshot's original analysis window. Restart closes
unfinished assessments and resumes saved deliveries. No weather timeout is submitted
without a stored finding. Paid polling remains disabled. Weather and validation domain
implementations are still owner workstreams.

## Projections from stored snapshots

projections.py prepares one baseline-vs-proposal projection per snapshot when all of
`DEPARTURE_CAPACITY_PER_HOUR`, `DEPARTURE_BACKLOG_UPSTREAM` and
`DEPARTURE_BACKLOG_RUNWAY_QUEUE` are set. Setting only some of them is a configuration
error.

- **Demand:** the snapshot's stored FlightAware batch only: confirmed, uncancelled
  departures with a runway time, in five-minute buckets spread evenly per minute. This is
  exactly what the `store_projection` reducer re-derives. The batch must be at most
  5 minutes old at the window start. Missing or stale demand gives no projection, never
  zero-valued results.
- **Backlog:** stored flight records have no gate times, so the initial upstream waiting
  and runway queue are operator settings, labelled "not observed". Candidates citing such a
  projection have limited quality and say so in their assumptions.
- **Proposal:** release at the configured capacity for the whole horizon. The baseline
  has no response.
- **Persistence:** the payload (`projection_id = proj-<snapshot_id>`) is stored through
  `adapter.store_projection` before the candidate that cites it. Retries resend the
  identical payload. Validation recomputes it on retrieval.

Live projections need stored FlightAware batches, and FlightAware ingestion is not
scheduled yet. Observed backlog would need gate times in stored flight records; both
are database/ingestion changes outside this folder.

See [setup and verification](../../docs/congestion-spacetime.md).
