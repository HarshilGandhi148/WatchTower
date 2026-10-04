"""Weather workflow with injected durable persistence and fixed evidence loading."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from agents.weather.assessment import DEFAULT_RULES, WeatherRules, assess_weather
from agents.weather.llm import Explainer
from shared.contracts import (
    WeatherAssessment,
    WeatherAssessmentRequest,
    WeatherSnapshot,
    weather_utc,
)
from shared.messages import EnvelopeModel, WeatherAssessmentReplyMessage

type LoadSnapshot = Callable[[str], Awaitable[WeatherSnapshot]]
type PersistAssessment = Callable[[WeatherAssessment], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class WeatherDependencies:
    load_snapshot: LoadSnapshot
    persist_assessment: PersistAssessment


class WeatherProtocolError(ValueError):
    """Safe rejection category."""


@dataclass(slots=True)
class _RequestState:
    request: WeatherAssessmentRequest
    task: asyncio.Task[WeatherAssessmentReplyMessage] | None = None
    result: WeatherAssessment | None = None
    snapshot: WeatherSnapshot | None = None
    reply: WeatherAssessmentReplyMessage | None = None


class WeatherService:
    def __init__(
        self,
        dependencies: WeatherDependencies,
        *,
        rules: WeatherRules = DEFAULT_RULES,
        explainer: Explainer | None = None,
        clock: Callable[[], datetime] | None = None,
        max_requests: int = 512,
    ) -> None:
        if max_requests < 2:
            raise ValueError("Request capacity must be at least two")
        self._dependencies, self._rules = dependencies, rules
        self._explainer = explainer or Explainer()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_requests = max_requests
        self._requests: dict[str, _RequestState] = {}

    def _now(self) -> datetime:
        now = self._clock()
        weather_utc(now)
        return now

    async def handle(self, request: WeatherAssessmentRequest) -> WeatherAssessmentReplyMessage:
        now, e = self._now(), request.envelope
        if e.valid_until_utc <= now or e.created_at_utc > now:
            raise WeatherProtocolError("expired or future request")
        # Keep failed requests until expiry to detect changed retries and preserve
        # deterministic persisted content after a lost acknowledgement.
        for identity, old in list(self._requests.items()):
            if old.request.envelope.valid_until_utc <= now and (
                old.task is None or old.task.done()
            ):
                del self._requests[identity]
        state = self._requests.get(e.message_id)
        if state is not None:
            if state.request != request:
                raise WeatherProtocolError("changed retry payload")
            if state.reply is not None:
                return state.reply
        else:
            case_requests = [
                s.request
                for s in self._requests.values()
                if s.request.envelope.case_id == e.case_id
            ]
            for previous in case_requests:
                if (
                    previous.envelope.snapshot_id != e.snapshot_id
                    or previous.envelope.evidence_ids != e.evidence_ids
                    or previous.window_start_utc != request.window_start_utc
                    or previous.window_end_utc != request.window_end_utc
                ):
                    raise WeatherProtocolError("case snapshot or window changed")
                if previous.attempt == request.attempt:
                    raise WeatherProtocolError("round already has a request")
                if e.valid_until_utc > previous.envelope.valid_until_utc:
                    raise WeatherProtocolError("correction cannot extend the original deadline")
            if request.attempt == 2 and not any(r.attempt == 1 for r in case_requests):
                raise WeatherProtocolError("second round without first round")
            if request.attempt == 2 and any(
                s.request.envelope.case_id == e.case_id
                and s.request.attempt == 1
                and s.reply is None
                for s in self._requests.values()
            ):
                raise WeatherProtocolError("first round has not completed")
            if len(self._requests) >= self._max_requests:
                raise WeatherProtocolError("weather request capacity reached")
            state = _RequestState(request)
            self._requests[e.message_id] = state
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self._run(state))
        return await asyncio.shield(state.task)

    async def _run(self, state: _RequestState) -> WeatherAssessmentReplyMessage:
        try:
            remaining = (state.request.envelope.valid_until_utc - self._now()).total_seconds()
            async with asyncio.timeout(min(12.0, remaining)):
                if state.result is None:
                    snapshot = await self._dependencies.load_snapshot(
                        state.request.envelope.snapshot_id
                    )
                    state.snapshot = snapshot
                    result = assess_weather(state.request, snapshot, self._now(), self._rules)
                    state.result = await self._explainer.explain(result)
                if state.snapshot is not None:
                    current = assess_weather(
                        state.request, state.snapshot, self._now(), self._rules
                    )
                    if (current.status, current.quality, current.missing_inputs) != (
                        state.result.status,
                        state.result.quality,
                        state.result.missing_inputs,
                    ):
                        raise WeatherProtocolError(
                            "evidence freshness changed; a new case is required"
                        )
                if state.request.envelope.valid_until_utc <= self._now():
                    raise WeatherProtocolError("request expired during assessment")
                await self._dependencies.persist_assessment(state.result)
                if state.request.envelope.valid_until_utc <= self._now():
                    raise WeatherProtocolError("request expired during persistence")
                reply = WeatherAssessmentReplyMessage(
                    envelope=EnvelopeModel.from_envelope(state.result.envelope),
                    status=state.result.status.value,
                    assessment_id=state.result.assessment_id,
                )
                reply.to_reply()
                state.reply = reply
                return reply
        except WeatherProtocolError:
            raise
        except Exception:
            raise WeatherProtocolError("weather dependency or assessment failed") from None

    async def close(self) -> None:
        tasks = [
            s.task for s in self._requests.values() if s.task is not None and not s.task.done()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
