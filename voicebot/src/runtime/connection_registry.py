"""Shared tracking for live websocket and bridge resources."""

from __future__ import annotations

import asyncio
from typing import Any

from voicebot.src.utils.logging import get_logger


class ConnectionRegistry:
    """Tracks active realtime resources so shutdown can close them cleanly."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.shutdown_event = asyncio.Event()
        self._websockets: set[Any] = set()
        self._bridges: set[Any] = set()
        self._lock = asyncio.Lock()

    async def register_websocket(self, websocket: Any) -> None:
        async with self._lock:
            self._websockets.add(websocket)

    async def unregister_websocket(self, websocket: Any) -> None:
        async with self._lock:
            self._websockets.discard(websocket)

    async def register_bridge(self, bridge: Any) -> None:
        async with self._lock:
            self._bridges.add(bridge)

    async def unregister_bridge(self, bridge: Any) -> None:
        async with self._lock:
            self._bridges.discard(bridge)

    async def close_all(self) -> None:
        """Close all tracked bridges and websockets during shutdown."""
        self.shutdown_event.set()
        async with self._lock:
            bridges = list(self._bridges)
            websockets = list(self._websockets)

        if bridges:
            bridge_results = await asyncio.gather(
                *(bridge.close() for bridge in bridges),
                return_exceptions=True,
            )
            for result in bridge_results:
                if isinstance(result, Exception):
                    self.logger.warning(
                        "connection_registry_bridge_close_failed",
                        error=str(result),
                    )

        if websockets:
            websocket_results = await asyncio.gather(
                *(self._close_websocket(websocket) for websocket in websockets),
                return_exceptions=True,
            )
            for result in websocket_results:
                if isinstance(result, Exception):
                    self.logger.warning(
                        "connection_registry_websocket_close_failed",
                        error=str(result),
                    )

    async def _close_websocket(self, websocket: Any) -> None:
        try:
            await websocket.close(code=1012, reason="Server shutting down")
        except RuntimeError:
            return
