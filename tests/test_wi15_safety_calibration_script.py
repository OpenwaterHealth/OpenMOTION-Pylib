import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from omotion.WI15LaserCalibration import FailureKind, ProcedureStatus
from omotion.WI15SafetyCalibration import ShippingTopology
from omotion.WI15SafetyCalibrationWorkflow import SafetyCalibrationResult
from omotion.WI15SingleSensorLaserCalibration import ReportArtifactStatus


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "wi15_safety_calibration.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location(
        "wi15_safety_script_under_test", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeRecorder:
    def __init__(self, root):
        self.run_directory = Path(root) / "run"
        self.run_directory.mkdir(parents=True)
        self.json_path = self.run_directory / "run.json"
        self.checkpoints = []

    def checkpoint(self, result):
        self.checkpoints.append(result)


class FakeBench:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.closed = 0
        self.close_error = None

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


class FakeReport:
    def __init__(self, directory):
        self.report_path = Path(directory) / "report.html"

    def write(self, request, result, json_path):
        self.report_path.write_text("safety report", encoding="ascii")
        return self.report_path


def _result(status=ProcedureStatus.PASSED, **changes):
    values = {
        "status": status,
        "shipping_topology": ShippingTopology.SINGLE_LEFT,
        "sdk_version": "test-sdk",
        "started_at": datetime.now(timezone.utc),
        "ended_at": datetime.now(timezone.utc),
    }
    values.update(changes)
    return SafetyCalibrationResult(**values)


def _complete_args(tmp_path, topology="single-left"):
    return [
        "--output-dir",
        str(tmp_path),
        "--operator",
        "operator",
        "--build-revision",
        "build-7",
        "--fixture-id",
        "bench-2",
        "--shipping-topology",
        topology,
    ]


def _configured_script(monkeypatch, tmp_path, terminal_result=None):
    script = load_script()
    recorder = FakeRecorder(tmp_path)
    captured = {}

    def make_bench(*, power_cycle_coordinator):
        bench = FakeBench(power_cycle_coordinator)
        captured["bench"] = bench
        return bench

    class FakeWorkflow:
        def __init__(self, bench, workflow_recorder):
            assert bench is captured["bench"]
            assert workflow_recorder is recorder

        def run(self, request):
            captured["request"] = request
            return terminal_result or _result(
                shipping_topology=request.shipping_topology
            )

    monkeypatch.setattr(script, "recorder_factory", lambda *args: recorder)
    monkeypatch.setattr(script, "bench_factory", make_bench)
    monkeypatch.setattr(script, "workflow_factory", FakeWorkflow)
    monkeypatch.setattr(script, "report_factory", FakeReport)
    return script, recorder, captured


def test_script_source_is_ascii_safe_and_has_no_meter_dependency():
    source = SCRIPT_PATH.read_bytes()
    source.decode("ascii")
    assert b"Ophir" not in source
    assert b"EnergyMeter" not in source


def test_metadata_topology_and_connection_confirmation_finish_before_hardware_construction(
    monkeypatch, tmp_path
):
    script = load_script()
    calls = []
    recorder = FakeRecorder(tmp_path)

    monkeypatch.setattr(
        script,
        "recorder_factory",
        lambda *args: calls.append("recorder") or recorder,
    )
    monkeypatch.setattr(
        script,
        "bench_factory",
        lambda **kwargs: calls.append("bench") or FakeBench(kwargs["power_cycle_coordinator"]),
    )

    class FakeWorkflow:
        def __init__(self, *_args):
            pass

        def run(self, request):
            calls.append(("workflow", request.shipping_topology))
            return _result(shipping_topology=request.shipping_topology)

    monkeypatch.setattr(script, "workflow_factory", FakeWorkflow)
    monkeypatch.setattr(script, "report_factory", FakeReport)
    replies = iter(("operator", "build", "bench", "dual", "yes"))

    exit_code = script.main(
        ["--output-dir", str(tmp_path)],
        input_func=lambda prompt: calls.append(("prompt", prompt)) or next(replies),
    )

    assert exit_code == 0
    bench_index = calls.index("bench")
    prompts = [item for item in calls[:bench_index] if isinstance(item, tuple)]
    assert any("shipping topology" in prompt.lower() for _, prompt in prompts)
    assert any("exact dual" in prompt.lower() for _, prompt in prompts)
    assert calls[-1] == ("workflow", ShippingTopology.DUAL)


def test_declined_shipping_topology_confirmation_cancels_before_hardware(monkeypatch, tmp_path):
    script = load_script()
    constructed = []
    monkeypatch.setattr(
        script,
        "bench_factory",
        lambda **kwargs: constructed.append(kwargs) or FakeBench(None),
    )

    messages = []
    exit_code = script.main(
        _complete_args(tmp_path),
        input_func=lambda _prompt: "no",
        output_func=messages.append,
    )

    assert exit_code == 1
    assert constructed == []
    assert messages == [
        "Declared shipping topology: single-left.",
        "Safety Calibration canceled before hardware construction.",
    ]


def test_manual_power_cycle_observes_disconnect_before_measured_dwell_and_power_on_prompt():
    script = load_script()
    clock = FakeClock()
    prompts = []
    outputs = []
    replies = iter(("yes", "yes"))

    def connected():
        return clock.now < 1.0 or clock.now >= 17.0

    coordinator = script.ManualPowerCycleCoordinator(
        input_func=lambda prompt: prompts.append((prompt, clock.now)) or next(replies),
        output_func=outputs.append,
        clock=clock,
        wall_clock=lambda: 2_000.0 + clock.now,
        sleep=clock.sleep,
        poll_interval_s=0.5,
        disconnect_timeout_s=5.0,
        reconnect_timeout_s=5.0,
    )

    evidence = coordinator.perform(
        minimum_off_s=15.0,
        expected_console_serial="C-1",
        is_console_connected=connected,
        read_console_serial=lambda: "C-1",
    )

    assert evidence.disconnect_observed
    assert evidence.reconnect_observed
    assert evidence.restart_proven
    assert evidence.off_duration_s == pytest.approx(15.0)
    assert prompts[0][1] == 0.0
    assert "power off" in prompts[0][0].lower()
    assert prompts[1][1] == pytest.approx(16.0)
    assert "power on" in prompts[1][0].lower()
    assert evidence.console_serial_before == "C-1"
    assert evidence.console_serial_after == "C-1"
    assert any("15-second" in message for message in outputs)


