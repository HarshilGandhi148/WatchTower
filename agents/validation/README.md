# Validation and integration workstream

Owner: TODO_VALIDATION_OWNER. Own this agent, backend/, runtime/ and integration
infrastructure identified in docs/ownership.md. Coordinate shared contract changes.

orchestrator.py implements bounded case routing; protocol.py attaches signed
Fetch.ai handlers to this same agent. runtime/bureau.py assembles the three
prepared identities. See docs/orchestration.md for callbacks and integration.

Check evidence, UTC/units, airport/window/snapshot, freshness, bounded actions,
projection calculations and factual wording. Allow one reassessment; do not
make agent agreement count as independent source corroboration. See plan 6.4–10.

## Domain validation (pure, implemented)

`service.validate_packet(packet, config, previous)` takes an already-loaded
`ValidationPacket` and returns a `ValidationVerdict`, the findings and, on publish,
a `pending_review` `ValidatedAdvisory` (shared/contracts.py). It reads no storage,
clock or network.

| Module | Checks |
| --- | --- |
| packet.py | Private DTOs: evidence records, provisional weather finding, projection packet, findings, demo configuration |
| evidence.py | Candidate is the one CandidateReady named; same case/snapshot/airport; cited records exist, are live, KDTW, same snapshot, not fetched after the cutoff; plan section 10 freshness; projections never cited as observations; window starts at the snapshot and spans 15/30 min |
| calculations.py | Recomputes the projection with `projection.aggregate_flow.compare`; runs `verify_branch`; projected metrics and action parameters must equal the recomputation; hypothetical capacity and instant transfer become limitations |
| policy.py | Issue→action catalog, parameter names/units/integers/bounds, required evidence per advisory type, weather consistency, untrusted text limits, quality never raised |
| lifecycle.py | Advisory ID per issue/resource/window bucket, material fingerprint, duplicate suppression and next version, expiry, demo priority and owner role |
| wording.py | Deterministic templates: numbers only from checked values; tradeoffs regenerated from deltas, including upstream waiting; review phrasing only |

The most severe finding decides the verdict. Integrity problems and "nothing to
present" (no change, duplicate) reject. Missing or stale input is insufficient
data. A weather/candidate contradiction asks for the one reassessment.
Limitations are published, with quality lowered to limited and the limitation
shown. Candidate free text (assumptions, missing inputs) is carried only as
displayed data; it never changes the action, parameters, wording or verdict.

## Agent (agent.py)

`build_agent(AgentConfig.from_env(os.environ))` prepares the validator for
`runtime.bureau.build_bureau`. It registers, on the one validation identity:

- **Coordination:** the partner-built gateway (`protocol.py`, `storage.py`) with
  receipts and restart recovery. `validate` loads the stored packet (`loader.py`: the
  candidate, the snapshot's copied evidence, the weather finding and the recomputed
  projection), then runs `validate_packet`. `persist` records the outcome in
  `ledger.py`.
- **Advisory chat:** a read-only ASI:One chat (`chat.py`, following
  `demo/asi1_status`). Answers come from a code-built FACTS JSON of validated
  advisories and recent internal results. The model only phrases them. With no key, on
  any error, or above 20 calls/minute, it replies with a deterministic summary. Chat
  cannot open cases or change anything.

| Variable | Required | Purpose |
| --- | --- | --- |
| `VALIDATION_AGENT_SEED` | yes | This agent's identity; private, fixed, never reused |
| `WEATHER_AGENT_ADDRESS`, `CONGESTION_AGENT_ADDRESS` | yes | The only senders coordination accepts |
| `SPACETIMEDB_HTTP_URL`, `SPACETIMEDB_DATABASE`, `SPACETIMEDB_VALIDATION_TOKEN` | yes | Read stored records; record receipts (validation role) |
| `ASI_ONE_API_KEY` | no | Model-phrased chat answers; `ASI_ONE_BASE_URL` and `ASI_ONE_MODEL` are optional |

The agent uses an Agentverse mailbox so ASI:One can reach the chat. Grant the
validation database role as in docs/congestion-spacetime.md.

### Run it alone (inspector, mailbox, chat)

From the **repository root**, with the variables above set in the terminal (cmd: no
quotes around values):

```bat
python -m agents.validation.agent
```

Running `python agent.py` inside `agentsalidation` fails with `No module named
'agents'`, because Python then cannot see the repository root. On the first run, open
the **Agent inspector** link from the log, choose **Connect → Mailbox**, then restart.
`VALIDATION_AGENT_PORT` (default 8002) avoids the demo's 8001. Run alone, the chat
works at once. Coordination waits for congestion and weather to send cases, and
database recovery is retried in the log if the database is unreachable.

## Pending

- **Advisory storage:** the database has no advisory table or publish reducer, so
  published advisories live in this process's ledger and log, and are lost on restart.
  The database owner needs to add `publish_validated_advisory` and an `advisories` table
  before the dashboard can show them.
- **Weather:** a full live case needs the weather agent, which is still pending, and a
  `WeatherAssessment` contract. `packet.WeatherFinding` is a provisional reference.

The agent never approves. Server reducers enforce controller assignment,
version/expiry, material changes and idempotent atomic decisions.
