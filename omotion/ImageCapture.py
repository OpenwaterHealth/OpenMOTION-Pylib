"""Drip-scan full-frame single-exposure image capture (camera-fpga#8).

Host side of the drip-scan feature: the FPGA pushes each sensor row as a
2408-B packed-RAW10 line; sensor firmware forwards it blind on the HISTO USB
endpoint as a 2420-B TYPE_IMAGE (0x03) stream packet; this module parses,
CRC-verifies, and reassembles lines into 1280x1920 uint16 frames, retimes the
OX02C1B for the slow sweep, and orchestrates a capture end to end.

Wire contracts (must match the FPGA and sensor-fw companion implementations):

Line push (2408 B, FPGA -> MCU -> host, opaque to the MCU):
  [0]=magic 0xB6  [1]=version 0x01  [2]=line[7:0]  [3]={flags[3:0],line[11:8]}
  [4]=frame_cnt   [5]=0x00          [6..2405]=1920 px packed RAW10
  [2406..2407]=CRC-16 over bytes 0..2405, big-endian, CRC-CCITT-FALSE
  (poly 0x1021 / init 0xFFFF / MSB-first — byte-identical to sensor-fw
  utils.c util_crc16; we reuse omotion.utils.util_crc16).
  flags bit0 = FPGA line-buffer overrun since sweep start.

RAW10 packing: 4 px -> 5 B; pixel k (k=0..3, readout order) occupies bits
[10k+9:10k] of a 40-bit little-endian group (low byte first on the wire).

USB stream envelope (2420 B, mirrors the histogram envelope conventions in
MotionProcessing.parse_histogram_packet_structured):
  [0]=SOF 0xAA  [1]=TYPE_IMAGE 0x03  [2:6]=u32 LE total length (2420)
  [6]=SOH 0xFF  [7]=cam_id  [8:2416]=line push  [2416]=EOH 0xEE
  [2417:2419]=transport CRC (NOT verified here — the MCU forwards blind, the
  line CRC above is the authoritative integrity check)  [2419]=EOF 0xDD
"""

import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from omotion import _log_root
from omotion.config import OX02C1B_I2C_ADDR
from omotion.i2c_packet import I2C_Packet
from omotion.utils import util_crc16

logger = logging.getLogger(
    f"{_log_root}.ImageCapture" if _log_root else "ImageCapture"
)

# --- Line-push geometry (pinned by the design spec) ------------------------
IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1280
IMAGE_LINE_MAGIC = 0xB6
IMAGE_LINE_VERSION = 0x01
IMAGE_LINE_PIXEL_BYTES = IMAGE_WIDTH * 5 // 4          # 2400
IMAGE_LINE_SIZE = 6 + IMAGE_LINE_PIXEL_BYTES + 2       # 2408
FLAG_OVERRUN = 0x1                                     # header flags bit0
FLAG_WEDGE = 0x2                                       # header flags bit1 — pusher-watchdog wedge

# --- USB envelope (contract B) ---------------------------------------------
_ENV_SOF, _ENV_SOH, _ENV_EOH, _ENV_EOF = 0xAA, 0xFF, 0xEE, 0xDD
IMAGE_PACKET_SIZE = 6 + 1 + 1 + IMAGE_LINE_SIZE + 1 + 3  # 2420


class ImageLineError(ValueError):
    """A line/packet failed framing, header, or CRC validation."""


# ---------------------------------------------------------------------------
# RAW10 pack / unpack
# ---------------------------------------------------------------------------

def unpack_raw10(packed) -> np.ndarray:
    """Unpack RAW10 bytes (4 px -> 5 B little-endian groups) to uint16 pixels.

    Vectorized: reshape to (n_groups, 5), rebuild each 40-bit group value,
    then extract the four 10-bit fields at shifts 0/10/20/30.
    """
    raw = np.frombuffer(bytes(packed), dtype=np.uint8)
    if raw.size == 0 or raw.size % 5:
        raise ValueError(
            f"packed RAW10 length {raw.size} is not a positive multiple of 5"
        )
    g = raw.reshape(-1, 5).astype(np.uint64)
    v = (g[:, 0]
         | (g[:, 1] << np.uint64(8))
         | (g[:, 2] << np.uint64(16))
         | (g[:, 3] << np.uint64(24))
         | (g[:, 4] << np.uint64(32)))
    shifts = (np.arange(4, dtype=np.uint64) * np.uint64(10))[None, :]
    px = ((v[:, None] >> shifts) & np.uint64(0x3FF)).astype(np.uint16)
    return px.reshape(-1)


