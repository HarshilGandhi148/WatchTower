# FlightAware AeroAPI fixtures

TEST data, hand-written. Not captured responses: no real flights, idents or account data.
Field names and structure follow the AeroAPI 4.30.0 OpenAPI spec (`scheduled_departures` /
`scheduled_arrivals` lists of BaseFlight, `fa_flight_id`, nullable `origin`/`destination`
with `code_icao`, gate `*_out`/`*_in` and runway `*_off`/`*_on` date-times, `links.next`).
Only a subset of fields is included. Replace or supplement with a captured, header-free
response once a key and budget are configured.

Deliberate cases: a flight taxiing out (pushed back, not airborne), one held past its
scheduled gate-out, a future scheduled-runway flight, a gate-time-only flight with no
destination, a cancelled flight, a wrong-origin record, a record without an id, a
duplicate id, an invalid and a naive timestamp, a non-object entry, a diverted arrival and
a truncated page (`links.next`).

Only tests import this directory.
