import base64
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from scripts.mint_tokens import mint, token_identity


def token(identity):
    payload = base64.urlsafe_b64encode(json.dumps({"hex_identity": identity}).encode()).decode()
    return "TEST." + payload.rstrip("=") + ".TEST"


class ProvisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_preserves_saved_tokens_and_grants_all_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.env"
            roles = ("ingestion", "weather", "congestion", "validation", "bridge")
            content = "".join(
                f"SPACETIMEDB_{'INGEST' if role == 'ingestion' else role.upper()}_TOKEN="
                f"{token(str(i + 1) * 64)}\n"
                for i, role in enumerate(roles)
            )
            path.write_text(content)
            os.chmod(path, 0o600)
            client = AsyncMock()
            with (
                patch("scripts.mint_tokens.Client", return_value=client),
                redirect_stdout(StringIO()),
            ):
                await mint("http://localhost:3000", "test", "TEST-admin", path, resume=True)
            self.assertEqual(path.read_text(), content)
            self.assertEqual(client.call.await_count, 5)
            for index, role in enumerate(roles):
                self.assertEqual(
                    client.call.await_args_list[index].args,
                    ("seed_agent_identity", role, str(index + 1) * 64),
                )

    async def test_default_refuses_to_overwrite_and_invalid_resume_cannot_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.env"
            path.write_text("SPACETIMEDB_INGEST_TOKEN=invalid\n")
            os.chmod(path, 0o600)
            client = AsyncMock()
            with patch("scripts.mint_tokens.Client", return_value=client):
                with self.assertRaises(FileExistsError):
                    await mint("http://localhost:3000", "test", "TEST-admin", path)
                with self.assertRaises(ValueError):
                    await mint("http://localhost:3000", "test", "TEST-admin", path, resume=True)
            client.call.assert_not_awaited()
            self.assertEqual(path.read_text(), "SPACETIMEDB_INGEST_TOKEN=invalid\n")

    def test_invalid_saved_identity_is_redacted(self):
        for value in ("SECRET-value", token("wrong")):
            with self.assertRaisesRegex(ValueError, "^Invalid saved service token$"):
                token_identity(value)
