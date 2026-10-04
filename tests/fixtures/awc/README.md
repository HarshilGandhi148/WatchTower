# AWC TEST fixtures

Hand-written TEST-only KDTW METAR/TAF JSON following AWC's public OpenAPI schema
retrieved October 4, 2026. These are not captured observations or actual forecasts.
No headers or credentials are included. Units: statute miles for visib, knots for
wind, feet for cloud bases, Celsius for temperature, UTC epoch seconds/ISO times.
Tests derive malformed, conditional, amended and boundary cases from these inputs.
Runtime never imports this directory; these fixtures are not a live fallback.
