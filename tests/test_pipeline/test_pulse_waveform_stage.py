"""PulseWaveformStage — consumes live_side SideAverageSamples, emits
LiveEmit("pulse", PulseAnalysis). Additive only; numpy; no hardware."""

from types import SimpleNamespace

import numpy as np

from omotion.pipeline.batch import LiveEmit, SideAverageSample
from omotion.pipeline.stages.pulse_waveform import PulseWaveformStage
from omotion.pulse.types import PulseAnalysis
from omotion.pulse.synth import synth_pair


def _ls_event(side, t, bfi):
    return LiveEmit(channel="live_side",
                    payload=SideAverageSample(t=float(t), frame_id=0,
                                              side=int(side), bfi=float(bfi),
                                              bvi=0.0))


def _batch(events):
    return SimpleNamespace(events=list(events))


def _pulse_emits(batch):
    return [e for e in batch.events
            if isinstance(e, LiveEmit) and e.channel == "pulse"]


def _feed_pair(stage, t, left, right):
    """One batch per frame, each carrying a left + right live_side sample."""
    out = []
    for i in range(len(t)):
        b = _batch([_ls_event(0, t[i], left[i]), _ls_event(1, t[i], right[i])])
        stage.process(b)
        out.extend(_pulse_emits(b))
    return out


def test_disabled_stage_emits_nothing():
    stage = PulseWaveformStage(enabled=False)
    t, left, right = synth_pair(duration_s=6.0, bpm=72.0, seed=1)
    assert _feed_pair(stage, t, left, right) == []


def test_emits_pulse_events_for_both_sides():
    stage = PulseWaveformStage(enabled=True, emit_every=8)
    t, left, right = synth_pair(duration_s=10.0, bpm=72.0, seed=1)
    emits = _feed_pair(stage, t, left, right)
    assert emits, "expected at least one pulse emit"
    sides = {e.payload.side for e in emits}
    assert sides == {"left", "right"}
    assert all(isinstance(e.payload, PulseAnalysis) for e in emits)


def test_does_not_disturb_existing_events():
    stage = PulseWaveformStage(enabled=True, emit_every=1)
    b = _batch([_ls_event(0, 0.0, 5.0), _ls_event(1, 0.0, 5.0)])
    n_before = len(b.events)
    stage.process(b)
    # The two original live_side events remain; only pulse events were added.
    live_side = [e for e in b.events if e.channel == "live_side"]
    assert len(live_side) == 2
    assert len(b.events) >= n_before


def test_recovers_heart_rate_through_stage():
    stage = PulseWaveformStage(enabled=True, emit_every=8)
    t, left, right = synth_pair(duration_s=14.0, bpm=72.0, hrv_frac=0.0,
                                noise=0.0, wander=0.0, seed=1)
    emits = _feed_pair(stage, t, left, right)
    last_left = [e.payload for e in emits if e.payload.side == "left"][-1]
    assert 68.0 <= last_left.features.hr_bpm <= 76.0


def test_amplitude_asymmetry_shows_in_features():
    stage = PulseWaveformStage(enabled=True, emit_every=8)
    t, left, right = synth_pair(duration_s=14.0, bpm=72.0, hrv_frac=0.0,
                                noise=0.0, wander=0.0, right_amp_ratio=0.5,
                                seed=2)
    emits = _feed_pair(stage, t, left, right)
    last_left = [e.payload for e in emits if e.payload.side == "left"][-1]
    last_right = [e.payload for e in emits if e.payload.side == "right"][-1]
    assert last_right.features.amp < last_left.features.amp


def test_reset_clears_analyzers():
    stage = PulseWaveformStage(enabled=True, emit_every=8)
    t, left, right = synth_pair(duration_s=10.0, bpm=72.0, seed=1)
    _feed_pair(stage, t, left, right)
    stage.reset()
    b = _batch([_ls_event(0, 0.0, 5.0), _ls_event(1, 0.0, 5.0)])
    stage.process(b)
    for e in _pulse_emits(b):
        assert e.payload.beat_count == 0


def test_modwt_band_method_recovers_heart_rate_through_stage():
    stage = PulseWaveformStage(enabled=True, emit_every=8, band_method="modwt")
    t, left, right = synth_pair(duration_s=14.0, bpm=72.0, hrv_frac=0.0,
                                noise=0.0, wander=0.0, seed=1)
    emits = _feed_pair(stage, t, left, right)
    last_left = [e.payload for e in emits if e.payload.side == "left"][-1]
    assert 68.0 <= last_left.features.hr_bpm <= 76.0


def test_has_stage_protocol_shape():
    stage = PulseWaveformStage(enabled=True)
    assert stage.name == "pulse_waveform"
    assert hasattr(stage, "process") and hasattr(stage, "reset")
    assert hasattr(stage, "on_scan_stop")
