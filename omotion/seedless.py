"""SEEDLESS engineering-test controller (SDK issue #146, app issue #361).

Runs the first N frames of a scan with the seed laser OFF, the TA pulsing
2 ms (deliberately unseeded — the test intends to characterize TA
self-lasing), and camera exposure 2295 us; then restores normal parameters
mid-scan. Requested by bahartl; laser-engineer approved 2026-07-16. Spec:
docs/superpowers/specs/2026-07-15-seedless-frames-design.md.

Everything is host-side register writes:
- console FPGAs over UART I2C (seed gains, TA pulse width, safety limits)
- camera exposure over USB I2C passthrough

SAFETY PROPERTIES this module must preserve:
1. The widened safety-FPGA pulse-width upper limits exist only between
   apply() and restore(). restore() is idempotent and is also called from
   ScanWorkflow teardown on every exit path (complete/cancel/crash).
2. Restore ORDER: TA_PULSE_WIDTH is narrowed FIRST, then (after a settle
   delay covering one in-flight pulse) the safety ULs are re-tightened,
   then the seed comes back on, then exposure. Re-tightening the ULs while
   the TA still fires 2 ms pulses latches TA_shutdown in the safety FPGA
   (pulse_upper_limit_fail), killing the TA for the rest of the scan.
3. apply() snapshots live register values first (I2C read-back) so restore
   writes back what was actually there; the bundled laser_params.json
   baseline is only a fallback when a read fails.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("openmotion.sdk.seedless")

# Console FPGA I2C locations (omotion/data/fpga_model.json): all mux 1,
# device 0x41, little-endian.
_MUX = 1
_DEV = 0x41
_TA_CH, _TA_PW_REG, _TA_PW_LEN = 4, 0x00, 3
_SEED_CH, _SEED_DDS_REG, _SEED_CW_REG, _SEED_GAIN_LEN = 5, 0x02, 0x04, 2
_EE_CH, _OPT_CH, _UL_REG, _UL_LEN = 6, 7, 0x04, 4

# Values. Baselines mirror omotion/data/laser_params.json (locked data).
TA_PULSE_WIDTH_BASELINE = bytes([0x1B, 0x06, 0x00])   # 1563 * 0.32us = 500 us
TA_PULSE_WIDTH_SEEDLESS = bytes([0x6A, 0x18, 0x00])   # 6250 * 0.32us = 2000 us
SEED_DDS_GAIN_BASELINE  = bytes([0x00, 0x00])
SEED_CW_GAIN_BASELINE   = bytes([0x0E, 0x08])         # 2062 -> ~142 mV
SEED_GAIN_OFF           = bytes([0x00, 0x00])
PULSE_WIDTH_UL_BASELINE = bytes([0x35, 0x0C, 0x00, 0x00])  # 3125 -> 1.000 ms
PULSE_WIDTH_UL_SEEDLESS = bytes([0x85, 0x1E, 0x00, 0x00])  # 7813 -> 2.500 ms

# OV2312 exposure via passthrough: byte = us/9. Restore value mirrors the
# sensor-fw config-table default (X02C1B_Sensor_Config.h: 0x3502=0x48,
# 72 rows = 648 us). If sensor-fw changes its default, update this.
EXPOSURE_SEEDLESS_BYTE = 0xFF   # 255 rows = 2295 us
EXPOSURE_RESTORE_BYTE  = 0x48   # 72 rows = 648 us

# (name, channel, reg, len, seedless value, fallback baseline) in APPLY
# order: safety limits widen first, TA width raised last. Restore reverses
# the risk: TA narrows first, limits re-tighten after the settle delay.
_APPLY_SEQUENCE = [
    ("EE_PULSE_WIDTH_UL",  _EE_CH,   _UL_REG,      _UL_LEN,        PULSE_WIDTH_UL_SEEDLESS, PULSE_WIDTH_UL_BASELINE),
    ("OPT_PULSE_WIDTH_UL", _OPT_CH,  _UL_REG,      _UL_LEN,        PULSE_WIDTH_UL_SEEDLESS, PULSE_WIDTH_UL_BASELINE),
    ("SEED_DDS_GAIN",      _SEED_CH, _SEED_DDS_REG, _SEED_GAIN_LEN, SEED_GAIN_OFF,           SEED_DDS_GAIN_BASELINE),
    ("SEED_CW_GAIN",       _SEED_CH, _SEED_CW_REG,  _SEED_GAIN_LEN, SEED_GAIN_OFF,           SEED_CW_GAIN_BASELINE),
    ("TA_PULSE_WIDTH",     _TA_CH,   _TA_PW_REG,    _TA_PW_LEN,     TA_PULSE_WIDTH_SEEDLESS, TA_PULSE_WIDTH_BASELINE),
]


class SeedlessController:
    """Owns the SEEDLESS register lifecycle for one scan.

    Args:
        console: connected MotionConsole (read_i2c_packet/write_i2c_packet).
        sensors: list of (side_name, MotionSensor, camera_mask) for the
            active sides. Cameras are addressed by mask bit index (0-7).
        n_frames: the seedless region length N (frames 1..N).
        settle_s: delay between narrowing TA_PULSE_WIDTH and re-tightening
            the safety ULs on restore (default 0.05 s = 2 frame periods).
            Tests pass 0.
    """

    def __init__(self, *, console: Any, sensors: list, n_frames: int,
                 settle_s: float = 0.05):
        self._console = console
        self._sensors = [(s, sen, m) for (s, sen, m) in sensors
                         if sen is not None and m]
        self.n_frames = int(n_frames)
        self._settle_s = float(settle_s)
        self._snapshot: dict[str, bytes] = {}
        self._applied = False
        self._restored = threading.Event()
        self._restore_started = False
        self._lock = threading.Lock()

    # -- I2C helpers (retry once -- spec error-handling table) --

    def _write(self, name: str, ch: int, reg: int, data: bytes) -> bool:
        for attempt in (1, 2):
            if self._console.write_i2c_packet(
                    mux_index=_MUX, channel=ch, device_addr=_DEV,
                    reg_addr=reg, data=bytearray(data)):
                return True
            logger.warning("seedless: write %s failed (attempt %d)", name, attempt)
        return False

    def _read(self, name: str, ch: int, reg: int, length: int) -> Optional[bytes]:
        try:
            data, dlen = self._console.read_i2c_packet(
                mux_index=_MUX, channel=ch, device_addr=_DEV,
                reg_addr=reg, read_len=length)
        except Exception:
            logger.exception("seedless: read %s raised", name)
            return None
        if data is None or dlen != length:
            return None
        return bytes(data)

    def _set_exposure(self, byte: int) -> bool:
        from omotion.i2c_packet import I2C_Packet
        ok = True
        for side, sensor, mask in self._sensors:
            for cam in range(8):
                if not (mask >> cam) & 1:
                    continue
                try:
                    sensor.switch_camera(cam)
                    sensor.camera_i2c_write(I2C_Packet(
                        device_address=0x36, register_address=0x3501, data=0x00))
                    if self._settle_s:
                        time.sleep(self._settle_s)
                    if not sensor.camera_i2c_write(I2C_Packet(
                            device_address=0x36, register_address=0x3502,
                            data=byte)):
                        ok = False
                    if self._settle_s:
                        time.sleep(self._settle_s)
                except Exception:
                    logger.exception(
                        "seedless: exposure write failed (%s cam %d)", side, cam)
                    ok = False
        return ok

    # -- lifecycle --

    def apply(self) -> bool:
        """Snapshot live values, then write the seedless configuration.

        Returns False on any console write failure -- the caller must abort
        the scan BEFORE starting the trigger (and call restore()).
        """
        for name, ch, reg, length, _, baseline in _APPLY_SEQUENCE:
            live = self._read(name, ch, reg, length)
            if live is None:
                logger.warning(
                    "seedless: snapshot read of %s failed; restore will use "
                    "the bundled baseline", name)
                live = baseline
            self._snapshot[name] = live
        self._applied = True   # before writes: any partial apply must restore

        logger.info("seedless: applying (N=%d): seed OFF, TA 2 ms, safety "
                    "ULs widened, exposure 2295 us", self.n_frames)
        for name, ch, reg, _, seedless_val, _ in _APPLY_SEQUENCE:
            if not self._write(name, ch, reg, seedless_val):
                logger.error("seedless: apply failed at %s -- aborting", name)
                return False
        if not self._set_exposure(EXPOSURE_SEEDLESS_BYTE):
            logger.error("seedless: exposure apply failed -- aborting")
            return False
        return True

    def schedule_restore(self) -> None:
        """Fire-and-forget restore on a dedicated thread (called by
        SeedlessWatchStage from the pipeline runner thread)."""
        with self._lock:
            if self._restore_started:
                return
            self._restore_started = True
        threading.Thread(target=self.restore, daemon=True,
                         name="SeedlessRestore").start()

    def restore(self) -> bool:
        """Write everything back. Idempotent; safe from any thread.

        ORDER MATTERS -- see module docstring. Never re-tighten the safety
        ULs while TA_PULSE_WIDTH is still at the seedless value.
        """
        with self._lock:
            if not self._applied or self._restored.is_set():
                return True
            ok = True
            snap = self._snapshot
            ok &= self._write("TA_PULSE_WIDTH", _TA_CH, _TA_PW_REG,
                              snap.get("TA_PULSE_WIDTH", TA_PULSE_WIDTH_BASELINE))
            if self._settle_s:
                time.sleep(self._settle_s)   # let any in-flight 2 ms pulse clear
            ok &= self._write("EE_PULSE_WIDTH_UL", _EE_CH, _UL_REG,
                              snap.get("EE_PULSE_WIDTH_UL", PULSE_WIDTH_UL_BASELINE))
            ok &= self._write("OPT_PULSE_WIDTH_UL", _OPT_CH, _UL_REG,
                              snap.get("OPT_PULSE_WIDTH_UL", PULSE_WIDTH_UL_BASELINE))
            ok &= self._write("SEED_DDS_GAIN", _SEED_CH, _SEED_DDS_REG,
                              snap.get("SEED_DDS_GAIN", SEED_DDS_GAIN_BASELINE))
            ok &= self._write("SEED_CW_GAIN", _SEED_CH, _SEED_CW_REG,
                              snap.get("SEED_CW_GAIN", SEED_CW_GAIN_BASELINE))
            ok &= self._set_exposure(EXPOSURE_RESTORE_BYTE)
            if ok:
                self._restored.set()
                logger.info("seedless: restore complete")
            else:
                logger.error(
                    "seedless: restore INCOMPLETE -- laser/safety registers "
                    "may still hold test values; they self-heal on the next "
                    "apply_laser_power() or console power-cycle")
            return ok

    def wait_restored(self, timeout: float = 10.0) -> bool:
        return self._restored.wait(timeout)