def pack_raw10(pixels) -> bytes:
    """Reference packer — exact inverse of :func:`unpack_raw10`.

    Used by the test suite to synthesize wire-true lines and by bench tooling
    to build golden inputs. Not performance-critical.
    """
    px = np.asarray(pixels, dtype=np.uint64)
    if px.size == 0 or px.size % 4:
        raise ValueError(f"pixel count {px.size} is not a positive multiple of 4")
    px = px.reshape(-1, 4) & np.uint64(0x3FF)
    v = (px[:, 0] | (px[:, 1] << np.uint64(10))
         | (px[:, 2] << np.uint64(20)) | (px[:, 3] << np.uint64(30)))
    out = bytearray()
    for val in v:
        out += int(val).to_bytes(5, "little")
    return bytes(out)


# ---------------------------------------------------------------------------
# Line / packet parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageLine:
    cam_id: int
    line: int
    flags: int
    overrun: bool
    wedge: bool
    frame_cnt: int
    pixels: np.ndarray   # uint16[IMAGE_WIDTH]


def parse_image_line(line_bytes, cam_id: int = -1) -> ImageLine:
    """Validate and decode one 2408-B line push. Raises ImageLineError."""
    b = bytes(line_bytes)
    if len(b) != IMAGE_LINE_SIZE:
        raise ImageLineError(
            f"line length {len(b)} != {IMAGE_LINE_SIZE}"
        )
    if b[0] != IMAGE_LINE_MAGIC:
        raise ImageLineError(f"bad magic 0x{b[0]:02X} (expected 0xB6)")
    if b[1] != IMAGE_LINE_VERSION:
        raise ImageLineError(f"bad format version 0x{b[1]:02X} (expected 0x01)")
    crc_expected = (b[IMAGE_LINE_SIZE - 2] << 8) | b[IMAGE_LINE_SIZE - 1]
    crc_actual = util_crc16(b[: IMAGE_LINE_SIZE - 2])
    if crc_actual != crc_expected:
        raise ImageLineError(
            f"line CRC mismatch (got 0x{crc_actual:04X}, "
            f"expected 0x{crc_expected:04X})"
        )
    line = b[2] | ((b[3] & 0x0F) << 8)
    flags = (b[3] >> 4) & 0x0F
    return ImageLine(
        cam_id=cam_id,
        line=line,
        flags=flags,
        overrun=bool(flags & FLAG_OVERRUN),
        wedge=bool(flags & FLAG_WEDGE),
        frame_cnt=b[4],
        pixels=unpack_raw10(b[6 : 6 + IMAGE_LINE_PIXEL_BYTES]),
    )


def parse_image_packet(pkt) -> ImageLine:
    """Validate the 2420-B USB envelope and decode the line inside.

    The envelope transport-CRC field is intentionally NOT verified: the MCU
    forwards image lines blind (spec §4.1/§4.4) and the FPGA-computed line CRC
    inside the payload is the authoritative integrity check.
    """
    b = bytes(pkt)
    if len(b) != IMAGE_PACKET_SIZE:
        raise ImageLineError(
            f"image packet length {len(b)} != {IMAGE_PACKET_SIZE}"
        )
    if b[0] != _ENV_SOF or b[1] != 0x03:
        raise ImageLineError(
            f"bad envelope header {b[0]:02X} {b[1]:02X} (expected AA 03)"
        )
    total = int.from_bytes(b[2:6], "little")
    if total != IMAGE_PACKET_SIZE:
        raise ImageLineError(f"envelope length field {total} != {IMAGE_PACKET_SIZE}")
    if b[6] != _ENV_SOH:
        raise ImageLineError("missing SOH")
    if b[8 + IMAGE_LINE_SIZE] != _ENV_EOH:
        raise ImageLineError("missing EOH")
    if b[-1] != _ENV_EOF:
        raise ImageLineError("missing EOF")
    return parse_image_line(b[8 : 8 + IMAGE_LINE_SIZE], cam_id=b[7])


# ---------------------------------------------------------------------------
# Frame assembly
# ---------------------------------------------------------------------------

