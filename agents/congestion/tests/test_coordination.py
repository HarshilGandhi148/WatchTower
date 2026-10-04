"""Case coordination: bounded exchanges, timeouts, duplicates, late and forged messages."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest import TestCase

from agents.congestion.candidates import WeatherSignal
from agents.congestion.coordination import (
    CaseClosedError,
    CaseConflictError,
    CaseCoordinator,
    CaseState,
    CloseReason,
    CoordinationError,
    NotReadyError,
    Outcome,
    UnknownCaseError,
)
from shared.contracts import (
    AdvisoryType,
    AgentName,
    AssessmentRequest,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
    MessageEnvelope,
)

T0 = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
EVIDENCE = ("adsb-1", "flights-1")


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def message(
    sender: AgentName,
    message_id: str,
    reply_to: str | None,
    *,
    case_id: str = "case-1",
    snapshot_id: str = "snap-1",
    valid_for: float = 60,
    now: datetime = T0,
) -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id=message_id,
        case_id=case_id,
        sender_agent=sender,
        created_at_utc=now,
        snapshot_id=snapshot_id,
        evidence_ids=(),
        airport_icao="KDTW",
        valid_until_utc=now + timedelta(seconds=valid_for),
        reply_to=reply_to,
    )


def weather(message_id: str, reply_to: str | None, **kwargs: object) -> MessageEnvelope:
    return message(AgentName.WEATHER, message_id, reply_to, **kwargs)  # type: ignore[arg-type]


def correction(message_id: str, reply_to: str | None, **kwargs: object) -> MessageEnvelope:
    return message(AgentName.VALIDATION, message_id, reply_to, **kwargs)  # type: ignore[arg-type]


class Builder:
    """Stands in for candidates.build_candidate; records how often it was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[WeatherSignal, str | None, str | None]] = []

    def __call__(
        self, envelope: MessageEnvelope, signal: WeatherSignal, assessment_id: str | None
    ) -> FlowCandidate:
        self.calls.append((signal, assessment_id, envelope.reply_to))
        return FlowCandidate(
            envelope=envelope,
            status=CandidateStatus.NO_CHANGE,
            issue_type=AdvisoryType.TRAFFIC_PRESSURE_NOTICE,
            resource="KDTW",
            window_start_utc=envelope.created_at_utc,
            window_end_utc=envelope.created_at_utc + timedelta(minutes=15),
            quality=EvidenceQuality.LIMITED,
        )


def opened(coordinator: CaseCoordinator | None = None) -> tuple[CaseCoordinator, AssessmentRequest]:
    coordinator = coordinator or CaseCoordinator()
    request = coordinator.open_case(
        "case-1", "snap-1", EVIDENCE, T0, T0, T0 + timedelta(minutes=15)
    )
    return coordinator, request


class OpenCaseTests(TestCase):
    def test_request_is_bound_to_the_fixed_snapshot(self) -> None:
        _, request = opened()
        self.assertEqual(request.attempt, 1)
        self.assertEqual(request.envelope.snapshot_id, "snap-1")
        self.assertEqual(request.envelope.evidence_ids, EVIDENCE)
        self.assertEqual(request.envelope.sender_agent, AgentName.CONGESTION)
        self.assertEqual(request.window_end_utc - request.window_start_utc, timedelta(minutes=15))

    def test_retried_open_returns_the_same_request_and_one_case(self) -> None:
        coordinator, first = opened()
        again = coordinator.open_case("case-1", "snap-1", EVIDENCE, at(3), T0, at(900))
        self.assertEqual(again, first)
        self.assertEqual(coordinator.view("case-1").exchanges, 1)

    def test_same_case_with_another_snapshot_conflicts(self) -> None:
        coordinator, _ = opened()
        with self.assertRaises(CaseConflictError):
            coordinator.open_case("case-1", "snap-2", EVIDENCE, T0, T0, at(900))

    def test_invalid_input_does_not_leave_a_half_open_case(self) -> None:
        coordinator = CaseCoordinator()
        with self.assertRaises(ValueError):
            coordinator.open_case("case-1", "", EVIDENCE, T0, T0, at(900))
        with self.assertRaises(UnknownCaseError):
            coordinator.view("case-1")

    def test_unknown_case_and_naive_time(self) -> None:
        coordinator, _ = opened()
        with self.assertRaises(UnknownCaseError):
            coordinator.poll("nope", T0)
        with self.assertRaises(ValueError):
            coordinator.poll("case-1", datetime(2026, 10, 4, 1))

    def test_bad_timing_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CaseCoordinator(weather_timeout=timedelta(seconds=60), case_ttl=timedelta(seconds=30))


