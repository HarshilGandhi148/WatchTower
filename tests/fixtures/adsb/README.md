# ADSB.lol fixtures

TEST data. `point_response.json` is hand-written to follow the documented shape of
the v2 point response; it is not a captured provider response and holds no real
aircraft. It deliberately includes a duplicate hex, a ground aircraft, a stale
position, a prompt-injection-style callsign, a missing longitude, an implausible
speed, a non-ICAO address, an invalid hex and a non-object record.

Only tests import this directory. Replace or supplement it with a captured,
header-free response once live access is verified.
