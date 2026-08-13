from dataclasses import replace

import pytest

from omotion.WI15SingleSensorLaserCalibration import (
    OphirEvidenceApplicability,
    OphirSettingEvidence,
    PreflightSnapshot,
    SingleSensorLaserCalibrationRequest,
    SingleSensorLaserCalibrationWorkflow,
)
from omotion.WI15LaserCalibration import (
    DEFAULT_USER_CONFIG,
    DeviceIdentity,
    EnergyMeasurement,
    FailureKind,
    OphirIdentity,
    ProcedureStatus,
    TopologySnapshot,
)


class FakeLaserBench:
    def __init__(
        self,
        preflight_queue=(),
        measurements=(),
        user_configuration_reads=None,
        write_result=object(),
        active_registers=None,
        trigger_rate=40.0,
        mutate_written_configuration=False,
        stop_trigger_outcomes=(),
    ):
        self.calls = []
        self.preflight_queue = list(preflight_queue)
        self.measurements = list(measurements or [_valid_measurement()])
        self.user_configuration_reads = list(
            user_configuration_reads
            if user_configuration_reads is not None
            else [{"LEGACY_SETTING": 1}, dict(DEFAULT_USER_CONFIG)]
        )
        self.write_result = write_result
        self.active_registers = dict(active_registers or DEFAULT_USER_CONFIG)
        self.trigger_rate = trigger_rate
        self.mutate_written_configuration = mutate_written_configuration
        self.stop_trigger_outcomes = list(stop_trigger_outcomes)

    def preflight(self, side):
        self.calls.append(f"preflight:{side}")
        outcome = self.preflight_queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def write_user_configuration(self, configuration):
        self.calls.append("write_user_configuration")
        if self.mutate_written_configuration:
            configuration["TA_CURRENT_DRV"] = 1
            self.user_configuration_reads[0] = dict(configuration)
        self.written_user_configuration = dict(configuration)
        return self.write_result

    def read_user_configuration(self):
        self.calls.append("read_user_configuration")
        return self.user_configuration_reads.pop(0)

    def bring_up_laser_configuration(self):
        self.calls.append("bring_up_laser_configuration")

    def read_register(self, name):
        self.calls.append(f"read_register:{name}")
        return self.active_registers[name]

    def write_register(self, name, value):
        self.calls.append(f"write_register:{name}")
        self.active_registers[name] = value
        return None

    def read_trigger_rate_hz(self):
        self.calls.append("read_trigger_rate_hz")
        return self.trigger_rate

    def write_trigger_rate_hz(self, rate_hz):
        self.calls.append(f"write_trigger_rate_hz:{rate_hz}")
        self.trigger_rate = rate_hz
        return rate_hz

    def measure_energy(self):
        self.calls.append("measure_energy")
        return self.measurements.pop(0)

    def stop_trigger(self):
        self.calls.append("stop_trigger")
        if self.stop_trigger_outcomes:
            outcome = self.stop_trigger_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome


def _valid_measurement(**changes):
    values = {
        "n": 26,
        "discarded": 0,
        "mean_uj": 350.0,
        "stdev_uj": 10.0,
        "rate_hz": 40.0,
        "min_uj": 330.0,
        "max_uj": 370.0,
        "duration_s": 0.65,
    }
    values.update(changes)
    return EnergyMeasurement(**values)


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
    console_responsive=True,
    ophir_ready=True,
    ophir_identity=OphirIdentity("meter", "meter-1", "sensor", "ophir-1", "2027-01-01"),
    ophir_setting_evidence=None,
    ophir_failure_reason=None,
):
    if ophir_setting_evidence is None:
        ophir_setting_evidence = _valid_ophir_setting_evidence()
    return PreflightSnapshot(
        topology=topology,
        console_identity=DeviceIdentity("console", console_serial, "1.0", "console-hw"),
        selected_sensor_identity=DeviceIdentity("sensor", sensor_serial, "1.0", "sensor-hw"),
        ophir_identity=ophir_identity,
        ophir_ready=ophir_ready,
        console_responsive=console_responsive,
        ophir_setting_evidence=ophir_setting_evidence,
        ophir_failure_reason=ophir_failure_reason,
    )