class FrameAssembler:
    """Ordered reassembly of one 1280x1920 uint16 frame from ImageLines.

    Thread-safe (the collector thread adds lines while the orchestrator polls
    completeness — same cross-thread pattern as the rest of the SDK transport
    layer).

    Single-exposure enforcement: the first accepted line pins ``frame_cnt``;
    lines carrying a different value are rejected (counted in
    ``rejected_lines``) unless ``allow_mixed`` is set, in which case they fill
    their gap and the result is flagged ``mixed_exposure`` — the frame is then
    usable for focus inspection but is NOT a single-exposure speckle frame.
    """

    def __init__(self, height: int = IMAGE_HEIGHT, width: int = IMAGE_WIDTH,
                 allow_mixed: bool = False):
        self.height = height
        self.width = width
        self.allow_mixed = allow_mixed
        self._lock = threading.Lock()
        self._img = np.zeros((height, width), dtype=np.uint16)
        self._filled = np.zeros(height, dtype=bool)
        self._frame_cnts: set[int] = set()
        self.frame_cnt: int | None = None
        self.rejected_lines = 0
        self.overrun_seen = False
        self.wedge_seen = False

    def add(self, line: ImageLine) -> bool:
        """Accept one parsed line. Returns True if it was placed."""
        with self._lock:
            if not (0 <= line.line < self.height):
                self.rejected_lines += 1
                logger.warning("cam %d: line %d out of range — rejected",
                               line.cam_id, line.line)
                return False
            if self.frame_cnt is None:
                self.frame_cnt = line.frame_cnt
            elif line.frame_cnt != self.frame_cnt and not self.allow_mixed:
                self.rejected_lines += 1
                logger.warning(
                    "cam %d: line %d frame_cnt 0x%02X != pinned 0x%02X — "
                    "rejected (single-exposure enforcement)",
                    line.cam_id, line.line, line.frame_cnt, self.frame_cnt)
                return False
            if line.overrun:
                self.overrun_seen = True
            if line.wedge:
                self.wedge_seen = True
            self._frame_cnts.add(line.frame_cnt)
            self._img[line.line] = line.pixels
            self._filled[line.line] = True
            return True

    def missing(self) -> list[int]:
        with self._lock:
            return [int(i) for i in np.nonzero(~self._filled)[0]]

    @property
    def complete(self) -> bool:
        with self._lock:
            return bool(self._filled.all())

    @property
    def mixed_exposure(self) -> bool:
        with self._lock:
            return len(self._frame_cnts) > 1

    def image(self) -> np.ndarray:
        """Copy of the frame so far (unfilled rows are zero)."""
        with self._lock:
            return self._img.copy()

    def reset(self) -> None:
        """Discard everything and start a fresh exposure (strict retry)."""
        with self._lock:
            self._img.fill(0)
            self._filled.fill(False)
            self._frame_cnts.clear()
            self.frame_cnt = None
            self.rejected_lines = 0
            self.overrun_seen = False
            self.wedge_seen = False


# ---------------------------------------------------------------------------
# Camera-FPGA register access (I2C 0x5A, register map v2)
#
# Ported from openmotion-camera-fpga tools/full_frame_capture/fpga_link.py.
# Reads use MotionSensor.i2c_read_register directly; writes use the same
# passthrough with the 16-bit-register-address trick (reg_addr_size=2: the
# slave interprets the high byte as the register pointer and the low byte as
# a data write — see the feature/5 design spec).
#
# NOTE: the FPGA control plane is clocked from the MIPI-derived pixel clock —
# register access only works while the camera is streaming (enable first).
# ---------------------------------------------------------------------------

FPGA_I2C_ADDR = 0x5A
REG_ID, REG_VERSION, REG_SCRATCH, REG_CTRL = 0x00, 0x01, 0x02, 0x03
REG_LINE_L, REG_LINE_H, REG_LINE_CUR_L, REG_LINE_CUR_H = 0x04, 0x05, 0x06, 0x07
REG_FRAME_CNT, REG_STATUS = 0x08, 0x09
FPGA_ID_VAL = 0x5A
FPGA_MAP_VERSION_MIN = 0x02      # map v2 = drip-scan capable
CTRL_IMAGE_MODE = 0x01           # CTRL bit0
CTRL_SWEEP = 0x02                # CTRL bit1 — valid only with bit0, sampled at fv
STATUS_OVERRUN = 0x04            # STATUS bit2 — overrun latch, cleared on sweep arm
STATUS_WEDGE = 0x08             # STATUS bit3 — pusher-watchdog wedge latch


