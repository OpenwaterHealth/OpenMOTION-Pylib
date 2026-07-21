"""The positional dark schedule — single source of truth for both
FrameClassificationStage (which types frames) and DarkCorrectionStage
(which detects darks that never arrived)."""

import pytest

from omotion.pipeline.dark_schedule import is_dark_frame, missed_dark_ids


DEFAULTS = {"discard_count": 9, "dark_interval": 600}


def test_first_dark_is_at_discard_count_plus_one():
    assert is_dark_frame(10, **DEFAULTS)


def test_warmup_frames_are_not_dark():
    for abs_id in range(1, 10):
        assert not is_dark_frame(abs_id, **DEFAULTS)


def test_subsequent_darks_land_every_dark_interval():
    assert is_dark_frame(601, **DEFAULTS)
    assert is_dark_frame(1201, **DEFAULTS)


def test_ordinary_light_frames_are_not_dark():
    for abs_id in (11, 300, 600, 602, 1200):
        assert not is_dark_frame(abs_id, **DEFAULTS)


def test_missed_dark_ids_finds_the_gap():
    """Interval 10..1201 should have closed at 601; that dark never arrived."""
    assert missed_dark_ids(10, 1201, **DEFAULTS) == [601]


def test_missed_dark_ids_empty_for_a_nominal_interval():
    assert missed_dark_ids(601, 1201, **DEFAULTS) == []
    assert missed_dark_ids(10, 601, **DEFAULTS) == []


def test_missed_dark_ids_excludes_both_endpoints():
    """The bounding darks themselves are not 'missed'."""
    assert 10 not in missed_dark_ids(10, 1201, **DEFAULTS)
    assert 1201 not in missed_dark_ids(10, 1201, **DEFAULTS)


def test_missed_dark_ids_reports_every_gap():
    """A short interval makes multiple consecutive misses easy to construct:
    with dark_interval=3, darks fall at 10, 13, 16, 19."""
    cfg = {"discard_count": 9, "dark_interval": 3}
    assert missed_dark_ids(10, 19, **cfg) == [13, 16]


def test_dark_interval_zero_raises_valueerror():
    """dark_interval=0 must raise ValueError, not ZeroDivisionError."""
    with pytest.raises(ValueError, match="dark_interval must be positive"):
        is_dark_frame(10, discard_count=9, dark_interval=0)


def test_dark_interval_negative_raises_valueerror():
    """dark_interval < 0 must raise ValueError, not produce silent nonsense."""
    with pytest.raises(ValueError, match="dark_interval must be positive"):
        is_dark_frame(10, discard_count=9, dark_interval=-1)


def test_missed_dark_ids_adjacent_boundaries():
    """Adjacent boundaries with nothing between them yield empty list."""
    assert missed_dark_ids(10, 11, discard_count=9, dark_interval=600) == []


def test_missed_dark_ids_reversed_bounds():
    """Reversed bounds (left >= right) yield empty list, not nonsense."""
    assert missed_dark_ids(1201, 10, discard_count=9, dark_interval=600) == []


def test_is_dark_frame_with_zero_discard_count():
    """With discard_count=0, frame 1 is the first dark, frame 0 is not."""
    assert is_dark_frame(1, discard_count=0, dark_interval=600)
    assert not is_dark_frame(0, discard_count=0, dark_interval=600)
