# End-to-end rehearsal

## Isolated frontend rehearsal

Run `npm --prefix frontend run dev:test` and open http://127.0.0.1:5174.
This is labeled Demo mode in the UI, with synthetic TEST display data and a
local TEST transport. It contains no database or provider integration.

1. Preview the departure advisory and select +15 minutes. Compare runway queue,
   upstream waiting and total waiting; this fixture intentionally shows a tradeoff.
2. Open Demo settings in the footer and Switch to second session. Approve an advisory in the first;
   switch to the second session, open History, and verify the recorded controller/version and accepted projection.
3. Edit a different pending bounded advisory, enter a rationale, and submit.
   Verify it cannot be approved while awaiting revalidation. Use Revalidate draft in Demo settings, then review and approve the new version. TEST revalidation uses
   fixed synthetic series rather than an agent model.
4. Ignore a pending advisory, open History, and check synchronized version history.
5. Disconnect sessions and verify decisions are disabled. Reconnect and verify
   they become available only after assignments/versions synchronize.
6. Switch to a read-only viewer and confirm only preview remains available.
7. Simulate missing feeds and verify unavailable traffic metrics, no invented
   aircraft, and explicit source-health failures. Reset scenarios to start over.
8. Navigate by keyboard, expand evidence and source ages, and use Escape to close
   a projection with focus restored to its trigger.

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
