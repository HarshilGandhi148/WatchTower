"""Provision/resume separate service identities without printing credentials."""

import argparse
import base64
import json
import os
import re
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from shared.settings import SpacetimeSettings
from watchtower_client import Client

ROLES = ("ingestion", "weather", "congestion", "validation", "bridge")


def token_identity(token: str) -> str:
    """Read the public identity of an existing, privately saved service credential."""
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        identity = claims["hex_identity"]
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError
        return identity
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("Invalid saved service token") from None


async def mint(
    origin: str, database: str, admin_token: str, output: Path, *, resume: bool = False
) -> None:
    client = Client(SpacetimeSettings(origin, database, admin_token))
    values: dict[str, str] = {}
    if resume:
        for line in output.read_text().splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in values:
                raise ValueError("Invalid saved token file")
            values[key] = value
        valid_keys = {
            f"SPACETIMEDB_{'INGEST' if role == 'ingestion' else role.upper()}_TOKEN"
            for role in ROLES
        }
        if set(values) - valid_keys:
            raise ValueError("Unknown saved token key")
        # Validate before contacting the database or granting any permissions.
        for token in values.values():
            token_identity(token)
        if output.stat().st_mode & 0o077:
            raise ValueError("Saved token file must have private permissions (0600)")
    # Normal runs must never overwrite an existing identity file.
    flags = os.O_WRONLY | (os.O_APPEND if resume else os.O_CREAT | os.O_EXCL)
    descriptor = os.open(output, flags, 0o600)
    with os.fdopen(descriptor, "a" if resume else "w") as file:
        for role in ROLES:
            key = f"SPACETIMEDB_{'INGEST' if role == 'ingestion' else role.upper()}_TOKEN"
            if key in values:
                identity = token_identity(values[key])
            else:
                # Redirects are explicitly rejected; do not trust identity response headers.
                class NoRedirect(HTTPRedirectHandler):
                    def redirect_request(self, *args: object, **kwargs: object) -> None:
                        return None

                with build_opener(NoRedirect).open(
                    Request(f"{origin.rstrip('/')}/v1/identity", data=b"", method="POST"),
                    timeout=10,
                ) as response:
                    value = json.loads(response.read(65536))
                identity, token = value["identity"], value["token"]
                if token_identity(token) != identity:
                    raise ValueError("Identity/token mismatch")
                file.write(f"{key}={token}\n")
                file.flush()
                os.fsync(file.fileno())  # Preserve credentials before granting database access.
            await client.call("seed_agent_identity", role, identity)
            print(f"{role}: {identity}")  # Public identity only.


def main() -> None:
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("secrets/spacetime-tokens.env"))
    parser.add_argument(
        "--resume", action="store_true", help="Reuse saved tokens after partial failure"
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(
        mint(
            os.environ["SPACETIMEDB_HTTP_URL"],
            os.environ["SPACETIMEDB_DATABASE"],
            os.environ["SPACETIMEDB_ADMIN_TOKEN"],
            args.output,
            resume=args.resume,
        )
    )


if __name__ == "__main__":
    main()
