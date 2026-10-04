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

The backend workspace reserves a TypeScript module using spacetimedb/server;
the frontend SDK must match the tested database release before bindings are
generated. Local typechecking is not a deployed-host compatibility test.

## When SpacetimeDB access arrives

1. Select and record a tested 2.x CLI, SDK/module, and server combination.
2. Fill SPACETIMEDB_HTTP_URL, SPACETIMEDB_WS_URL, SPACETIMEDB_DATABASE and
   the four separate service tokens in root .env.
3. Implement tables, role checks, and reducers. Test with unassigned and wrong-role
   identities; test simultaneous/idempotent controller requests.
4. Implement shared/spacetimedb_adapter.py using documented HTTP reducer/snapshot
   calls. Do not build a custom Python WebSocket client.
5. Generate bindings from the exact schema into frontend/src/module_bindings/;
   replace its placeholder README with the recorded generation provenance.
6. Set only public VITE_SPACETIMEDB_WS_URL, VITE_SPACETIMEDB_DATABASE,
   and VITE_DISPLAY_TIMEZONE in frontend/.env. Controller tokens come from
   each user's session flow, not from VITE variables.

Do not publish the empty module as an implementation of the planned backend.

## Eventual live start order

After implementation and release gates: start/publish the database, provision
identities, generate bindings, start provider ingestion, verify one live
observation and health record, start the Bureau with exactly three agents,
then start the dashboard. Keep controllers read-only until authorization,
versioning, and race checks pass.

AWC/ADSB/FAA are the initial adapters. AeroAPI remains disabled until credentials
and a monetary cap are configured. Fixtures are imported only by tests, and quiet
live conditions must remain quiet in the demonstration.

The full fault/quiet-data rehearsal and two-session demo follow plan section 13.
