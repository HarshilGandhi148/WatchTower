# Runtime composition

bureau.py implements build_bureau() and build_coordinated_bureau() for exactly
three distinct uAgent identities. The coordinated builder attaches workflow
routing to validation through required validation and persistence callbacks.

See docs/orchestration.md for the handoff and limits. Specialist factories,
provider polling, real domain validation and database callbacks remain pending.
scheduler.py and data/ingestion.py are infrastructure, never extra agents.

Install the hashed runtime requirements and development tools from README.md.
Fill separate seeds when implementing the factories. The Bureau helper itself
does not create identities, load fixtures, register a fourth agent or start a server
until the caller explicitly runs it. It accepts prepared agents with their handlers.