class WeatherReplyTests(TestCase):
    def setUp(self) -> None:
        self.coordinator, self.request = opened()
        self.request_id = self.request.envelope.message_id

    def reply(
        self,
        envelope: MessageEnvelope,
        signal: WeatherSignal = WeatherSignal.NO_MATERIAL_IMPACT,
        assessment_id: str | None = "wx-1",
        now: datetime | None = None,
    ):
        return self.coordinator.on_weather_reply(envelope, signal, assessment_id, now or at(2))

    def test_matching_reply_is_accepted_and_case_becomes_ready(self) -> None:
        result = self.reply(weather("w-1", self.request_id), WeatherSignal.IMPACT)
        self.assertEqual(result.outcome, Outcome.ACCEPTED)
        view = self.coordinator.view("case-1")
        self.assertEqual(view.state, CaseState.READY)
        self.assertEqual(
            (view.weather_signal, view.weather_assessment_id), (WeatherSignal.IMPACT, "wx-1")
        )

    def test_duplicate_reply_is_idempotent(self) -> None:
        envelope = weather("w-1", self.request_id)
        self.reply(envelope)
        self.assertEqual(self.reply(envelope).outcome, Outcome.DUPLICATE)
        self.assertEqual(self.coordinator.view("case-1").state, CaseState.READY)

    def test_second_distinct_reply_cannot_overwrite_the_first(self) -> None:
        self.reply(weather("w-1", self.request_id), WeatherSignal.NO_MATERIAL_IMPACT)
        result = self.reply(weather("w-2", self.request_id), WeatherSignal.IMPACT)
        self.assertEqual(
            (result.outcome, result.reason), (Outcome.REJECTED, "no_outstanding_request")
        )
        self.assertEqual(
            self.coordinator.view("case-1").weather_signal, WeatherSignal.NO_MATERIAL_IMPACT
        )

    def test_rejected_replies_leave_the_case_waiting(self) -> None:
        cases = {
            "wrong_sender_role": message(AgentName.VALIDATION, "w", self.request_id),
            "snapshot_mismatch": weather("w", self.request_id, snapshot_id="snap-9"),
            "not_the_outstanding_request": weather("w", "other-request"),
            "expired_reply": weather("w", self.request_id, valid_for=1),
            "unknown_case": weather("w", self.request_id, case_id="nope"),
        }
        for reason, envelope in cases.items():
            with self.subTest(reason=reason):
                result = self.reply(envelope)
                self.assertEqual((result.outcome, result.reason), (Outcome.REJECTED, reason))
        self.assertEqual(self.coordinator.view("case-1").state, CaseState.AWAITING_WEATHER)

    def test_impact_needs_an_assessment_id(self) -> None:
        result = self.reply(weather("w", self.request_id), WeatherSignal.IMPACT, None)
        self.assertEqual(result.reason, "impact_without_assessment_id")

    def test_insufficient_weather_is_unknown_and_drops_the_assessment_id(self) -> None:
        self.reply(weather("w", self.request_id), WeatherSignal.UNKNOWN, "wx-1")
        self.assertIsNone(self.coordinator.view("case-1").weather_assessment_id)


