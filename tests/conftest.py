"""
Shared fixtures and configuration for the OpenMotion SDK hardware test suite.

All session-scoped fixtures skip gracefully when the required hardware is
not present, so a partial rig (console-only, sensor-only, etc.) still
produces a meaningful test run.
"""

import os
import time

import pytest

from omotion.Interface import MOTIONInterface


# ---------------------------------------------------------------------------
# Session-level interface fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def motion():
    """Initialise MOTIONInterface and yield for the whole session."""
    demo = os.getenv("OPENMOTION_DEMO", "0") == "1"
    iface = MOTIONInterface(demo_mode=demo)
    time.sleep(0.5)  # brief settle after enumeration
    yield iface
    iface.disconnect()


# ---------------------------------------------------------------------------
# Console fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def console(motion):
    c = motion.console_module
    if c is None or not c.is_connected():
        pytest.skip("Console module not connected")
    return c


# ---------------------------------------------------------------------------
# Sensor fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sensor_left(motion):
    s = motion.sensors.get("left") if motion.sensors else None
    if s is None or not s.is_connected():
        pytest.skip("Left sensor not connected")
    return s


@pytest.fixture(scope="session")
def sensor_right(motion):
    s = motion.sensors.get("right") if motion.sensors else None
    if s is None or not s.is_connected():
        pytest.skip("Right sensor not connected")
    return s


@pytest.fixture(
    scope="session",
    params=["left", "right"],
    ids=["sensor_left", "sensor_right"],
)
def any_sensor(request, motion):
    """Parametrised fixture — each sensor test runs against both sides."""
    side = request.param
    s = motion.sensors.get(side) if motion.sensors else None
    if s is None or not s.is_connected():
        pytest.skip(f"{side} sensor not connected")
    return s
