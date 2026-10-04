# WatchTower

**Three AI agents. Real flight data. Clear suggestions for air traffic controllers.**

WatchTower is being built to assist air traffic controllers across airports. It uses three AI agents to monitor weather and flight activity, automatically create suggestions, and check those suggestions before showing them to a controller.

**Detroit Metropolitan Airport (DTW) is our current demo location.** The same three-agent approach is intended for demos at other airports, using the flight data, weather, and local settings for each location.

The idea is simple: give controllers a clearer picture of what is happening and what may need their attention. Each suggestion explains the problem, the data behind it, and the possible effects of a response. **The controller makes the final decision.**

## How the three agents work together

### 1. Weather Agent — watches the conditions

The Weather Agent reads real aviation weather reports and forecasts. It looks for changes such as strong winds, low visibility, or approaching storms that could affect airport traffic. It explains what is happening, when it matters, and whether the information describes current conditions or a forecast.

### 2. Flight Congestion Agent — watches the traffic

The Flight Congestion Agent looks at aircraft positions, available flight schedules, and official delay notices. It works with the Weather Agent to identify growing congestion and suggest a response for the controller to consider.

For example, it could suggest reviewing the departure flow during a busy period affected by poor weather. When enough data is available, it also compares how queues and waiting times might change under the suggested response.

### 3. Validation Agent — checks the suggestion

Before a suggestion reaches the controller, the Validation Agent checks that the supporting data is recent, the weather and traffic findings agree, and the explanation and calculations are supported by the evidence. It asks for a correction or holds back a suggestion when information is missing or conflicting.

Its job is to check the suggestion, not approve it. Approval belongs to the controller.

**The intended flow:** real flight and weather data → weather and traffic analysis → automatic suggestion → validation → controller review.

## What a controller sees

WatchTower brings the airport map, weather, traffic, and suggestions into one dashboard. A controller can:

- **Understand the situation:** see what changed and why it needs attention.
- **Check the evidence:** review the source information and when it was updated.
- **Preview a response:** compare the current approach with a suggested change over the next 15 minutes, where the data supports it.
- **Approve, edit, or ignore:** choose how to respond and keep a record of the decision.

For example, imagine a busy departure period while visibility is expected to worsen. The Weather Agent identifies the forecast change. The Congestion Agent checks whether flight demand makes that change relevant and prepares a suggestion to review the departure flow. The Validation Agent checks the evidence before the controller sees it.

This is an example of the intended workflow, not a report of current airport conditions. Approving a suggestion records the decision and selects its preview; it does not send instructions to aircraft. Actual observations stay separate from the “what if” view.

## Why it matters

