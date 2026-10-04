# Frontend workstream

Owner: **FrontEndOwner**. React + TypeScript + Vite, using Node 24.

## Run MissionControl

From this directory:

```sh
npm ci
npm run dev
```

Open http://127.0.0.1:5173. `src/main.tsx` renders
`src/components/exchange/MissionControl.tsx`. The current entrypoint connects through
`src/lib/spacetimeTransport.ts` using only the three public settings in `.env.example`.
Follow the [development guide](../docs/development.md) to start a local database.
Preserve existing settings and ensure the frontend and backend target the same server
and database. Missing configuration and disconnected subscriptions remain visible.

MissionControl shows the DTW map, observed aircraft, weather and source health,
recommendations from stored informational validation reports, and the associated
Weather/Congestion/Validation conversation. Evidence inspection loads copied records
from the selected case snapshot. Exactly three agent roles are displayed.

Controller decisions, editing, dismissal and History are pending in this entrypoint.
`src/App.tsx` and its component tests remain in the repository, but main and the replay
entrypoint both render MissionControl. A successful informational report does not
establish controller authorization or operational validation.

## Separate TEST replay

```sh
npm run dev:test
```

Open http://127.0.0.1:5174. `tests/preview.tsx` uses `tests/replay-captures.json`.
Captures use synthetic TEST input with real congestion calculations and informational
report checks; weather and delivery are test doubles. This preview requires no database
or provider credentials and is clearly labeled **TEST REPLAY**.

Choose a **Replay scenario** to show weather/traffic concern, correction rounds, or a
provider failure. **Pause**, **Play**, and **Restart** control the 45-second replay.
Select a recommendation to inspect the full case conversation, timestamps, evidence,
and validation checks. During reassessment, earlier-round advice is marked as previous
and cannot select routes. A supported, current report offers **Accept for TEST path
preview**; this selects illustrative routes locally and records no backend decision.
Route playback and observed TEST aircraft remain separate. Reduced motion disables
automatic path playback and decorative motion.

## Map and evidence boundaries

MapLibre GL JS 6.12.0 renders OpenFreeMap tiles and retains required attribution.
Internet access and WebGL are needed for the map; map failures do not fabricate
observations. Aircraft symbols use observed track where available. The view radius
adjusts map framing; nearby-aircraft counts remain tied to the selected provider batch
and the monitored 40 NM area. Nearby aircraft do not establish DTW departure demand.

Observed UTC timestamps already include the provider's position age. Transport and
freshness checks use that normalized timestamp once. Future/invalid positions are
unavailable; positions older than 120 seconds leave the current map. Retained aircraft
from an earlier fetch cannot inflate the latest successful batch's count. Missing,
failed or stale evidence stays unknown.

Production subscribes using reviewed generated SpacetimeDB 2.10.2 bindings. Never
edit those bindings by hand. Browser configuration contains only public connection
settings; seeds and service tokens belong to backend processes. No TEST captures are
imported by the production entrypoint, and live path selection remains unavailable.

## Verification

```sh
npm run check
npm run build:demo
```

Run repository-wide `make check` and the isolated `make check-spacetime` gate before
proposing a merge. Component tests cover conversations, correction rounds, evidence,
connection/freshness behavior, focus, reduced motion and observed/projected isolation.
The database gate exercises TEST identities and data. Passing these checks does not
verify live provider coverage or a connected three-agent runtime. Browser visual
review remains unavailable because the browser tool could not verify the
administrator-enforced access policy.
