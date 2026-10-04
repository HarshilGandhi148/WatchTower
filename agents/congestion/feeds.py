"""Interim live input for the congestion agent: public, keyless feeds via the stdlib.

Interim because the planned path is provider ingestion -> SpacetimeDB -> agent snapshot
reads (data/ingestion.py, shared/spacetimedb_adapter.py), which do not exist yet. Replace
``fetch_feeds`` with a snapshot read once they do; the rest of the agent is unaffected.

Each feed fails independently to ``None`` with a recorded error: a failed feed is
unknown, never an empty one. No AeroAPI here (not configured), so demand stays
unavailable.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from data.adsb import AdsbParseError, AdsbSnapshot, parse_point_response, point_url
from data.faa_status import FaaParseError, FaaStatusSnapshot, parse_status_xml

TIMEOUT_S = 10
MAX_RESPONSE_BYTES = 5_000_000

HttpGet = Callable[[str, str], bytes]  # (url, user_agent) -> body


@dataclass(frozen=True, slots=True)
class FeedConfig:
    adsb_base_url: str
    faa_url: str
    user_agent: str
    center: tuple[float, float]
    radius_nm: int = 40


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    adsb: AdsbSnapshot | None
    faa: FaaStatusSnapshot | None
    errors: tuple[str, ...] = ()


def http_get(url: str, user_agent: str) -> bytes:
    """Bounded HTTPS GET with an identifying User-Agent. Raises on any failure."""
    if not url.startswith("https://"):
        raise ValueError("only https URLs are fetched")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response missing or too large")
    return body


def fetch_feeds(
    config: FeedConfig,
    get: HttpGet = http_get,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FeedSnapshot:
    """Fetch and normalize both feeds; the fetch time is taken after each response."""
    errors: list[str] = []
    adsb: AdsbSnapshot | None = None
    faa: FaaStatusSnapshot | None = None
    try:
        url = point_url(config.adsb_base_url, *config.center, config.radius_nm)
        body = get(url, config.user_agent)
        adsb = parse_point_response(json.loads(body), clock())
    except (OSError, ValueError, AdsbParseError) as error:
        errors.append(f"adsb: {type(error).__name__}: {error}")
    try:
        faa = parse_status_xml(get(config.faa_url, config.user_agent), clock())
    except (OSError, ValueError, FaaParseError) as error:
        errors.append(f"faa: {type(error).__name__}: {error}")
    return FeedSnapshot(adsb, faa, tuple(errors))
