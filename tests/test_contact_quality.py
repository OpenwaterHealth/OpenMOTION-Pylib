"""Unit tests for the contact quality monitor."""
from __future__ import annotations

from omotion.ContactQuality import (
    ContactQualityMonitor,
    ContactQualityResult,
    ContactQualityWarning,
    ContactQualityWarningType,
    DARK_MEAN_THRESHOLD_DN,
    LIGHT_MEAN_THRESHOLD_DN,
    LIVE_LIGHT_WINDOW_FRAMES,
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


def test_finalize_emits_poor_contact_when_avg_below_threshold():
    """10 light frames averaging 80 DN (below pedestal+threshold=94 DN) →
    finalize emits exactly one POOR_CONTACT warning. (The live rolling
    path may fire on the 10th frame; finalize path is independent and
    still emits based on the cumulative average.)"""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(10):
        mon.update_light(0, 80.0, i, side="left")
    out = mon.finalize()
    assert len(out) == 1
    w = out[0]
    assert w.warning_type is ContactQualityWarningType.POOR_CONTACT
    assert w.camera_id == 0
    assert w.side == "left"
    assert w.value == 80.0
    assert w.frame_index == 10


def test_finalize_emits_nothing_when_avg_above_threshold():
    """10 light frames averaging 100 DN (above pedestal+threshold=94 DN) →
    finalize emits no warnings."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(10):
        assert mon.update_light(0, 100.0, i, side="left") == []
    assert mon.finalize() == []


def test_per_camera_summary_returns_stats_per_camera():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(5):
        mon.update_light(0, 90.0, i, side="left")
    for i in range(3):
        mon.update_light(1, 110.0, i, side="left")
    mon.finalize()
    rows = mon.per_camera_summary()
    assert len(rows) == 2
    by_cam = {r["cam_id"]: r for r in rows}
    assert by_cam[0]["light_frames"] == 5
    assert by_cam[0]["light_mean_avg"] == 90.0
    assert by_cam[0]["label"] == "L1"
    assert by_cam[0]["ambient_latched"] is False
    assert by_cam[1]["light_frames"] == 3
    assert by_cam[1]["light_mean_avg"] == 110.0
    assert by_cam[1]["label"] == "L2"


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


def test_monitor_emits_distinct_warnings_per_camera():
    """Smoke-level integration: feed a sequence representing two cameras
    and verify aggregated warnings."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    # Camera 0: ambient on first dark frame.
    a = mon.update_dark(0, _bg_dark_above(), 0)
    # Camera 1: averaged poor-contact warning emitted on finalize.
    for i in range(LOW_LIGHT_CONSEC_FRAMES):
        assert mon.update_light(1, _bg_light_below(), i) == []
    out_b = mon.finalize()
    cams = sorted({w.camera_id for w in [*a, *out_b]})
    types = sorted({w.warning_type.value for w in [*a, *out_b]})
    assert cams == [0, 1]
    assert types == ["ambient_light", "poor_contact"]


def test_result_has_error_field():
    """``ContactQualityResult`` carries an optional human-readable error
    string; defaults to empty when not provided."""
    r_ok = ContactQualityResult(ok=True)
    assert r_ok.error == ""

    r_fail = ContactQualityResult(ok=False, error="x")
    assert r_fail.error == "x"
    assert r_fail.ok is False
    assert r_fail.warnings == []


def test_side_is_propagated_into_warning():
    """The optional ``side`` kwarg on ``update_dark``/``update_light`` is
    stamped into the emitted ``ContactQualityWarning``."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    dark = mon.update_dark(3, _bg_dark_above(), 0, side="left")
    assert len(dark) == 1
    assert dark[0].side == "left"
    assert dark[0].camera_id == 3

    # Drive camera 5 on the right module to a poor-contact emission via
    # the averaged ``finalize`` path. Per-frame updates return [] now.
    for i in range(LOW_LIGHT_CONSEC_FRAMES):
        assert mon.update_light(5, _bg_light_below(), i, side="right") == []
    light = [
        w for w in mon.finalize()
        if w.warning_type is ContactQualityWarningType.POOR_CONTACT and w.camera_id == 5
    ]
    assert len(light) == 1
    assert light[0].side == "right"
    assert light[0].camera_id == 5


# --- Live rolling-window poor-contact detection ----------------------------


def test_live_rolling_window_emits_after_full_window_below_threshold():
    """Feed LIVE_LIGHT_WINDOW_FRAMES-1 below-threshold light frames — no
    emission. The window-filling frame triggers exactly one POOR_CONTACT
    warning with value equal to the rolling average."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    low = 80.0  # < 64 + 30 = 94
    for i in range(LIVE_LIGHT_WINDOW_FRAMES - 1):
        assert mon.update_light(0, low, i, side="left") == []
    out = mon.update_light(0, low, LIVE_LIGHT_WINDOW_FRAMES - 1, side="left")
    assert len(out) == 1
    w = out[0]
    assert w.warning_type is ContactQualityWarningType.POOR_CONTACT
    assert w.camera_id == 0
    assert w.side == "left"
    assert w.value == low
    assert w.frame_index == LIVE_LIGHT_WINDOW_FRAMES - 1


def test_live_rolling_window_does_not_emit_with_partial_window():
    """A partial window (< LIVE_LIGHT_WINDOW_FRAMES samples) never fires,
    even if every sample is below threshold."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(5):
        assert mon.update_light(0, 80.0, i, side="left") == []


def test_live_rolling_window_clears_when_avg_rises():
    """After latching on 10 below-threshold frames, feeding high-value
    frames eventually drops the rolling average back above threshold,
    clears the latch, and a subsequent dip can re-fire."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    # Latch.
    for i in range(LIVE_LIGHT_WINDOW_FRAMES):
        mon.update_light(0, 80.0, i, side="left")
    assert mon._state_for("left", 0).contact_latched is True

    # Feed high values until the window average recovers and the latch
    # clears. 110 DN is well above the 94 DN threshold.
    i = LIVE_LIGHT_WINDOW_FRAMES
    for _ in range(LIVE_LIGHT_WINDOW_FRAMES * 2):
        mon.update_light(0, 110.0, i, side="left")
        i += 1
        if not mon._state_for("left", 0).contact_latched:
            break
    assert mon._state_for("left", 0).contact_latched is False

    # A subsequent dip should re-fire once the window refills with lows.
    fired = None
    for _ in range(LIVE_LIGHT_WINDOW_FRAMES):
        out = mon.update_light(0, 80.0, i, side="left")
        i += 1
        if out:
            fired = out
            break
    assert fired is not None
    assert len(fired) == 1
    assert fired[0].warning_type is ContactQualityWarningType.POOR_CONTACT


def test_live_rolling_window_does_not_re_emit_while_latched():
    """Once latched, additional below-threshold frames return [] until
    the rolling average clears."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(LIVE_LIGHT_WINDOW_FRAMES):
        mon.update_light(0, 80.0, i, side="left")
    # Latched now. Feed more lows; no re-emission.
    for j in range(LIVE_LIGHT_WINDOW_FRAMES):
        assert mon.update_light(
            0, 80.0, LIVE_LIGHT_WINDOW_FRAMES + j, side="left",
        ) == []


def test_per_camera_summary_includes_rolling_and_contact_latched():
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(LIVE_LIGHT_WINDOW_FRAMES):
        mon.update_light(0, 80.0, i, side="left")
    rows = mon.per_camera_summary()
    assert len(rows) == 1
    r = rows[0]
    assert r["contact_latched"] is True
    assert r["rolling_avg_light_mean"] == 80.0


# --- Side-keyed state isolation (regression: left/right shared state) ------


def test_left_and_right_sides_have_independent_state():
    """cam_id 0 on the left module and cam_id 0 on the right module must
    maintain independent ambient-latch state. Before the (side, cam_id)
    keying fix, the second side's warning was suppressed by the first
    side's latch."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    raw = _bg_dark_above()
    left_out = mon.update_dark(0, raw, 0, side="left")
    right_out = mon.update_dark(0, raw, 0, side="right")
    assert len(left_out) == 1
    assert left_out[0].side == "left"
    assert len(right_out) == 1
    assert right_out[0].side == "right"


def test_per_camera_summary_includes_both_sides_for_same_cam_id():
    """Feeding light frames for left cam 0 and right cam 0 should yield
    two distinct summary rows with labels ``L1`` and ``R1``."""
    mon = ContactQualityMonitor(pedestal=PEDESTAL)
    for i in range(5):
        mon.update_light(0, 90.0, i, side="left")
    for i in range(5):
        mon.update_light(0, 90.0, i, side="right")
    rows = mon.per_camera_summary()
    assert len(rows) == 2
    labels = sorted(r["label"] for r in rows)
    assert labels == ["L1", "R1"]
    sides = sorted(r["side"] for r in rows)
    assert sides == ["left", "right"]
    for r in rows:
        assert r["cam_id"] == 0
        assert r["light_frames"] == 5
        assert r["light_mean_avg"] == 90.0
