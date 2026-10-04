"""ASI:One client: request shape, HTTPS-only settings and redacted failures (no network)."""

import json
import urllib.error
from collections.abc import Mapping
from email.message import Message
from unittest import TestCase

from shared.llm import LlmError, LlmSettings, chat_completion

KEY = "TEST-not-a-real-key"


def settings() -> LlmSettings:
    return LlmSettings(api_key=KEY)


def reply(content: object) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class SettingsTests(TestCase):
    def test_from_env_defaults_and_disabled_without_key(self) -> None:
        empty = LlmSettings.from_env({})
        self.assertFalse(empty.enabled)
        self.assertEqual((empty.base_url, empty.model), ("https://api.asi1.ai/v1", "asi1"))
        configured = LlmSettings.from_env({"ASI_ONE_API_KEY": f" {KEY} ", "ASI_ONE_MODEL": "m"})
        self.assertTrue(configured.enabled)
        self.assertEqual((configured.api_key, configured.model), (KEY, "m"))

    def test_key_is_never_in_repr(self) -> None:
        self.assertNotIn(KEY, repr(settings()))

    def test_only_https_urls_and_wellformed_keys(self) -> None:
        for kwargs in (
            {"base_url": "http://api.asi1.ai/v1"},
            {"base_url": "https://user:pw@api.asi1.ai/v1"},
            {"api_key": "two words"},
            {"model": " "},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LlmSettings(**kwargs)  # type: ignore[arg-type]


class ChatCompletionTests(TestCase):
    def test_request_shape(self) -> None:
        seen: dict[str, object] = {}

        def transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
            seen.update(url=url, body=json.loads(body), headers=dict(headers), timeout=timeout)
            return reply("Two advisories are pending.")

        answer = chat_completion(settings(), "system text", "user text", transport=transport)
        self.assertEqual(answer, "Two advisories are pending.")
        self.assertEqual(seen["url"], "https://api.asi1.ai/v1/chat/completions")
        body = seen["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "asi1")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        headers = seen["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")

    def test_disabled_without_a_key(self) -> None:
        with self.assertRaises(LlmError) as raised:
            chat_completion(LlmSettings(), "s", "u", transport=lambda *a: reply("x"))
        self.assertEqual(raised.exception.category, "disabled")

    def test_failures_are_categorized_without_secrets_or_bodies(self) -> None:
        def http_error(*_: object) -> bytes:
            raise urllib.error.HTTPError(
                "https://api.asi1.ai/v1", 401, f"bad key {KEY}", Message(), None
            )

        def offline(*_: object) -> bytes:
            raise OSError("connection reset")

        cases = {
            "http_401": http_error,
            "unavailable": offline,
            "malformed_response": lambda *_: b"not json",
            "empty_response": lambda *_: reply(""),
        }
        for category, transport in cases.items():
            with self.subTest(category=category), self.assertRaises(LlmError) as raised:
                chat_completion(settings(), "s", "u", transport=transport)
            self.assertEqual(raised.exception.category, category)
            self.assertNotIn(KEY, str(raised.exception))