class SubmitAndTimeoutTests(TestCase):
    def test_submit_waits_for_weather(self) -> None:
        coordinator, _ = opened()
        with self.assertRaises(NotReadyError):
            coordinator.submit("case-1", Builder(), at(1))

    def test_submit_builds_once_and_returns_the_same_candidate(self) -> None:
        coordinator, request = opened()
        coordinator.on_weather_reply(
            weather("w-1", request.envelope.message_id), WeatherSignal.IMPACT, "wx-1", at(1)
        )
        build = Builder()
        first = coordinator.submit("case-1", build, at(2))
        self.assertEqual(coordinator.submit("case-1", build, at(3)), first)
        self.assertEqual(build.calls, [(WeatherSignal.IMPACT, "wx-1", None)])
        self.assertEqual(first.envelope.message_id, "case-1:candidate:1")
        self.assertEqual(coordinator.view("case-1").state, CaseState.SUBMITTED)

    def test_weather_timeout_proceeds_with_unknown_weather(self) -> None:
        coordinator, _ = opened()
        view = coordinator.poll("case-1", at(15))
        self.assertEqual((view.state, view.weather_timed_out), (CaseState.READY, True))
        build = Builder()
        coordinator.submit("case-1", build, at(16))
        self.assertEqual(build.calls[0][:2], (WeatherSignal.UNKNOWN, None))

    def test_submit_applies_the_timeout_itself(self) -> None:
        coordinator, _ = opened()
        coordinator.submit("case-1", Builder(), at(20))
        self.assertTrue(coordinator.view("case-1").weather_timed_out)

    def test_late_reply_after_timeout_is_rejected_not_applied(self) -> None:
        coordinator, request = opened()
        result = coordinator.on_weather_reply(
            weather("w-1", request.envelope.message_id, valid_for=300),
            WeatherSignal.IMPACT,
            "wx-1",
            at(30),
        )
        self.assertEqual(result.outcome, Outcome.REJECTED)
        self.assertEqual(coordinator.view("case-1").weather_signal, WeatherSignal.UNKNOWN)

    def test_expired_case_closes_and_blocks_everything(self) -> None:
        coordinator, request = opened()
        view = coordinator.poll("case-1", at(120))
        self.assertEqual((view.state, view.close_reason), (CaseState.CLOSED, CloseReason.EXPIRED))
        with self.assertRaises(CaseClosedError):
            coordinator.submit("case-1", Builder(), at(121))
        result = coordinator.on_weather_reply(
            weather("w", request.envelope.message_id, valid_for=500),
            WeatherSignal.IMPACT,
            "wx",
            at(121),
        )
        self.assertEqual(result.reason, "case_closed")

    def test_candidate_must_match_the_case(self) -> None:
        coordinator, _ = opened()
        coordinator.poll("case-1", at(15))

        def wrong(
            envelope: MessageEnvelope, signal: WeatherSignal, ident: str | None
        ) -> FlowCandidate:
            return Builder()(replace(envelope, snapshot_id="snap-9"), signal, ident)

        with self.assertRaises(CoordinationError):
            coordinator.submit("case-1", wrong, at(16))
        self.assertEqual(coordinator.view("case-1").candidates_sent, 0)


