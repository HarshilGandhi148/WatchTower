# Shared backend

Owned by validation/integration. module/ contains the TypeScript SpacetimeDB
observation foundation; it is independent of frontend/ and the Python agents.

Unit checks need no host or token. The explicit integration gate creates its own
local TEST server and identities. Application writes require a separately
authorized ingestion identity. See module/README.md and docs/development.md.