def _valid_ophir_setting_evidence():
    return (
        OphirSettingEvidence(
            "measurement_mode",
            "Energy",
            "Energy",
            OphirEvidenceApplicability.APPLICABLE,
            True,
        ),
        OphirSettingEvidence(
            "range_mj", 2.0, 2.0, OphirEvidenceApplicability.APPLICABLE, True
        ),
        OphirSettingEvidence(
            "wavelength_nm", 795, 795, OphirEvidenceApplicability.APPLICABLE, True
        ),
        OphirSettingEvidence(
            "pulse_length_ms", 1.0, 1.0, OphirEvidenceApplicability.APPLICABLE, True
        ),
        OphirSettingEvidence(
            "threshold",
            "minimum_available",
            "minimum_available",
            OphirEvidenceApplicability.APPLICABLE,
            True,
        ),
        OphirSettingEvidence(
            "display_averaging_s",
            3,
            None,
            OphirEvidenceApplicability.NOT_APPLICABLE,
            True,
        ),
        OphirSettingEvidence(
            "graph_mode",
            "Statistics",
            None,
            OphirEvidenceApplicability.NOT_APPLICABLE,
            True,
        ),
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


@pytest.mark.parametrize("console_responsive", [False, None])
def test_preflight_requires_an_explicitly_responsive_console(console_responsive):
    """A connected-but-unresponsive console cannot safely authorize a run."""
    snapshot = _preflight(console_responsive=console_responsive)
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == "Console must be responsive before continuing."
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == [result]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"ophir_identity": None}, "Ophir identity must be present."),
        (
            {
                "ophir_identity": OphirIdentity(
                    "meter", None, "sensor", "ophir-1", "2027-01-01"
                )
            },
            "Ophir identity fields must be nonblank text.",
        ),
    ],
)
def test_preflight_requires_complete_ophir_identity(changes, reason):
    """Incomplete Ophir evidence must not authorize configuration or firing."""
    snapshot = _preflight(**changes)
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == reason
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == [result]


def test_preflight_accepts_exact_complete_ophir_setting_evidence():
    """Changing or omitting any approved Ophir setting must fail closed."""
    snapshot = _preflight()
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.PASSED
    assert result.ophir_setting_evidence == snapshot.ophir_setting_evidence
    assert bench.calls[-1] == "stop_trigger"
    assert "write_user_configuration" in bench.calls
    assert recorder.checkpoints == []


@pytest.mark.parametrize(
    "evidence",
    [
        _valid_ophir_setting_evidence()[:-1],
        _valid_ophir_setting_evidence()
        + (
            OphirSettingEvidence(
                "extra_setting",
                1,
                1,
                OphirEvidenceApplicability.APPLICABLE,
                True,
            ),
        ),
        _valid_ophir_setting_evidence()[:-1] + (_valid_ophir_setting_evidence()[0],),
        _valid_ophir_setting_evidence()[:1]
        + (replace(_valid_ophir_setting_evidence()[1], actual=3.0),)
        + _valid_ophir_setting_evidence()[2:],
        (replace(_valid_ophir_setting_evidence()[0], passed=False),)
        + _valid_ophir_setting_evidence()[1:],
        (replace(_valid_ophir_setting_evidence()[0], name="arbitrary_name"),)
        + _valid_ophir_setting_evidence()[1:],
        (
            replace(
                _valid_ophir_setting_evidence()[0],
                actual=None,
                applicability=OphirEvidenceApplicability.NOT_APPLICABLE,
            ),
        )
        + _valid_ophir_setting_evidence()[1:],
    ],
    ids=[
        "missing",
        "extra",
        "duplicate",
        "mismatched",
        "failed",
        "arbitrary-name",
        "wrongly-applicable",
    ],
)
def test_preflight_rejects_invalid_ophir_setting_evidence(evidence):
    """Incomplete or unverified Ophir setup must not authorize laser work."""
    snapshot = _preflight(ophir_setting_evidence=evidence)
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == "Ophir setting evidence is incomplete or invalid."
    assert result.ophir_setting_evidence == evidence
    assert bench.calls == ["preflight:left", "stop_trigger"]
    assert recorder.checkpoints == [result]


