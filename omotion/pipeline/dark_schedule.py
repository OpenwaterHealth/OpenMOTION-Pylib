"""The positional dark-frame schedule.

Single source of truth, imported by both FrameClassificationStage (which
types incoming frames) and DarkCorrectionStage (which detects scheduled
darks that never arrived). Two independent copies of this rule would drift
and silently corrupt dark correction — see docs/SciencePipeline.md §4.2.
"""

from __future__ import annotations


def is_dark_frame(abs_id: int, *, discard_count: int, dark_interval: int) -> bool:
    """True when abs_id lands on a scheduled dark position.

    Per SciencePipeline.md §4.2:
        n == discard_count + 1
        OR (n > discard_count + 1 AND (n - 1) mod dark_interval == 0)
    """
    if dark_interval <= 0:
        raise ValueError(f"dark_interval must be positive, got {dark_interval}")
    if abs_id == discard_count + 1:
        return True
    if abs_id <= discard_count + 1:
        return False
    return (abs_id - 1) % dark_interval == 0


def missed_dark_ids(left_abs: int, right_abs: int, *,
                    discard_count: int, dark_interval: int) -> list[int]:
    """Scheduled dark positions strictly between two interval boundaries.

    A closed interval should be bounded by consecutive scheduled darks. Any
    scheduled position falling *inside* it is a dark that never arrived, so
    the interval spans wider than nominal and its baseline interpolation is
    stretched across the gap. Both endpoints are excluded — they are the
    darks that did arrive.

    When left_abs >= right_abs, the range is non-advancing, so an empty list
    is returned (not an error). This contract ensures a swapped-argument bug
    at a call site reports 0 missed darks instead of silently producing wrong
    results.
    """
    return [
        n for n in range(int(left_abs) + 1, int(right_abs))
        if is_dark_frame(n, discard_count=discard_count,
                         dark_interval=dark_interval)
    ]
