"""
Closing out a match clock.

A match keeps its elapsed time in two halves: `timer_elapsed_seconds`, which is
only ever added to when the match is paused, and `timer_started_at`, the epoch
milliseconds of the current run. The live figure is the sum of the two, and the
UI stops counting when `is_timer_running` goes false.

Nothing was setting that flag false when a match finished. Start, pause and
resume each maintained the timer properly, but the paths that end a match --
the last board completing it, and the umpire confirming the result -- wrote
`status = 'completed'` and nothing else. So the clock kept running on a match
that had already been won, and the recorded duration was whatever it had been
at the last pause rather than the time the match actually took.
"""
from datetime import datetime, timezone
from typing import Any, Dict


def freeze_timer_when_finished(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add the timer fields to `patch` when it takes the match to 'completed'.

    `current` is the match row as it stands; `patch` is the update about to be
    applied. Returns the same patch, extended in place, so callers can wrap an
    existing dict without restructuring.
    """
    if patch.get("status") != "completed":
        return patch

    # Already stopped -- paused, or completed by an earlier write. Whatever is
    # banked is the real total, so only assert the flag.
    if not current.get("is_timer_running"):
        patch["is_timer_running"] = False
        return patch

    elapsed = current.get("timer_elapsed_seconds") or 0
    started_at = current.get("timer_started_at")
    if started_at:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        # A negative delta means the clocks disagree; banking it would shorten a
        # match that had genuinely been running.
        elapsed += max(0, int((now_ms - started_at) / 1000))

    patch["is_timer_running"] = False
    patch["timer_elapsed_seconds"] = elapsed
    return patch