def test_manual_power_cycle_never_invites_power_on_without_observed_disconnect():
    script = load_script()
    clock = FakeClock()
    prompts = []
    coordinator = script.ManualPowerCycleCoordinator(
        input_func=lambda prompt: prompts.append(prompt) or "yes",
        output_func=lambda _message: None,
        clock=clock,
        wall_clock=lambda: 2_000.0 + clock.now,
        sleep=clock.sleep,
        poll_interval_s=0.25,
        disconnect_timeout_s=1.0,
        reconnect_timeout_s=1.0,
    )

    evidence = coordinator.perform(
        minimum_off_s=15.0,
        expected_console_serial="C-1",
        is_console_connected=lambda: True,
        read_console_serial=lambda: "C-1",
    )

    assert not evidence.disconnect_observed
    assert not evidence.reconnect_observed
    assert evidence.off_duration_s is None
    assert len(prompts) == 1
    assert all("power on" not in prompt.lower() for prompt in prompts)


@pytest.mark.parametrize(
    ("topology", "expected"),
    [
        ("single-left", ShippingTopology.SINGLE_LEFT),
        ("single-right", ShippingTopology.SINGLE_RIGHT),
        ("dual", ShippingTopology.DUAL),
    ],
)
def test_runner_passes_declared_topology_to_shared_workflow_and_finalizes_artifacts(
    monkeypatch, tmp_path, topology, expected
):
    script, recorder, captured = _configured_script(monkeypatch, tmp_path)
    messages = []

    exit_code = script.main(
        _complete_args(tmp_path, topology),
        input_func=lambda _prompt: "yes",
        output_func=messages.append,
    )

    assert exit_code == 0
    assert captured["request"].shipping_topology is expected
    assert captured["bench"].coordinator is not None
    assert captured["bench"].closed == 1
    assert recorder.checkpoints[-1].report_artifact.status is ReportArtifactStatus.FINALIZED
    assert "Terminal status: passed" in messages
    assert any(message.startswith("JSON evidence: ") for message in messages)
    assert any(message.startswith("HTML report: ") for message in messages)


def test_terminal_failure_prints_exact_structured_category_and_reason(monkeypatch, tmp_path):
    terminal = _result(
        ProcedureStatus.FAILED,
        failure_kind=FailureKind.CONFIGURATION,
        failure_reason="Post-restart complete User Configuration mismatched OPT_DRIVE_CL.",
    )
    script, _, _ = _configured_script(monkeypatch, tmp_path, terminal)
    messages = []

    exit_code = script.main(
        _complete_args(tmp_path),
        input_func=lambda _prompt: "yes",
        output_func=messages.append,
    )

    assert exit_code == 1
    assert "Terminal status: failed" in messages
    assert "Failure category: configuration" in messages
    assert (
        "Failure reason: Post-restart complete User Configuration mismatched "
        "OPT_DRIVE_CL."
    ) in messages


def test_hardware_cleanup_failure_replaces_a_pending_pass_before_report(monkeypatch, tmp_path):
    script, recorder, captured = _configured_script(monkeypatch, tmp_path)

    def make_bench(*, power_cycle_coordinator):
        bench = FakeBench(power_cycle_coordinator)
        bench.close_error = RuntimeError("Motion shutdown failed")
        captured["bench"] = bench
        return bench

    monkeypatch.setattr(script, "bench_factory", make_bench)

    exit_code = script.main(
        _complete_args(tmp_path), input_func=lambda _prompt: "yes"
    )

    assert exit_code == 1
    terminal = recorder.checkpoints[-1]
    assert terminal.status is ProcedureStatus.FAILED
    assert terminal.failure_kind is FailureKind.MEASUREMENT
    assert terminal.failure_reason == "Hardware resource cleanup failed."
    assert terminal.resource_cleanup_failure == "Motion shutdown failed"


def test_report_failure_replaces_prior_pass_in_durable_json(monkeypatch, tmp_path):
    script = load_script()
    monkeypatch.setattr(script, "_run_id", lambda: "report-failure")
    monkeypatch.setattr(
        script,
        "bench_factory",
        lambda **kwargs: FakeBench(kwargs["power_cycle_coordinator"]),
    )

    class PassingWorkflow:
        def __init__(self, _bench, recorder):
            self.recorder = recorder

        def run(self, request):
            result = _result(shipping_topology=request.shipping_topology)
            self.recorder.checkpoint(result)
            return result

    monkeypatch.setattr(script, "workflow_factory", PassingWorkflow)
    monkeypatch.setattr(
        script,
        "report_factory",
        lambda _directory: (_ for _ in ()).throw(RuntimeError("report failed")),
    )

    exit_code = script.main(
        _complete_args(tmp_path), input_func=lambda _prompt: "yes"
    )

    assert exit_code == 1
    payload = json.loads(
        (tmp_path / "WI-00015-report-failure" / "run.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["status"] == ProcedureStatus.FAILED.value
    assert payload["failure_kind"] == FailureKind.REPORT.value
    assert payload["report_artifact"]["status"] == ReportArtifactStatus.FAILED.value