Weather and congestion have real consequences for people trying to get somewhere. The FAA reports that weather caused **about 74% of system-impacting air traffic delays longer than 15 minutes between June 2017 and May 2023**. It also explains that weather creates greater delays when airports are already close to capacity. [FAA: Weather Delay FAQ](https://www.faa.gov/nextgen/programs/weather/faq).

Better coordination can make a difference. In a separate project at Charlotte Douglas International Airport, NASA reported that its airport traffic-management tools helped save **more than one million gallons of fuel and 916 hours of delay** during the first four years of testing. One approach was to let aircraft wait at the gate with engines off instead of joining a runway queue too early. [NASA: An Electronic Traffic Monitor for Airports](https://spinoff.nasa.gov/Electronic-Traffic-Monitor-for-Airports).

WatchTower aims to make useful information easier for controllers to act on. If that leads to better coordination, passengers could face less unnecessary waiting, airport teams could plan more effectively, and aircraft could spend less time burning fuel in queues. Those are goals to evaluate: **WatchTower has not yet demonstrated delay or fuel savings.** The NASA results show why this problem is worth working on.

## Where the project stands

WatchTower is a prototype. The dashboard, a local backend for weather and aircraft observations, and communication tools for the three agents are implemented. You can explore the review experience in a separate sample-data demo.

ADS-B/FAA polling, full AWC METAR/TAF ingestion, immutable snapshots, durable weather findings, congestion candidates, and validation reports/receipts are implemented. The three specialists can run together with `python -m runtime.live --env-file .env --user-agent 'WatchTower/0.1 (MHacks DTW demo)'`; see [runtime setup](runtime/README.md). Cases refresh every minute and recommendations expire automatically. Unavailable weather and forecasts remain explicit gaps. Operational advisory publication and backend controller decisions remain unfinished. The separate demo uses sample data and illustrative aircraft paths. WatchTower is not a certified air traffic control system.

See the [technical plan](full_technical_plan.md) and [remaining release checks](docs/quality-control.md) for details. See the [congestion integration guide](docs/congestion-spacetime.md) for the persistence flow, service roles, and TEST coverage.

## How to use it

### Try the demo

You need **Node.js 24**. From the project folder, run:

```sh
npm --prefix frontend ci
npm --prefix frontend run dev:test
```

Open **http://127.0.0.1:5174** to explore the Detroit demo. No API keys or database are needed. The map needs an internet connection and a browser that supports WebGL.

1. Choose a **Replay scenario** and watch Weather, Congestion, and Validation activity appear.
2. Select a recommendation to open its agent conversation and inspect the cited evidence.
3. Try the correction scenario to see an earlier recommendation held while reassessment runs.
4. When a supported report is available, select **Accept for TEST path preview** to play illustrative routes.

Use **Pause**, **Play**, and **Restart** beside the scenario selector to control replay. Acceptance is a local preview selection; it does not record a backend controller decision. The current MissionControl page has no History page or controller edit/ignore workflow. Everything in this mode uses sample data.

### Run the local backend and dashboard

You need **Python 3.13** (3.12 also works), **Node.js 24**, and **SpacetimeDB 2.10.2**. From the project folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --require-hashes -r requirements-runtime.txt
npm --prefix backend/module ci
npm --prefix frontend ci
cp -n .env.example .env
cp -n frontend/.env.example frontend/.env
```

Start the database in one terminal:

```sh
spacetime start --listen-addr 127.0.0.1:3000
```

In another terminal, from the project folder:

```sh
spacetime dev watchtower-local --server http://127.0.0.1:3000 --delete-data=never
```

The checked-in database configuration points to Maincloud, so specify the local target explicitly as above. Set both environment files to the same intended database and server before starting; preserve any existing credentials. Open the dashboard URL printed in the terminal, usually **http://127.0.0.1:5173**. Before ingestion is started, a new local database has no observations. MissionControl displays observations and stored informational reports; the complete controller-decision workflow remains pending.

Keep API keys and tokens out of Git and browser settings. For data access, configuration, and the remaining startup steps, follow the [development guide](docs/development.md).

To check the project after making changes:

```sh
source .venv/bin/activate
make check
```

The separate `make check-spacetime` command checks the local database integration. Passing these checks does not mean the project is ready for operational use. Contributor responsibilities are in [ownership](docs/ownership.md) and [contributing](CONTRIBUTING.md).

## Tech stack

| Technology | What we use it for |
| --- | --- |
| **Python 3.13 / 3.12** | AI agent code, data processing, and traffic calculations |
| **Fetch.ai uAgents 0.26.0** | Communication and coordination between the three AI agents |
| **SpacetimeDB 2.10.2** | Shared data storage and real-time dashboard updates |
| **React 19, TypeScript, and Vite 7** | The controller dashboard |
| **MapLibre GL JS 6.12.0 and OpenFreeMap** | The airport map |
| **ASI:One / replaceable AI model interface** | Planned weather explanations and suggestion wording |
| **NOAA Aviation Weather Center** | Planned aviation weather reports and forecasts |
| **ADSB.lol** | Planned real aircraft positions near the selected airport; nearby aircraft are not necessarily arriving or departing there |
| **FAA NAS Status** | Planned official airport delay and restriction notices |
| **FlightAware AeroAPI (optional)** | Planned flight schedules and status; requires paid access and a budget |
| **NWS / OpenSky (optional)** | Additional weather information or aircraft-position sources |
| **unittest, Vitest, and Testing Library** | Automated tests |
| **Ruff, mypy, ESLint, Prettier, and Gitleaks** | Code quality, formatting, and secret checks |
