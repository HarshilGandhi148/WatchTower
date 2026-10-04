# FAA NAS Status fixtures

TEST data, hand-written. `events.xml` follows the AIRPORT_STATUS_INFORMATION layout:
the root, Update_Time, Dtd_File and Airport_Closure_List shapes were observed on the
live feed on 2026-10-04, but this file is not a captured response and its airports,
reasons and times are invented. The ground delay, ground stop and arrival/departure
delay sections follow the documented legacy layout and are NOT yet verified against a
live response. It also contains a prompt-injection-style reason, an entry with no
airport, an unparseable time, a year-boundary time and an unknown section.

Only tests import this directory.
