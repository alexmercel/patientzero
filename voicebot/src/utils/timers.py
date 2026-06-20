"""Timer helpers for instrumentation."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter


@dataclass(slots=True)
class Timer:
    """Small wall-clock timer."""

    started_at: float | None = None
    finished_at: float | None = None

    def start(self) -> None:
        """Start the timer."""
        self.started_at = perf_counter()
        self.finished_at = None

    def stop(self) -> float:
        """Stop the timer and return elapsed seconds."""
        if self.started_at is None:
            raise RuntimeError("Timer was never started.")
        self.finished_at = perf_counter()
        return self.elapsed_seconds

    @property
    def elapsed_seconds(self) -> float:
        """Return elapsed wall-clock time."""
        if self.started_at is None:
            raise RuntimeError("Timer was never started.")
        endpoint = self.finished_at if self.finished_at is not None else perf_counter()
        return endpoint - self.started_at
