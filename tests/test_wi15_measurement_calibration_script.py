from types import SimpleNamespace

import pytest

from wi15_script_harness import load_wi15_script, wi15_script_path


SCRIPT_PATH = wi15_script_path("wi15_measurement_calibration.py")


def load_script():
    return load_wi15_script(SCRIPT_PATH, "wi15_measurement_cal_under_test")


class FakeConsole:
    def read_config(self):
        return SimpleNamespace(
            json_data={"TA_PULSE_WIDTH": 590.0, "TA_CURRENT_DRV": 5000.0}
        )


class FakeInterface:
    def __init__(self, *, outcome="passed", refuse_start=False, **_kwargs):
        self.console = FakeConsole()
        self.outcome = outcome
        self.refuse_start = refuse_start
        self.requests = []
        self.started = 0
        self.stopped = 0
        self.laser_applied = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def wait_for_ready(self, **_kwargs):
        return True

    def is_device_connected(self):
        return (True, True, True)

    def apply_laser_power(self):
        self.laser_applied += 1
        return True

    def start_calibration(self, request, *, on_complete_fn, on_progress_fn=None,
                          on_confirm_fn=None):
        self.requests.append(request)
        if self.refuse_start:
            return False
        on_complete_fn(
            SimpleNamespace(
                outcome=SimpleNamespace(value=self.outcome),
                error="" if self.outcome == "passed" else "below threshold",
                rows=[],
                csv_path="cal.csv",
                json_path="cal.json",
            )
        )
        return True

    def cancel_calibration(self):
        pass


def run_main(tmp_path, monkeypatch, *, argv_extra=(), answers=(),
             outcome="passed", refuse_start=False):
    script = load_script()
    fake = FakeInterface(outcome=outcome, refuse_start=refuse_start)
    monkeypatch.setattr(script, "interface_factory", lambda **kwargs: fake)
    replies = iter(answers)
    lines = []
    code = script.main(
        ["--output-dir", str(tmp_path), "--operator", "op", *argv_extra],
        input_func=lambda prompt: next(replies),
        output_func=lines.append,
    )
    return code, fake, lines


def test_declined_phantom_attestation_cancels_before_hardware(
    monkeypatch, tmp_path
):
    code, fake, lines = run_main(
        tmp_path, monkeypatch, argv_extra=["--side", "left"], answers=("no",)
    )
    assert code == 1
    assert fake.started == 0
    assert any("canceled before hardware construction" in line for line in lines)


def test_side_prompt_reprompts_until_left_or_right(monkeypatch, tmp_path):
    code, fake, _ = run_main(
        tmp_path, monkeypatch,
        argv_extra=["--phantom-confirmed"],
        answers=("both", "LEFT"),
    )
    assert code == 0
    assert fake.requests[0].left_camera_mask == 0xFF
    assert fake.requests[0].right_camera_mask == 0x00


@pytest.mark.parametrize(
    ("side", "left_mask", "right_mask"),
    [("left", 0xFF, 0x00), ("right", 0x00, 0xFF)],
)
def test_only_the_selected_side_is_calibrated(
    monkeypatch, tmp_path, side, left_mask, right_mask
):
    code, fake, _ = run_main(
        tmp_path, monkeypatch,
        argv_extra=["--side", side, "--phantom-confirmed"],
    )
    assert code == 0
    request = fake.requests[0]
    assert request.left_camera_mask == left_mask
    assert request.right_camera_mask == right_mask
    assert fake.laser_applied == 1
    assert fake.stopped == 1


def test_factory_thresholds_encode_spec_69_and_straddle_zero_bfi(monkeypatch, tmp_path):
    code, fake, lines = run_main(
        tmp_path, monkeypatch,
        argv_extra=["--side", "left", "--phantom-confirmed"],
    )
    assert code == 0
    thresholds = fake.requests[0].thresholds
    assert thresholds.min_bfi_per_camera == [-0.5] * 8
    assert thresholds.max_bfi_per_camera == [0.5] * 8
    assert thresholds.min_bvi_per_camera == [4.5] * 8
    assert thresholds.max_bvi_per_camera == [5.5] * 8
    assert thresholds.min_mean_per_camera[0] == 40.0
    assert thresholds.min_mean_per_camera[1] == 80.0


def test_bench_thresholds_disable_brightness_gates_loudly(monkeypatch, tmp_path):
    code, fake, lines = run_main(
        tmp_path, monkeypatch,
        argv_extra=["--side", "left", "--phantom-confirmed",
                    "--bench-thresholds"],
    )
    assert code == 0
    thresholds = fake.requests[0].thresholds
    assert thresholds.min_mean_per_camera == [0.0] * 8
    assert thresholds.min_contrast_per_camera == [0.0] * 8
    assert any("does NOT certify" in line for line in lines)


@pytest.mark.parametrize(
    ("outcome", "expected_code"), [("passed", 0), ("failed", 1)]
)
def test_exit_code_follows_engine_outcome(
    monkeypatch, tmp_path, outcome, expected_code
):
    code, fake, lines = run_main(
        tmp_path, monkeypatch,
        argv_extra=["--side", "right", "--phantom-confirmed"],
        outcome=outcome,
    )
    assert code == expected_code
    assert fake.stopped == 1
    assert any(line.startswith("Terminal status:") for line in lines)


def test_refused_engine_start_fails_and_stops_interface(monkeypatch, tmp_path):
    code, fake, _ = run_main(
        tmp_path, monkeypatch,
        argv_extra=["--side", "left", "--phantom-confirmed"],
        refuse_start=True,
    )
    assert code == 1
    assert fake.stopped == 1
