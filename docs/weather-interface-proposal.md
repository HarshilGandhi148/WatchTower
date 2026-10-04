# Weather component interface proposal

Status: implemented additive proposal; integration and congestion review required
before live wiring. This component does not implement a database schema or the
coordinated operational runtime. A read-only weather inspector launcher is included.

`shared/contracts.py` adds immutable WeatherConditions, MetarEvidence,
TafEvidence/ForecastGroup, WeatherSnapshot, WeatherFact, WeatherHazard,
WeatherImplication and WeatherAssessment. Existing WeatherRecord and weather
request/reply wire shapes are unchanged. New evidence must be retained and listed
in the case's fixed evidence set before weather can use it. All times are aware UTC;
units and visibility bounds are explicit. Snapshot cutoff excludes later fetches.
Calculated wind/gust/direction changes cite both retained reports through
related_evidence_ids; primary and related references must belong to the envelope.
TAF max/min temperatures retain their valid UTC time and Celsius unit.

The service requires async load_snapshot(snapshot_id) and persist_assessment(result)
callbacks. Persistence is idempotent by assessment_id and must reject changed content
for an existing ID. load_snapshot returns retained evidence, never latest-state rows.
The adapter must enforce live provenance; the DTO's live marker is not authentication.
No default in-memory production store is provided.

Optional async trigger callbacks supply a complete snapshot and open a case from a
weather-role envelope. Integration must include traffic and weather evidence and
teach congestion to resolve weather-opened cases. Absent callbacks explicitly disable
autonomous opening. A failed opening is retried with the same case/envelope.

Congestion currently removes assessment IDs for insufficient_data/UNKNOWN replies;
those cases are not submitted to validation. This behavior is preserved. Weather
stores and references insufficient-data findings for audit, but changing downstream
behavior needs a separate reviewed PR.

Delivery sequence: contracts/proposal; AWC and deterministic assessment; model/service.
No dependency, backend schema, generated binding, or frontend changes are required.
Shared-interface review is pending, not implied by passing tests.
