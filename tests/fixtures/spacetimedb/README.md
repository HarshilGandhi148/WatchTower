# TEST-only SpacetimeDB observations

Synthetic normalized payloads for the isolated database integration test.
These are not provider responses or live DTW observations. The integration runner
creates a fresh temporary local server/database and never contacts a configured
application database. Wire data_mode=live exercises the production schema only
inside that TEST database; it is not a claim of real provider provenance.
