"""ASI:One selects supported explanations. All text and numbers are rendered by code."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from agents.weather.assessment import render_summary
from agents.weather.prompts.explanations import SYSTEM_PROMPT
from data.awc import _NoRedirect
from shared.contracts import WeatherAssessment, WeatherBasis, WeatherImplication


class ExplanationModel(Protocol):
    async def complete(self, packet: str, *, repair: bool) -> str: ...


@dataclass(frozen=True, slots=True)
class AsiConfig:
    api_key: str = field(repr=False)
    base_url: str = "https://api.asi1.ai/v1"
    model: str = "asi1"

    def __post_init__(self) -> None:
        parts = urlsplit(self.base_url)
        if (
            not self.api_key.strip()
            or not self.model.strip()
            or (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            )
        ):
            raise ValueError("Invalid ASI configuration")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AsiConfig | None:
        key = env.get("ASI_ONE_API_KEY", "").strip()
        return (
            cls(
                key,
                env.get("ASI_ONE_BASE_URL", "https://api.asi1.ai/v1"),
                env.get("ASI_ONE_MODEL", "asi1"),
            )
            if key
            else None
        )


class AsiModel:
    def __init__(self, config: AsiConfig) -> None:
        self._config = config

    async def complete(self, packet: str, *, repair: bool) -> str:
        return await asyncio.to_thread(self._complete, packet, repair)

    def _complete(self, packet: str, repair: bool) -> str:
        body = json.dumps(
            {
                "model": self._config.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": packet},
                    *(
                        [
                            {
                                "role": "user",
                                "content": (
                                    "Previous response failed validation. Return the exact "
                                    "required JSON schema and all supplied IDs."
                                ),
                            }
                        ]
                        if repair
                        else []
                    ),
                ],
                "temperature": 0,
                "max_tokens": 512,
            }
        ).encode()
        request = Request(
            self._config.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + self._config.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with build_opener(_NoRedirect).open(request, timeout=3) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError
            decoded = json.loads(raw)
            choice = decoded["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") != "stop" or not isinstance(content, str):
                raise ValueError
            return content
        except HTTPError as error:
            error.close()
            raise ValueError("model unavailable") from None
        except (URLError, OSError, ValueError, KeyError, IndexError, TypeError):
            raise ValueError("model unavailable") from None


def _select(content: str, assessment: WeatherAssessment) -> tuple[WeatherImplication, ...]:
    if len(content) > 16384:
        raise ValueError("Model output too large")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate model key")
            result[key] = value
        return result

    parsed = json.loads(content, object_pairs_hook=unique_object)
    if not isinstance(parsed, dict) or set(parsed) != {"selections"}:
        raise ValueError("Invalid explanation structure")
    selections = parsed["selections"]
    allowed = {i.implication_id: i for i in assessment.operational_implications}
    if not isinstance(selections, list) or len(selections) != len(allowed):
        raise ValueError("Explanation must retain every supported implication")
    selected: list[WeatherImplication] = []
    seen: set[str] = set()
    for item in selections:
        if not isinstance(item, dict) or set(item) != {"implication_id", "evidence_ids"}:
            raise ValueError("Invalid explanation selection")
        identity = item["implication_id"]
        if not isinstance(identity, str) or identity not in allowed or identity in seen:
            raise ValueError("Invalid implication reference")
        implication = allowed[identity]
        if item["evidence_ids"] != list(implication.evidence_ids):
            raise ValueError("Invalid explanation evidence")
        selected.append(implication)
        seen.add(identity)
    return tuple(selected)


class Explainer:
    def __init__(
        self, model: ExplanationModel | None = None, *, budget_s: float = 6.0, max_cache: int = 256
    ) -> None:
        if not 0 < budget_s <= 6 or max_cache < 1:
            raise ValueError("Invalid explanation budget/cache")
        self._model, self._budget, self._max_cache = model, budget_s, max_cache
        self._cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()
        self.model_calls = 0
        self.fallbacks = 0

    async def explain(self, assessment: WeatherAssessment) -> WeatherAssessment:
        if self._model is None or not assessment.operational_implications:
            return assessment
        # Includes normalized facts, units, windows, rules/prompt version and evidence
        # content IDs; excludes case identity, message times and retrieval times.
        packet = json.dumps(
            {
                "airport": "KDTW",
                "status": assessment.status,
                "quality": assessment.quality,
                "window": [
                    assessment.window_start_utc.isoformat(),
                    assessment.window_end_utc.isoformat(),
                ],
                "rules_version": assessment.rules_version,
                "prompt_version": assessment.prompt_version,
                "facts": [
                    asdict(f)
                    | (
                        {"start_utc": None, "end_utc": None}
                        if f.basis is WeatherBasis.OBSERVED
                        else {}
                    )
                    for f in assessment.weather_facts
                ],
                "hazards": [
                    asdict(h)
                    | (
                        {"start_utc": None, "end_utc": None}
                        if h.basis is WeatherBasis.OBSERVED
                        else {}
                    )
                    for h in assessment.hazards
                ],
                "missing_inputs": assessment.missing_inputs,
                "implications": [asdict(i) for i in assessment.operational_implications],
            },
            default=str,
            sort_keys=True,
        )
        key = hashlib.sha256(packet.encode()).hexdigest()
        selected: tuple[WeatherImplication, ...] | None = None
        if key in self._cache:
            allowed = {i.implication_id: i for i in assessment.operational_implications}
            selected = tuple(allowed[i] for i in self._cache[key])
            self._cache.move_to_end(key)
        else:
            try:
                async with asyncio.timeout(self._budget):
                    for repair in (False, True):
                        self.model_calls += 1
                        content = await self._model.complete(packet, repair=repair)
                        try:
                            selected = _select(content, assessment)
                            break
                        except (ValueError, TypeError):
                            continue
            except Exception:
                selected = None
            if selected is None:
                self.fallbacks += 1
                selected = assessment.operational_implications
            self._cache[key] = tuple(i.implication_id for i in selected)
            if len(self._cache) > self._max_cache:
                self._cache.popitem(last=False)
        return replace(assessment, summary=render_summary(assessment, selected))
