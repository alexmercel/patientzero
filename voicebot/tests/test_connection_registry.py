from __future__ import annotations

import pytest

from voicebot.src.runtime.connection_registry import ConnectionRegistry


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = []

    async def close(self, code: int, reason: str) -> None:
        self.closed = True
        self.close_calls.append((code, reason))


class FakeBridge:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_connection_registry_closes_tracked_resources() -> None:
    registry = ConnectionRegistry()
    websocket = FakeWebSocket()
    bridge = FakeBridge()

    await registry.register_websocket(websocket)
    await registry.register_bridge(bridge)
    await registry.close_all()

    assert registry.shutdown_event.is_set() is True
    assert websocket.closed is True
    assert websocket.close_calls == [(1012, "Server shutting down")]
    assert bridge.close_calls == 1


@pytest.mark.asyncio
async def test_connection_registry_shutdown_is_idempotent() -> None:
    registry = ConnectionRegistry()

    await registry.close_all()
    await registry.close_all()

    assert registry.shutdown_event.is_set() is True