@pytest.mark.parametrize(
    ("display_applicability", "graph_applicability"),
    [
        (
            OphirEvidenceApplicability.APPLICABLE,
            OphirEvidenceApplicability.NOT_APPLICABLE,
        ),
        (
            OphirEvidenceApplicability.NOT_APPLICABLE,
            OphirEvidenceApplicability.APPLICABLE,
        ),
        (
            OphirEvidenceApplicability.APPLICABLE,
            OphirEvidenceApplicability.APPLICABLE,
        ),
    ],
    ids=["display-applicable", "graph-applicable", "both-applicable"],
)
def test_preflight_rejects_non_direct_streaming_optional_ophir_evidence(
    display_applicability, graph_applicability
):
    """Direct streaming requires both display-only settings to be inapplicable."""
    evidence = _valid_ophir_setting_evidence()
    evidence = evidence[:5] + (
        replace(
            evidence[5],
            actual=3 if display_applicability is OphirEvidenceApplicability.APPLICABLE else None,
            applicability=display_applicability,
        ),
        replace(
            evidence[6],
            actual="Statistics"
            if graph_applicability is OphirEvidenceApplicability.APPLICABLE
            else None,
            applicability=graph_applicability,
        ),
    )
    snapshot = _preflight(ophir_setting_evidence=evidence)
    bench = FakeLaserBench([snapshot])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_reason == "Ophir setting evidence is incomplete or invalid."
    assert result.ophir_setting_evidence == evidence
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
    assert result.ophir_setting_evidence == snapshot.ophir_setting_evidence
    assert [event.stage for event in result.events] == [
        "confirmation",
        "preflight",
        "default_configuration",
        "tuning",
    ]
    assert bench.calls == [
        "preflight:left",
        "read_user_configuration",
        "write_user_configuration",
        "read_user_configuration",
        "bring_up_laser_configuration",
        "read_register:TA_CURRENT_DRV",
        "read_register:TA_PULSE_WIDTH",
        "read_register:SEED_CW_GAIN",
        "read_register:EE_PULSE_WIDTH_UL",
        "read_register:OPT_PULSE_WIDTH_UL",
        "read_trigger_rate_hz",
        "measure_energy",
        "stop_trigger",
    ]


def test_checked_default_configuration_preserves_prior_config_and_requires_exact_readback():
    """Skipping any configuration evidence can conceal a partial default write."""
    snapshot = _preflight()
    prior = {"TA_PULSE_WIDTH": 470, "SITE_MARKER": 3}
    bench = FakeLaserBench(
        [snapshot],
        user_configuration_reads=[prior, dict(DEFAULT_USER_CONFIG)],
    )

    result = SingleSensorLaserCalibrationWorkflow(bench, FakeRecorder()).run(_request())

    assert result.status is ProcedureStatus.PASSED
    assert result.pre_existing_config == prior
    assert result.requested_default_config == DEFAULT_USER_CONFIG
    assert result.default_config_readback == DEFAULT_USER_CONFIG
    assert bench.written_user_configuration == DEFAULT_USER_CONFIG
    assert bench.calls.index("bring_up_laser_configuration") > bench.calls.index(
        "read_user_configuration", 2
    )


def test_default_write_without_a_checked_result_fails_before_bringup_or_measurement():
    """Treating a missing SDK write result as success could fire unknown laser settings."""
    bench = FakeLaserBench([_preflight()], write_result=None)
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.CONFIGURATION
    assert result.pre_existing_config == {"LEGACY_SETTING": 1}
    assert result.requested_default_config == DEFAULT_USER_CONFIG
    assert result.default_config_readback is None
    assert "bring_up_laser_configuration" not in bench.calls
    assert "measure_energy" not in bench.calls
    assert recorder.checkpoints == [result]


