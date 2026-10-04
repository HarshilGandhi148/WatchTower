# Shared interfaces

contracts.py has the validated domain envelope and bounded catalog.
orchestration.py defines workflow phases, verdicts and stored-record references.
messages.py provides Fetch.ai wire models, explicitly converting their envelopes
to the immutable domain contract. SDK serializer compatibility is tested.

WeatherAssessment, FlowCandidate and ValidatedAdvisory payload DTOs, settings,
DB HTTP adapter and LLM interface remain pending. The routing messages carry
references to those stored payloads; they do not implement the payload analyses.

Review contract changes with producers and consumers. Do not import agent,
provider, projection or runtime implementations here. Backend secrets are never
frontend configuration. Sender roles are structural data; orchestration checks
the actual peer address after SDK verification.
