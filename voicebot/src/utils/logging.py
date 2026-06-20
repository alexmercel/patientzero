"""Structured logging helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import structlog

_event_publisher: Any | None = None
_log_file_path: Path | None = None


def set_log_file_path(log_file_path: Path | None) -> None:
    """Register an optional JSONL sink for application logs."""
    global _log_file_path
    _log_file_path = log_file_path


def set_event_publisher(event_publisher: Any | None) -> None:
    """Register a side-channel publisher for dashboard log streaming."""
    global _event_publisher
    _event_publisher = event_publisher


def _publish_log_event(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Forward structured log entries to the dashboard event bus."""
    if _event_publisher is not None:
        _event_publisher("log", dict(event_dict))
    if _log_file_path is not None:
        _log_file_path.parent.mkdir(parents=True, exist_ok=True)
        with _log_file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event_dict, default=str) + "\n")
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    event_publisher: Any | None = None,
    log_file_path: Path | None = None,
) -> None:
    """Configure application-wide JSON logging."""
    set_event_publisher(event_publisher)
    set_log_file_path(log_file_path)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _publish_log_event,
        structlog.processors.JSONRenderer(),
    ]

    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO), format="%(message)s")
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger."""
    return structlog.get_logger(name)
