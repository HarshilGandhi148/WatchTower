# Validation TEST fixtures

Synthetic packets for the Validation agent's unit tests. Status: **TEST**.

- Hand-written values; no provider response was captured or replayed.
- The projection is computed with the real deterministic model
  (`projection/aggregate_flow.py`) from synthetic inputs, so its numbers are
  internally consistent: TEST capacity 30 aircraft/hour, backlog 6 upstream plus 4
  queued, and one departure per minute.
- Evidence records are labelled `data_mode="live"` only so that the validator's
  live-input path can be exercised. They describe no real observation and must
  never be imported by runtime code. The repository policy rejects runtime imports
  of `tests/fixtures`.
