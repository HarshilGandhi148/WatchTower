# Shared interfaces

contracts.py has the initial validated envelope and enum catalog. Payload DTOs,
serialization, settings, DB HTTP adapter and LLM interface remain pending.

Review contract changes with producers and consumers. Do not import agent,
provider, projection or runtime implementations here. Backend secrets are never
frontend configuration. Actual sender addresses must be checked by runtime;
sender_agent alone is not authentication.
