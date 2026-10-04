# Retained weather and rolling recommendations

Additive interface proposal for the weather, congestion, validation and frontend
workstreams. This repair uses the existing database tables and generated bindings.

`WeatherRecord` accepts optional `metar_evidence` and `taf_evidence` fields using
the existing immutable `MetarEvidence` / `TafEvidence` DTOs. Legacy scalar payloads
remain valid and explicitly partial. Full records retain decoded sky, weather codes,
visibility bounds, forecast groups, conditional probabilities, issue/fetch times
and original source text. METAR identity/times/source and scalar measurements must
agree; forecasts cannot be fetched after the weather commit. Payloads are bounded.
Source text remains untrusted data. No fixtures enter the live launcher.

The weather reducer retains these optional fields in the existing `content_json`.
Equal observation times can refresh by strictly newer fetch time, preserving newer
TAFs and retry idempotency. Each immutable snapshot copies the full bundle and
registers separate forecast evidence IDs; weather findings cite those IDs. Existing
two-element scalar content remains readable. The poller fetches METAR each minute
and TAF every five minutes. Forecast failure does not discard current METAR; it
produces an explicit forecast gap. Lost commit acknowledgements retry the identical
bundle before fetching new data.

`create_snapshot(..., align_window_to_cutoff=True)` opts live cycles into an atomic
server-clock window: start equals the evidence cutoff; the requested duration and
validity length are preserved. Stored request JSON still governs identical-ID
retries. Explicit windows from older callers retain their behavior. This removes
the spurious historical-weather gap caused by network delay between planning a
live window and freezing it. New live snapshots are opened every 60 seconds. If a just-committed weather/feed
fetch is ahead of the database clock, the transaction rejects before publishing
a snapshot. Congestion retries the identical request after a bounded one-second
wait, preserving the strict cutoff rather than changing evidence timestamps.

Informational report text uses recomputed nearby-aircraft counts, retained weather
measurements, weather assessment status and named missing inputs. Missing traffic,
forecast coverage or optional metrics do not withhold an informational recommendation:
the report uses available weather/traffic and gives a concrete refresh step for gaps.
Incomplete results display as "Limited data" while retaining the failed checks.
The unretained previous-cycle traffic delta is no longer published as an observed
metric; it can still trigger internal review. Unsupported supplemental metrics are
excluded from the recommendation and named in its limitations. Mismatched values,
binding, expired evidence, unchecked projections and operational actions still
require reassessment. Missing weather/traffic cannot become an approval. Nearby
aircraft never substitute for airport-associated demand.

The frontend keeps at most five results created within the last ten minutes and
removes each at its actual expiry, whichever is earlier. A newer assessment round
supersedes the earlier round of that case. Clock ticks remove cards without waiting
for database updates; an expired selection cannot retain its conversation or path
preview. The active list is bounded; immutable historical database records remain
available for audit. No destructive database retention change is included.

Verification covers scalar compatibility, full METAR/TAF round trips, failed forecast
reads and lost acknowledgements, server-clock alignment, case refresh, distinct
clear/concern/missing outcomes, clock-only expiry, supersession and list limits.
The isolated database gate exercises all three specialist services with partial,
clear and concerning TEST weather. Public AWC checks exercise the live parsers.
