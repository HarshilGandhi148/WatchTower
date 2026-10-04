# WatchTower

DTW / KDTW flow advisory prototype with exactly three cooperating agents and a
separate React frontend. The architecture and requirements live in
[full_technical_plan.md](full_technical_plan.md).

This repository implements a local SpacetimeDB observation foundation: authorized
weather/aircraft ingestion, source health, a Python HTTP bridge, and React
subscriptions. Provider polling, specialist logic, projections and advisory
decisions remain pending. No synthetic observations are seeded into the app.

Workflow orchestration is implemented in the Validation / Coordinating Agent,
with typed Fetch.ai messages and a three-agent Bureau builder. Specialist
factories and live/backend callbacks still need implementation. See
[orchestration](docs/orchestration.md).

## Four workstreams

| Contributor template | Primary directory | Additional ownership |
| --- | --- | --- |
| Weather owner | agents/weather/ | data/awc.py, weather parsing and prompts |
| Congestion owner | agents/congestion/ | traffic adapters, projection/ |
| Validation / integration owner | agents/validation/ | backend/, runtime/, shared integration infrastructure |
| Frontend owner | frontend/ | components, subscriptions, UI tests |

Shared contracts and end-to-end tests require review from the affected owners.
Usernames are intentionally left as templates in [.github/CODEOWNERS](.github/CODEOWNERS).

## Structure

```text
agents/
  weather/                 # agent, assessments, parsing, prompts, tests
  congestion/              # agent, metrics, coordination, candidates, tests
  validation/              # agent, evidence, policy, lifecycle, wording, tests
data/                      # provider adapters, normalization, health
projection/                # deterministic aggregate flow and assumptions
shared/                    # message contracts, DB adapter, settings, LLM interface
runtime/                   # three-agent Bureau assembly and polling infrastructure
backend/module/
  src/tables/              # live state, evidence, projections, decisions
  src/reducers/            # ingestion, findings, projections, decisions
  src/auth/                # server identity/role checks
  tests/                   # reducer integration tests
frontend/
  src/components/          # dashboard, advisories, projections, history
  src/features/            # live state, advisory decisions, projection comparison
  src/lib/                 # connection, configuration, time formatting
  src/module_bindings/     # generated from the tested database module
tests/
  contracts/               # shared message compatibility
  architecture/            # boundary and configuration guard regression tests
  integration/             # cross-agent/provider/database integration
  decisions/               # authorization, versioning, races
  fixtures/                # TEST-only payloads, never runtime input
  e2e/                     # two-session controller rehearsal
scripts/                   # repository policy and shared developer tooling
docs/                      # ownership, interfaces, setup, quality gates
.github/                   # CI, ownership templates, PR/issue templates
```

## Development setup

Use Python 3.13 (3.12 also supported) and Node 24, matching the version files.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --require-hashes -r requirements-runtime.txt
cp .env.example .env
npm --prefix backend/module ci
npm --prefix frontend ci
cp frontend/.env.example frontend/.env
make check
npm --prefix frontend run dev
```

The unit/build checks do not need API credentials. The local database test uses
its own temporary identities and TEST database. Do not enable a provider or
decision flow until its release gates pass. Each contributor should use a
separate clone/worktree and their own .env.

[Local database setup and eventual start order](docs/development.md) explains
`spacetime dev`, locked runtime dependencies, and binding generation.
[Ownership](docs/ownership.md), [contributing](CONTRIBUTING.md), and
[quality control](docs/quality-control.md) explain how four people work together.

## Checks

- `make policy`: three-agent layout, import boundaries, fixture isolation,
  public frontend configuration.
- `make test`: Python regression tests, including real Fetch.ai message
  serialization, coordinator behavior, and SDK registration with mocked services.
- `make check-python`: policy, tests, Ruff lint/format, strict mypy.
- `make check-js`: backend/frontend lint, format, TypeScript checks, frontend
  tests, backend reducer-rule tests, production frontend build.
- `make check`: the same application checks required by CI.
- `make check-spacetime`: real local database, Python HTTP calls, two SDK clients,
  authorization, rollback, concurrent updates and generated-binding consistency.
- CI also runs the database gate and scans commit history with Gitleaks.

CI configuration takes effect after the files are pushed. CODEOWNERS needs real
usernames, and protected-branch settings must be enabled in GitHub; see
[GitHub enforcement](docs/github-settings.md).
