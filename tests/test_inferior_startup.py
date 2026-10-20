import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.inferior_startup import connect_when_ready, wait_for_startup


class InferiorStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_refused_connections_are_retried(self):
        client = SimpleNamespace(connected=False, connect=AsyncMock(
            side_effect=[ConnectionRefusedError(), ConnectionRefusedError(), None]))
        await connect_when_ready(client, asyncio.get_running_loop(), interval=0)
        self.assertEqual(client.connect.await_count, 3)

    async def test_established_connection_is_not_retried(self):
        client = SimpleNamespace(connected=True, connect=AsyncMock(
            side_effect=ConnectionRefusedError()))
        with self.assertRaises(ConnectionRefusedError):
            await connect_when_ready(client, asyncio.get_running_loop(), interval=0)
        self.assertEqual(client.connect.await_count, 1)

    async def test_other_connection_errors_are_not_retried(self):
        client = SimpleNamespace(connected=False, connect=AsyncMock(
            side_effect=ValueError("handshake failed")))
        with self.assertRaises(ValueError):
            await connect_when_ready(client, asyncio.get_running_loop(), interval=0)
        self.assertEqual(client.connect.await_count, 1)

    async def test_process_exit_interrupts_connection(self):
        cancelled = asyncio.Event()

        async def connect():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        process = SimpleNamespace(wait=AsyncMock(return_value=2))
        with self.assertRaisesRegex(RuntimeError, "code 2"):
            await wait_for_startup(connect(), process)
        self.assertTrue(cancelled.is_set())

    async def test_timeout_cancels_pending_tasks(self):
        process = SimpleNamespace(wait=lambda: asyncio.Event().wait())
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            await wait_for_startup(asyncio.Event().wait(), process, timeout=0.01)

    async def test_success_prepares_session_once(self):
        client = SimpleNamespace(connected=False, connect=AsyncMock(
            side_effect=[ConnectionRefusedError(), None]))
        prepare = AsyncMock()

        async def connect():
            await connect_when_ready(client, asyncio.get_running_loop(), interval=0)
            await prepare()

        process = SimpleNamespace(wait=lambda: asyncio.Event().wait())
        await wait_for_startup(connect(), process)
        prepare.assert_awaited_once()
