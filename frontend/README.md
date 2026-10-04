# Frontend workstream

Owner: **FrontEndOwner**. React + TypeScript + Vite, using Node 24.

## Run

```sh
npm ci
npm run dev
```

The normal application connects to the observation foundation using the three
public settings in `.env.example`. Run the local backend with `spacetime dev`
from the repository root. Without valid settings the dashboard is unconfigured.
Advisories, projections, history, and controller sessions remain unavailable.
There are no fixtures or synthetic observations in the production entrypoint.

For the separate sample-data preview (shown as **Demo mode**):

```sh
npm run dev:test
```

Open http://127.0.0.1:5174. This entrypoint lives in `tests/` and imports TEST-only
scenarios. It cannot access aircraft/weather providers or a database. The real basemap
loads geographic tiles from OpenFreeMap; aircraft remain synthetic TEST data.
The compact Demo mode disclosure identifies sample data. Rehearsal controls are
under **Demo settings** in the workspace footer. It supports switching between two independent
controller identities in the same workspace, read-only viewing, outages, reconnects,
bounded edits, explicit TEST revalidation, approvals, dismissals and history.
Its revalidation uses fixed synthetic series; it is not a projection calculation
or evidence of backend authorization. Reload to reset scenarios.

## Implemented frontend behavior

- Interactive DTW geographic map, observed positions, source health, evidence
  timestamps, weather, published events, and specialist findings.
- Three prioritized visible advisories with an expandable overflow queue.
- Fixed-snapshot path playback and baseline/proposed comparison, with exact
  0/5/10/15-minute model values in optional scenario details.
- Dedicated History page and visible Weather → Congestion → Validation findings.
- Unknown values remain unavailable. Stale positions are labeled and positions
  older than 120 seconds are excluded from the current map.
- Assignment/version/expiry/connection gating, request idempotency, bounded
  edits awaiting revalidation, server error feedback and exact-version history.
- Keyboard controls, focus restoration, source text escaping and Detroit DST.

## Workspace layout

Desktop windows at least 1000 × 650 CSS pixels use a fixed viewport workspace.
The map fills remaining space; the advisory list scrolls within its sidebar.
Evidence and bounded edits open beside the map in a wider review column. The
map slides left and retains its height; covered controls are avoided by giving
both regions their own space. Menu resizing honors reduced motion. History is a
separate `#/history` page with decision totals and full versioned records; browser
Back/Forward navigation works, and accepted scenarios can be reopened on the map.
The map remains mounted while History is shown, preserving its state and live subscription.

Preview and accepted decisions select paths directly on the map. The MHacks
preview supplies hand-authored illustrative trajectories with a 0–15 minute
scrubber and playback. Acceptance starts playback unless reduced motion is
requested. Projected aircraft are hollow purple symbols with dashed paths;
observed aircraft remain separate. Scenario details is an optional wider menu
with exact aggregate model values, provenance, assumptions and limitations.
The three specialist assessments are visible beneath the map in taller desktop
windows and available under Specialist findings at smaller sizes.

Narrow or short windows use a stacked layout with normal document scrolling.
No observations, assignments or projection snapshots are changed by navigation,
playback, preview, approval or menu resizing.

## Geographic map

MapLibre GL JS 6.12.0 renders the OpenFreeMap dark basemap. No map account,
API key or extra browser environment settings are required. Source-managed
attribution remains visible directly below the map, so credits do not cover
airport detail. Attribution updates and cleanup still use the built-in MapLibre
control. Tile loading requires internet access and WebGL; failures
show a separate map notice and Retry map button without changing observations.

Airport view centers on the FAA KDTW reference point (-83.3533933°, 42.2124311°).
The View radius slider fits a 5–40 NM area and updates its geographic range ring.
Moving the slider selects Traffic view; Airport view returns to the airport zoom.
The selected radius survives map retries. It adjusts the view only: observation
coverage and the monitored nearby-aircraft count remain at 40 NM.
Pan and zoom remain stable across live updates. Airplane symbols rotate only
when an observed true track is supplied; unknown track is identified in details.

References: [OpenFreeMap](https://openfreemap.org/quick_start/),
[FAA KDTW reference](https://www.faa.gov/air_traffic/publications/atpubs/aip_html/part3_ad_2.0_michigan.html).

## Backend handoff — observation foundation connected

See [frontend integration handoff](../docs/frontend-integration.md). Display
models in `src/features/live/types.ts` are frontend presentation contracts,
not database row copies. `src/lib/connection.ts` defines an adapter interface
and a fail-closed subscription/request lifecycle. The production entrypoint installs `src/lib/spacetimeTransport.ts`, which
subscribes to the three public observation tables using reviewed generated bindings.
It reconnects and keeps sessions read-only. Controller integration remains pending.

The official SDK, module and host are pinned to 2.10.2. Generated
bindings belong in the backend schema PR and must never be edited by hand.
Controller session credentials must not enter browser environment variables.

## Verification

```sh
npm run check
```

Component and lifecycle tests use isolated TEST data. A passing test preview is
not a live smoke test or an operational authorization/race/isolation release gate.
Real two-session browser rehearsal remains pending the backend handoff. Run
repository-wide `make check` before proposing a merge.

## Aircraft paths and demo boundaries

`Projection.aircraftPaths` is an optional frontend display contract, not a copy
of database rows. Each aircraft has ordered baseline/proposed waypoints with
explicit minute offsets and latitude/longitude degrees. Paths must belong to
the selected projection snapshot; malformed paths are omitted. Playback
interpolates only supplied waypoints and never extrapolates observed tracks.

The TEST fixtures use the `illustrative` basis. These paths are authored for
visual demonstration and do not result from the aggregate flow calculation;
changing release-rate parameters does not calculate individual routes. The
production client rejects illustrative paths. Backend-generated `modeled`
trajectories require integration-owner review, source provenance and declared
path assumptions. Aggregate-only results remain reviewable, while the map says
that aircraft paths are unavailable. No fixtures are imported by production.
