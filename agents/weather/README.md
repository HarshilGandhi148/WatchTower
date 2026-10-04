# Weather workstream

Implemented component handoff for KDTW METAR/TAF; database and runtime integration
are pending. See plan 4.2, 6.2, 11 and the
[shared interface proposal](../../docs/weather-interface-proposal.md).

Use live AWC evidence; conditional forecasts remain conditional. Clear weather
is valid. Do not infer official capacity/runway closures. Shared envelopes/payloads
belong in shared/contracts.py; do not import congestion or validation internals.

## Start the server and inspector

From this weather worktree, using Python 3.12 or 3.13:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-runtime.txt
.\.venv\Scripts\python.exe -m agents.weather
```

`python -m agents.weather.agent` also starts the launcher. It listens on
`http://127.0.0.1:8001` and prints the Agentverse inspector URL. Keep the terminal
running and open that printed URL. Stop with Ctrl+C. Use `--port 8002` if 8001 is
occupied. This launcher starts only the weather identity and read-only weather
chat; it does not stand in for the coordinated operational three-agent runtime.

No credentials are required for the local inspector or public AWC reads. Without
`WEATHER_AGENT_SEED`, the launcher generates an ephemeral identity in memory and
the address changes on restart. To keep an identity and optionally enable ASI,
put backend settings in an ignored local `.env`, then explicitly load it:

```powershell
.\.venv\Scripts\python.exe -m agents.weather --env-file .env
```

Environment variables override that file. The parser accepts simple `NAME=value`
lines and optional surrounding quotes; it does not expand shell expressions.
If copying `.env.example`, replace the `DATA_USER_AGENT` placeholder with an
identifying application/team string. No file is loaded implicitly, and no seed
or API key is printed or generated on disk. A missing ASI key uses deterministic
explanations. Fetch failures remain unknown weather.

Add `--mailbox` for Agentverse mailbox connection and use the inspector's connect
flow. Local mode avoids public Almanac registration; mailbox mode uses SDK
registration. Public listing remains disabled. Connecting/publishing externally
and end-to-end mailbox delivery require their own verification.

The inspector endpoints bind to loopback because they expose recent messages.
uAgents maintains an **in-memory inspector message cache**, while disk message
history is disabled. The standalone listener sets the pinned SDK's host constant;
recheck that behavior when upgrading uAgents. Live AWC reads happen on demand,
with METAR/TAF refresh floors of 60/300 seconds and Retry-After respected; no
polling daemon or database ingestion is started. These ephemeral snapshots serve
chat only. Operational snapshot/persistence callbacks still belong to integration.

## Component API

- `data.awc`: scoped URL builders, pure JSON parsers and `AwcClient.fetch(product)`.
  Five-second timeout, bounded response, success-empty distinction and safe errors
  with retry timing. Integration schedules METAR every 60 seconds and TAF every
  five minutes, respects retry timing and stores retained evidence.
- `assessment.assess_weather(request, snapshot, now_utc, rules)`: deterministic
  assessment from fixed evidence. Wind/gust/visibility/ceiling and supported
  weather-code rules are configurable demo thresholds, not operational standards.
- `llm.Explainer`: ASI:One selects/orders supported implication IDs and evidence.
  Code renders all factual sentences, numbers and units. One repair, six-second
  combined budget, bounded cache and deterministic fallback; raw source text never
  enters the model prompt. Missing key disables model calls.
- `service.WeatherService`: required async snapshot loader and idempotent
  assessment persister; concurrent identical requests share one operation, changed
  retries and overlapping rounds are rejected. A persisted reply can be replayed
  after send failure. Pending request state is bounded and pruned after expiry.
- `agent.build_agent(config, dependencies, ...)`: signed, allowlisted weather
  handler. Does not start a server, read .env or write observations.
  `AgentConfig.from_env` reads WEATHER_AGENT_SEED, CONGESTION_AGENT_ADDRESS,
  VALIDATION_AGENT_ADDRESS and existing ASI settings.
- `triggers.ChangeDetector`: optional injected current-snapshot/case-opening
  callbacks. Detects threshold crossings, hazard onset/clearance, relevant
  amendments, wind changes and quality changes. Ignores unchanged fetches, opens
  at most once/minute and retries failed openings using the same envelope.

Create `WeatherDependencies(load_snapshot, persist_assessment)` and pass it to
`build_agent`. The persister must store the typed result with weather-only authority
before its assessment ID is sent. Snapshot loading must retain all weather evidence
listed in the case and enforce live provenance. Latest-state METAR rows alone cannot
implement the reviewed shared snapshot contract.

## Weather chat (adapted from `demo/asi1_status`)

