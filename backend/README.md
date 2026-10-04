# Shared backend

Owned by validation/integration. module/ reserves the TypeScript SpacetimeDB
database module; it is independent of frontend/ and the Python agents.

No host, API token or database is required for scaffold checks. Runtime access
is configured later through root .env; the frontend gets only public connection
values. See module/README.md and docs/interfaces.md.
