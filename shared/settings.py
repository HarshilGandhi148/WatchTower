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
            raise ValueError("Missing or invalid SpacetimeDB ingestion token")
        if not 0 < self.timeout_s <= 60:
            raise ValueError("SpacetimeDB timeout must be between 0 and 60 seconds")

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> "SpacetimeSettings":
        env = os.environ if environment is None else environment
        return cls(
            http_url=env.get("SPACETIMEDB_HTTP_URL", "").strip(),
            database=env.get("SPACETIMEDB_DATABASE", "").strip(),
            ingest_token=env.get("SPACETIMEDB_INGEST_TOKEN", "").strip(),
        )
