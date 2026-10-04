"""Congestion's stored projection payload passes validation's recomputing retrieval hook.

Cross-owner test: prepares a projection from a TEST stored snapshot with the real
congestion code, stores it the way the adapter serializes it, and loads it through the
real ValidationStorage.projection(), which rejects anything a recomputation differs from.
No database or provider is contacted; the adapter is a TEST double.
"""

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agents.congestion.feeds import stored_feeds
from agents.congestion.projections import ProjectionSettings, prepare_projection
from agents.congestion.tests.test_projections import stored_snapshot
from agents.validation.storage import ValidationStorage
from shared.persistence import storage_json
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError


class ProjectionStorageTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        prepared = prepare_projection(stored_feeds(stored_snapshot()), ProjectionSettings(30, 6, 4))
        assert prepared is not None
        self.prepared = prepared
        self.stored = json.loads(storage_json(prepared.payload()))
        self.adapter = MagicMock(spec=SpacetimeAdapter)
        self.adapter.load_projection = AsyncMock(return_value=self.stored)

    async def test_validation_recomputes_the_stored_congestion_projection(self) -> None:
        result = await ValidationStorage(self.adapter).projection(self.prepared.projection_id)
        self.assertEqual(result, self.prepared.comparison)

    async def test_a_changed_stored_result_is_rejected(self) -> None:
        self.stored["result"]["proposal"]["metrics"]["peak_runway_queue"] += 1
        with self.assertRaises(SpacetimeError):
            await ValidationStorage(self.adapter).projection(self.prepared.projection_id)
