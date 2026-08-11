"""Diagnostics consumers — DiagnosticsLogSink + ScanDBSink session_meta summary.

Ordinary integrity events are logged and summarized in session_meta. Detailed
inbound-data anomalies are explicitly formatted and bounded in logs, but never
stored in SQLite. Routine operational events are ignored by both records.
"""

import json
import logging
import sqlite3
from types import SimpleNamespace

from omotion.pipeline.batch import (
    DarkIntegrityWarning,
    FrameGapFillAnomaly,
    FrameIdConsensusCorrection,
    FrameIdPacketAnomaly,
    PipelineError,
    TerminalDarkResult,
    TimestampRepairInputAnomaly,
    TriggerStateEvent,
)
from omotion.pipeline.sinks import DiagnosticsLogSink, ScanDBSink, ScanMetadata


def _meta():
    return ScanMetadata(
        scan_id="diag", subject_id="subj", operator="op",
        started_at_iso="2026-06-10T00:00:00Z", duration_sec=60,
        left_camera_mask=0x01, right_camera_mask=0, reduced_mode=False,
    )


def _dark_warning(abs_id=42):
    return DarkIntegrityWarning(
        side="left", cam_id=0, abs_frame_id=abs_id,
        u1=90.0, pedestal=64.0, threshold=5.0,
    )


def _final_interval():
    """One corrected frame so the session persists as a real scan. Empty scans
    (no corrected rows) are deleted by ScanDBSink — that path is covered by
    test_scan_db_sink_empty.py; these tests exercise the session_meta /
    diagnostics summary on a scan that actually produced rows."""
    frame = SimpleNamespace(
        cam_id=0, side="left", abs_frame_id=1, t=0.1,
        bfi=1.0, bvi=2.0, mean=3.0, contrast=0.4, quality="ok",
    )
    return SimpleNamespace(frames=[frame])


def test_log_sink_warns_on_integrity_events(caplog):
    sink = DiagnosticsLogSink()
    sink.on_scan_start(_meta())
    with caplog.at_level(logging.WARNING, logger="openmotion.sdk.pipeline.sinks"):
        sink.consume("diagnostics", _dark_warning())
        sink.consume("diagnostics", PipelineError(
            error="RuntimeError('x')", n_frames=10, first_timestamp_s=1.0))
        sink.on_complete()
    assert "integrity event" in caplog.text
    assert "DarkIntegrityWarning" in caplog.text
    assert "PipelineError" in caplog.text


def test_log_sink_ignores_routine_events(caplog):
    from omotion.pipeline.batch import TerminalFsyncCount
    sink = DiagnosticsLogSink()
    sink.on_scan_start(_meta())
    with caplog.at_level(logging.WARNING, logger="openmotion.sdk.pipeline.sinks"):
        sink.consume("diagnostics", TriggerStateEvent(state="ON", timestamp_s=0.1))
        sink.consume("diagnostics", TerminalDarkResult(
            side="left", cam_id=0, abs_frame_id=100, u1=64.0,
            threshold=69.0, found=True))
        sink.consume("diagnostics", TerminalFsyncCount(count=842, timestamp_s=60.0))
        sink.on_complete()
    assert caplog.text == ""


def _inbound_events():
    return [
        FrameIdConsensusCorrection(
            side=1, timestamp_s=1.25, cam_id=3,
            observed_raw_frame_id=5, consensus_raw_frame_id=197,
            previous_raw_frame_id=196, previous_abs_frame_id=196,
            corrected_abs_frame_id=197,
            packet=((0, 197), (1, 197), (2, 197), (3, 5)),
        ),
        FrameIdPacketAnomaly(
            side=0, timestamp_s=2.5, reason="no_single_outlier_consensus",
            packet=((0, 10), (1, 11), (2, 10), (3, 11)),
        ),
        TimestampRepairInputAnomaly(
            side=1, cam_id=3, raw_frame_id=5, abs_frame_id=197,
            original_timestamp_s=4.925,
            previous_raw_frame_id=196, previous_abs_frame_id=196,
            previous_timestamp_s=4.900, nominal_period_s=0.025,
            frame_id_gap=1, expected_timestamp_s=4.925,
            signed_residual_s=0.0, tolerance_s=0.008,
            packet=((0, 197, 197), (1, 197, 197), (2, 197, 197),
                    (3, 5, 197)),
            detector="absolute_frame_id_disagreement",
            action="timestamp_corrected",
        ),
        FrameGapFillAnomaly(
            side=1, cam_id=3,
            previous_raw_frame_id=196, previous_abs_frame_id=196,
            previous_timestamp_s=4.900,
            current_raw_frame_id=199, current_abs_frame_id=199,
            current_timestamp_s=4.975, missing_count=2,
            first_missing_abs_frame_id=197,
            last_missing_abs_frame_id=198,
        ),
    ]


