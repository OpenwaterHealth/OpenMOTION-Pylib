"""Unit tests for the contact quality monitor."""
from __future__ import annotations

import pytest

from omotion.ContactQuality import (
    ContactQualityMonitor,
    ContactQualityWarning,
    ContactQualityWarningType,
    DARK_MEAN_THRESHOLD_DN,
    LIGHT_MEAN_THRESHOLD_DN,
    LOW_LIGHT_CONSEC_FRAMES,
)


PEDESTAL = 64.0  # matches MotionProcessing.PEDESTAL_HEIGHT at time of writing


def _bg_dark_above() -> float:
    return PEDESTAL + DARK_MEAN_THRESHOLD_DN + 1.0


def _bg_dark_below() -> float:
    return PEDESTAL + DARK_MEAN_THRESHOLD_DN - 1.0


def _bg_light_above() -> float:
    return PEDESTAL + LIGHT_MEAN_THRESHOLD_DN + 1.0


def _bg_light_below() -> float:
    return PEDESTAL + LIGHT_MEAN_THRESHOLD_DN - 1.0


def test_dark_mean_above_threshold_emits_ambient_warning():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    warnings = mon.update_dark(camera_id=0, raw_dark_mean=_bg_dark_above(), frame_index=0)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.camera_id == 0
    assert w.warning_type is ContactQualityWarningType.AMBIENT_LIGHT


def test_dark_mean_below_threshold_emits_nothing():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    warnings = mon.update_dark(camera_id=0, raw_dark_mean=_bg_dark_below(), frame_index=0)
    assert warnings == []


def test_ambient_warning_latches_until_clear():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    assert mon.update_dark(0, _bg_dark_above(), 0)  # first emission
    # Subsequent dark frames above threshold do NOT re-emit while latched.
    assert mon.update_dark(0, _bg_dark_above(), 1) == []
    assert mon.update_dark(0, _bg_dark_above(), 2) == []


def test_ambient_warning_rearms_after_clear_streak():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    mon.update_dark(0, _bg_dark_above(), 0)
    # Clear for LOW_LIGHT_CONSEC_FRAMES dark frames.
    for i in range(LOW_LIGHT_CONSEC_FRAMES):
        assert mon.update_dark(0, _bg_dark_below(), 1 + i) == []
    # Next above-threshold dark frame should re-emit.
    out = mon.update_dark(0, _bg_dark_above(), 100)
    assert len(out) == 1
    assert out[0].warning_type is ContactQualityWarningType.AMBIENT_LIGHT


def test_low_light_streak_emits_after_n_consecutive():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(LOW_LIGHT_CONSEC_FRAMES - 1):
        assert mon.update_light(0, _bg_light_below(), i) == []
    out = mon.update_light(0, _bg_light_below(), LOW_LIGHT_CONSEC_FRAMES - 1)
    assert len(out) == 1
    assert out[0].warning_type is ContactQualityWarningType.POOR_CONTACT


def test_low_light_streak_resets_on_good_frame():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(LOW_LIGHT_CONSEC_FRAMES - 1):
        mon.update_light(0, _bg_light_below(), i)
    # Single good frame resets the counter.
    assert mon.update_light(0, _bg_light_above(), LOW_LIGHT_CONSEC_FRAMES - 1) == []
    # Now we need another full streak to fire.
    for i in range(LOW_LIGHT_CONSEC_FRAMES - 1):
        assert mon.update_light(0, _bg_light_below(), 100 + i) == []
    out = mon.update_light(0, _bg_light_below(), 200)
    assert len(out) == 1


def test_per_camera_state_is_independent():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    # Camera 0 latches ambient.
    mon.update_dark(0, _bg_dark_above(), 0)
    # Camera 1 starting fresh should still emit on its first above-threshold dark.
    out = mon.update_dark(1, _bg_dark_above(), 0)
    assert len(out) == 1
    assert out[0].camera_id == 1


def test_pedestal_change_shifts_comparison():
    """Threshold constants are background-subtracted; raising the pedestal
    raises the absolute DN at which warnings fire."""
    mon_low = ContactQualityMonitor(pedestal=64.0)
    mon_high = ContactQualityMonitor(pedestal=100.0)
    raw = 64.0 + DARK_MEAN_THRESHOLD_DN + 1.0  # above for pedestal=64
    assert mon_low.update_dark(0, raw, 0)              # fires
    assert mon_high.update_dark(0, raw, 0) == []       # below for pedestal=100
