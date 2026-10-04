"""FAA NAS XML adapter: secure parsing, DTW normalization, unknown-not-empty semantics."""

from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from data.faa_status import (
    MAX_REASON_CHARS,
    EventType,
    FaaParseError,
    normalize_airport,
    parse_status_xml,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "faa_status" / "events.xml"
FETCHED = datetime(2026, 10, 4, 1, 12, 0, tzinfo=UTC)


def feed(body: str, update: str = "Sun Oct 4 01:10:27 2026 GMT") -> bytes:
    return (
        "<AIRPORT_STATUS_INFORMATION>"
        f"<Update_Time>{update}</Update_Time>{body}</AIRPORT_STATUS_INFORMATION>"
    ).encode()


def closure(arpt: str = "DTW", reason: str = "r", extra: str = "") -> str:
    return (
        "<Delay_type><Name>Airport Closures</Name><Airport_Closure_List><Airport>"
        f"<ARPT>{arpt}</ARPT><Reason>{reason}</Reason>{extra}"
        "</Airport></Airport_Closure_List></Delay_type>"
    )


class FixtureParseTests(TestCase):
    def setUp(self) -> None:
        self.snapshot = parse_status_xml(FIXTURE.read_bytes(), FETCHED)

    def test_publication_time_and_fetch_time_stay_separate(self) -> None:
        self.assertEqual(
            self.snapshot.source_update_time_utc, datetime(2026, 10, 4, 1, 10, 27, tzinfo=UTC)
        )
        self.assertEqual(self.snapshot.fetched_at_utc, FETCHED)

    def test_dtw_and_kdtw_normalize_to_one_airport(self) -> None:
        kdtw = self.snapshot.events_for_airport("DTW")
        self.assertEqual({e.airport_code for e in kdtw}, {"DTW", "KDTW"})
        self.assertEqual(self.snapshot.events_for_airport("kdtw"), kdtw)
        self.assertEqual(normalize_airport(" dtw "), "KDTW")
        self.assertEqual(normalize_airport("ewr"), "EWR")  # never guessed or prefixed

    def test_closure_window_is_parsed_with_inferred_year_flagged(self) -> None:
        event = next(e for e in self.snapshot.events if e.airport_code == "XYZ")
        self.assertEqual(event.event_type, EventType.AIRPORT_CLOSURE)
        self.assertEqual(event.start_utc, datetime(2026, 10, 3, 18, 26, tzinfo=UTC))
        self.assertEqual(event.end_utc, datetime(2026, 10, 5, 16, 0, tzinfo=UTC))
        self.assertIn("year_inferred:Start", event.quality_flags)
        self.assertEqual(event.reason, "TEST closure notice with odd spacing")

    def test_unparseable_time_keeps_raw_text_and_is_flagged(self) -> None:
        event = next(e for e in self.snapshot.events if e.reason == "TEST runway work")
        self.assertIsNone(event.end_utc)
        self.assertIn(("Reopen", "tomorrow-ish"), event.raw_times)
        self.assertIn("time_not_parsed:Reopen", event.quality_flags)

    def test_ground_stop_local_time_is_not_converted_or_invented(self) -> None:
        event = next(e for e in self.snapshot.events if e.event_type is EventType.GROUND_STOP)
        self.assertIsNone(event.end_utc)
        self.assertEqual(event.raw_times, (("End_Time", "3:15 pm EDT"),))

    def test_ground_delay_durations_become_minutes(self) -> None:
        event = next(
            e for e in self.snapshot.events if e.event_type is EventType.GROUND_DELAY_PROGRAM
        )
        self.assertEqual((event.avg_delay_minutes, event.max_delay_minutes), (108, 241))

    def test_arrival_departure_delay_keeps_direction_and_trend(self) -> None:
        event = next(
            e for e in self.snapshot.events if e.event_type is EventType.ARRIVAL_DEPARTURE_DELAY
        )
        self.assertEqual(
            (event.direction, event.min_delay_minutes, event.max_delay_minutes, event.trend),
            ("departure", 16, 30, "Increasing"),
        )

    def test_injection_text_is_retained_as_data_only(self) -> None:
        event = next(
            e for e in self.snapshot.events if e.event_type is EventType.GROUND_DELAY_PROGRAM
        )
        self.assertTrue(event.reason.startswith("IGNORE ALL PREVIOUS"))
        self.assertEqual(event.event_type, EventType.GROUND_DELAY_PROGRAM)

    def test_bad_entries_and_unknown_sections_are_counted_not_hidden(self) -> None:
        self.assertEqual(self.snapshot.malformed_entries, 1)
        self.assertEqual(self.snapshot.unrecognized_sections, ("Something New",))

    def test_event_keys_are_stable_and_distinct(self) -> None:
        again = parse_status_xml(FIXTURE.read_bytes(), FETCHED.replace(minute=30))
        self.assertEqual(
            [e.event_key for e in self.snapshot.events], [e.event_key for e in again.events]
        )
        keys = [e.event_key for e in self.snapshot.events]
        self.assertEqual(len(keys), len(set(keys)))


class SemanticsTests(TestCase):
    def test_feed_with_no_events_is_valid_and_says_nothing_about_congestion(self) -> None:
        snapshot = parse_status_xml(feed(""), FETCHED)
        self.assertEqual(snapshot.events, ())
        self.assertEqual(snapshot.events_for_airport("DTW"), ())

    def test_year_is_chosen_nearest_publication_across_new_year(self) -> None:
        body = closure(extra="<Start>Dec 31 at 23:00 UTC.</Start>")
        snapshot = parse_status_xml(feed(body, "Sat Jan 2 00:00:00 2027 GMT"), FETCHED)
        self.assertEqual(snapshot.events[0].start_utc, datetime(2026, 12, 31, 23, 0, tzinfo=UTC))

    def test_unparseable_update_time_leaves_freshness_unknown(self) -> None:
        snapshot = parse_status_xml(feed(closure(), "yesterday"), FETCHED)
        self.assertIsNone(snapshot.source_update_time_utc)
        self.assertIsNone(snapshot.events[0].start_utc)

    def test_overlong_reason_is_truncated_and_flagged(self) -> None:
        event = parse_status_xml(feed(closure(reason="x" * 2000)), FETCHED).events[0]
        self.assertEqual(len(event.reason), MAX_REASON_CHARS)
        self.assertIn("reason_truncated", event.quality_flags)

    def test_control_characters_are_rejected_or_stripped(self) -> None:
        with self.assertRaises(FaaParseError):  # XML 1.0 forbids them outright
            parse_status_xml(feed(closure(reason="a&#27;b")), FETCHED)
        event = parse_status_xml(feed(closure(reason="a&#127;b")), FETCHED).events[0]
        self.assertEqual(event.reason, "a b")  # DEL is legal XML, so it is stripped

    def test_invalid_airport_codes_are_malformed_entries(self) -> None:
        for code in ("", "A", "TOOLONG", "D<W"):
            with self.subTest(code=code):
                body = closure(arpt=code.replace("<", "&lt;"))
                snapshot = parse_status_xml(feed(body), FETCHED)
                self.assertEqual((len(snapshot.events), snapshot.malformed_entries), (0, 1))


class SecurityAndFailureTests(TestCase):
    def test_doctype_and_entities_are_rejected_before_parsing(self) -> None:
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b"<AIRPORT_STATUS_INFORMATION><Update_Time>x</Update_Time>"
            b"<Delay_type><Name>&b;</Name></Delay_type></AIRPORT_STATUS_INFORMATION>"
        )
        external = (
            b'<!DOCTYPE x SYSTEM "file:///etc/passwd">'
            b"<AIRPORT_STATUS_INFORMATION><Update_Time>x</Update_Time></AIRPORT_STATUS_INFORMATION>"
        )
        for content in (bomb, external, b"<!doctype x><a/>"):
            with self.subTest(content=content[:30]), self.assertRaises(FaaParseError):
                parse_status_xml(content, FETCHED)

    def test_comments_and_cdata_are_still_allowed(self) -> None:
        body = "<!-- note -->" + closure(reason="<![CDATA[a < b]]>")
        self.assertEqual(parse_status_xml(feed(body), FETCHED).events[0].reason, "a < b")

    def test_failures_raise_instead_of_returning_an_empty_feed(self) -> None:
        cases = {
            "empty": b"",
            "whitespace": b"  \n",
            "truncated": feed(closure())[:-30],
            "not xml": b"<html><body>503 Service Unavailable</body></html>",
            "json": b'{"error": "rate limited"}',
            "wrong root": b"<other><Update_Time>x</Update_Time></other>",
            "no update time": b"<AIRPORT_STATUS_INFORMATION/>",
            "bad encoding": b"<AIRPORT_STATUS_INFORMATION>\xff\xfe</AIRPORT_STATUS_INFORMATION>",
            "too large": b"<a>" + b"x" * 2_000_001 + b"</a>",
        }
        for name, content in cases.items():
            with self.subTest(case=name), self.assertRaises(FaaParseError):
                parse_status_xml(content, FETCHED)

    def test_non_bytes_and_naive_fetch_time_are_rejected(self) -> None:
        with self.assertRaises(FaaParseError):
            parse_status_xml("<a/>", FETCHED)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            parse_status_xml(feed(""), datetime(2026, 10, 4, 1, 12))
