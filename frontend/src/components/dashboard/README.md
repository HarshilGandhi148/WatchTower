# Frontend implementation boundary

Header, source health, recent observed positions, weather, published events and
specialist findings. MapLibre and the OpenFreeMap dark basemap provide real DTW
geography with airport and 40 NM views. Aircraft use WGS84 geographic coordinates
and observed true track when available. Stale planes are hollow amber; positions
over 120 seconds old or outside 40 NM are excluded. Basemap errors remain
separate from observation health. Real subscriptions are still pending.

Owner: FrontEndOwner. See frontend/README.md and docs/frontend-integration.md
for verification and the pending backend handoff.
