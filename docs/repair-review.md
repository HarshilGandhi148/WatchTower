# Merged-main repair review — October 4, 2026

## Live ADS-B ingestion follow-up

After explicit user approval, the tested module was deployed to the existing Maincloud
WatchTower database. The compatibility plan confirmed an automatic update with no
major-version upgrade or client-breaking changes. The deployment receipt confirmed
`op: updated`; no database clear was requested. The deployed equal-position fix
resolved the live reducer rejection.

The aircraft poller now runs from the real checkout using the existing backend `.env`,
with only ADS-B selected and an explicit WatchTower application identifier. The supplied
identifier contained example placeholders, so the launcher overrides it in memory and
preserves the file. Several consecutive live batches committed. A temporary provider
fetch failure used backoff and then recovered to healthy source status.

The final read-only dashboard SDK check returned 12 current aircraft and a nearby
count of 10 from the latest batch; source health showed a successful fetch less than
one second old. These are live ADSB.lol positions, separate from the TEST replay.
This verifies ingestion and dashboard delivery, without claiming browser visual review
or completion of the weather/congestion/validation recommendation workflow.

Restart from the repository root, with Python 3.12/3.13:

```sh
.venv/bin/python -m data.ingestion --env-file .env --provider adsb_lol --user-agent 'WatchTower/0.1 (MHacks DTW demo)'
```

The launcher validates configuration and live mode, reads only ingestion settings,
retains failed commits for identical retries, and logs safe failure categories without
seeds, tokens, headers or response bodies. Its new configuration/redaction tests pass;
full repository checks now pass 540 Python, 80 backend and 104 frontend tests.

The earlier review below describes the state before this approved deployment and live
startup. Credentials and existing environment files remain unchanged.


Reviewed main at `53d3614` in an isolated clone, preserving existing environment files
and generated bindings. Clean dependency installation is required after the merge:
stale frontend dependencies omitted the pinned SpacetimeDB SDK.

## Corrected behavior

- Repeated ADS-B polls with the same position timestamp can refresh fetch time and
  position age without aborting ingestion. Conflicting identical-time payloads still
  fail; older observations cannot replace newer positions.
- Aircraft timestamps are normalized once, preventing valid positions from aging
  twice. Current counts use membership in the latest successful batch; retained rows
  do not silently inflate them. Future evidence remains unknown.
- Weather stream availability uses the established 90-minute METAR limit, rather
  than rejecting otherwise usable reports after five minutes.
- Revoked or reassigned service identities cannot continue writing streams through
  an old agent allowlist entry.
- MissionControl includes later correction-round messages and blocks selection of
  a previous recommendation while reassessment runs. Path selection also requires
  a ready connection and a current report.
- Congestion and validation evaluate evidence freshness at immutable snapshot
  capture time while keeping the original planning window. Records captured between
  window start and snapshot creation remain usable; records beyond capture time
  remain unknown. Demand and backlog retain the original window boundaries.
- Startup documentation explicitly targets the local database and describes the
  active MissionControl/replay interface.

The time handling reuses the existing `cutoff_at_utc` snapshot field. The internal
`FeedSnapshot` retains it; deterministic demand/backlog helpers accept an optional
evidence time. Defaults preserve standalone callers. There is no database schema,
generated-binding, shared message contract or dependency-lockfile change.

## Connected live agent follow-up

The dashboard originally received only the aircraft poller. Added the local
three-specialist launcher, scheduled AWC METAR ingestion, immutable normalized
weather loading and durable weather findings. The existing validation gateway
persists informational reports and receipts consumed by MissionControl.

Congestion now preserves an insufficient-weather finding's ID while retaining its
unknown weather signal. That finding reaches validation instead of abandoning the
case. Timeouts still carry no invented finding. Startup recovery completes once,
before any new snapshots, and duplicate/terminal notifications no longer declare a
mandatory SDK reply. Signed handlers and explicit peer allowlists remain active.

Saved peer addresses did not match the current seeds. The local launcher can derive
addresses from those three seeds using an explicit flag; the override is in process
memory and the private environment files are unchanged. No fourth identity,
synthetic live observation, schema change or generated-binding edit was added.

## Verification and remaining work

`make check` passes with 566 Python, 82 backend and 110 frontend tests, plus policy,
lint, formatting, types and production builds. The SpacetimeDB 2.10.2 gate passes
against an isolated TEST database, including the real three-specialist stored
handoff with insufficient weather, report persistence and delivery receipt. This
TEST handoff uses explicit in-process delivery; the configured live run additionally
verified the real SDK handlers and local specialist delivery.

The earlier user-approved compatible backend update was deployed without clearing
the existing database. Aircraft ingestion continues independently. The live agent
run committed KDTW METAR observations, weather findings, congestion candidates,
validation reports and receipts. A separate read-only public SDK check used the
same eight dashboard tables and its actual exchange adapter: all three roles had
activity, reports parsed, and the adapter returned no issues.

Restart from the real repository root:

```sh
.venv/bin/python -m runtime.live --env-file .env --user-agent 'WatchTower/0.1 (MHacks DTW demo)' --derive-peer-addresses
```

Weather polls METAR every minute and TAF every five minutes; new fixed-snapshot
cases refresh every minute. Run the ADS-B ingestion process separately as documented in README.md.
The frontend server remains at port 5173, with the separate TEST replay at 5174.

Full retained METAR/TAF evidence preserves cloud details, weather codes, visibility
bounds and forecast groups. AWC can omit decoded weather codes; a complete, matching
US METAR body can supply the weather group, with malformed/abridged reports and
sensor failures remaining unknown. Reports name observed traffic, weather and specific
gaps. The active list keeps at most five cards from the last five minutes, removes
expired/superseded results on the clock, and preserves database audit history.
See recommendation-window-interface.md for the additive contract and verification.
Missing traffic or optional metrics no longer withhold an informational
recommendation: available weather and copied aircraft observations determine the
next step, and incomplete coverage is displayed as "Limited data". The historical
traffic-change metric is omitted because the previous cycle is not retained in
the current snapshot. Integrity, expiry and operational-action checks remain active.
Operational advisory persistence and controller decisions remain unfinished.
Browser visual inspection is still blocked by administrator-policy verification;
public subscription/adapter checks establish the data path, not visual QA.
Changes remain local and uncommitted.