@pytest.mark.parametrize(
    "readback",
    [
        {key: value for key, value in DEFAULT_USER_CONFIG.items() if key != "TEC_TRIP"},
        {**DEFAULT_USER_CONFIG, "UNAPPROVED_KEY": 1},
        {**DEFAULT_USER_CONFIG, "TA_CURRENT_DRV": 4999},
    ],
    ids=["missing-key", "extra-key", "mismatched-value"],
)
def test_default_readback_mismatch_fails_before_bringup_or_measurement(readback):
    """A non-exact readback leaves the persisted default configuration unproven."""
    bench = FakeLaserBench(
        [_preflight()],
        user_configuration_reads=[{"LEGACY_SETTING": 1}, readback],
    )

    result = SingleSensorLaserCalibrationWorkflow(bench, FakeRecorder()).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.CONFIGURATION
    assert result.default_config_readback == readback
    assert "bring_up_laser_configuration" not in bench.calls
    assert "measure_energy" not in bench.calls


def test_mutating_bench_write_cannot_change_the_approved_default_readback_contract():
    """A mutable SDK write argument must not redefine the approved default object."""
    bench = FakeLaserBench(
        [_preflight()],
        mutate_written_configuration=True,
    )
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.CONFIGURATION
    assert result.requested_default_config == DEFAULT_USER_CONFIG
    assert result.default_config_readback == {**DEFAULT_USER_CONFIG, "TA_CURRENT_DRV": 1}
    assert "bring_up_laser_configuration" not in bench.calls
    assert "measure_energy" not in bench.calls
    assert recorder.checkpoints == [result]


@pytest.mark.parametrize(
    ("actual_current", "expected_status"),
    [(5100.0, ProcedureStatus.PASSED), (5101.0, ProcedureStatus.FAILED)],
)
def test_active_default_registers_allow_only_inclusive_two_percent_quantization(
    actual_current, expected_status
):
    """A wider active-current tolerance would authorize a materially wrong laser drive."""
    bench = FakeLaserBench(
        [_preflight()], active_registers={**DEFAULT_USER_CONFIG, "TA_CURRENT_DRV": actual_current}
    )

    result = SingleSensorLaserCalibrationWorkflow(bench, FakeRecorder()).run(_request())

    assert result.status is expected_status
    assert result.configurations[0].name == "TA_CURRENT_DRV"
    assert result.configurations[0].requested == 5000
    assert result.configurations[0].actual == actual_current
    if expected_status is ProcedureStatus.FAILED:
        assert result.failure_kind is FailureKind.CONFIGURATION
        assert "measure_energy" not in bench.calls


@pytest.mark.parametrize(
    ("name", "actual"),
    [
        ("TA_CURRENT_DRV", 5150.0),
        ("TA_PULSE_WIDTH", 515.0),
        ("SEED_CW_GAIN", 143.0),
        ("EE_PULSE_WIDTH_UL", 567.0),
        ("OPT_PULSE_WIDTH_UL", 567.0),
    ],
)
def test_each_required_active_default_register_must_stay_within_two_percent(
    name, actual
):
    """Skipping an active default register could fire with an unsafe laser limit."""
    bench = FakeLaserBench(
        [_preflight()], active_registers={**DEFAULT_USER_CONFIG, name: actual}
    )

    result = SingleSensorLaserCalibrationWorkflow(bench, FakeRecorder()).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.CONFIGURATION
    assert result.configurations[-1].name == name
    assert result.configurations[-1].actual == actual
    assert "measure_energy" not in bench.calls


def test_corrects_only_trigger_rate_to_40_hz_and_verifies_before_measurement():
    """Measuring before a checked 40 Hz correction would invalidate the energy rate gate."""
    bench = FakeLaserBench([_preflight()], trigger_rate=37.0)

    result = SingleSensorLaserCalibrationWorkflow(bench, FakeRecorder()).run(_request())

    assert result.status is ProcedureStatus.PASSED
    assert bench.calls.index("write_trigger_rate_hz:40.0") < bench.calls.index(
        "measure_energy"
    )
    assert bench.calls.count("read_trigger_rate_hz") == 2
    assert result.configurations[-1].name == "trigger_rate_hz"
    assert result.configurations[-1].actual == 40.0