`chat.py` replaces the example's "sun" subject with a read-only KDTW weather
report. Attach it to `build_agent(..., chat=chat)` so it uses the **same weather
identity**. It acknowledges signed SDK chat messages, returns TextContent and
EndSessionContent, and keeps UTC timestamps. It neither persists assessments nor
opens cases. Questions choose the fixed next-15-minute report; this is not a
general-purpose conversational forecast or aircraft-instruction assistant.

Integration supplies an async `load_current_weather_snapshot()` returning retained
live evidence. It must not load test fixtures. For example, in the runtime's wiring:

```python
import os
from dataclasses import replace

from agents.weather.agent import AgentConfig, build_agent
from agents.weather.chat import WeatherChat
from agents.weather.llm import AsiModel, Explainer

config = AgentConfig.from_env(os.environ)
explainer = Explainer(AsiModel(config.asi) if config.asi else None)
chat = WeatherChat(load_current_weather_snapshot, explainer=explainer)
agent = build_agent(replace(config, mailbox=True), dependencies, chat=chat)
# Runtime owns startup; dependencies and the reader are integration callbacks.
```

Use existing `WEATHER_AGENT_SEED` and `ASI_ONE_API_KEY` backend settings, not the
example's `AGENT_SEED`/`ASI1_API_KEY`. Keep `.env` local and ignored; use empty
placeholders in `.env.example`, or inject secrets through the deployment secret
store. No keys or seeds belong in this file, a browser, logs or Git. Agent listing,
inspector and manifest publication remain disabled by default. Runtime owns
Agentverse mailbox/endpoint configuration and any reviewed public publication.

No new OpenAI SDK dependency is needed: the existing asynchronous ASI wrapper
uses the configured chat-completions endpoint. ASI only orders supported
explanations; code renders facts and numbers. User questions and raw provider text
never enter its prompt. Missing keys use deterministic explanations. Reads/model
work are bounded to 12 seconds and shared for one minute, including failures;
responses retain their assessment timestamp. Off-topic/oversized requests do not
read snapshots. The chat service stores no conversation history or user identifiers;
the optional inspector's SDK message cache is described above.

Chat unit tests cover concurrent requests, scope, injection, missing/stale/future
evidence, redacted failure caching, SDK round trips and signed protocol delivery
without persistence. Mailbox connectivity and credentialed ASI smoke testing
remain release checks; adding this protocol does not establish live wiring.

## Evidence semantics and limitations

Missing sky is not clear sky. Variable wind has no fixed direction. Visibility
keeps exact/greater-than/less-than bounds; ceiling is lowest BKN/OVC or vertical
visibility. UTC issue/observation/fetch times stay separate. METAR older than 90
minutes cannot support current clear conditions. Observed facts apply at assessment
time; TAF must cover the future window independently. Conditional and BECMG
transition findings retain their basis and lower evidence quality. No regional
hazard, surface-condition, official capacity or runway-closure claim is produced.

Weather-opened cases need congestion snapshot resolution before live use.
The local launcher now polls METAR and TAF, loads immutable stored snapshots,
and saves WeatherFinding before replying (see runtime/README.md). Congestion keeps
insufficient-data finding IDs, so those cases reach validation and explain their
missing inputs on MissionControl. Full retained METAR/TAF records preserve weather codes,
cloud details and forecast groups; unavailable or legacy scalar inputs remain unknown.
Live SDK delivery and persisted reports were verified in the
configured database; distributed mailbox operation remains a separate handoff.
Request deduplication here is process-local; the supplied store must enforce
idempotency after restart and reject conflicting content for an existing finding ID.

## Verification

Local server smoke: passed on 2026-10-04. `python -m agents.weather` started
the SDK listener on loopback port 8001 and printed its inspector URL. Windows
HTTP requests to `/agent_info` and `/messages` succeeded. The smoke used an
ephemeral seed held only in memory. Remote mailbox connection and credentialed
ASI calls were not tested by this startup check.

Run `python -m unittest discover -s agents/weather/tests -v`, then `make check`.
Unit tests use explicit TEST fixtures and injected stores/transports. Credentialed
ASI smoke testing and live database/three-agent gates are separate and pending.

Public AWC smoke: passed on 2026-10-04 at 08:12 UTC. A one-shot `AwcClient.fetch`
parsed one live KDTW METAR and one TAF. It used no fixtures or provider credentials
and wrote no observation/finding records. This establishes current response
compatibility, not continuous availability or end-to-end integration.

Repository verification after rebasing onto current main: `make check` passed
with 518 Python tests (61 weather), 73 backend tests, 99 frontend tests and the
production build, plus repository policy, Ruff formatting/lint and strict mypy.
The existing
MapLibre bundle-size warning remains. No database schema/bindings were changed;
the real database and three-agent operational gates remain pending.
