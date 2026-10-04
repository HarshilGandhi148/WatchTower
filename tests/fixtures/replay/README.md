# TEST replay provenance

`generate.py` constructs synthetic ADS-B and weather observations and runs the
real CongestionService and validator evaluator. Weather messaging and delivery
are explicit test doubles. The correction case deliberately changes one metric
in round one; the validator rejects it and evaluates a distinct corrected candidate.
The outage case has failed ADS-B health and unknown traffic, never zero demand.
Captures are written only to the separate frontend TEST entrypoint and are never
loaded into a live database. Original UTC timestamps are preserved.