def test_corrects_an_in_range_non_40_hz_trigger_to_40_before_measurement():
    """Leaving a 40.5 Hz trigger unchanged would violate the mandated 40 Hz default."""
    bench = FakeLaserBench([_preflight()], trigger_rate=40.5)

    result = SingleSensorLaserCalibrationWorkflow(bench, FakeRecorder()).run(_request())

    assert result.status is ProcedureStatus.PASSED
    assert bench.calls.index("write_trigger_rate_hz:40.0") < bench.calls.index(
        "measure_energy"
    )
    assert result.configurations[-1] == result.configurations[-1].__class__(
        "trigger_rate_hz", 40.0, 40.0
    )


@pytest.mark.parametrize(
    "measurement",
    [
        _valid_measurement(n=25),
        _valid_measurement(stdev_uj=40.0),
        _valid_measurement(rate_hz=38.9),
        _valid_measurement(mean_uj=float("nan")),
    ],
    ids=["pulse-count", "stdev", "rate", "non-finite"],
)
def test_invalid_initial_measurement_is_recorded_checkpointed_and_never_advances_to_tuning(
    measurement,
):
    """Using an invalid Ophir observation for tuning would bypass the quality gates."""
    bench = FakeLaserBench([_preflight()], measurements=[measurement])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.MEASUREMENT
    assert result.measurements == (measurement,)
    assert {criterion.name for criterion in result.measurement_criteria[0]} == {
        "n",
        "pulse_count",
        "discarded",
        "mean_uj",
        "stdev_uj",
        "rate_hz",
        "min_uj",
        "max_uj",
        "duration_s",
    }
    assert "tuning" not in [event.stage for event in result.events]
    assert bench.calls[bench.calls.index("measure_energy") + 1] == "stop_trigger"
    assert recorder.checkpoints == [result]


def test_valid_initial_measurement_records_criteria_and_advances_to_tuning():
    """Dropping valid measurement evidence would leave the next tuning stage unauditable."""
    measurement = _valid_measurement()
    bench = FakeLaserBench([_preflight()], measurements=[measurement])

    result = SingleSensorLaserCalibrationWorkflow(bench, FakeRecorder()).run(_request())

    assert result.status is ProcedureStatus.PASSED
    assert result.measurements == (measurement,)
    assert all(criterion.passed for criterion in result.measurement_criteria[0])
    assert result.events[-1].stage == "tuning"
    assert bench.calls[bench.calls.index("measure_energy") + 1] == "stop_trigger"


def test_measurement_exception_still_stops_trigger_and_becomes_a_measurement_failure():
    """A meter exception must not leave the trigger running or lose terminal state."""
    bench = FakeLaserBench([_preflight()], measurements=[RuntimeError("meter failed")])
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.MEASUREMENT
    assert bench.calls[bench.calls.index("measure_energy") + 1] == "stop_trigger"
    assert recorder.checkpoints == [result]


def test_failed_measurement_stop_is_retried_and_returns_a_structured_failure():
    """Marking cleanup complete before stop succeeds can leave a laser firing."""
    bench = FakeLaserBench(
        [_preflight()],
        stop_trigger_outcomes=[RuntimeError("first stop failed"), None],
    )
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.MEASUREMENT
    assert bench.calls.count("stop_trigger") == 2
    assert recorder.checkpoints == [result]


def test_persistent_measurement_stop_failure_is_reported_without_losing_failure_state():
    """A second stop failure must remain reportable after the best-effort retry."""
    bench = FakeLaserBench(
        [_preflight()],
        stop_trigger_outcomes=[
            RuntimeError("first stop failed"),
            RuntimeError("retry stop failed"),
        ],
    )
    recorder = FakeRecorder()

    result = SingleSensorLaserCalibrationWorkflow(bench, recorder).run(_request())

    assert result.status is ProcedureStatus.FAILED
    assert result.failure_kind is FailureKind.MEASUREMENT
    assert result.failure_reason == "Energy measurement failed."
    assert result.trigger_cleanup_failure == "Trigger stop failed."
    assert result.events[-2].stage == "trigger_cleanup"
    assert bench.calls.count("stop_trigger") == 2
    assert recorder.checkpoints == [result]
