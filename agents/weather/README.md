# Weather workstream

Owner: TODO_WEATHER_OWNER. Implement agent.py, assessment.py, parsing.py and
data/awc.py. Keep prompts in prompts/ and tests in tests/. See plan 4.2, 6.2, 11.

Use live AWC evidence; conditional forecasts remain conditional. Clear weather
is valid. Do not infer official capacity/runway closures. Shared envelopes/payloads
belong in shared/contracts.py; do not import congestion or validation internals.

First handoff: a typed WeatherAssessment for a supplied case/snapshot with
status, hazards, named facts+units+evidence, quality, missing inputs, assumptions
and summary. Include parsing/time/unit tests before model-generated explanations.
