"""Concurrent requests, persistence and real SDK handler boundaries."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from uagents import Context
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent

from agents.weather.agent import AgentConfig, build_agent
from agents.weather.chat import WeatherChat
from agents.weather.service import WeatherDependencies, WeatherProtocolError, WeatherService
from agents.weather.tests.helpers import NOW, request, snapshot
from shared.messages import WeatherAssessmentReplyMessage, WeatherAssessmentRequestMessage


class ServiceTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.snap = snapshot()
        self.req = request(self.snap)
        self.now = NOW
        self.load = AsyncMock(return_value=self.snap)
        self.persist = AsyncMock()
        self.dependencies = WeatherDependencies(self.load, self.persist)
        self.service = WeatherService(self.dependencies, clock=lambda: self.now)

    async def test_concurrent_duplicates_share_assessment_and_persistence(self) -> None:
        gate = asyncio.Event()

        async def load(identity):  # type: ignore[no-untyped-def]
            await gate.wait()
            return self.snap

        self.load.side_effect = load
        tasks = [asyncio.create_task(self.service.handle(self.req)) for _ in range(5)]
        await asyncio.sleep(0.01)
        gate.set()
        replies = await asyncio.gather(*tasks)
        self.assertTrue(all(r == replies[0] for r in replies))
        self.load.assert_awaited_once()
        self.persist.assert_awaited_once()
        self.assertEqual(await self.service.handle(self.req), replies[0])
        _, status, identity = WeatherAssessmentReplyMessage.parse_raw(replies[0].json()).to_reply()
        self.assertEqual(status, "no_material_impact")
        self.assertEqual(identity, self.persist.call_args.args[0].assessment_id)
        self.assertEqual(replies[0].envelope.correlation_id, self.req.envelope.correlation_id)

    async def test_changed_retry_and_case_snapshot_or_window_rejected(self) -> None:
        await self.service.handle(self.req)
        changed = replace(self.req, window_end_utc=self.req.window_end_utc + timedelta(minutes=1))
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(changed)
        for modified in (
            replace(self.req, envelope=replace(self.req.envelope, message_id="other")),
            replace(
                self.req,
                attempt=2,
                envelope=replace(self.req.envelope, message_id="other", snapshot_id="other"),
            ),
            replace(
                self.req,
                attempt=2,
                envelope=replace(self.req.envelope, message_id="other"),
                window_end_utc=self.req.window_end_utc + timedelta(minutes=1),
            ),
        ):
            with self.assertRaises(WeatherProtocolError):
                await self.service.handle(modified)

    async def test_two_rounds_only_and_expired_replay_rejected(self) -> None:
        second = request(self.snap, attempt=2, message_id="request-2")
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(second)
        first = await self.service.handle(self.req)
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(
                replace(
                    second,
                    envelope=replace(
                        second.envelope,
                        valid_until_utc=second.envelope.valid_until_utc + timedelta(seconds=1),
                    ),
                )
            )
        reply = await self.service.handle(second)
        self.assertNotEqual(first.assessment_id, reply.assessment_id)
        self.assertEqual(reply.envelope.reply_to, "request-2")
        self.now += timedelta(minutes=3)
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(self.req)

    async def test_retry_cannot_publish_evidence_that_became_stale(self) -> None:
        from agents.weather.tests.helpers import metar_payload

        self.snap = snapshot(metar={**metar_payload(), "obsTime": NOW.timestamp() - 5399})
        self.load.return_value = self.snap
        self.req = request(self.snap)
        self.persist.side_effect = RuntimeError("lost acknowledgement")
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(self.req)
        self.now += timedelta(seconds=2)
        self.persist.side_effect = None
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(self.req)
        self.assertEqual(self.persist.await_count, 1)

    async def test_failed_persistence_reuses_exact_result(self) -> None:
        self.persist.side_effect = [RuntimeError("secret database response"), None]
        with self.assertRaises(WeatherProtocolError) as caught:
            await self.service.handle(self.req)
        self.assertNotIn("secret", str(caught.exception))
        first = self.persist.call_args.args[0]
        self.now += timedelta(seconds=1)
        reply = await self.service.handle(self.req)
        self.assertEqual(first, self.persist.call_args.args[0])
        self.assertEqual(reply.assessment_id, first.assessment_id)
        self.load.assert_awaited_once()

    async def test_load_failure_and_snapshot_mismatch_do_not_persist(self) -> None:
        for failure in (RuntimeError("secret"), replace(self.snap, snapshot_id="other")):
            self.load.side_effect = failure if isinstance(failure, Exception) else None
            self.load.return_value = failure
            with self.assertRaises(WeatherProtocolError):
                await self.service.handle(self.req)
            self.persist.assert_not_awaited()

    async def test_expiry_after_persistence_no_reply(self) -> None:
        async def persist(result):  # type: ignore[no-untyped-def]
            self.now = NOW + timedelta(minutes=3)

        self.persist.side_effect = persist
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(self.req)

    async def test_cancellation_of_waiter_does_not_cancel_shared_work(self) -> None:
        gate = asyncio.Event()

        async def load(identity):  # type: ignore[no-untyped-def]
            await gate.wait()
            return self.snap

        self.load.side_effect = load
        waiter = asyncio.create_task(self.service.handle(self.req))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        gate.set()
        await self.service.handle(self.req)
        self.persist.assert_awaited_once()
        await self.service.close()

    async def test_second_round_cannot_overlap_first(self) -> None:
        gate = asyncio.Event()

        async def load(identity):  # type: ignore[no-untyped-def]
            await gate.wait()
            return self.snap

        self.load.side_effect = load
        first = asyncio.create_task(self.service.handle(self.req))
        await asyncio.sleep(0.01)
        with self.assertRaises(WeatherProtocolError):
            await self.service.handle(request(self.snap, attempt=2, message_id="second"))
        gate.set()
        await first

    async def test_insufficient_data_is_stored_and_referenced(self) -> None:
        self.snap = snapshot(metar=False, taf=False)
        self.load.return_value = self.snap
        reply = await self.service.handle(request(self.snap))
        self.assertEqual(reply.status, "insufficient_data")
        self.assertIsNotNone(reply.assessment_id)
        self.persist.assert_awaited_once()


ENV = {
    "WEATHER_AGENT_SEED": "WatchTower TEST ONLY weather identity",
    "CONGESTION_AGENT_ADDRESS": "test-congestion",
    "VALIDATION_AGENT_ADDRESS": "test-validation",
}


class AgentTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for target, value in (
            ("uagents.agent.get_ledger", MagicMock()),
            ("uagents.agent.get_almanac_contract", None),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_signed_handler_allowlist_and_send_retry(self) -> None:
        snap = snapshot()
        load, persist = AsyncMock(return_value=snap), AsyncMock()
        dependencies = WeatherDependencies(load, persist)
        service = WeatherService(dependencies, clock=lambda: NOW)
        agent = build_agent(
            AgentConfig.from_env(ENV),
            dependencies,
            service=service,
            loop=asyncio.get_running_loop(),
        )
        digest = WeatherAssessmentRequestMessage.build_schema_digest(
            WeatherAssessmentRequestMessage
        )
        handler = agent._protocol.signed_message_handlers[digest]
        self.assertNotIn(digest, agent._protocol.unsigned_message_handlers)
        ctx = MagicMock(spec=Context)
        ctx.send = AsyncMock(side_effect=[RuntimeError("secret send failure"), None])
        message = WeatherAssessmentRequestMessage.from_request(request(snap))
        await handler(ctx, "unassigned", message)
        load.assert_not_awaited()
        await handler(ctx, "test-congestion", message)
        await handler(ctx, "test-congestion", message)
        self.assertEqual(ctx.send.await_count, 2)
        persist.assert_awaited_once()
        self.assertEqual(ctx.send.call_args_list[0].args, ctx.send.call_args_list[1].args)
        self.assertEqual(agent.name, "weather")

    async def test_chat_protocol_shares_identity_and_never_persists(self) -> None:
        load, persist = AsyncMock(return_value=snapshot()), AsyncMock()
        chat_load = AsyncMock(return_value=snapshot())
        agent = build_agent(
            AgentConfig.from_env(ENV),
            WeatherDependencies(load, persist),
            chat=WeatherChat(chat_load, clock=lambda: NOW),
            loop=asyncio.get_running_loop(),
        )
        digest = ChatMessage.build_schema_digest(ChatMessage)
        handler = agent._signed_message_handlers[digest]
        self.assertNotIn(digest, agent._unsigned_message_handlers)
        ctx = MagicMock(spec=Context)
        ctx.send = AsyncMock()
        message = ChatMessage(
            timestamp=NOW, msg_id=uuid4(), content=[TextContent(type="text", text="weather")]
        )
        await handler(ctx, "chat-user", message)
        acknowledgement = ctx.send.call_args_list[0].args[1]
        self.assertIsInstance(acknowledgement, ChatAcknowledgement)
        self.assertEqual(acknowledgement.acknowledged_msg_id, message.msg_id)
        self.assertIsNotNone(acknowledgement.timestamp.tzinfo)
        self.assertIn("KDTW", ctx.send.call_args_list[1].args[1].content[0].text)
        load.assert_not_awaited()
        persist.assert_not_awaited()


class ConfigTests(TestCase):
    def test_required_config_and_secret_repr(self) -> None:
        for name in ENV:
            with self.assertRaises(ValueError):
                AgentConfig.from_env({**ENV, name: ""})
        self.assertNotIn(ENV["WEATHER_AGENT_SEED"], repr(AgentConfig.from_env(ENV)))
        with self.assertRaises(ValueError):
            AgentConfig.from_env({**ENV, "APP_MODE": "test"})
