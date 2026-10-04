# Local setup and start order

The scaffold is intentionally credential-free. Development checks and frontend
build do not contact providers or SpacetimeDB. Copy the two .env.example files
to local .env files when ready; empty DB fields mean unconfigured, not connected.

## Toolchains and dependencies

Python 3.13 and Node 24 are the team defaults. requirements-dev.txt pins the
Python quality tools. JavaScript dependencies are locked per workspace and
installed with npm ci. Do not share .venv or node_modules between teammates.

uAgents 0.26.0 is pinned in pyproject.toml, with a universal hashed dependency lock
in requirements-runtime.txt. Install it with
`python -m pip install --require-hashes -r requirements-runtime.txt`.
SDK construction, signed protocol registration and serialization are tested on
Python 3.13; these tests do not connect a live agent system.

The validation-owned orchestrator and Bureau builder are implemented; see
docs/orchestration.md. Agent factories and live/domain/database callbacks remain
pending. Add direct HTTP/XML/environment dependencies as their adapters are
implemented, then regenerate the runtime lock with the command in its header.
Do not hand-edit generated dependency hashes.

## Local SpacetimeDB observation foundation

CLI, standalone server and both JavaScript SDKs are pinned to 2.10.2. Check
`spacetime --version`; install/use that version with the CLI version commands
when necessary. The backend and frontend npm lockfiles must be reviewed together.

After npm ci in both workspaces, copy frontend/.env.example to frontend/.env
if you do not already have a local file. Preserve existing local settings. The
example connects to ws://127.0.0.1:3000 / watchtower-local. Root spacetime.json
points to the existing backend/module and frontend; do not run a new template.

Terminal 1, repository root:

```sh
spacetime start --listen-addr 127.0.0.1:3000
```

Terminal 2, repository root:

```sh
spacetime dev --delete-data=never
```

The dev command publishes the implemented observation module, regenerates
bindings, watches changes and starts Vite. Confirm the publish target is the
local server; no automatic data deletion is configured. Open the Vite URL
(normally http://localhost:5173). Without provider ingestion it should show
Connected / No live observations, never fabricated aircraft. The first publish
records the publisher's identity as administrator. Local database names can be
overridden in the already-ignored spacetime.local.json; update both environment
files to match. Do not use the database test fixtures as demo/live input.

## Authorizing ingestion

Create a separate ingestion identity through local POST /v1/identity. Store its
token only in ignored root .env or the launcher's environment; never capture
response headers in committed files. Run the following with the publishing
CLI identity, replacing the public identity placeholder:

```sh
spacetime call watchtower-local set_ingestion_permission '"<64-character-identity-hex>"' true
```

The administrator can revoke it by calling the same reducer with false. The
administrator itself cannot ingest; browser sessions are read-only. Settings
read SPACETIMEDB_HTTP_URL, SPACETIMEDB_DATABASE and SPACETIMEDB_INGEST_TOKEN
from process environment (the launcher must load/export .env). No secrets are
provisioned or production observations seeded by repository setup.

ADSB/FAA parsers and independent polling are implemented in data/ingestion.py.
AWC polling and optional paid providers remain pending. The observation boundary is
shared.contracts.WeatherRecord / AircraftBatch / SourceFailure and the adapter's
async ingest_weather_record / ingest_aircraft_batch / report_source_failure
methods. Read methods query only the public observation tables, bounded to
1000 rows. Refer to docs/interfaces.md for normalized units and UTC encoding.

## Checks

Activate .venv so Python, Ruff and mypy use the same installed dependencies.
Run `make check` for repository, unit, lint, type and build checks.
Run `make check-spacetime` for the required real database gate. It creates an
isolated temporary local server and unique TEST database, verifies two SDK
clients and Python access, and compares regenerated bindings. It needs no
provider credentials and fails if CLI/server 2.10.2 is unavailable. CI runs both
gates. This verifies the observation and congestion persistence path using a TEST
weather double; operational release gates remain open.

## Eventual live start order

After the remaining implementation and release gates: start/publish the database, provision
identities, generate bindings, start provider ingestion, verify one live
observation and health record, start the Bureau with exactly three agents,
then start the dashboard. Keep controllers read-only until authorization,
versioning, and race checks pass.

AWC/ADSB/FAA are the initial adapters. AeroAPI remains disabled until credentials
and a monetary cap are configured. Fixtures are imported only by tests, and quiet
live conditions must remain quiet in the demonstration.

The full fault/quiet-data rehearsal and two-session demo follow plan section 13.


The [congestion integration guide](congestion-spacetime.md) documents separate service
grants, ingestion startup, immutable evidence, durable deliveries and owner handoffs.
