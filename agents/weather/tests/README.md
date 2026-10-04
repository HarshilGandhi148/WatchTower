# Weather component tests

METAR missing/variable wind, gusts, visibility/ceiling and units; TAF conditional
groups, amendments, midnight validity; no-material-impact and insufficient-data
assessment. Tests also cover structured model selection/repair/fallback, source
injection, concurrent/retried requests, persistence failure, signed-handler
allowlisting, SDK reply serialization, bounded rounds and change deduplication.
Fixtures belong in tests/fixtures/ and are never used as live input.

Run `python -m unittest discover -s agents/weather/tests -v` or `make check`.
Live AWC and credentialed ASI smoke results are recorded separately. Database
finding persistence, cross-process restart recovery and the full three-agent
cycle remain integration gates, not claims established by these unit tests.
