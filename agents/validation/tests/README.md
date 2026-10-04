# Validation tests

Unit tests use synthetic TEST packets from tests/fixtures/validation/ and never
import another agent's implementation.

Covered:

- test_orchestrator.py and test_protocol.py: case routing, rounds, duplicates,
  expiry and signed handlers.
- test_evidence.py: cross-case, snapshot and airport references; missing, stale,
  TEST-mode and post-cutoff evidence; projections cited as observations; windows.
- test_calculations.py: recomputation, tampered series, conservation, projected
  numbers and parameters, unavailable and hypothetical projections.
- test_policy.py: action catalog, parameter bounds, required evidence, weather
  contradictions, untrusted text, quality.
- test_lifecycle.py: dedupe IDs, material fingerprint, versions, expiry, priority.
- test_wording.py and test_service.py: validated-values-only wording, verdict
  mapping, prompt-injected source text, duplicate suppression and supersession.

The cross-owner flow with the real congestion service is in
tests/integration/test_congestion_validation.py. Backend controller
authorization/race tests belong in backend/module/tests/ and tests/decisions/.
