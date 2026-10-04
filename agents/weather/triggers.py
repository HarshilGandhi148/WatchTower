"""Bounded weather-change intents. Integration supplies complete stored snapshots."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from agents.weather.assessment import DEFAULT_RULES, WeatherRules, assess_weather, hazard_kinds
from agents.weather.parsing import latest_metar, latest_taf
from shared.contracts import AgentName, MessageEnvelope, WeatherAssessmentRequest, WeatherSnapshot

type WindState = tuple[float | None, float | None, float | None]


@dataclass(frozen=True, slots=True)
class TriggerDependencies:
    obtain_snapshot: Callable[[], Awaitable[WeatherSnapshot]]
    open_case: Callable[[MessageEnvelope], Awaitable[None]]


class ChangeDetector:
    def __init__(
        self,
        dependencies: TriggerDependencies,
        *,
        rules: WeatherRules = DEFAULT_RULES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dependencies, self._rules = dependencies, rules
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._signature: tuple[object, ...] | None = None
        self._wind: WindState = (None, None, None)
        self._last_open: datetime | None = None
        self._pending: tuple[MessageEnvelope, tuple[object, ...], WindState] | None = None

    async def poll(self) -> bool:
        if self._lock.locked():
            return False
        async with self._lock:
            now = self._clock()
            if self._pending is not None:
                envelope, signature, wind = self._pending
                if envelope.valid_until_utc <= now:
                    self._pending = None
                else:
                    return await self._open(envelope, signature, wind, now)
            async with asyncio.timeout(5):
                snapshot = await self._dependencies.obtain_snapshot()
            now = self._clock()
            end = now + timedelta(minutes=15)
            identity = (
                "weather-change-"
                + hashlib.sha256((snapshot.snapshot_id + now.isoformat()).encode()).hexdigest()
            )
            opening = MessageEnvelope(
                schema_version=1,
                message_id=identity + ":open",
                case_id=identity,
                sender_agent=AgentName.CONGESTION,
                created_at_utc=now,
                snapshot_id=snapshot.snapshot_id,
                evidence_ids=snapshot.evidence_ids,
                airport_icao="KDTW",
                valid_until_utc=now + timedelta(seconds=120),
            )
            assessment = assess_weather(
                WeatherAssessmentRequest(opening, now, end), snapshot, now, self._rules
            )
            metar = latest_metar(snapshot.metars, snapshot.cutoff_utc)
            taf = latest_taf(snapshot.tafs, snapshot.cutoff_utc, now, end)
            wind = (
                (
                    metar.conditions.wind_speed_kt,
                    metar.conditions.wind_direction_deg,
                    metar.conditions.wind_gust_kt,
                )
                if metar
                else (None, None, None)
            )
            # Relevant amendments are material; routine issue/fetch timestamps are not.
            amendment = taf.evidence_id if taf is not None and taf.amendment != "original" else None
            signature = (
                tuple(
                    sorted(
                        {
                            (h.kind, h.basis.value, h.probability_percent)
                            for h in assessment.hazards
                        },
                        key=lambda h: (h[0], h[1], h[2] or 0),
                    )
                ),
                assessment.quality.value,
                assessment.missing_inputs,
                amendment,
                tuple(
                    (
                        g.change,
                        g.start_utc,
                        g.end_utc,
                        g.transition_end_utc,
                        g.probability_percent,
                        hazard_kinds(g.conditions, self._rules),
                    )
                    for g in (taf.groups if taf is not None else ())
                    if g.start_utc < end
                    and g.end_utc > now
                    and hazard_kinds(g.conditions, self._rules)
                ),
            )
            changed_wind = self._wind_changed(wind)
            if self._signature == signature and not changed_wind:
                return False
            # Quiet first observation seeds the detector without opening a case.
            if self._signature is None and not assessment.hazards:
                self._signature, self._wind = signature, wind
                return False
            if self._last_open is not None and now - self._last_open < timedelta(seconds=60):
                return False
            envelope = replace(opening, sender_agent=AgentName.WEATHER)
            self._pending = (envelope, signature, wind)
            return await self._open(envelope, signature, wind, now)

    def _wind_changed(self, wind: WindState) -> bool:
        speed, direction, gust = wind
        old_speed, old_direction, old_gust = self._wind
        if (
            speed is not None
            and old_speed is not None
            and abs(speed - old_speed) >= self._rules.wind_change_kt
        ):
            return True
        if (
            gust is not None
            and old_gust is not None
            and abs(gust - old_gust) >= self._rules.wind_change_kt
        ):
            return True
        if direction is not None and old_direction is not None:
            angle = abs(direction - old_direction) % 360
            return min(angle, 360 - angle) >= self._rules.direction_change_deg
        return False

    async def _open(
        self,
        envelope: MessageEnvelope,
        signature: tuple[object, ...],
        wind: WindState,
        now: datetime,
    ) -> bool:
        async with asyncio.timeout(5):
            await self._dependencies.open_case(envelope)
        self._signature, self._wind, self._last_open = signature, wind, now
        self._pending = None
        return True
