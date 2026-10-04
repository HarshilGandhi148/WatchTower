# Python subscription bridge

Uses the official pinned TypeScript SDK and generated module bindings. Python
writes via HTTP and receives authenticated notifications; no Python WebSocket SDK.
See [contract](../docs/python-bridge-contract.md).

1. Use Maincloud, or start local SpacetimeDB 2.10.2, and publish `backend/module`.
   Maincloud needs no local `spacetime start`.
2. Export backend origin/database and the database owner's token as
   `SPACETIMEDB_ADMIN_TOKEN`. Run `python -m scripts.mint_tokens`. This creates five
   identities, seeds their allowlist, prints only public identities, and stores
   tokens in a new mode-0600 `secrets/spacetime-tokens.env`. Do not commit it.
   After a partial failure, add `--resume` to reuse saved identities.
   New databases initialize the publisher as admin. Starter-database migrations
   require an owner-restricted administrator bootstrap before provisioning;
   the completed demo bootstrap is deliberately excluded from this reusable module.
3. Load these tokens into the backend environment, along with `.env.example`
   settings. Generate a random `BRIDGE_WEBHOOK_SECRET` of at least 32 characters.
   Use the same secret for bridge and listener. `.env` is not automatically loaded;
   export it or use your process manager's environment-file support.
4. Run `python -m runtime.bridge_listener`, then `make bridge` in another terminal.
5. Start the existing ingestion service. Successful stored weather/feed batches
   automatically append ingestion signals in the same reducer transaction.

`make check-spacetime` includes a real SDK-to-Python pipeline smoke test, authorization,
retry/restart and duplicate delivery. No live credentials or paid APIs are needed.
The test identifies its weather availability handler explicitly; it does not
establish complete weather interpretation or controller advisory publication.

The SQLite receiver receipts and bridge cursor file must persist across restarts.
One bridge process owns a cursor file. Rows are retained for backfill; automatic
stream pruning is intentionally not enabled. Large recovery queries fail loudly
at 8 MiB instead of advancing over truncated data. A later deployment can add
paged recovery/Redis without changing the stream contract.
