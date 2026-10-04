"""Backend SpacetimeDB configuration; tokens never enter browser configuration."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class SpacetimeSettings:
    http_url: str
    database: str
    ingest_token: str = field(repr=False)
    timeout_s: float = 10.0

    def __post_init__(self) -> None:
        url = urlsplit(self.http_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Invalid SpacetimeDB HTTP origin")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote SpacetimeDB connections require HTTPS")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.database):
            raise ValueError("Invalid SpacetimeDB database name")
        if not self.ingest_token.strip() or any(c.isspace() for c in self.ingest_token):
            raise ValueError("Missing or invalid SpacetimeDB service token")
        if not 0 < self.timeout_s <= 60:
            raise ValueError("SpacetimeDB timeout must be between 0 and 60 seconds")

    @classmethod
    def from_env(
        cls, environment: Mapping[str, str] | None = None, *, role: str = "ingest"
    ) -> "SpacetimeSettings":
        if role not in {"ingest", "weather", "congestion", "validation"}:
            raise ValueError("Unsupported service role")
        env = os.environ if environment is None else environment
        return cls(
            http_url=env.get("SPACETIMEDB_HTTP_URL", "").strip(),
            database=env.get("SPACETIMEDB_DATABASE", "").strip(),
            ingest_token=env.get(f"SPACETIMEDB_{role.upper()}_TOKEN", "").strip(),
        )


@dataclass(frozen=True, slots=True)
class AirportConfig:
    """One configuration for ingestion, snapshot capture and traffic calculations."""

    airport_icao: str = "KDTW"
    latitude_deg: float = 42.2124311
    longitude_deg: float = -83.3533933
    radius_nm: int = 40
    version: str = "airport-v1"

    def __post_init__(self) -> None:
        import math

        if self.airport_icao != "KDTW":
            raise ValueError("Current shared contracts support KDTW only")
        if not math.isfinite(self.latitude_deg) or not -90 <= self.latitude_deg <= 90:
            raise ValueError("Invalid airport latitude")
        if not math.isfinite(self.longitude_deg) or not -180 <= self.longitude_deg <= 180:
            raise ValueError("Invalid airport longitude")
        if type(self.radius_nm) is not int or not 1 <= self.radius_nm <= 250:
            raise ValueError("Invalid traffic radius")
        if not self.version.strip():
            raise ValueError("Missing configuration version")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AirportConfig":
        return cls(
            airport_icao=env.get("AIRPORT_ICAO", "KDTW"),
            latitude_deg=float(env.get("AIRPORT_LATITUDE_DEG", "42.2124311")),
            longitude_deg=float(env.get("AIRPORT_LONGITUDE_DEG", "-83.3533933")),
            radius_nm=int(env.get("TRAFFIC_RADIUS_NM", "40")),
        )
