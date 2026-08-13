import pytest

from omotion.WI15SingleSensorLaserCalibration import (
    PreflightSnapshot,
    SingleSensorLaserCalibrationRequest,
    SingleSensorLaserCalibrationWorkflow,
)
from omotion.WI15LaserCalibration import (
    DeviceIdentity,
    OphirIdentity,
    ProcedureStatus,
    TopologySnapshot,
)


class FakeLaserBench:
    def __init__(self, preflight_queue=(), measurements=(), readbacks=()):
        self.calls = []
        self.preflight_queue = list(preflight_queue)
        self.measurements = list(measurements)
        self.readbacks = list(readbacks)

    def preflight(self, side):
        self.calls.append(f"preflight:{side}")
        outcome = self.preflight_queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def write_user_configuration(self, configuration):
        self.calls.append("write_user_configuration")
        return self.readbacks.pop(0)

    def read_register(self, name):
        self.calls.append(f"read_register:{name}")
        return self.readbacks.pop(0)

    def write_register(self, name, value):
        self.calls.append(f"write_register:{name}")
        return self.readbacks.pop(0)

    def measure_energy(self):
        self.calls.append("measure_energy")
        return self.measurements.pop(0)

    def stop_trigger(self):
        self.calls.append("stop_trigger")


class FakeRecorder:
    def __init__(self):
        self.events = []
        self.checkpoints = []

    def record(self, event):
        self.events.append(event)

    def checkpoint(self, result):
        self.checkpoints.append(result)


def _request(**changes):
    values = {
        "side": "left",
        "side_confirmed": True,
        "fixture_confirmed": True,
        "operator": "operator",
        "build_id": "build-1",
        "fixture_id": "fixture-1",
        "procedure_id": "WI-00015",
        "output_root": "output",
        "run_id": "run-1",
    }
    values.update(changes)
    return SingleSensorLaserCalibrationRequest(**values)


def _preflight(
    *,
    topology=TopologySnapshot(True, True, False),
    console_serial="console-1",
    sensor_serial="sensor-1",
    ophir_ready=True,
    ophir_failure_reason=None,
):
    return PreflightSnapshot(
        topology=topology,
        console_identity=DeviceIdentity("console", console_serial, "1.0", "console-hw"),
        selected_sensor_identity=DeviceIdentity("sensor", sensor_serial, "1.0", "sensor-hw"),
        ophir_identity=OphirIdentity("meter", "meter-1", "sensor", "ophir-1", "2027-01-01"),
        ophir_ready=ophir_ready,
        ophir_failure_reason=ophir_failure_reason,
    )


@pytest.mark.parametrize(
    ("side", "side_confirmed"),
    [("left", False), ("neither", True)],
)
def test_preflight_rejects_an_unconfirmed_or_invalid_sensor_side_before_bench_access(
    side, side_confirmed
):
    """Allowing an unconfirmed side would let a run target the wrong module."""
    bench = FakeLaserBench()
    recorder = FakeRecorder()
    request = _request(side=side, side_confirmed=side_confirmed)

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(request)

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == "Confirm the selected sensor side before continuing."
    assert bench.calls == ["stop_trigger"]
    assert recorder.checkpoints == [result]


def test_preflight_requires_confirmed_fixture_placement_before_bench_access():
    """Skipping fixture confirmation could fire a laser outside containment."""
    bench = FakeLaserBench()
    recorder = FakeRecorder()
    request = _request(side="right", fixture_confirmed=False)

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(request)

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == "Confirm fixture placement before continuing."
    assert bench.calls == ["stop_trigger"]
    assert recorder.checkpoints == [result]


def test_preflight_rejects_non_exact_topology_before_any_configuration_or_measurement():
    """A second or wrong-side sensor makes laser calibration ambiguous."""
    snapshot = _preflight(topology=TopologySnapshot(True, True, True))
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.topology == TopologySnapshot(True, True, True)
    assert result.failure_reason == "Expected a console and exactly the declared sensor side."
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == [result]


@pytest.mark.parametrize(
    ("console_serial", "sensor_serial", "reason"),
    [
        ("  ", "sensor-1", "Console serial must be nonblank text."),
        ("console-1", None, "Selected-sensor serial must be nonblank text."),
    ],
)
def test_preflight_rejects_blank_console_or_selected_sensor_serial(
    console_serial, sensor_serial, reason
):
    """Blank identity evidence would make a completed calibration untraceable."""
    snapshot = _preflight(
        console_serial=console_serial,
        sensor_serial=sensor_serial,
    )
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == reason
    assert result.identities == (snapshot.console_identity, snapshot.selected_sensor_identity)
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == [result]


def test_preflight_returns_an_ophir_failure_as_a_structured_setup_result():
    """A missing Ophir meter must stop before configuration or laser firing."""
    snapshot = _preflight(
        ophir_ready=False,
        ophir_failure_reason="No Ophir energy meter was found.",
    )
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == "No Ophir energy meter was found."
    assert result.ophir_identity == snapshot.ophir_identity
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == [result]


def test_preflight_exception_becomes_a_checkpointed_setup_failure_and_stops_trigger():
    """Leaking a bench preflight exception would skip the procedure evidence."""
    bench = FakeLaserBench([RuntimeError("meter startup failed")])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == "Bench preflight failed."
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == [result]


def test_completed_ophir_preflight_precedes_later_configuration_or_measurement_work():
    """Configuration or firing before an Ophir-ready result violates fail-closed order."""
    snapshot = _preflight()
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.PASSED
    assert result.topology == snapshot.topology
    assert result.identities == (snapshot.console_identity, snapshot.selected_sensor_identity)
    assert result.ophir_identity == snapshot.ophir_identity
    assert [event.stage for event in result.events] == ["confirmation", "preflight"]
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == []
