import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from omotion.WI15DualSensorLaserCalibration import (
    DualSensorLaserCalibrationResult,
    PlacementChangeRequest,
)
from omotion.WI15LaserCalibration import FailureKind, ProcedureStatus
from omotion.WI15SingleSensorLaserCalibration import ReportArtifactStatus


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "wi15_dual_sensor_laser_calibration.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location("wi15_dual_script_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class FakeRecorder:
    def __init__(self, root):
        self.run_directory = Path(root) / "run"
        self.run_directory.mkdir(parents=True)
        self.json_path = self.run_directory / "run.json"
        self.checkpoints = []

    def checkpoint(self, result):
        self.checkpoints.append(result)


class FakeMeter:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeBench:
    def __init__(self, meter):
        self.meter = meter
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeReport:
    def __init__(self, directory):
        self.report_path = Path(directory) / "report.html"

    def write(self, request, result, json_path):
        self.report_path.write_text("dual report", encoding="ascii")
        return self.report_path


def result(status=ProcedureStatus.PASSED):
    return DualSensorLaserCalibrationResult(
        status=status,
        sdk_version="test-sdk",
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
    )


def complete_args(tmp_path):
    return [
        "--output-dir",
        str(tmp_path),
        "--operator",
        "operator",
        "--build-revision",
        "build-7",
        "--fixture-id",
        "fixture-2",
        "--fixture-calibration-status",
        "current",
    ]


def configured_script(monkeypatch, tmp_path, terminal_result=None, *, request_placements=False):
    script = load_script()
    recorder = FakeRecorder(tmp_path)
    meter = FakeMeter()
    bench = FakeBench(meter)
    captured = {}

    class FakeWorkflow:
        def __init__(self, workflow_bench, workflow_recorder, placement_callback):
            assert workflow_bench is bench
            assert workflow_recorder is recorder
            self.placement_callback = placement_callback

        def run(self, request):
            captured["request"] = request
            if request_placements:
                captured["placement_results"] = [
                    self.placement_callback(
                        PlacementChangeRequest(
                            None,
                            "left",
                            "LEFT-001",
                            "Initial paired measurement",
                            "Initial paired measurement - place the left sensor in the Ophir 0 cm fixture",
                        )
                    ),
                    self.placement_callback(
                        PlacementChangeRequest(
                            "left",
                            "right",
                            "RIGHT-001",
                            "Initial paired measurement",
                            "Initial paired measurement - switch to the right sensor",
                        )
                    ),
                ]
            return terminal_result or result()

    monkeypatch.setattr(script, "recorder_factory", lambda *args: recorder)
    monkeypatch.setattr(script, "meter_factory", lambda: meter)
    monkeypatch.setattr(script, "bench_factory", lambda value: bench)
    monkeypatch.setattr(script, "workflow_factory", FakeWorkflow)
    monkeypatch.setattr(script, "report_factory", lambda directory: FakeReport(directory))
    return script, recorder, meter, bench, captured


def test_script_source_is_ascii_safe_and_does_not_import_legacy_tuning():
    source = SCRIPT_PATH.read_bytes()
    source.decode("ascii")
    assert b"omotion.tuning" not in source


def test_placement_callback_is_courteous_and_names_side_serial_and_zero_cm_fixture(
    monkeypatch, tmp_path
):
    script, _recorder, _meter, bench, captured = configured_script(
        monkeypatch, tmp_path, request_placements=True
    )
    prompts = []
    messages = []
    replies = iter(("yes", "yes"))

    def prompt(text):
        prompts.append(text)
        return next(replies)

    exit_code = script.main(
        complete_args(tmp_path), input_func=prompt, output_func=messages.append
    )

    assert exit_code == 0
    assert captured["placement_results"] == [True, True]
    assert "left sensor module (serial LEFT-001)" in prompts[0]
    assert "right sensor module (serial RIGHT-001)" in prompts[1]
    assert all("Ophir 0 cm fixture" in prompt for prompt in prompts)
    assert any("Initial paired measurement" in message for message in messages)
    assert all("_measure" not in text for text in prompts + messages)
    assert bench.closed == 1


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (ProcedureStatus.PASSED, 0),
        (ProcedureStatus.FAILED, 1),
        (ProcedureStatus.FAILED_NCR, 1),
        (ProcedureStatus.CANCELED, 1),
    ],
)
def test_terminal_status_controls_exit_code(monkeypatch, tmp_path, status, exit_code):
    script, _recorder, _meter, _bench, _captured = configured_script(
        monkeypatch, tmp_path, result(status)
    )
    assert script.main(complete_args(tmp_path), input_func=lambda _: "yes") == exit_code


def test_metadata_prompts_finish_before_hardware_construction(monkeypatch, tmp_path):
    script = load_script()
    calls = []
    recorder = FakeRecorder(tmp_path)
    meter = FakeMeter()
    bench = FakeBench(meter)
    monkeypatch.setattr(script, "recorder_factory", lambda *args: calls.append("recorder") or recorder)
    monkeypatch.setattr(script, "meter_factory", lambda: calls.append("meter") or meter)
    monkeypatch.setattr(script, "bench_factory", lambda value: calls.append("bench") or bench)

    class FakeWorkflow:
        def __init__(self, *_args):
            pass

        def run(self, request):
            calls.append("workflow")
            return result()

    monkeypatch.setattr(script, "workflow_factory", FakeWorkflow)
    monkeypatch.setattr(script, "report_factory", lambda directory: FakeReport(directory))
    replies = iter(("operator", "build", "fixture", "current"))

    assert script.main(["--output-dir", str(tmp_path)], input_func=lambda _: next(replies)) == 0
    assert calls == ["recorder", "meter", "bench", "workflow"]
    assert bench.closed == 1


def test_one_run_id_is_shared_by_recorder_and_workflow_request(monkeypatch, tmp_path):
    script, recorder, _meter, _bench, captured = configured_script(monkeypatch, tmp_path)
    recorder_ids = []
    monkeypatch.setattr(script, "_run_id", lambda: "one-run-id")
    monkeypatch.setattr(
        script,
        "recorder_factory",
        lambda output, procedure, run_id: recorder_ids.append(run_id) or recorder,
    )

    assert script.main(complete_args(tmp_path), input_func=lambda _: "yes") == 0
    assert recorder_ids == ["one-run-id"]
    assert captured["request"].run_id == "one-run-id"


def test_report_failure_replaces_a_prior_pass_in_json(monkeypatch, tmp_path):
    script = load_script()
    monkeypatch.setattr(script, "_run_id", lambda: "report-failure")
    meter = FakeMeter()
    bench = FakeBench(meter)
    monkeypatch.setattr(script, "meter_factory", lambda: meter)
    monkeypatch.setattr(script, "bench_factory", lambda value: bench)

    class PassingWorkflow:
        def __init__(self, _bench, recorder, _placement):
            self.recorder = recorder

        def run(self, request):
            passed = result()
            self.recorder.checkpoint(passed)
            return passed

    monkeypatch.setattr(script, "workflow_factory", PassingWorkflow)
    monkeypatch.setattr(
        script, "report_factory", lambda directory: (_ for _ in ()).throw(RuntimeError("report failed"))
    )

    assert script.main(complete_args(tmp_path), input_func=lambda _: "yes") == 1
    payload = json.loads(
        (tmp_path / "WI-00015-report-failure" / "run.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == ProcedureStatus.FAILED.value
    assert payload["failure_kind"] == FailureKind.REPORT.value
    assert payload["report_artifact"]["status"] == ReportArtifactStatus.FAILED.value


def test_declined_placement_is_passed_to_workflow_as_false(monkeypatch, tmp_path):
    script, _recorder, _meter, _bench, captured = configured_script(
        monkeypatch,
        tmp_path,
        terminal_result=result(ProcedureStatus.CANCELED),
        request_placements=True,
    )
    replies = iter(("yes", "no"))
    exit_code = script.main(complete_args(tmp_path), input_func=lambda _: next(replies))
    assert exit_code == 1
    assert captured["placement_results"] == [True, False]
