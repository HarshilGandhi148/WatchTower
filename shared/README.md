# Shared interfaces

contracts.py has the validated domain envelope and bounded catalog.
orchestration.py defines workflow phases, verdicts and stored-record references.
messages.py provides Fetch.ai wire models, explicitly converting their envelopes
to the immutable domain contract. SDK serializer compatibility is tested.

Normalized observation DTOs, backend SpacetimeDB settings and the asynchronous
HTTP adapter are implemented and tested. Settings read process environment;
local .env loading is the responsibility of the application launcher.
WeatherAssessment and FlowCandidate domain contracts are implemented. Weather adds
immutable METAR/TAF evidence and snapshot contracts; see
[weather proposal](../docs/weather-interface-proposal.md). Shared review and live
persistence are pending. ValidatedAdvisory and the shared LLM interface remain
pending; weather has its own replaceable explanation client. Routing messages carry
references to stored payloads, not provider feeds.

Review contract changes with producers and consumers. Do not import agent,
provider, projection or runtime implementations here. Backend secrets are never
frontend configuration. Sender roles are structural data; orchestration checks
the actual peer address after SDK verification.