class CorrectionTests(TestCase):
    def setUp(self) -> None:
        self.coordinator, self.request = opened()
        self.coordinator.on_weather_reply(
            weather("w-1", self.request.envelope.message_id), WeatherSignal.IMPACT, "wx-1", at(1)
        )
        self.build = Builder()
        self.first = self.coordinator.submit("case-1", self.build, at(2))
        self.first_id = self.first.envelope.message_id

    def test_one_correction_triggers_exactly_one_more_weather_exchange(self) -> None:
        result = self.coordinator.on_correction_request(correction("c-1", self.first_id), at(3))
        self.assertEqual(result.outcome, Outcome.ACCEPTED)
        assert result.request
        self.assertEqual(result.request.attempt, 2)
        self.assertEqual(result.request.envelope.snapshot_id, "snap-1")
        view = self.coordinator.view("case-1")
        self.assertEqual(
            (view.state, view.exchanges, view.corrections), (CaseState.AWAITING_WEATHER, 2, 1)
        )

    def test_revised_candidate_answers_the_correction(self) -> None:
        result = self.coordinator.on_correction_request(correction("c-1", self.first_id), at(3))
        assert result.request
        self.coordinator.on_weather_reply(
            weather("w-2", result.request.envelope.message_id),
            WeatherSignal.NO_MATERIAL_IMPACT,
            "wx-2",
            at(5),
        )
        second = self.coordinator.submit("case-1", self.build, at(6))
        self.assertEqual(second.envelope.message_id, "case-1:candidate:2")
        self.assertEqual(second.envelope.reply_to, "c-1")
        self.assertEqual(self.build.calls[-1][:2], (WeatherSignal.NO_MATERIAL_IMPACT, "wx-2"))

    def test_duplicate_correction_does_not_open_another_exchange(self) -> None:
        envelope = correction("c-1", self.first_id)
        first = self.coordinator.on_correction_request(envelope, at(3))
        again = self.coordinator.on_correction_request(envelope, at(4))
        self.assertEqual(again.outcome, Outcome.DUPLICATE)
        self.assertEqual(again.request, first.request)
        self.assertEqual(self.coordinator.view("case-1").exchanges, 2)

    def test_second_correction_closes_the_case_and_never_exceeds_two_exchanges(self) -> None:
        asked = self.coordinator.on_correction_request(correction("c-1", self.first_id), at(3))
        assert asked.request
        self.coordinator.on_weather_reply(
            weather("w-2", asked.request.envelope.message_id), WeatherSignal.IMPACT, "wx-2", at(4)
        )
        second = self.coordinator.submit("case-1", self.build, at(5))
        result = self.coordinator.on_correction_request(
            correction("c-2", second.envelope.message_id), at(6)
        )
        self.assertEqual(
            (result.outcome, result.reason), (Outcome.REJECTED, "correction_limit_reached")
        )
        view = self.coordinator.view("case-1")
        self.assertEqual(
            (view.state, view.close_reason), (CaseState.CLOSED, CloseReason.PERSISTENT_DISAGREEMENT)
        )
        self.assertEqual((view.exchanges, view.corrections), (2, 1))
        with self.assertRaises(CaseClosedError):
            self.coordinator.submit("case-1", self.build, at(7))

    def test_second_weather_timeout_keeps_the_earlier_assessment(self) -> None:
        self.coordinator.on_correction_request(correction("c-1", self.first_id), at(3))
        view = self.coordinator.poll("case-1", at(19))
        self.assertEqual((view.state, view.weather_timed_out), (CaseState.READY, True))
        self.assertEqual(
            (view.weather_signal, view.weather_assessment_id), (WeatherSignal.IMPACT, "wx-1")
        )

    def test_invalid_corrections_are_rejected_without_changing_the_case(self) -> None:
        cases = {
            "wrong_sender_role": message(AgentName.WEATHER, "c", self.first_id),
            "snapshot_mismatch": correction("c", self.first_id, snapshot_id="snap-9"),
            "expired_request": correction("c", self.first_id, valid_for=1),
            "not_about_the_latest_candidate": correction("c", "case-1:candidate:9"),
            "unknown_case": correction("c", self.first_id, case_id="nope"),
        }
        for reason, envelope in cases.items():
            with self.subTest(reason=reason):
                result = self.coordinator.on_correction_request(envelope, at(3))
                self.assertEqual((result.outcome, result.reason), (Outcome.REJECTED, reason))
        view = self.coordinator.view("case-1")
        self.assertEqual(
            (view.state, view.corrections, view.exchanges), (CaseState.SUBMITTED, 0, 1)
        )

    def test_correction_before_a_candidate_exists_is_rejected(self) -> None:
        coordinator, _ = opened()
        result = coordinator.on_correction_request(correction("c", "case-1:candidate:1"), at(1))
        self.assertEqual(result.reason, "no_submitted_candidate")


class AssessmentRequestContractTests(TestCase):
    def envelope(self, sender: AgentName = AgentName.CONGESTION) -> MessageEnvelope:
        return message(sender, "m-1", None)

    def test_valid_request(self) -> None:
        request = AssessmentRequest(self.envelope(), T0, at(900))
        self.assertEqual(request.attempt, 1)

    def test_only_congestion_may_send_it(self) -> None:
        with self.assertRaises(ValueError):
            AssessmentRequest(self.envelope(AgentName.VALIDATION), T0, at(900))

    def test_window_and_attempt_are_bounded(self) -> None:
        for kwargs in (
            {"window_start_utc": datetime(2026, 10, 4, 1)},
            {"window_end_utc": T0},
            {"attempt": 0},
            {"attempt": 3},
            {"attempt": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AssessmentRequest(
                    **{
                        "envelope": self.envelope(),
                        "window_start_utc": T0,
                        "window_end_utc": at(900),
                        **kwargs,
                    }  # type: ignore[arg-type]
                )
