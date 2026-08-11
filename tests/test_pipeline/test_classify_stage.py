"""FrameClassificationStage — abs_frame_id unwrap + frame_type labeling."""

import logging

import numpy as np
import pytest
from omotion.pipeline.batch import FrameBatch
from omotion.pipeline.stages.classify import FrameClassificationStage


def _batch_with_raw_ids(raw_ids_per_side_cam):
    """raw_ids_per_side_cam: dict {(side_idx, cam_id): list_of_raw_frame_ids}."""
    rows = []
    for (side_idx, cam_id), raw_ids in raw_ids_per_side_cam.items():
        for raw_id in raw_ids:
            rows.append((side_idx, cam_id, raw_id))

    n = len(rows)
    cam_ids = np.array([r[1] for r in rows], dtype=np.int8)
    frame_ids = np.array([r[2] for r in rows], dtype=np.uint8)
    side_ids = np.array([r[0] for r in rows], dtype=np.int8)
    raw_hists = np.zeros((n, 2, 8, 1024), dtype=np.uint32)
    for i, (s, c, _) in enumerate(rows):
        raw_hists[i, s, c, 0] = 1
    return FrameBatch(
        cam_ids=cam_ids,
        frame_ids=frame_ids,
        side_ids=side_ids,
        raw_histograms=raw_hists,
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.arange(n, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
    )


def test_first_frame_with_raw_id_1_is_warmup_not_stale():
    batch = _batch_with_raw_ids({(0, 0): [1, 2, 3]})
    FrameClassificationStage(discard_count=9, dark_interval=600).process(batch)
    np.testing.assert_array_equal(batch.abs_frame_ids, [1, 2, 3])
    np.testing.assert_array_equal(batch.frame_type, ["warmup", "warmup", "warmup"])


def test_first_frame_with_raw_id_other_than_1_is_stale():
    batch = _batch_with_raw_ids({(0, 0): [42, 43, 44]})
    FrameClassificationStage(discard_count=9, dark_interval=600).process(batch)
    assert batch.frame_type[0] == "stale"


def test_warmup_range_marks_first_9_as_warmup():
    raw_ids = list(range(1, 15))
    batch = _batch_with_raw_ids({(0, 0): raw_ids})
    FrameClassificationStage(discard_count=9, dark_interval=600).process(batch)
    expected = ["warmup"] * 9 + ["dark"] + ["light"] * 4
    np.testing.assert_array_equal(batch.frame_type, expected)


def test_dark_schedule_fires_at_intervals():
    raw_ids = list(range(1, 25))
    batch = _batch_with_raw_ids({(0, 0): raw_ids})
    FrameClassificationStage(discard_count=9, dark_interval=10).process(batch)

    expected_darks_at = {10, 11, 21}
    for i, raw_id in enumerate(raw_ids):
        is_dark = (i + 1) in expected_darks_at
        assert (batch.frame_type[i] == "dark") == is_dark, \
            f"abs={i+1}: got {batch.frame_type[i]}, expected dark={is_dark}"


def test_unwrap_handles_8bit_rollover():
    raw_ids = list(range(250, 256)) + list(range(0, 5))
    batch = _batch_with_raw_ids({(0, 0): raw_ids})
    FrameClassificationStage(discard_count=9, dark_interval=600).process(batch)
    expected_abs = [250, 251, 252, 253, 254, 255, 256, 257, 258, 259, 260]
    np.testing.assert_array_equal(batch.abs_frame_ids, expected_abs)


def test_stale_leftover_frames_at_start_are_rejected_not_offsetting_epoch():
    """Reproduces the Varun-unit left-sensor capture (issue #172): an unflushed
    histogram buffer emits stale frames (raw 255, 173) after the real first
    frame, then the real sequence resumes (4, 5, 6 …). The old unwrapper read
    255 as a forward jump and 4 as a rollover, injecting a permanent +256
    epoch offset that shifted the entire dark/warmup schedule. The stale frames
    must be rejected without advancing the counter."""
    raw_ids = [1, 255, 173, 4, 5, 6, 7, 8, 9, 10, 11]
    batch = _batch_with_raw_ids({(0, 0): raw_ids})
    FrameClassificationStage(discard_count=9, dark_interval=600).process(batch)
    # 255 and 173 rejected as stale; real frames keep their true abs_id (no +256).
    np.testing.assert_array_equal(
        batch.frame_type,
        ["warmup", "stale", "stale", "warmup", "warmup", "warmup",
         "warmup", "warmup", "warmup", "dark", "light"],
    )
    np.testing.assert_array_equal(
        batch.abs_frame_ids, [1, 255, 173, 4, 5, 6, 7, 8, 9, 10, 11]
    )


def test_midscan_counter_blip_is_rejected_and_sequence_resumes():
    """A mid-scan counter glitch (…3, 4, [2, 3], 5 …) must not reset the epoch:
    the spurious backward 2,3 are rejected and 5 continues the run."""
    raw_ids = [1, 2, 3, 4, 2, 3, 5, 6]
    batch = _batch_with_raw_ids({(0, 0): raw_ids})
    FrameClassificationStage(discard_count=9, dark_interval=600).process(batch)
    # backward 2,3 rejected; the real run 1..6 keeps monotonic abs ids.
    assert list(batch.frame_type) == [
        "warmup", "warmup", "warmup", "warmup",
        "stale", "stale", "warmup", "warmup"]
    np.testing.assert_array_equal(
        batch.abs_frame_ids, [1, 2, 3, 4, 2, 3, 5, 6]
    )


def test_zero_filled_row_keeps_source_assigned_side():
    """A row whose raw_histogram is all zeros (e.g. firmware-dropped frame)
    must still be routed to its source-assigned side.

    Before the side_ids fix, classify.py inferred side via
    ``np.argmax(raw_histograms[i].sum(axis=(-2, -1)))`` which silently
    defaults to 0 whenever the histogram is all zeros, so right-side dropped
    frames were misclassified as left.
    """
    # Two right-side rows, both with all-zero histograms — would have
    # defaulted to side=0 under the old logic.
    raw_ids = [1, 2]
    n = len(raw_ids)
    batch = FrameBatch(
        cam_ids=np.array([0, 0], dtype=np.int8),
        frame_ids=np.array(raw_ids, dtype=np.uint8),
        side_ids=np.array([1, 1], dtype=np.int8),  # right
        raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.arange(n, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
    )

    stage = FrameClassificationStage(discard_count=9, dark_interval=600)
    stage.process(batch)

    # The unwrapper key is (side_idx, cam_id) — verify state went to side=1.
    assert (1, 0) in stage._unwrappers
    assert (0, 0) not in stage._unwrappers


def test_dropped_stale_frames_are_logged_and_summarised(caplog):
    """Stale/non-monotonic frames the unwrapper drops must be visible in the
    log — a hardware-health signal. First occurrence logs live per camera;
    on_scan_stop emits a per-scan total."""
    batch = _batch_with_raw_ids({(0, 0): [1, 255, 173, 4, 5, 6]})
    stage = FrameClassificationStage(discard_count=9, dark_interval=600)
    with caplog.at_level(logging.WARNING):
        stage.process(batch)
        assert any("dropping stale frame" in r.message for r in caplog.records), \
            "expected a live WARNING when a stale frame is dropped"
        caplog.clear()
        stage.on_scan_stop(batch)
    summary = [r.message for r in caplog.records if "Scan summary" in r.message]
    assert summary, "expected a stale-frame scan summary at on_scan_stop"
    assert "2" in summary[0]  # 255 and 173 were dropped


def test_clean_scan_logs_no_stale_warnings(caplog):
    batch = _batch_with_raw_ids({(0, 0): list(range(1, 15))})
    stage = FrameClassificationStage(discard_count=9, dark_interval=600)
    with caplog.at_level(logging.WARNING):
        stage.process(batch)
        stage.on_scan_stop(batch)
    assert not any("stale" in r.message.lower() for r in caplog.records)


def test_reset_clears_unwrapper_state():
    stage = FrameClassificationStage(discard_count=9, dark_interval=600)
    batch1 = _batch_with_raw_ids({(0, 0): [1, 2, 3]})
    stage.process(batch1)
    assert batch1.abs_frame_ids[0] == 1

    stage.reset()
    batch2 = _batch_with_raw_ids({(0, 0): [1, 2, 3]})
    stage.process(batch2)
    assert batch2.abs_frame_ids[0] == 1
    assert batch2.frame_type[0] == "warmup"


def _packet(raw_ids, timestamp_s, *, side=0, cam_ids=None):
    """One coalesced packet: camera index follows row position."""
    n = len(raw_ids)
    if cam_ids is None:
        cam_ids = range(n)
    return FrameBatch(
        cam_ids=np.array(cam_ids, dtype=np.int8),
        frame_ids=np.array(raw_ids, dtype=np.uint8),
        side_ids=np.full(n, side, dtype=np.int8),
        raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.full(n, timestamp_s, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
    )


def test_single_frame_id_outlier_uses_packet_consensus_without_changing_raw_id():
    """A lone corrupt wire ID must not poison the outlier camera's unwrapper.

    Removing the consensus pass would make camera 1 stale at the second
    packet and leave its absolute ID at 130 instead of capture 2.
    """
    stage = FrameClassificationStage()
    stage.process(_packet([1, 1, 1], 0.000))
    batch = _packet([2, 130, 2], 0.025)

    result = stage.process(batch)

    np.testing.assert_array_equal(result.frame_ids, [2, 130, 2])
    np.testing.assert_array_equal(result.abs_frame_ids, [2, 2, 2])
    np.testing.assert_array_equal(result.frame_type, ["warmup"] * 3)
    corrections = [e for e in result.events
                   if type(e).__name__ == "FrameIdConsensusCorrection"]
    assert len(corrections) == 1
    event = corrections[0]
    assert event.side == 0
    assert event.timestamp_s == 0.025
    assert event.cam_id == 1
    assert event.observed_raw_frame_id == 130
    assert event.consensus_raw_frame_id == 2
    assert event.previous_raw_frame_id == 1
    assert event.previous_abs_frame_id == 1
    assert event.corrected_abs_frame_id == 2
    assert event.packet == ((0, 2), (1, 130), (2, 2))


def test_ambiguous_two_camera_packet_is_logged_but_not_guessed():
    """With no strict packet consensus, keep ordinary stale handling and
    record the pre-mutation packet evidence instead of inventing an ID."""
    stage = FrameClassificationStage()
    stage.process(_packet([1, 1], 0.000))
    batch = _packet([2, 130], 0.025)

    result = stage.process(batch)

    np.testing.assert_array_equal(result.abs_frame_ids, [2, 130])
    np.testing.assert_array_equal(result.frame_type, ["warmup", "stale"])
    anomalies = [e for e in result.events
                 if type(e).__name__ == "FrameIdPacketAnomaly"]
    assert len(anomalies) == 1
    assert anomalies[0].reason == "fewer_than_three_cameras"
    assert anomalies[0].packet == ((0, 2), (1, 130))
    assert anomalies[0].action == "left_unchanged"


def test_first_seen_outlier_is_logged_but_not_consensus_corrected():
    """Consensus cannot safely seed a camera whose prior counter is unknown."""
    stage = FrameClassificationStage()
    batch = _packet([1, 1, 130], 0.000)

    result = stage.process(batch)

    assert result.abs_frame_ids[2] == 130
    assert result.frame_type[2] == "stale"
    anomalies = [e for e in result.events
                 if type(e).__name__ == "FrameIdPacketAnomaly"]
    assert len(anomalies) == 1
    assert anomalies[0].reason == "outlier_has_no_prior_state"


@pytest.mark.parametrize(
    ("raw_ids", "cam_ids", "reason"),
    [
        ([2, 2, 130, 130], None, "no_single_outlier_consensus"),
        ([200, 200, 2], None, "consensus_is_not_forward_continuation"),
        ([2, 130, 2], [0, 1, 1], "duplicate_camera_ids"),
    ],
)
def test_unsafe_packet_shapes_are_logged_without_consensus_correction(
        raw_ids, cam_ids, reason):
    stage = FrameClassificationStage()
    stage.process(_packet([1] * len(raw_ids), 0.000))
    batch = _packet(raw_ids, 0.025, cam_ids=cam_ids)

    stage.process(batch)

    assert not any(type(e).__name__ == "FrameIdConsensusCorrection"
                   for e in batch.events)
    anomalies = [e for e in batch.events
                 if type(e).__name__ == "FrameIdPacketAnomaly"]
    assert len(anomalies) == 1
    assert anomalies[0].reason == reason
    if reason == "no_single_outlier_consensus":
        assert anomalies[0].consensus_raw_frame_id is None
