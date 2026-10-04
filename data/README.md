# Provider and ingestion infrastructure

AWC is weather-owned. ADS-B, FAA XML and optional AeroAPI are congestion-owned.
Normalization, source health and polling/writes are integration-owned.

All modules are placeholders. Implement P0 before optional providers. Test
success, empty, malformed, unavailable, missing fields and out-of-order data.
Provider response time, record observation/publication time and fetch success
time remain separate. Use bounded timeouts/backoff and centralized caching.
No adapter generates synthetic live flights or treats missing input as zero.
