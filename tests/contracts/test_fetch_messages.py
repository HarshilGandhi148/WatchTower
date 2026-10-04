"""Compatibility checks against the actual pinned uAgents SDK serializer."""

import json
from unittest import TestCase

from shared.messages import CaseOpenedMessage, EnvelopeModel
from tests.fixtures.orchestration import opening


class FetchMessageTests(TestCase):
    def test_json_round_trip_preserves_domain_envelope(self) -> None:
        original = opening()
        message = CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(original))
        decoded = CaseOpenedMessage.model_validate_json(message.model_dump_json())
        self.assertEqual(decoded.envelope.to_envelope(), original)

    def test_received_invalid_envelopes_are_rejected_by_the_wire_boundary(self) -> None:
        message = CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(opening()))
        for changes in (
            {"schema_version": True},
            {"schema_version": 2},
            {"sender_agent": "planning"},
            {"airport_icao": "KJFK"},
            {"created_at_utc": "2026-10-03T12:00:00-04:00"},
            {"evidence_ids": ["test-record", "test-record"]},
            {"case_id": ""},
            {"unexpected_field": "test-extra"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                payload = json.loads(message.model_dump_json())
                payload["envelope"].update(changes)
                CaseOpenedMessage.model_validate_json(json.dumps(payload))
