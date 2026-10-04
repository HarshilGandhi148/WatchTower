# Runtime composition

bureau.py will assemble exactly three distinct uAgent identities. No fourth
agent. scheduler.py and data/ingestion.py are ordinary infrastructure.

Integration owner pins and smoke-tests uAgents/Python compatibility, fills
seeds/allowlisted addresses, and wires the shared adapter. No runtime exists yet.
Poll once per provider interval, prevent overlapping case/version assessments,
bound specialist exchanges and shut down cleanly. Fixtures never enter this layer.
