# Deterministic projection

Congestion owns implementation; validation reviews/consumes the public functions.

No agent, provider adapter, runtime or database-adapter imports. Fixed immutable
snapshot plus explicit assumptions gives baseline and proposal with conserved
upstream waiting, in-transit demand, runway queue and served counts. Change only
declared response parameters. Include both upstream and runway waiting; missing
live demand gives unavailable metrics. See plan section 8. Implementation pending.
