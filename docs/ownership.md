# Ownership template

| Workstream                              | GitHub username       | Primary paths                                                                                                                                                                | Plan sections    |
| --------------------------------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| Weather                                 | TODO_WEATHER_OWNER    | agents/weather/, data/awc.py                                                                                                                                                 | 4.2, 6.2, 11     |
| Congestion + projection                 | TODO_CONGESTION_OWNER | agents/congestion/, data/adsb.py, data/faa_status.py, data/flightaware.py, projection/                                                                                       | 4.3–4.5, 6.3, 8  |
| Validation + shared backend/integration | TODO_VALIDATION_OWNER | agents/validation/, backend/, runtime/, shared/spacetimedb_adapter.py, shared/settings.py, shared/llm.py, data/ingestion.py, data/normalization.py, data/health.py, scripts/ | 6.4–6.5, 7, 9–10 |
| Frontend                                | TODO_FRONTEND_OWNER   | frontend/                                                                                                                                                                    | 12–13            |

shared/contracts.py, tests/contracts/, docs/interfaces.md, and release policies
are shared review surfaces. The integration owner coordinates; affected owners
review. Each agent owns its colocated unit tests. Shared integration, decision,
and end-to-end tests are maintained by the owners of the affected behavior.

Optional NWS/OpenSky adapters are added only after P0 works; assign their owner
in this table and CODEOWNERS when introduced. Do not add extra agents.

## Reducing merge conflicts

- Keep provider-specific parsing in its adapter or agent folder, not in one
  shared catch-all file.
- Put projection implementation in projection/; the congestion owner writes it
  and the validation owner consumes its public deterministic interface.
- runtime/bureau.py is the composition point; specialists do not initialize or
  reach into each other's agents.
- New public DTOs go in shared/contracts.py; private DTOs stay with their owner.
- Frontend uses generated module bindings, not manually copied backend rows.
- New fixtures live in tests/fixtures/<provider-or-case>/ with a README naming
  their provenance and TEST status.
- Coordinate root tooling and dependency files in a dedicated PR.

The frontend workstream is assigned to **FrontEndOwner**. This is a workstream
identity; its GitHub username has not yet been confirmed. CODEOWNERS lines remain
comments until actual GitHub accounts with repository access are confirmed.
