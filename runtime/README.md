# Runtime composition

bureau.py implements build_bureau() and build_coordinated_bureau() for exactly
three distinct uAgent identities. The coordinated builder attaches workflow
routing to validation through required validation and persistence callbacks.

Run the three specialists against the configured database from the repository root:

```sh
.venv/bin/python -m runtime.live --env-file .env --user-agent 'WatchTower/0.1 (MHacks DTW demo)'
```

The launcher loads backend settings only, derives the three peer addresses from their
fixed seeds, and refuses mismatched or reused identities. It finishes restart recovery
before opening cases, runs local signed uAgent delivery on port 8000, polls AWC METAR
every minute and TAF every five minutes, and uses separate specialist database credentials.
The active MissionControl subscriptions receive saved requests, findings, candidates,
validation reports and receipts. ADS-B ingestion runs independently (see README.md).

Full METAR/TAF evidence is retained in each immutable snapshot, including cloud details,
weather codes and visibility bounds. Legacy scalar records and unavailable forecasts
remain explicit missing inputs. Cases refresh every minute; MissionControl removes
expired results and retains at most five cards from the latest five minutes. No operational
approval is inferred from a completed informational report. Advisory publication and
controller decisions retain the limits in docs/quality-control.md.

See docs/orchestration.md for the handoff and limits. scheduler.py, provider pollers
and the Bureau launcher are infrastructure, never extra application agents.

Install the hashed runtime requirements and development tools from README.md.
Fill separate seeds when implementing the factories. The Bureau helper itself
does not create identities, load fixtures, register a fourth agent or start a server
until the caller explicitly runs it. It accepts prepared agents with their handlers.
The local launcher does not require public Almanac registration or a fourth identity.
If saved peer addresses belong to old seeds, add `--derive-peer-addresses` to replace
those addresses only in process memory. The environment file is preserved.
