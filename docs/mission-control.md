# Mission-control demo and integration handoff

The production frontend now opens a map and three-agent exchange. Advice is the primary view; conversations and evidence open on demand. Live activity is reconstructed from
stored assessments, findings, candidates, receipts and validation reports; it is
an assessment summary, not a verbatim chat transcript. On reconnect, existing
history loads without replaying its animation. Evidence opens on demand from the
selected immutable snapshot. Aircraft highlighting only uses referenced batch IDs.

## Run the presentation

From this worktree:

```sh
npm --prefix frontend run dev:test
```

Open http://localhost:5174. Choose Weather + traffic concern, Correction round,
or Provider failure. Replay starts automatically. Advice appears when validation completes; click a
recommendation to reveal its conversation. Pause and Restart are the only playback
controls. The screen uses neutral surfaces without gradients. The TEST label applies to both
map and exchange. A weather test double and scripted delivery surround real
CongestionService calculations and the real deterministic report evaluator.
These are generated TEST captures, not recordings of a live bureau.

```sh
npm --prefix frontend run dev
npm --prefix frontend run build
npm --prefix frontend run build:demo
```

The live frontend runs on port 5173. Its existing three public environment values
are unchanged. Deploy the separate `.cache/test-preview` build at `/demo/` to
provide the replay link on a hosted installation. TEST captures are not imported
by the live entrypoint or included in its production bundle. Review controls in
live mode still require the unfinished advisory/controller integration.

Regenerate captures with `python -m tests.fixtures.replay.generate`, then run the
frontend formatter. `frontend/tests/replay-captures.json` contains synthetic
inputs only. Its original timestamps are retained; a replay clock drives display.

## Validation report contract

`shared/validation_report.py` defines an informational report referencing exactly
one stored candidate, snapshot and assessment round. Outcomes are supported,
insufficient_data or rejected. Six named checks cover binding, freshness, metrics,
inputs, action scope and optional projection verification. Suggestions use fixed
review templates. A supported report is not a published advisory or approval.

The validation gateway saves this report before recording delivery receipt.
Failure leaves notification retry possible. The reducer permits only the
validation role, enforces references/round/expiry, rejects contradictory outcomes,
and makes identical retries idempotent. Saved candidates can finish report
persistence after congestion restart without reopening closed assessments.

Deterministic input reconstruction and traffic metrics now live in data/ as
reusable infrastructure; congestion keeps compatibility exports. Validation never
imports congestion implementation. The report evaluator verifies supported
metrics, resolves evidence, checks weather evidence availability and recomputes
attached projections. Unsupported metrics/actions produce insufficient data.
Weather interpretation itself remains the Weather Agent's responsibility.

The new public `validation_reports` table and `store_validation_report` reducer
ship with regenerated SpacetimeDB 2.10.2 bindings. Existing tables and data remain
compatible; publish the updated module before starting the new live UI. Service
credentials remain exclusively on the backend. Frontend mappings report malformed
or oversized collections instead of silently treating them as complete.

## Remaining boundaries

The Weather Agent is not completed by this work. Domain advisory publication,
authenticated controller decisions, paid polling and operational readiness remain
open. Source text is rendered as plain text; the optional JSON inspector strips
credential-like fields and URL queries. The UI shows receipt, report, expiry and
connection state separately. System reduced-motion settings disable animations.

Verification includes Python report tests, reducer authorization/idempotency tests,
frontend transport and component tests, and two real SDK subscribers decoding
persisted reports through the frontend adapter in `make check-spacetime`.

## Accepted advice and path preview

A supported TEST recommendation exposes **Accept for TEST path preview**. This
local demo selection draws the original illustrative route fixtures translated
to aircraft in the selected snapshot. Hollow projected markers and dashed routes
remain separate from observed positions. A 0–15 minute slider and play/pause
control expose the effect. These routes are hypothetical illustrations, not
agent-generated flight predictions or clearances. No acceptance reducer is called.
Live acceptance and route predictions remain unavailable until authorized controller
decisions and validated stored aircraft trajectories are supplied.
