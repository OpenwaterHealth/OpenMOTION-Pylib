import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from omotion.WI15LaserCalibration import ProcedureStatus


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "wi15_single_sensor_laser_calibration.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location("wi15_script_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@dataclass(frozen=True)
class FakeResult:
    status: ProcedureStatus
    report_paths: tuple[Path, ...] = ()


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


class FakeWorkflow:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return self.result


class FakeReport:
    def __init__(self, directory):
        self.report_path = Path(directory) / "report.html"
        self.writes = []

    def write(self, request, result, json_path):
        self.writes.append((request, result, Path(json_path)))
        self.report_path.write_text("report", encoding="ascii")
        return self.report_path


def configured_script(monkeypatch, tmp_path, result=FakeResult(ProcedureStatus.PASSED)):
    script = load_script()
    recorder = FakeRecorder(tmp_path)
    meter = FakeMeter()
    bench = FakeBench(meter)
    workflow = FakeWorkflow(result)
    report = FakeReport(recorder.run_directory)
    monkeypatch.setattr(script, "recorder_factory", lambda *args: recorder)
    monkeypatch.setattr(script, "meter_factory", lambda: meter)
    monkeypatch.setattr(script, "bench_factory", lambda value: bench)
    monkeypatch.setattr(script, "workflow_factory", lambda *args: workflow)
    monkeypatch.setattr(script, "report_factory", lambda directory: report)
    return script, recorder, meter, bench, workflow, report


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


def answers(*values):
    values = iter(values)
    return lambda _prompt: next(values)


def test_script_source_is_ascii_decodable_and_does_not_use_legacy_tuning():
    """A non-ASCII console script or old runner import would break supported operation."""
    source = SCRIPT_PATH.read_bytes()
    source.decode("ascii")
    script = load_script()
    assert "omotion.tuning" not in script.__dict__.get("__doc__", "")
    assert "omotion.tuning" not in SCRIPT_PATH.read_text(encoding="ascii")


def test_main_reprompts_side_echoes_it_and_requires_both_confirmations(monkeypatch, tmp_path):
    """An unconfirmed or ambiguous module selection could fire the wrong fixture."""
    script, recorder, meter, bench, workflow, report = configured_script(monkeypatch, tmp_path)
    messages = []

    exit_code = script.main(
        complete_args(tmp_path),
        input_func=answers("middle", "right", "yes", "yes"),
        output_func=messages.append,
    )

    assert exit_code == 0
    assert workflow.requests[0].side == "right"
    assert workflow.requests[0].side_confirmed is True
    assert workflow.requests[0].fixture_confirmed is True
    assert any("Selected sensor side: right" in message for message in messages)
    assert bench.closed == 1
    assert meter.closed == 0
    assert report.writes[0][1].report_paths == (recorder.json_path, report.report_path)
    assert recorder.checkpoints[-1].report_paths == (recorder.json_path, report.report_path)


@pytest.mark.parametrize("reply", ["no", EOFError()])
def test_cancel_or_eof_before_hardware_returns_nonzero_without_constructing_hardware(
    monkeypatch, tmp_path, reply
):
    """Cancellation before consent must never acquire or configure hardware."""
    script = load_script()
    constructed = []
    monkeypatch.setattr(script, "recorder_factory", lambda *args: constructed.append("recorder"))
    monkeypatch.setattr(script, "meter_factory", lambda: constructed.append("meter"))

    def prompt(_prompt):
        if not hasattr(prompt, "side_given"):
            prompt.side_given = True
            return "left"
        if isinstance(reply, Exception):
            raise reply
        return reply

    exit_code = script.main(complete_args(tmp_path), input_func=prompt)

    assert exit_code != 0
    assert constructed == []


@pytest.mark.parametrize(
    "status, expected_exit",
    [
        (ProcedureStatus.PASSED, 0),
        (ProcedureStatus.FAILED, 1),
        (ProcedureStatus.FAILED_NCR, 1),
        (ProcedureStatus.CANCELED, 1),
    ],
)
def test_terminal_status_controls_exit_code(monkeypatch, tmp_path, status, expected_exit):
    """A non-passing terminal result must be visible to calling automation."""
    script, _recorder, _meter, _bench, _workflow, _report = configured_script(
        monkeypatch, tmp_path, FakeResult(status)
    )

    exit_code = script.main(
        complete_args(tmp_path), input_func=answers("left", "yes", "yes")
    )

    assert exit_code == expected_exit


def test_main_generates_one_run_id_for_recorder_and_workflow_request(
    monkeypatch, tmp_path
):
    """A crossed second must not split request evidence from its artifact directory."""
    script, recorder, _meter, _bench, workflow, _report = configured_script(
        monkeypatch, tmp_path
    )
    generated_ids = iter(("first-run-id", "second-run-id"))
    recorder_run_ids = []
    monkeypatch.setattr(script, "_run_id", lambda: next(generated_ids))
    monkeypatch.setattr(
        script,
        "recorder_factory",
        lambda output_dir, procedure_id, run_id: recorder_run_ids.append(run_id)
        or recorder,
    )

    exit_code = script.main(
        complete_args(tmp_path), input_func=answers("left", "yes", "yes")
    )

    assert exit_code == 0
    assert recorder_run_ids == ["first-run-id"]
    assert workflow.requests[0].run_id == "first-run-id"


def test_metadata_prompts_complete_before_hardware_and_cleanup_survives_workflow_error(
    monkeypatch, tmp_path
):
    """Metadata must be attributable before hardware use and cleanup must survive errors."""
    script = load_script()
    calls = []
    meter = FakeMeter()
    bench = FakeBench(meter)
    monkeypatch.setattr(script, "recorder_factory", lambda *args: calls.append("recorder") or FakeRecorder(tmp_path))
    monkeypatch.setattr(script, "meter_factory", lambda: calls.append("meter") or meter)
    monkeypatch.setattr(script, "bench_factory", lambda value: calls.append("bench") or bench)

    class RaisingWorkflow:
        def run(self, request):
            raise RuntimeError("workflow boom")

    monkeypatch.setattr(script, "workflow_factory", lambda *args: RaisingWorkflow())
    monkeypatch.setattr(script, "report_factory", lambda directory: FakeReport(directory))

    exit_code = script.main(
        ["--output-dir", str(tmp_path)],
        input_func=answers(
            "operator",
            "build-7",
            "fixture-2",
            "current",
            "left",
            "yes",
            "yes",
        ),
    )

    assert exit_code != 0
    assert calls == ["recorder", "meter", "bench"]
    assert bench.closed == 1
    assert meter.closed == 0