class FpgaRegs:
    """Register access to one camera FPGA through the sensor firmware's
    I2C passthrough (MotionSensor.i2c_read_register)."""

    def __init__(self, sensor, cam: int):
        self.sensor = sensor
        self.cam = cam

    def read(self, reg: int) -> int:
        r = self.sensor.i2c_read_register(
            FPGA_I2C_ADDR, reg, read_len=1, reg_addr_size=1,
            mux_channel=self.cam)
        if r is False or r is None:
            raise IOError(f"cam{self.cam}: I2C read reg 0x{reg:02X} failed")
        return r[0]

    def write(self, reg: int, value: int) -> None:
        r = self.sensor.i2c_read_register(
            FPGA_I2C_ADDR, ((reg & 0xFF) << 8) | (value & 0xFF),
            read_len=1, reg_addr_size=2, mux_channel=self.cam)
        if r is False or r is None:
            raise IOError(f"cam{self.cam}: I2C write reg 0x{reg:02X} failed")

    def check_id(self) -> bool:
        try:
            return self.read(REG_ID) == FPGA_ID_VAL
        except IOError:
            return False

    def check_version(self) -> bool:
        """True if the loaded bitstream speaks register map v2 (drip-scan)."""
        try:
            return self.read(REG_VERSION) >= FPGA_MAP_VERSION_MIN
        except IOError:
            return False

    def set_start_line(self, line: int) -> None:
        """Map v2: LINE_L/H hold the sweep start line (12-bit)."""
        self.write(REG_LINE_L, line & 0xFF)
        self.write(REG_LINE_H, (line >> 8) & 0x0F)

    def arm_sweep(self, start_line: int = 0) -> None:
        """Program the start line, then set CTRL = image|sweep. The FPGA
        samples CTRL at the frame-valid boundary and clears the overrun latch
        on arm; every subsequent frame pushes all lines >= start_line."""
        self.set_start_line(start_line)
        self.write(REG_CTRL, CTRL_IMAGE_MODE | CTRL_SWEEP)

    def stop_sweep(self) -> None:
        """Stop sweeping but stay in image mode (no further line pushes)."""
        self.write(REG_CTRL, CTRL_IMAGE_MODE)

    def exit_image_mode(self) -> None:
        """Back to histogram mode. Host rule (feature/5, unchanged): the first
        histogram frame after leaving image mode is garbage — discard it."""
        self.write(REG_CTRL, 0x00)

    def frame_count(self) -> int:
        return self.read(REG_FRAME_CNT)

    def overrun(self) -> bool:
        """STATUS bit2: a line was dropped since the last sweep arm."""
        return bool(self.read(REG_STATUS) & STATUS_OVERRUN)

    def wedge(self) -> bool:
        """STATUS bit3 (map v2, spec §4.3): the pusher watchdog aborted a push
        with no serializer progress since the last sweep arm — an
        electrical/SEU wedge, distinct from a timing overrun (bit2)."""
        return bool(self.read(REG_STATUS) & STATUS_WEDGE)


# ---------------------------------------------------------------------------
# Sensor sweep retiming (group-hold, spec §4.2)
# ---------------------------------------------------------------------------

# OX02C1B group access register (OmniVision datasheet idiom; the shipped
# config table X02C1B_Sensor_Config.h never touches it — drip-scan is the
# first user in this system). Writes bracketed by HOLD_START/HOLD_END land in
# group 0's shadow bank; DELAYED_LAUNCH latches the whole group atomically at
# the next frame boundary. Atomicity matters: tc_r_initial (FSIN slave
# timing) is VTS-coupled and must never be visible with a mismatched VTS.
GROUP_ACCESS_REG = 0x3208
GROUP0_HOLD_START = 0x00
GROUP0_HOLD_END = 0x10
GROUP0_DELAYED_LAUNCH = 0xA0

# Settle delay between passthrough writes — same pacing MotionSensor uses for
# its own multi-write register sequences (camera_set_gain / camera_set_exposure).
_I2C_WRITE_SETTLE_S = 0.02


def write_timing_profile(sensor, cam: int, profile) -> bool:
    """Write one timing profile (config.SWEEP_TIMING_PROFILE or
    config.PRODUCTION_TIMING_PROFILE) to one camera as a single atomic
    group-hold, via the OW_I2C_PASSTHRU path (MotionSensor.camera_i2c_write).

    The new timing takes effect at the camera's NEXT frame boundary — after a
    restore at the 0.8 Hz sweep rate, wait up to one sweep period (1.25 s)
    before assuming production timing is live.

    Returns True only if every write acknowledged.
    """
    sensor.switch_camera(cam)
    sequence = (
        (GROUP_ACCESS_REG, GROUP0_HOLD_START),
        *profile,
        (GROUP_ACCESS_REG, GROUP0_HOLD_END),
        (GROUP_ACCESS_REG, GROUP0_DELAYED_LAUNCH),
    )
    ok = True
    for reg, val in sequence:
        ok = sensor.camera_i2c_write(
            I2C_Packet(device_address=OX02C1B_I2C_ADDR,
                       register_address=reg, data=val)
        ) and ok
        time.sleep(_I2C_WRITE_SETTLE_S)
    if not ok:
        logger.error("cam %d: timing-profile group write failed", cam)
    return ok
