# ASI:One DTW traffic status (demo agent)

A read-only chat agent that answers questions about current traffic around Detroit Metro
(KDTW) from ASI:One / Agentverse. It is a hackathon demo, **not** one of WatchTower's three
agents (weather, congestion, validation): it never opens cases, creates advisories, or
writes anything. It only reads public data and the optional FlightAware schedule.

## What it does

For each chat message it:

1. Fetches live ADS-B positions (ADSB.lol, 40 nm around KDTW) and the FAA NAS Status feed,
   using `agents/congestion/feeds.py` and `agents/congestion/metrics.py`.
2. Optionally fetches scheduled KDTW departures for the next hour from FlightAware AeroAPI
   (cached for 10 minutes, at most 20 paid calls per day).
3. Builds a `FACTS` JSON (counts, data quality, KDTW FAA events, feed errors, data time) and
   asks ASI:One to answer **only** from those facts.

Numbers come from code, not the model. Missing or failed feeds are reported as unavailable,
never as zero. Aircraft near KDTW are not assumed to be KDTW arrivals or departures.

## Setup

From the repo root, in a virtual environment with the project's runtime lock installed:

```bat
pip install --require-hashes -r requirements-runtime.txt
pip install -r demo\asi1_status\requirements.txt
```

## Configuration

Set these in your terminal or in the git-ignored `.env`. **Never put values in code or commits.**

| Variable | Required | Purpose |
|---|---|---|
| `ASI_ONE_API_KEY` | yes | ASI:One LLM access (https://asi1.ai/developer) |
| `ASI1_HELLO_SEED` | yes | This demo agent's identity. Keep it private and fixed; never reuse a WatchTower agent seed |
| `DATA_USER_AGENT` | yes | Identifying User-Agent for provider requests, e.g. `WatchTower-DTW/0.1 (team contact)` |
| `FLIGHTAWARE_API_KEY` | no | Enables the scheduled-departures fact; without it that fact is unavailable |

In cmd, set values **without quotes** (cmd keeps quote characters as part of the value,
which changes the seed and therefore the agent address).

## Run

```bat
set PYTHONPATH=.
python demo\asi1_status\agent.py
```

First run only: open the **Agent inspector** link printed in the log, choose
**Connect → Mailbox**, then restart the agent. The `Agent mailbox not found` warning should
disappear. The agent only answers while this process is running. Warnings about Almanac
contract funds are expected and can be ignored.

## Testing

Ask from ASI:One, for example:

- "How busy is DTW right now?" — real counts plus data time and the nearby-aircraft caveat.
- "Any FAA delays or closures at DTW?" — a listed KDTW event, "none listed", or unavailable.
- "How many departures are scheduled in the next hour?" — FlightAware figure, or unavailable.

Each request logs a `facts:` line; the reply's numbers should match it exactly.

## Cost and safety

- The agent is **publicly listed** and anyone can message it. AeroAPI calls are therefore
  cached (`CACHE_SECONDS = 600`) and hard-capped (`MAX_CALLS_PER_DAY = 20`). Set the cap from
  the real per-result-set price shown in the AeroAPI portal.
- AeroAPI endpoint/parameter names were written from the documentation and should be
  confirmed in the portal; the first real call verifies them.
- Text in feeds and chat messages is treated as data, not instructions.
- If a key or seed is ever exposed, revoke or replace it (a new seed means a new address and
  a new mailbox connection).