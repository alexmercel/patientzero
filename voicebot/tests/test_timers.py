from __future__ import annotations

from voicebot.src.utils.timers import Timer


def test_timer_reports_elapsed_time() -> None:
    timer = Timer()
    timer.start()
    elapsed = timer.stop()
    assert elapsed >= 0.0