def test_log_sink_spells_out_all_inbound_anomaly_evidence(caplog):
    sink = DiagnosticsLogSink()
    sink.on_scan_start(_meta())
    with caplog.at_level(logging.WARNING, logger="openmotion.sdk.pipeline.sinks"):
        for event in _inbound_events():
            sink.consume("diagnostics", event)

    assert caplog.text.count("INBOUND DATA ANOMALY") == 4
    for name in (
        "FrameIdConsensusCorrection", "FrameIdPacketAnomaly",
        "TimestampRepairInputAnomaly", "FrameGapFillAnomaly",
    ):
        assert name in caplog.text
    for evidence in (
        "observed_raw_frame_id=5", "consensus_raw_frame_id=197",
        "previous_abs_frame_id=196", "corrected_abs_frame_id=197",
        "reason=no_single_outlier_consensus", "original_timestamp_s=4.925",
        "expected_timestamp_s=4.925", "signed_residual_s=0.0",
        "missing_count=2", "first_missing_abs_frame_id=197",
        "action=inserted_nan_fill_rows",
    ):
        assert evidence in caplog.text


def test_log_sink_caps_inbound_detail_but_counts_every_event(caplog):
    sink = DiagnosticsLogSink()
    sink.on_scan_start(_meta())
    event = _inbound_events()[0]
    with caplog.at_level(logging.WARNING, logger="openmotion.sdk.pipeline.sinks"):
        for _ in range(10):
            sink.consume("diagnostics", event)
        sink.on_complete()

    assert caplog.text.count("action=used_consensus_for_unwrap") == 8
    assert caplog.text.count("further detail suppressed after 8 events") == 1
    assert "FrameIdConsensusCorrection\u00d710" in caplog.text


def test_scan_db_sink_writes_diagnostics_summary_to_session_meta(tmp_path):
    db_path = str(tmp_path / "scan.db")
    sink = ScanDBSink(db_path=db_path)
    sink.on_scan_start(_meta())
    sink.consume("final", _final_interval())
    sink.consume("diagnostics", _dark_warning(abs_id=10))
    sink.consume("diagnostics", _dark_warning(abs_id=610))
    sink.consume("diagnostics", TerminalDarkResult(
        side="left", cam_id=0, abs_frame_id=2400, u1=120.0,
        threshold=69.0, found=False))
    # Routine events must not pollute the summary.
    sink.consume("diagnostics", TriggerStateEvent(state="OFF", timestamp_s=60.0))
    sink.on_complete()

    conn = sqlite3.connect(db_path)
    meta = json.loads(conn.execute(
        "SELECT session_meta FROM sessions").fetchone()[0])
    conn.close()
    diag = meta["diagnostics"]
    assert diag["DarkIntegrityWarning"] == {"count": 2, "first": 10, "last": 610}
    assert diag["TerminalDarkResult"]["count"] == 1
    assert "TriggerStateEvent" not in diag
    # The rest of the stamped meta survives the update.
    assert meta["data_semantics"] == "final"
    assert meta["sdk_flags"]["reduced_mode"] is False


def test_scan_db_sink_meta_has_no_diagnostics_key_when_clean(tmp_path):
    db_path = str(tmp_path / "scan.db")
    sink = ScanDBSink(db_path=db_path)
    sink.on_scan_start(_meta())
    sink.consume("final", _final_interval())
    sink.on_complete()

    conn = sqlite3.connect(db_path)
    meta = json.loads(conn.execute(
        "SELECT session_meta FROM sessions").fetchone()[0])
    conn.close()
    assert "diagnostics" not in meta


def test_scan_db_sink_does_not_retain_inbound_anomaly_events(tmp_path):
    db_path = str(tmp_path / "scan.db")
    sink = ScanDBSink(db_path=db_path)
    sink.on_scan_start(_meta())
    sink.consume("final", _final_interval())
    for event in _inbound_events():
        sink.consume("diagnostics", event)
    sink.on_complete()

    conn = sqlite3.connect(db_path)
    meta = json.loads(conn.execute(
        "SELECT session_meta FROM sessions").fetchone()[0])
    conn.close()
    assert "diagnostics" not in meta
