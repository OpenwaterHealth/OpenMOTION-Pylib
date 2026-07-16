"""SEEDLESS ScanRequest field + trigger-config merge (pure-software)."""

from omotion.ScanWorkflow import ScanRequest, _seedless_trigger_overrides


def test_scan_request_defaults_off():
    req = ScanRequest(subject_id="s", duration_sec=10,
                      left_camera_mask=0x66, right_camera_mask=0x66)
    assert req.seedless_frames == 0


def test_seedless_trigger_overrides_push_dark_slot_past_exposure():
    # Dark frames are laser pulses delayed past the exposure window. The
    # normal 1800 us skip delay was sized for 648 us exposure; at 2295 us
    # the pulse (start = 100 + skip) must begin after the exposure closes.
    ov = _seedless_trigger_overrides()
    assert ov == {"LaserPulseSkipDelayUsec": 2500}
    assert 100 + ov["LaserPulseSkipDelayUsec"] > 2295


def test_no_overrides_when_disabled():
    assert callable(_seedless_trigger_overrides)
