"""Unit tests for scripts/test_sensor_program.py (issue #160).

Covers the two bench defects from 2026-07-17:
- startup race: the script must wait for enumeration instead of checking
  connectivity immediately after ``interface.start()``;
- Unicode crash: the script must emit ASCII-only output so cp1252 Windows
  consoles never raise ``UnicodeEncodeError``.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "test_sensor_program.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("sensor_program_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeInterface:
    """Minimal stand-in for MotionInterface connectivity polling.

    ``states`` is a sequence of (console, left, right) tuples returned by
    successive ``is_device_connected()`` calls; the last one repeats forever.
    """

    def __init__(self, states):
        self._states = list(states)
        self.poll_count = 0

    def is_device_connected(self):
        self.poll_count += 1
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]


def test_script_source_is_ascii_only():
    # cp1252 consoles crash on emoji and U+2011 hyphens; keep the script pure ASCII.
    SCRIPT_PATH.read_bytes().decode("ascii")


def test_wait_for_sensor_returns_immediately_when_any_side_connected():
    mod = _load_script()
    iface = FakeInterface([(False, True, False)])
    assert mod._wait_for_sensor(iface, None, timeout_s=0.0) is True


def test_wait_for_sensor_specific_side_not_satisfied_by_other_side():
    mod = _load_script()
    iface = FakeInterface([(True, True, False)])
    assert mod._wait_for_sensor(iface, "right", timeout_s=0.3) is False


def test_wait_for_sensor_sees_late_connection():
    mod = _load_script()
    iface = FakeInterface(
        [(False, False, False), (False, False, False), (False, False, True)]
    )
    assert mod._wait_for_sensor(iface, "right", timeout_s=2.0) is True


def test_main_polls_until_timeout_instead_of_single_check(monkeypatch, capsys):
    mod = _load_script()

    instances = []

    class NeverConnects(FakeInterface):
        def __init__(self):
            super().__init__([(False, False, False)])
            instances.append(self)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(mod, "MotionInterface", NeverConnects)
    monkeypatch.setattr(mod, "STARTUP_TIMEOUT_S", 0.5)
    monkeypatch.setattr(sys, "argv", ["test_sensor_program.py", "fake.bin"])

    assert mod.main() == 1
    assert instances[0].poll_count >= 2, "main() must poll, not check connectivity once"
    out = capsys.readouterr().out
    assert "[FAIL]" in out


def test_main_fails_for_missing_requested_side_despite_other_side(monkeypatch, capsys):
    mod = _load_script()

    class LeftOnly(FakeInterface):
        def __init__(self):
            super().__init__([(True, True, False)])

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(mod, "MotionInterface", LeftOnly)
    monkeypatch.setattr(mod, "STARTUP_TIMEOUT_S", 0.3)
    monkeypatch.setattr(sys, "argv", ["test_sensor_program.py", "fake.bin", "--sensor", "right"])

    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "RIGHT" in out
    assert "[FAIL]" in out
