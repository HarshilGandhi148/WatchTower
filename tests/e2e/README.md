# End-to-end rehearsal

## Isolated frontend rehearsal

Run `npm --prefix frontend run dev:test` and open http://127.0.0.1:5174.
This is labeled Demo mode in the UI, with synthetic TEST display data and a
local TEST transport. It contains no database or provider integration.

1. Preview the departure advisory. Check the +15 outcomes, including increased
   total waiting. Scrub Now/+5/+10/+15 and expand Sampled comparison for exact
   values. Switch Baseline/Proposed without resetting time, select an aircraft
   from the review pane or map, and verify other projected routes dim.
2. Open Demo settings in the header and Switch to second session. Approve an advisory in the first;
   switch to the second session, open History, and verify the recorded controller/version and accepted projection.
3. Open a pending bounded advisory with Preview, choose Edit in the review footer, enter a rationale, and submit.
   Verify it cannot be approved while awaiting revalidation. Use Revalidate draft in Demo settings, then review and approve the new version. TEST revalidation uses
   fixed synthetic series rather than an agent model.
4. Preview a pending advisory, choose Ignore in the review footer, open History, and check synchronized version history.
5. Disconnect sessions and verify decisions are disabled. Reconnect and verify
   they become available only after assignments/versions synchronize.
6. Switch to a read-only viewer and confirm only preview remains available.
7. Simulate missing feeds and verify unavailable traffic metrics, no invented
   aircraft, and explicit source-health failures. Reset scenarios to start over.
8. Navigate by keyboard, use the Overview/Evidence tabs with arrow keys, expand
   source ages, and use Escape to close review/playback with focus restored.
9. Enable reduced motion before approval and during playback. Confirm autoplay
   stays off / playback pauses, while explicit Play and scrubbing still work.
10. Check 1920×1080, 1440×900, 1000×650 and 390×844 layouts; confirm the map
    and review stay usable, source states readable, and map attribution visible.

Automated component/transport coverage lives in frontend/src/*.test.tsx and
colocated tests. Simulated two-session behavior is not a live release gate.

## Real backend rehearsal — pending

After the integration owner supplies tested SDK bindings, schema, controller
sessions, assignments and reducers, repeat the walkthrough with two separate
browser contexts and database-issued controller identities. Include expiry,
material evidence changes, simultaneous conflicting decisions, lost
acknowledgements, changed assignments on reconnect and provider outages.
Verify preview/approval never write observations. Record actual smoke-test
results in the PR and release checklist; do not mark them passed using TEST data.
