"""FAA NAS Status XML adapter: secure parsing and normalization of published events.

Pure functions: no network, clock, database or agent access. The poller passes the
response bytes and its own successful-fetch time.

Semantics (plan 4.4): an absent KDTW event means "no KDTW event in this feed", not
"KDTW is uncongested". A failed, malformed or format-drifted response raises, so it is
recorded as unknown rather than as an empty feed. Publication time (``Update_Time``)
and fetch time stay separate. Reason text is untrusted: control characters are
stripped and length is capped, and it is never interpreted. Nothing here attributes
an event to a specific flight; regional delay averages are not departure times.

Parsing security: the document is size-limited and any DOCTYPE/ENTITY or other
``<!`` declaration is rejected before parsing, so external entities and entity
expansion are impossible. No third-party parser is needed.

Verification status: the AIRPORT_STATUS_INFORMATION root, Update_Time, Dtd_File and the
Airport_Closure_List section were observed live on 2026-10-04. Ground stop, ground
delay and arrival/departure delay sections follow the documented legacy layout and have
not yet been observed live; unknown sections are counted, never silently ignored.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

SOURCE_ID = "faa-nas-status"
MAX_RESPONSE_BYTES = 2_000_000
MAX_REASON_CHARS = 500
KDTW = "KDTW"

_FORBIDDEN_DECLARATION = re.compile(rb"<!(?!--|\[CDATA\[)")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_UPDATE_TIME = re.compile(
    r"^[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\d{4}) (?:GMT|UTC)$"
)
_YEARLESS_TIME = re.compile(r"^([A-Za-z]{3})[a-z]* (\d{1,2}) at (\d{1,2}):(\d{2}) UTC\.?$")
_DURATION = re.compile(r"^(?:(\d+) hours?)?(?: and )?(?:(\d+) minutes?)?$")


class FaaParseError(ValueError):
    """The response is unusable; callers must record unknown, not an empty event list."""


class EventType(StrEnum):
    GROUND_DELAY_PROGRAM = "ground_delay_program"
    GROUND_STOP = "ground_stop"
    AIRPORT_CLOSURE = "airport_closure"
    ARRIVAL_DEPARTURE_DELAY = "arrival_departure_delay"


@dataclass(frozen=True, slots=True)
class FaaEvent:
    """One published NAS event. Unparsed times keep their raw text and a flag."""

    event_key: str
    event_type: EventType
    airport_code: str
    reason: str
    direction: str | None
    start_utc: datetime | None
    end_utc: datetime | None
    min_delay_minutes: int | None
    avg_delay_minutes: int | None
    max_delay_minutes: int | None
    trend: str | None
    raw_times: tuple[tuple[str, str], ...]
    quality_flags: tuple[str, ...]

    @property
    def airport_icao_normalized(self) -> str:
        return normalize_airport(self.airport_code)


@dataclass(frozen=True, slots=True)
class FaaStatusSnapshot:
    source_id: str
    source_update_time_utc: datetime | None
    fetched_at_utc: datetime
    events: tuple[FaaEvent, ...]
    unrecognized_sections: tuple[str, ...]
    malformed_entries: int

    def events_for_airport(self, code: str) -> tuple[FaaEvent, ...]:
        """Events for one airport. An empty result does not mean the airport is uncongested."""
        wanted = normalize_airport(code)
        return tuple(e for e in self.events if e.airport_icao_normalized == wanted)


def normalize_airport(code: str) -> str:
    """Map DTW and KDTW to one ID; other codes are only upper-cased, never guessed."""
    cleaned = code.strip().upper()
    return KDTW if cleaned in ("DTW", KDTW) else cleaned


def parse_status_xml(content: bytes, fetched_at_utc: datetime) -> FaaStatusSnapshot:
    """Securely parse and normalize a NAS status response."""
    if fetched_at_utc.utcoffset() != timedelta(0):
        raise ValueError("fetched_at_utc must be an aware UTC datetime")
    if not isinstance(content, bytes):
        raise FaaParseError("response content must be bytes")
    if not content.strip():
        raise FaaParseError("empty response")
    if len(content) > MAX_RESPONSE_BYTES:
        raise FaaParseError("response exceeds the size limit")
    if _FORBIDDEN_DECLARATION.search(content):
        raise FaaParseError("DOCTYPE/ENTITY declarations are not accepted")
    try:
        content.decode("utf-8")
        root = ET.fromstring(content)
    except (UnicodeDecodeError, ET.ParseError) as error:
        raise FaaParseError("response is not well-formed UTF-8 XML") from error
    if root.tag != "AIRPORT_STATUS_INFORMATION":
        raise FaaParseError("unexpected root element")

    update_text = root.findtext("Update_Time")
    if update_text is None:
        raise FaaParseError("no Update_Time: cannot tell an empty feed from a broken one")
    update_time = _parse_update_time(update_text)

    events: list[FaaEvent] = []
    unrecognized: list[str] = []
    malformed = 0
    for section in root.findall("Delay_type"):
        name = _clean(section.findtext("Name") or "unnamed")
        handled = False
        for list_tag, event_type in _SECTIONS.items():
            container = section.find(list_tag)
            if container is None:
                continue
            handled = True
            for entry in container:
                event = _parse_entry(entry, event_type, update_time)
                if event is None:
                    malformed += 1
                else:
                    events.append(event)
        if not handled and name not in unrecognized:
            unrecognized.append(name)
    return FaaStatusSnapshot(
        source_id=SOURCE_ID,
        source_update_time_utc=update_time,
        fetched_at_utc=fetched_at_utc,
        events=tuple(events),
        unrecognized_sections=tuple(unrecognized),
        malformed_entries=malformed,
    )


_SECTIONS: dict[str, EventType] = {
    "Airport_Closure_List": EventType.AIRPORT_CLOSURE,
    "Ground_Stop_List": EventType.GROUND_STOP,
    "Ground_Delay_List": EventType.GROUND_DELAY_PROGRAM,
    "Arrival_Departure_Delay_List": EventType.ARRIVAL_DEPARTURE_DELAY,
}


def _clean(text: str) -> str:
    return " ".join(_CONTROL_CHARS.sub(" ", text).split())


def _parse_update_time(text: str) -> datetime | None:
    match = _UPDATE_TIME.match(text.strip())
    if not match or match.group(1).lower() not in _MONTHS:
        return None
    month, day, hour, minute, second, year = match.groups()
    try:
        return datetime(
            int(year), _MONTHS[month.lower()], int(day), int(hour), int(minute), int(second),
            tzinfo=UTC,
        )  # fmt: skip
    except ValueError:
        return None


def _parse_yearless(text: str, reference: datetime | None) -> datetime | None:
    """Parse 'May 27 at 18:26 UTC.' choosing the year nearest the publication time."""
    match = _YEARLESS_TIME.match(text.strip())
    if not match or reference is None or match.group(1).lower() not in _MONTHS:
        return None
    month = _MONTHS[match.group(1).lower()]
    best: datetime | None = None
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidate = datetime(
                year, month, int(match.group(2)), int(match.group(3)), int(match.group(4)),
                tzinfo=UTC,
            )  # fmt: skip
        except ValueError:
            continue
        if best is None or abs(candidate - reference) < abs(best - reference):
            best = candidate
    return best


def _parse_duration(text: str | None) -> int | None:
    if text is None:
        return None
    match = _DURATION.match(_clean(text).lower())
    if not match or not any(match.groups()):
        return None
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def _parse_entry(
    entry: ET.Element, event_type: EventType, update_time: datetime | None
) -> FaaEvent | None:
    airport = _clean(entry.findtext("ARPT") or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{3,4}", airport):
        return None
    flags: list[str] = []
    raw_reason = entry.findtext("Reason") or ""
    reason = _clean(raw_reason)
    if len(reason) > MAX_REASON_CHARS:
        reason = reason[:MAX_REASON_CHARS]
        flags.append("reason_truncated")

    raw_times: list[tuple[str, str]] = []
    resolved: dict[str, datetime | None] = {}
    for tag, label in (("Start", "start"), ("Reopen", "end"), ("End_Time", "end")):
        text = entry.findtext(tag)
        if text is None or not text.strip():
            continue
        raw_times.append((tag, _clean(text)))
        value = _parse_yearless(text, update_time)
        if value is None:
            flags.append(f"time_not_parsed:{tag}")
        else:
            flags.append(f"year_inferred:{tag}")
        resolved[label] = value

    direction: str | None = None
    min_delay = max_delay = None
    trend: str | None = None
    detail = entry.find("Arrival_Departure")
    if detail is not None:
        kind = _clean(detail.get("Type") or "").lower()
        direction = kind if kind in ("arrival", "departure") else None
        if direction is None:
            flags.append("direction_unknown")
        min_delay = _parse_duration(detail.findtext("Min"))
        max_delay = _parse_duration(detail.findtext("Max"))
        trend = _clean(detail.findtext("Trend") or "") or None
    avg_delay = _parse_duration(entry.findtext("Avg"))
    if event_type is EventType.GROUND_DELAY_PROGRAM:
        max_delay = _parse_duration(entry.findtext("Max"))

    start, end = resolved.get("start"), resolved.get("end")
    key_source = "|".join(
        (event_type, airport, reason, direction or "", *(f"{t}={v}" for t, v in raw_times))
    )
    return FaaEvent(
        event_key=hashlib.sha256(key_source.encode()).hexdigest()[:16],
        event_type=event_type,
        airport_code=airport,
        reason=reason,
        direction=direction,
        start_utc=start,
        end_utc=end,
        min_delay_minutes=min_delay,
        avg_delay_minutes=avg_delay,
        max_delay_minutes=max_delay,
        trend=trend,
        raw_times=tuple(raw_times),
        quality_flags=tuple(flags),
    )
