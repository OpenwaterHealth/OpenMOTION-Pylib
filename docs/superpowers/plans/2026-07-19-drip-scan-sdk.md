# Drip-Scan SDK Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add host-side support for drip-scan full-frame single-exposure image capture to the `omotion` SDK — stream routing for type-0x03 image packets, a CRC-verified RAW10 line parser and frame assembler, sensor sweep retiming, and a capture orchestrator that graduates `tools/full_frame_capture/` from the camera-fpga repo into the shipped wheel.

**Architecture:** A new `omotion/ImageCapture.py` module owns everything image-specific (line parse, RAW10 unpack, frame assembly, FPGA sweep registers, sensor retiming, orchestration); `StreamInterface` gains an opt-in second queue so image packets never reach histogram consumers; `config.py` gains the stream-type byte, the new firmware opcode, and the pinned sweep timing profiles. All logic below the USB boundary is pure and covered by software-only pytest unit tests with hand-computed golden vectors.

**Tech Stack:** Python 3.12+, numpy (existing dep), pytest (existing dev dep), pyusb (existing dep, untouched hot path), PIL via matplotlib's transitive dependency (optional, guarded).

---

## Context for the implementer (read once, before Task 1)

**Repo:** `C:/Users/ethan/Projects/openmotion-sdk`. Work happens on a new branch off `next` (created in Task 1). The approved design spec is `C:/Users/ethan/Projects/openmotion-camera-fpga/docs/superpowers/specs/2026-07-19-drip-scan-single-frame-design.md` — authoritative for intent; this plan is authoritative for SDK code.

### Wire contract A — FPGA line push (2408 B, pinned cross-plan, DO NOT DEVIATE)

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | Magic `0xB6` |
| 1 | 1 | Format version `0x01` |
| 2 | 1 | `line[7:0]` |
| 3 | 1 | `{flags[3:0], line[11:8]}` — flags in the **high** nibble; flag bit0 = overrun since sweep start |
| 4 | 1 | `frame_cnt[7:0]` (free-running fv counter — constant across one single-exposure image) |
| 5 | 1 | Reserved `0x00` |
| 6..2405 | 2400 | 1920 px packed RAW10: 4 px → 5 B; pixel *k* (k=0..3, readout order) occupies bits `[10k+9 : 10k]` of a 40-bit **little-endian** group (low byte first on the wire) |
| 2406..2407 | 2 | CRC-16 over bytes 0..2405, serialized **big-endian** (high byte at 2406) — matches the OW packet-framing serialization (`UartPacket.to_bytes`, `omotion/UartPacket.py:61`, `crc.to_bytes(2, "big")`). This byte order is a cross-plan pin; the FPGA plan must serialize identically. |

**CRC-16 algorithm (verified from real code, both ends):** sensor-fw `Core/Src/utils.c` `util_crc16` (line 59) is a table-driven, **MSB-first CRC-CCITT-FALSE: polynomial 0x1021, init 0xFFFF, no input/output reflection, no final XOR**. The SDK ships the byte-identical table in `omotion/utils.py` `util_crc16` (line 270), and `omotion/MotionProcessing.py:45-47` proves `binascii.crc_hqx(buf, 0xFFFF)` is the same function. Verified check value (computed from both the table code and `crc_hqx` during planning): `crc16(b"123456789") == 0x29B1`. **Use `omotion.utils.util_crc16` for line verification — do not write a new CRC.**

### Wire contract B — USB image packet on the HISTO endpoint (2420 B)

Mirrors the histogram envelope conventions in `omotion/MotionProcessing.py:369-495` (header `<BBI>`, SOH block, footer) per spec §4.4 ("existing header/SOH framing conventions, cam_id + 2408-B line payload"). **This layout must match the sensor-fw companion plan — flag any divergence to Ethan before coding around it.**

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | SOF `0xAA` |
| 1 | 1 | Stream type `0x03` (`TYPE_IMAGE`) |
| 2..5 | 4 | u32 little-endian total packet length = 2420 |
| 6 | 1 | SOH `0xFF` |
| 7 | 1 | `cam_id` (0–7) |
| 8..2415 | 2408 | Line push (contract A) |
| 2416 | 1 | EOH `0xEE` |
| 2417..2418 | 2 | Transport CRC-16 LE — **not verified by the SDK for image packets** (the MCU forwards blind, spec §4.1/§4.4; the FPGA line CRC inside the payload is the authoritative integrity check). Synthetic test packets set it to `0x0000`. |
| 2419 | 1 | EOF `0xDD` |

### Golden vectors (computed during planning by running the real algorithms — embed verbatim in tests)

- `util_crc16(b"123456789") == 0x29B1`
- **RAW10 hand vector:** pixels `[0x001, 0x3FF, 0x155, 0x2AA, 0x0F0, 0x10F, 0x333, 0x0CC]` pack to exactly these 10 bytes: `01 FC 5F 95 AA F0 3C 34 33 33`
- **Golden line:** pixels `p[k] = (7*k + 3) & 0x3FF` for k=0..1919, line=1234, flags=0, frame_cnt=0x5C → header bytes `B6 01 D2 04 5C 00`; first 10 packed payload bytes `03 28 10 01 06 1F 98 D0 02 0D`; `util_crc16(bytes 0..2405) == 0xA25E` (stored as `0xA2` at offset 2406, `0x5E` at 2407); `util_crc16(header 6 B alone) == 0x0075`
- Overrun-flag header variant: flags=0x1, line=1234 → byte3 = `0x14`
- Sweep register bytes: HTS 38400 → `0x96, 0x00`; VTS 1312 → `0x05, 0x20`; tc_r 1308 → `0x05, 0x1C`

### Key existing code you will touch or imitate

| What | Where |
|---|---|
| Stream reader thread, hot path | `omotion/StreamInterface.py:362-426` (`_stream_loop`); `_process_packet` at 327-360 shows the TYPE_HISTO/TYPE_HISTO_CMP dispatch idiom but currently has **no callers** — routing goes in `_stream_loop` |
| Histogram envelope parse conventions | `omotion/MotionProcessing.py:369-495` |
| Sensor command idiom | `omotion/MotionSensor.py:468` (`_send`), `:1658` (`enable_camera`), `:1711` (`switch_camera`), `:1723` (`camera_i2c_write`, device addr `0x36`), `:1376` (`camera_configure_registers`) |
| FPGA I2C register access from host | `openmotion-camera-fpga/tools/full_frame_capture/fpga_link.py:125-178` (`FpgaRegs`, uses `MotionSensor.i2c_read_register` at MotionSensor.py:960 — write is the 16-bit-reg-addr trick, `reg_addr_size=2`) |
| Trigger-rate setter (FSIN rate lives in the console trigger config) | `omotion/MotionConsole.py:1263` (`set_trigger_json`), `:1323` (`get_trigger_json`), `:1359`/`:1395` (`start_trigger`/`stop_trigger`); `TriggerFrequencyHz` key per `config.py:317-327` and `capture.py::setup_trigger` |
| Production sensor timing values to restore | `openmotion-sensor-fw/Core/Inc/X02C1B_Sensor_Config.h:723-726` (HTS 0x01B0=432, VTS 0x0AD0=2768), `:739-740` (exposure 0x0048=72 rows), `:744-745` (tc_r_initial shipped as 0x0000) |
| Prior host tooling being graduated | `openmotion-camera-fpga/tools/full_frame_capture/capture.py` (accumulate/parse loop, retry-from-gap, npy+png output, `meta.json`) |

**Group-hold note:** the shipped config table contains no `0x3208` writes — the group-access idiom comes from the OX02C1B datasheet (spec §4.2 mandates group-hold): `0x3208=0x00` (start group 0), … register writes …, `0x3208=0x10` (end group 0), `0x3208=0xA0` (delayed launch — latches atomically at the next frame boundary).

**FPGA register map v2 (I2C 0x5A, pinned):** `VERSION` (0x01) reads `0x02`; `CTRL` (0x03) bit0 = image mode, bit1 = SWEEP (valid only with bit0, sampled at frame-valid boundary); `LINE_L/H` (0x04/0x05) = sweep start line; `STATUS` (0x09) bit2 = overrun latch (cleared on sweep arm).

**New firmware opcode (pinned):** `OW_CAMERA_IMAGE_MODE = 0x30`, packet type `OW_CAMERA`, `reserved` byte = enable 0/1, `data[0]` = camera bitmask. (No collision: `OW_IMU_INIT`/`FPGA_PROG_OPEN` also use 0x30 but live in different packet-type namespaces.)

**Retry vs. single-exposure:** each FSIN at 0.8 Hz triggers a fresh exposure and a full sweep from the FPGA start line. Lines from different sweeps have different `frame_cnt` — mixing them breaks the single-exposure guarantee (spec §5 acceptance: "`frame_cnt` constant across all lines"). Policy implemented here: strict attempts **restart** assembly from line 0 on a new exposure; only the final `mixed_fill_sweeps` attempts fall back to gap-filling from the first missing line with the image flagged `mixed_exposure`.

**Dependency rule:** `omotion` is the deliverable wheel — no new dependencies. PNG output uses PIL, which arrives transitively via the declared `matplotlib` dependency (`pyproject.toml:19`); the import is guarded and the capture degrades to `.npy`-only with a logged warning if PIL is absent. Do **not** add `pillow` to `pyproject.toml`.

**File map:**

- Create: `omotion/ImageCapture.py`, `tests/test_image_capture.py`, `tests/test_stream_image_routing.py`
- Modify: `omotion/config.py`, `omotion/StreamInterface.py`, `omotion/MotionSensor.py`, `docs/TestSuite.md`

---

### Task 1: Companion issue, board card, branch

**Files:** none (process only). Run from `C:/Users/ethan/Projects/openmotion-sdk`.

- [ ] **Step 1: Check for an existing issue**

```bash
gh issue list -R OpenwaterHealth/openmotion-sdk --search "drip-scan image capture" --state all
```

If an open issue for SDK drip-scan capture already exists, record its number as `<N>` and skip Step 2.

- [ ] **Step 2: Create the issue**

```bash
gh issue create -R OpenwaterHealth/openmotion-sdk \
  --title "Drip-scan full-frame single-exposure image capture (SDK side)" \
  --label feature \
  --body "SDK companion to OpenwaterHealth/openmotion-camera-fpga#8 (drip-scan single-frame readout, design spec 2026-07-19).

Scope (spec §4.2, §4.5):
- config.py: TYPE_IMAGE stream byte 0x03, OW_CAMERA_IMAGE_MODE=0x30, pinned sweep/production timing profiles
- StreamInterface: route type-0x03 packets to a dedicated image queue (histogram consumers never see them)
- New omotion/ImageCapture.py: CRC-verified line parser (util_crc16, byte-identical to sensor-fw utils.c), vectorized RAW10 unpacker, FrameAssembler with frame_cnt single-exposure check, group-hold sweep retiming writer, capture orchestrator graduating camera-fpga tools/full_frame_capture/
- Software-only pytest coverage with hand-computed golden vectors

Cross-repo: FPGA register map v2 at I2C 0x5A; sensor-fw companion adds OW_CAMERA_IMAGE_MODE and 2408-B line forwarding."
```

Record the issue number as `<N>` — it is used in the branch name, every commit's context, and the PR body.

- [ ] **Step 3: Add to the project board and set In progress**

```bash
gh project item-add 11 --owner OpenwaterHealth --url https://github.com/OpenwaterHealth/openmotion-sdk/issues/<N> --format json
```

Take the returned item `id`, then:

```bash
gh project item-edit --id <ITEM_ID> --project-id PVT_kwDOAif52c4BVgTu --field-id PVTSSF_lADOAif52c4BVgTuzhQ7qcU --single-select-option-id 47fc9ee4
```

- [ ] **Step 4: Comment the planned approach on the issue**

```bash
gh issue comment <N> -R OpenwaterHealth/openmotion-sdk --body "Starting implementation per docs/superpowers/plans/2026-07-19-drip-scan-sdk-capture.md (this repo, on the feature branch). Order: config constants -> line parser/RAW10 unpacker -> FrameAssembler -> StreamInterface image-queue routing -> OW_CAMERA_IMAGE_MODE sender + FPGA v2 regs -> group-hold sweep retiming -> capture orchestrator. All parsing logic covered by software-only unit tests with golden vectors (line CRC 0xA25E vector, RAW10 8-px hand vector)."
```

- [ ] **Step 5: Branch off next**

```bash
git -C C:/Users/ethan/Projects/openmotion-sdk fetch origin
git -C C:/Users/ethan/Projects/openmotion-sdk checkout next
git -C C:/Users/ethan/Projects/openmotion-sdk pull --ff-only origin next
git -C C:/Users/ethan/Projects/openmotion-sdk checkout -b feature/<N>-drip-scan-capture
```

---

### Task 2: config.py constants

**Files:**
- Modify: `omotion/config.py` (three insertion points: after line 102 `OW_CAMERA_STREAM = 0x07`; after line 130 `CMP_UNCMP_CRC_SIZE = 2`; end of file)
- Test: `tests/test_image_capture.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_image_capture.py`:

```python
"""Software-only unit tests for drip-scan image capture (camera-fpga#8, SDK issue #<N>).

Covers: pinned config constants, RAW10 pack/unpack bit layout, line parse with
CRC verification, USB envelope parse, FrameAssembler, sweep retiming sequence,
and the sweep retry policy. No hardware required.
"""

import numpy as np
import pytest

pytestmark = pytest.mark.unit


def test_config_constants_pinned_values():
    """The cross-plan pinned wire/protocol constants. If this test fails after
    an edit to config.py, firmware/FPGA interop is broken — these values are
    fixed by the drip-scan design spec and must not drift."""
    from omotion import config

    assert config.TYPE_IMAGE == 0x03
    assert config.OW_IMAGE_PACKET == 0x03          # pre-existing, same value, different namespace
    assert config.OW_CAMERA_IMAGE_MODE == 0x30
    assert config.SWEEP_FSIN_HZ == 0.8
    assert config.PRODUCTION_FSIN_HZ == 40.0
    # Sweep profile: HTS=38400, VTS=1312, tc_r_initial=1308, exposure=1 row.
    assert config.SWEEP_TIMING_PROFILE == (
        (0x380C, 0x96), (0x380D, 0x00),
        (0x380E, 0x05), (0x380F, 0x20),
        (0x3826, 0x05), (0x3827, 0x1C),
        (0x3501, 0x00), (0x3502, 0x01),
    )
    # Restore profile: shipped production values from X02C1B_Sensor_Config.h.
    assert config.PRODUCTION_TIMING_PROFILE == (
        (0x380C, 0x01), (0x380D, 0xB0),
        (0x380E, 0x0A), (0x380F, 0xD0),
        (0x3826, 0x00), (0x3827, 0x00),
        (0x3501, 0x00), (0x3502, 0x48),
    )
```

- [ ] **Step 2: Run and verify it fails**

Run: `pytest tests/test_image_capture.py -v`
Expected: FAIL with `AttributeError: module 'omotion.config' has no attribute 'TYPE_IMAGE'`

- [ ] **Step 3: Add the constants**

In `omotion/config.py`, insert after line 102 (`OW_CAMERA_STREAM = 0x07`):

```python
# Full-frame image (drip-scan) receive mode — camera-fpga#8. Firmware payload:
# reserved byte = enable (0/1), data[0] = camera bitmask. 0x30 is free in the
# OW_CAMERA command namespace (OW_IMU_INIT and FPGA_PROG_OPEN reuse the value
# in their own packet-type namespaces — no conflict).
OW_CAMERA_IMAGE_MODE = 0x30
```

Insert after line 130 (`CMP_UNCMP_CRC_SIZE = 2`):

```python
# Image streaming packet type (byte[1] of stream packets on the HISTO
# endpoint) — sibling of TYPE_HISTO / TYPE_HISTO_CMP above. Same numeric value
# as OW_IMAGE_PACKET (0x03): that constant names the content class in the
# OW_*_PACKET family; TYPE_IMAGE is the stream-envelope type byte the reader
# thread dispatches on. Defined separately so each namespace stays coherent.
TYPE_IMAGE = 0x03
```

Append at end of file:

```python
# ---------------------------------------------------------------------------
# Drip-scan sweep retiming (camera-fpga#8, design spec 2026-07-19 §4.2).
#
# Ordered (register, value) writes for the OX02C1B, applied inside a sensor
# group-hold (0x3208) so they latch atomically at a frame boundary — see
# omotion/ImageCapture.py write_timing_profile(). Values are pinned by the
# cross-repo design; production values restore the shipped configuration in
# openmotion-sensor-fw Core/Inc/X02C1B_Sensor_Config.h (HTS/VTS lines 723-726,
# exposure 739-740, tc_r_initial 744-745).
# ---------------------------------------------------------------------------
OX02C1B_I2C_ADDR = 0x36
"""7-bit I2C address of the OX02C1B image sensor (see MotionSensor.camera_set_gain)."""

SWEEP_TIMING_PROFILE: tuple = (
    (0x380C, 0x96), (0x380D, 0x00),   # HTS = 38400  (~0.80 ms/row: row drain margin)
    (0x380E, 0x05), (0x380F, 0x20),   # VTS = 1312   (1280 active + minimal blanking)
    (0x3826, 0x05), (0x3827, 0x1C),   # tc_r_initial = 1308 (FSIN slave timing is VTS-coupled)
    (0x3501, 0x00), (0x3502, 0x01),   # exposure = 1 row (~0.80 ms shutter window)
)
"""Sweep (drip-scan) sensor timing. One atomic group-hold write."""

PRODUCTION_TIMING_PROFILE: tuple = (
    (0x380C, 0x01), (0x380D, 0xB0),   # HTS = 432
    (0x380E, 0x0A), (0x380F, 0xD0),   # VTS = 2768
    (0x3826, 0x00), (0x3827, 0x00),   # tc_r_initial shipped value
    (0x3501, 0x00), (0x3502, 0x48),   # exposure = 72 rows
)
"""Shipped production timing (X02C1B_Sensor_Config.h) — restore after a capture."""

SWEEP_FSIN_HZ: float = 0.8
"""FSIN trigger rate during a drip-scan capture (period > 1312 x 0.80 ms readout)."""

PRODUCTION_FSIN_HZ: float = 40.0
"""Normal histogram-mode FSIN rate (DEFAULT_TRIGGER_CONFIG TriggerFrequencyHz)."""
```

- [ ] **Step 4: Run and verify it passes**

Run: `pytest tests/test_image_capture.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add omotion/config.py tests/test_image_capture.py
git commit -m "feat: add drip-scan protocol and sweep-timing constants (#<N>)"
```

---

### Task 3: ImageCapture module — RAW10 pack/unpack, line parser, envelope parser

**Files:**
- Create: `omotion/ImageCapture.py`
- Test: `tests/test_image_capture.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_image_capture.py`:

```python
# ---------------------------------------------------------------------------
# RAW10 packing / line parsing
# ---------------------------------------------------------------------------

# Hand vector computed independently during planning: pixel k of each 4-pixel
# group occupies bits [10k+9:10k] of a 40-bit little-endian group.
_HAND_PIXELS = [0x001, 0x3FF, 0x155, 0x2AA, 0x0F0, 0x10F, 0x333, 0x0CC]
_HAND_BYTES = bytes([0x01, 0xFC, 0x5F, 0x95, 0xAA, 0xF0, 0x3C, 0x34, 0x33, 0x33])

# Golden full line: p[k] = (7k+3) & 0x3FF, line=1234, flags=0, frame_cnt=0x5C.
# Header, first packed bytes, and CRC computed independently during planning.
_GOLDEN_HDR = bytes([0xB6, 0x01, 0xD2, 0x04, 0x5C, 0x00])
_GOLDEN_PACKED_HEAD = bytes([0x03, 0x28, 0x10, 0x01, 0x06, 0x1F, 0x98, 0xD0, 0x02, 0x0D])
_GOLDEN_CRC = 0xA25E


def _golden_pixels():
    return [(7 * k + 3) & 0x3FF for k in range(1920)]


def _golden_line_bytes(line=1234, flags=0, frame_cnt=0x5C, pixels=None):
    """Build a full 2408-B line push with a correct CRC (reference builder for
    tests; bit-layout independence is anchored by _HAND_BYTES/_GOLDEN_* which
    were computed outside this codebase)."""
    from omotion.ImageCapture import pack_raw10
    from omotion.utils import util_crc16

    packed = pack_raw10(pixels if pixels is not None else _golden_pixels())
    hdr = bytes([0xB6, 0x01, line & 0xFF,
                 ((flags & 0xF) << 4) | ((line >> 8) & 0xF), frame_cnt, 0x00])
    body = hdr + packed
    crc = util_crc16(body)
    return body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])   # CRC big-endian


def test_crc16_is_ccitt_false():
    """Proves the SDK CRC used for line verification is the CRC-CCITT-FALSE
    variant implemented byte-identically in sensor-fw utils.c util_crc16
    (poly 0x1021, init 0xFFFF, MSB-first, no final XOR): check value 0x29B1."""
    import binascii
    from omotion.utils import util_crc16

    assert util_crc16(b"123456789") == 0x29B1
    assert binascii.crc_hqx(b"123456789", 0xFFFF) == 0x29B1


def test_unpack_raw10_hand_vector():
    """Proves the unpacker implements the exact pinned bit layout (spec §4.1:
    pixel k of a 4-px group at 40-bit-group bits [10k+9:10k], low byte first).
    Same math the FPGA packer TB anchors with its own hand vector; both were
    verified against the spec formulation independently."""
    from omotion.ImageCapture import unpack_raw10

    assert list(unpack_raw10(_HAND_BYTES)) == _HAND_PIXELS


def test_pack_raw10_hand_vector():
    """Reference packer is the exact inverse (same anchored bytes)."""
    from omotion.ImageCapture import pack_raw10

    assert pack_raw10(_HAND_PIXELS) == _HAND_BYTES


def test_unpack_raw10_rejects_bad_length():
    from omotion.ImageCapture import unpack_raw10

    with pytest.raises(ValueError):
        unpack_raw10(b"\x00" * 7)   # not a multiple of 5


def test_parse_image_line_golden_roundtrip():
    """Full-line proof: reference-packed golden line parses back to the exact
    header fields and all 1920 pixels, and the on-wire bytes match the
    independently computed header/packed-head/CRC anchors."""
    from omotion.ImageCapture import parse_image_line

    raw = _golden_line_bytes()
    assert len(raw) == 2408
    assert raw[:6] == _GOLDEN_HDR
    assert raw[6:16] == _GOLDEN_PACKED_HEAD
    assert raw[2406] == (_GOLDEN_CRC >> 8) and raw[2407] == (_GOLDEN_CRC & 0xFF)

    ln = parse_image_line(raw, cam_id=3)
    assert ln.cam_id == 3
    assert ln.line == 1234
    assert ln.flags == 0
    assert ln.overrun is False
    assert ln.frame_cnt == 0x5C
    assert ln.pixels.dtype == np.uint16
    assert list(ln.pixels) == _golden_pixels()


def test_parse_image_line_bad_crc_rejected():
    """A single flipped payload bit must fail CRC — the per-line integrity
    check that catches USART byte-slip (spec §6 risk table)."""
    from omotion.ImageCapture import ImageLineError, parse_image_line

    raw = bytearray(_golden_line_bytes())
    raw[100] ^= 0x01
    with pytest.raises(ImageLineError, match="CRC"):
        parse_image_line(bytes(raw), cam_id=0)


def test_parse_image_line_bad_magic_and_version():
    from omotion.ImageCapture import ImageLineError, parse_image_line

    good = _golden_line_bytes()
    bad_magic = b"\x00" + good[1:]
    with pytest.raises(ImageLineError, match="magic"):
        parse_image_line(bad_magic, cam_id=0)
    bad_ver = good[:1] + b"\x02" + good[2:]
    with pytest.raises(ImageLineError, match="version"):
        parse_image_line(bad_ver, cam_id=0)
    with pytest.raises(ImageLineError, match="length"):
        parse_image_line(good[:-1], cam_id=0)


def test_parse_image_line_overrun_flag():
    """flags live in the high nibble of byte 3: flags=1, line=1234 -> 0x14."""
    from omotion.ImageCapture import parse_image_line

    raw = _golden_line_bytes(flags=0x1)
    assert raw[3] == 0x14
    ln = parse_image_line(raw, cam_id=0)
    assert ln.overrun is True and ln.flags == 0x1 and ln.line == 1234


# ---------------------------------------------------------------------------
# USB envelope
# ---------------------------------------------------------------------------

def _envelope(line_bytes, cam_id=2):
    """Wrap a 2408-B line in the 2420-B TYPE_IMAGE stream envelope (contract B).
    Transport CRC field is 0x0000 — the SDK does not verify it for image
    packets (MCU forwards blind; the line CRC is authoritative)."""
    total = 6 + 1 + 1 + len(line_bytes) + 1 + 3
    return (bytes([0xAA, 0x03]) + total.to_bytes(4, "little")
            + bytes([0xFF, cam_id]) + line_bytes
            + bytes([0xEE, 0x00, 0x00, 0xDD]))


def test_parse_image_packet_envelope():
    from omotion.ImageCapture import IMAGE_PACKET_SIZE, parse_image_packet

    pkt = _envelope(_golden_line_bytes(), cam_id=5)
    assert len(pkt) == IMAGE_PACKET_SIZE == 2420
    ln = parse_image_packet(pkt)
    assert ln.cam_id == 5 and ln.line == 1234 and ln.frame_cnt == 0x5C


def test_parse_image_packet_bad_framing():
    from omotion.ImageCapture import ImageLineError, parse_image_packet

    pkt = bytearray(_envelope(_golden_line_bytes()))
    pkt[0] = 0x00
    with pytest.raises(ImageLineError):
        parse_image_packet(bytes(pkt))
    pkt = bytearray(_envelope(_golden_line_bytes()))
    pkt[-1] = 0x00   # EOF
    with pytest.raises(ImageLineError):
        parse_image_packet(bytes(pkt))
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_image_capture.py -v`
Expected: the new tests FAIL with `ModuleNotFoundError: No module named 'omotion.ImageCapture'` (the Task 2 test still passes).

- [ ] **Step 3: Create the module**

Create `omotion/ImageCapture.py`:

```python
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
FLAG_OVERRUN = 0x1

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
```

- [ ] **Step 4: Run and verify all pass**

Run: `pytest tests/test_image_capture.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add omotion/ImageCapture.py tests/test_image_capture.py
git commit -m "feat: drip-scan image line parser and vectorized RAW10 unpacker (#<N>)"
```

---

### Task 4: FrameAssembler

**Files:**
- Modify: `omotion/ImageCapture.py` (append)
- Test: `tests/test_image_capture.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_image_capture.py`:

```python
# ---------------------------------------------------------------------------
# FrameAssembler
# ---------------------------------------------------------------------------

def _mk_line(line_no, frame_cnt=0x10, value=None, overrun=False):
    """Cheap ImageLine for assembler tests (bypasses byte packing — the wire
    path is proven by the parser tests above)."""
    from omotion.ImageCapture import IMAGE_WIDTH, ImageLine

    px = np.full(IMAGE_WIDTH, value if value is not None else line_no,
                 dtype=np.uint16)
    flags = 0x1 if overrun else 0x0
    return ImageLine(cam_id=0, line=line_no, flags=flags, overrun=overrun,
                     frame_cnt=frame_cnt, pixels=px)


def test_assembler_complete_frame():
    """All 1280 lines of one exposure -> complete, consistent, right shape,
    rows land at their line index."""
    from omotion.ImageCapture import IMAGE_HEIGHT, IMAGE_WIDTH, FrameAssembler

    asm = FrameAssembler()
    for i in range(IMAGE_HEIGHT):
        assert asm.add(_mk_line(i)) is True
    assert asm.complete is True
    assert asm.missing() == []
    assert asm.mixed_exposure is False
    assert asm.frame_cnt == 0x10
    img = asm.image()
    assert img.shape == (IMAGE_HEIGHT, IMAGE_WIDTH) and img.dtype == np.uint16
    assert img[7, 0] == 7 and img[1279, 100] == 1279


def test_assembler_gap_list_and_incomplete():
    from omotion.ImageCapture import IMAGE_HEIGHT, FrameAssembler

    asm = FrameAssembler()
    for i in range(IMAGE_HEIGHT):
        if i not in (5, 900):
            asm.add(_mk_line(i))
    assert asm.complete is False
    assert asm.missing() == [5, 900]


def test_assembler_rejects_frame_cnt_mismatch_by_default():
    """Single-exposure enforcement: a line from a different exposure is
    rejected and counted, and the image stays attributable to one frame_cnt."""
    from omotion.ImageCapture import FrameAssembler

    asm = FrameAssembler()
    assert asm.add(_mk_line(0, frame_cnt=0x10)) is True
    assert asm.add(_mk_line(1, frame_cnt=0x11)) is False
    assert asm.rejected_lines == 1
    assert asm.frame_cnt == 0x10
    assert asm.mixed_exposure is False
    assert 1 in asm.missing()


def test_assembler_allow_mixed_gap_fill():
    """Retry fallback: with allow_mixed=True a different-exposure line fills
    its gap but the result is flagged mixed_exposure."""
    from omotion.ImageCapture import FrameAssembler

    asm = FrameAssembler()
    asm.add(_mk_line(0, frame_cnt=0x10))
    asm.allow_mixed = True
    assert asm.add(_mk_line(1, frame_cnt=0x11)) is True
    assert asm.mixed_exposure is True
    assert asm.rejected_lines == 0


def test_assembler_rejects_out_of_range_line():
    from omotion.ImageCapture import IMAGE_HEIGHT, FrameAssembler

    asm = FrameAssembler()
    assert asm.add(_mk_line(IMAGE_HEIGHT)) is False   # line 1280 of 0..1279
    assert asm.rejected_lines == 1


def test_assembler_reset_and_overrun_tracking():
    """reset() starts a fresh exposure (used by the strict retry policy);
    overrun on any accepted line is latched for reporting."""
    from omotion.ImageCapture import FrameAssembler

    asm = FrameAssembler()
    asm.add(_mk_line(0, frame_cnt=0x10, overrun=True))
    assert asm.overrun_seen is True
    asm.reset()
    assert asm.overrun_seen is False
    assert asm.frame_cnt is None
    assert asm.add(_mk_line(0, frame_cnt=0x22)) is True
    assert asm.frame_cnt == 0x22
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_image_capture.py -v -k assembler`
Expected: FAIL with `ImportError: cannot import name 'FrameAssembler' from 'omotion.ImageCapture'`

- [ ] **Step 3: Implement FrameAssembler**

Append to `omotion/ImageCapture.py`:

```python
# ---------------------------------------------------------------------------
# Frame assembly
# ---------------------------------------------------------------------------

import threading


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
```

(Move the `import threading` line up to the module's import block with the other stdlib imports — `logging`, `threading`, `time`.)

- [ ] **Step 4: Run and verify all pass**

Run: `pytest tests/test_image_capture.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add omotion/ImageCapture.py tests/test_image_capture.py
git commit -m "feat: drip-scan FrameAssembler with single-exposure frame_cnt enforcement (#<N>)"
```

---

### Task 5: StreamInterface — route TYPE_IMAGE packets to a dedicated queue

**Files:**
- Modify: `omotion/StreamInterface.py` (imports line 8; `__init__` 114-121; `start_streaming` 123-150; `stop_streaming` 152-174; `_stream_loop` 362-426; new module-level function)
- Test: `tests/test_stream_image_routing.py` (new file)

Today `_stream_loop` puts raw USB chunks straight into `data_queue` and downstream parsers do the framing (`_process_packet` at lines 327-360 shows the type-dispatch idiom but has no callers). Routing must therefore happen in `_stream_loop`, and only when an image queue is attached — the histogram hot path stays byte-identical when it isn't.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stream_image_routing.py`:

```python
"""StreamInterface TYPE_IMAGE routing — software-only (no USB device).

StreamInterface.__init__ (via USBInterfaceBase.__init__) only stores its
arguments, so a StreamInterface(None, 1, "test") is safe to construct and
lets us drive _route_chunk directly.
"""

import queue

import pytest

pytestmark = pytest.mark.unit

from omotion.StreamInterface import StreamInterface, extract_stream_packets


def _histo_pkt(payload=b"\x01\x02\x03\x04"):
    """Minimal TYPE_HISTO envelope: routing checks framing only, not CRC."""
    total = 6 + len(payload) + 3
    return (bytes([0xAA, 0x00]) + total.to_bytes(4, "little")
            + payload + bytes([0x00, 0x00, 0xDD]))


def _image_pkt(fill=0x55):
    """Minimal well-framed TYPE_IMAGE envelope (2420 B). Routing does not
    parse the payload, so a constant fill body is sufficient here."""
    body = bytes([fill]) * (1 + 1 + 2408 + 1)   # SOH..EOH region, framing only
    total = 6 + len(body) + 3
    pkt = bytearray(bytes([0xAA, 0x03]) + total.to_bytes(4, "little")
                    + body + bytes([0x00, 0x00, 0xDD]))
    pkt[6] = 0xFF                                # SOH
    pkt[len(pkt) - 4] = 0xEE                     # EOH
    return bytes(pkt)


def test_extract_splits_and_classifies():
    """One histo + one image packet concatenated -> each lands in its list
    and the buffer is fully consumed."""
    buf = bytearray(_histo_pkt() + _image_pkt())
    histo, image = extract_stream_packets(buf)
    assert [p[1] for p in histo] == [0x00]
    assert [p[1] for p in image] == [0x03]
    assert len(buf) == 0


def test_extract_holds_partial_packet():
    """A packet split across USB chunks stays buffered until complete."""
    ip = _image_pkt()
    buf = bytearray(ip[:1000])
    histo, image = extract_stream_packets(buf)
    assert histo == [] and image == []
    assert len(buf) == 1000
    buf += ip[1000:]
    histo, image = extract_stream_packets(buf)
    assert len(image) == 1 and image[0] == ip and len(buf) == 0


def test_extract_resyncs_past_garbage():
    """Garbage before SOF and a corrupt EOF are skipped without losing the
    following good packet."""
    good = _histo_pkt()
    bad = bytearray(_histo_pkt())
    bad[-1] = 0x00   # break EOF -> forces the 1-byte resync path
    buf = bytearray(b"\x00\x12" + bytes(bad) + good)
    histo, image = extract_stream_packets(buf)
    assert good in histo


def test_route_chunk_separates_queues():
    """End-to-end through the instance method: image packets only ever appear
    in image_queue, histogram packets only in data_queue — the guarantee that
    histogram consumers never see image traffic."""
    st = StreamInterface(None, 1, desc="test")
    hq: queue.Queue = queue.Queue()
    iq: queue.Queue = queue.Queue()
    st.data_queue = hq
    st.image_queue = iq
    st._route_buf = bytearray()

    ip, hp = _image_pkt(), _histo_pkt()
    blob = hp + ip + hp
    # feed in awkward chunk sizes to cross packet boundaries
    for i in range(0, len(blob), 700):
        st._route_chunk(blob[i:i + 700], hq)

    got_h = [hq.get_nowait() for _ in range(hq.qsize())]
    got_i = [iq.get_nowait() for _ in range(iq.qsize())]
    assert got_h == [hp, hp]
    assert got_i == [ip]
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_stream_image_routing.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_stream_packets' from 'omotion.StreamInterface'`

- [ ] **Step 3: Implement routing**

In `omotion/StreamInterface.py`:

**(a)** Change line 8 to import the new type byte:

```python
from omotion.config import TYPE_HISTO, TYPE_HISTO_CMP, TYPE_IMAGE
```

**(b)** After the `_util_crc16` definition (below line 49), add the module-level extractor:

```python
# Largest legal stream envelope = USB_HISTO_MAX_SIZE in sensor-fw usbd_histo.h
# (8-camera histogram packet). Image packets are 2420 B; both fit under this.
_MAX_STREAM_PACKET = 32837


def extract_stream_packets(buf: bytearray) -> tuple[list[bytes], list[bytes]]:
    """Split ``buf`` (consumed in place) into complete stream envelopes.

    An envelope is [SOF 0xAA][type][u32 LE total_len][...][CRC16][EOF 0xDD].
    Complete envelopes are removed from ``buf``; a trailing partial packet is
    left in place for the next chunk. Malformed candidates (implausible
    length, missing EOF) resync by advancing one byte — same recovery policy
    as parse_histogram_packet_structured's callers.

    Returns ``(other_packets, image_packets)`` where image packets are those
    with type byte TYPE_IMAGE (0x03); everything else (TYPE_HISTO,
    TYPE_HISTO_CMP, unknown) goes in the first list untouched.
    """
    other: list[bytes] = []
    image: list[bytes] = []
    while True:
        sof = buf.find(b"\xaa")
        if sof < 0:
            buf.clear()
            break
        if sof:
            del buf[:sof]
        if len(buf) < _HEADER_SIZE:
            break
        pkt_len = int.from_bytes(buf[2:6], "little")
        if not (_HEADER_SIZE + _FOOTER_SIZE <= pkt_len <= _MAX_STREAM_PACKET):
            del buf[:1]
            continue
        if len(buf) < pkt_len:
            break
        if buf[pkt_len - 1] != 0xDD:
            del buf[:1]
            continue
        pkt = bytes(buf[:pkt_len])
        del buf[:pkt_len]
        (image if pkt[1] == TYPE_IMAGE else other).append(pkt)
    return other, image
```

**(c)** In `__init__` (lines 114-121), add two attributes after `self.packets_received`:

```python
        # Optional second queue for drip-scan image packets (camera-fpga#8).
        # None (the default) keeps the historical raw-chunk fast path; when
        # set via start_streaming(image_queue=...), the reader thread frames
        # packets and routes TYPE_IMAGE to it so histogram consumers never
        # see image traffic.
        self.image_queue = None
        self._route_buf = bytearray()
```

**(d)** Change the `start_streaming` signature (line 123) and wire the queue in before the thread starts (after `self.packets_received = 0`, line 145):

```python
    def start_streaming(self, queue_obj, expected_size, image_queue=None):
```

```python
        self.image_queue = image_queue
        self._route_buf = bytearray()
```

**(e)** In `stop_streaming` (lines 152-174), after `self.expected_size = None` add:

```python
        self.image_queue = None
        self._route_buf = bytearray()
```

**(f)** Add the routing method after `_process_packet` (below line 360):

```python
    def _route_chunk(self, chunk: bytes, data_queue) -> None:
        """Framed routing used while an image_queue is attached.

        Accumulates chunks, extracts complete envelopes, and delivers
        TYPE_IMAGE packets to image_queue and everything else to data_queue.
        Bounded puts mirror _stream_loop's histogram policy: never block the
        reader thread for more than 1 s per packet.
        """
        self._route_buf += chunk
        other_pkts, image_pkts = extract_stream_packets(self._route_buf)
        image_queue = self.image_queue
        for pkt in image_pkts:
            if image_queue is None:
                break
            try:
                image_queue.put(pkt, timeout=1.0)
            except queue.Full:
                if self.stop_event.is_set():
                    return
                logger.warning(
                    "%s: image_queue full for >1s; dropping %d-byte image "
                    "packet (host retry will re-request the line)",
                    self.desc, len(pkt),
                )
        for pkt in other_pkts:
            try:
                data_queue.put(pkt, timeout=1.0)
            except queue.Full:
                if self.stop_event.is_set():
                    return
                logger.warning(
                    "%s: data_queue full for >1s during image session; "
                    "dropping %d-byte packet", self.desc, len(pkt),
                )
```

**(g)** In `_stream_loop`, replace the delivery block (lines 394-410, the `if data and data_queue is self.data_queue:` body) with:

```python
                if data and data_queue is self.data_queue:
                    if self.image_queue is not None:
                        # Drip-scan image session: framed routing so histogram
                        # consumers never see TYPE_IMAGE packets.
                        self._route_chunk(bytes(data), data_queue)
                        self.packets_received += 1
                    else:
                        # Use a bounded put so the loop can never block forever
                        # on a stopped/slow parser. With self.stop_event set the
                        # parser also drains until empty (see parse_histogram_stream),
                        # so this drop window is only ever 1s of backlog at scan
                        # teardown — small price for a guaranteed loop exit.
                        try:
                            data_queue.put(bytes(data), timeout=1.0)
                            self.packets_received += 1
                        except queue.Full:
                            if self.stop_event.is_set():
                                break
                            logger.warning(
                                "%s: data_queue full for >1s during streaming "
                                "(parser falling behind?); dropping %d-byte chunk",
                                self.desc, len(data),
                            )
```

- [ ] **Step 4: Run and verify all pass**

Run: `pytest tests/test_stream_image_routing.py tests/test_image_capture.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Commit**

```bash
git add omotion/StreamInterface.py tests/test_stream_image_routing.py
git commit -m "feat: route TYPE_IMAGE stream packets to a dedicated image queue (#<N>)"
```

---

### Task 6: MotionSensor image-mode command + FPGA v2 register access

**Files:**
- Modify: `omotion/MotionSensor.py` (config import block lines 13-76; new method after `disable_camera`, line 1688)
- Modify: `omotion/ImageCapture.py` (append)
- Test: `tests/test_image_capture.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_image_capture.py`:

```python
# ---------------------------------------------------------------------------
# OW_CAMERA_IMAGE_MODE sender + FPGA sweep registers
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, packetType):
        self.packetType = packetType


class _FakeComm:
    def __init__(self, resp_type):
        self.calls = []
        self._resp_type = resp_type

    def send_packet(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp(self._resp_type)


def _bare_sensor(resp_type):
    """MotionSensor without running __init__ (it wires USB/hotplug state we
    don't need): _send only touches self.uart.comm.send_packet, demo_mode,
    and _check_camera_mask — set exactly those."""
    from types import SimpleNamespace
    from omotion.MotionSensor import MotionSensor

    ms = MotionSensor.__new__(MotionSensor)
    ms.demo_mode = False
    ms.uart = SimpleNamespace(comm=_FakeComm(resp_type))
    return ms


def test_set_camera_image_mode_wire_format():
    """Pinned opcode contract: OW_CAMERA packet, command 0x30, reserved byte
    carries enable, data[0] carries the camera bitmask."""
    from omotion.config import OW_CAMERA, OW_CAMERA_IMAGE_MODE, OW_RESP

    ms = _bare_sensor(OW_RESP)
    assert ms.set_camera_image_mode(True, 0x42) is True
    call = ms.uart.comm.calls[-1]
    assert call["packetType"] == OW_CAMERA
    assert call["command"] == OW_CAMERA_IMAGE_MODE == 0x30
    assert call["reserved"] == 1
    assert call["data"] == bytes([0x42])

    assert ms.set_camera_image_mode(False, 0x01) is True
    assert ms.uart.comm.calls[-1]["reserved"] == 0


def test_set_camera_image_mode_error_response():
    from omotion.config import OW_ERROR

    ms = _bare_sensor(OW_ERROR)
    assert ms.set_camera_image_mode(True, 0xFF) is False


def test_set_camera_image_mode_rejects_bad_mask():
    from omotion.config import OW_RESP

    ms = _bare_sensor(OW_RESP)
    with pytest.raises(ValueError):
        ms.set_camera_image_mode(True, 0x1FF)


class _FakeRegSensor:
    """Records FpgaRegs traffic through the i2c_read_register passthrough."""

    def __init__(self):
        self.ops = []
        self.regs = {0x00: 0x5A, 0x01: 0x02, 0x09: 0x00}   # ID, VERSION v2, STATUS

    def i2c_read_register(self, dev_addr, reg_addr, read_len=1,
                          reg_addr_size=1, mux_channel=None):
        assert dev_addr == 0x5A
        if reg_addr_size == 1:            # read
            self.ops.append(("rd", mux_channel, reg_addr))
            return bytes([self.regs.get(reg_addr, 0x00)])
        reg, val = (reg_addr >> 8) & 0xFF, reg_addr & 0xFF   # write trick
        self.ops.append(("wr", mux_channel, reg, val))
        self.regs[reg] = val
        return b"\x00"


def test_fpga_regs_v2_sweep_arm():
    """arm_sweep programs the start line then CTRL = image|sweep (0x03);
    stop_sweep drops back to image-only; exit clears CTRL. LINE_L/H are the
    sweep start line in map v2."""
    from omotion.ImageCapture import CTRL_IMAGE_MODE, CTRL_SWEEP, FpgaRegs

    s = _FakeRegSensor()
    r = FpgaRegs(s, cam=3)
    assert r.check_id() is True
    assert r.check_version() is True

    r.arm_sweep(start_line=0x2A5)
    assert ("wr", 3, 0x04, 0xA5) in s.ops          # LINE_L
    assert ("wr", 3, 0x05, 0x02) in s.ops          # LINE_H (line[11:8])
    assert s.ops[-1] == ("wr", 3, 0x03, CTRL_IMAGE_MODE | CTRL_SWEEP)

    r.stop_sweep()
    assert s.ops[-1] == ("wr", 3, 0x03, CTRL_IMAGE_MODE)
    r.exit_image_mode()
    assert s.ops[-1] == ("wr", 3, 0x03, 0x00)


def test_fpga_regs_overrun_latch():
    from omotion.ImageCapture import STATUS_OVERRUN, FpgaRegs

    s = _FakeRegSensor()
    r = FpgaRegs(s, cam=0)
    assert r.overrun() is False
    s.regs[0x09] = STATUS_OVERRUN
    assert r.overrun() is True
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_image_capture.py -v -k "image_mode or fpga_regs"`
Expected: FAIL — first with `AttributeError: 'MotionSensor' object has no attribute 'set_camera_image_mode'`, then `ImportError: cannot import name 'FpgaRegs'`.

- [ ] **Step 3: Add the MotionSensor method**

In `omotion/MotionSensor.py`, add `OW_CAMERA_IMAGE_MODE` to the `from omotion.config import (...)` block (lines 13-76, alphabetical placement near `OW_CAMERA_GET_TELEMETRY`). Then insert after `disable_camera` (line 1688):

```python
    def set_camera_image_mode(self, enable: bool, camera_mask: int) -> bool:
        """Enter or exit drip-scan image receive mode (camera-fpga#8).

        Wire contract (OW_CAMERA_IMAGE_MODE = 0x30): the ``reserved`` byte
        carries enable (0/1) and ``data[0]`` carries the camera bitmask.

        While enabled the firmware suspends histogram streaming (exclusive
        modes) and arms a fixed 2408-B line DMA per enabled camera. The FIRST
        histogram frame after exiting image mode contains counts accumulated
        across the whole image session and must be discarded by any consumer
        (spec §4.4 — the capture orchestrator in ImageCapture handles this).
        """
        self._check_camera_mask(camera_mask)
        if self.demo_mode:
            return True
        r = self._send(
            packetType=OW_CAMERA,
            command=OW_CAMERA_IMAGE_MODE,
            reserved=1 if enable else 0,
            data=bytes([camera_mask]),
            timeout=1.5,
        )
        return r.packetType not in _ERROR_TYPES
```

- [ ] **Step 4: Add FpgaRegs to ImageCapture**

Append to `omotion/ImageCapture.py`:

```python
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
```

- [ ] **Step 5: Run and verify all pass**

Run: `pytest tests/test_image_capture.py -v`
Expected: PASS (23 tests)

- [ ] **Step 6: Commit**

```bash
git add omotion/MotionSensor.py omotion/ImageCapture.py tests/test_image_capture.py
git commit -m "feat: OW_CAMERA_IMAGE_MODE sender and FPGA map-v2 sweep registers (#<N>)"
```

---

### Task 7: Group-hold sweep retiming writer

**Files:**
- Modify: `omotion/ImageCapture.py` (append)
- Test: `tests/test_image_capture.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_image_capture.py`:

```python
# ---------------------------------------------------------------------------
# Group-hold sweep retiming
# ---------------------------------------------------------------------------

class _FakeI2CSensor:
    """Records switch_camera / camera_i2c_write traffic for one camera."""

    def __init__(self):
        self.ops = []

    def switch_camera(self, cam):
        self.ops.append(("switch", cam))

    def camera_i2c_write(self, packet):
        self.ops.append(("wr", packet.device_address,
                         packet.register_address, packet.data))
        return True


def test_write_timing_profile_group_hold_sequence():
    """The whole profile must land inside ONE group-hold: 0x3208=0x00 opens
    group 0, the timing registers follow in pinned order, 0x3208=0x10 closes
    the group, and 0x3208=0xA0 (delayed launch) latches everything atomically
    at the next frame boundary — the atomicity spec §4.2 requires because
    tc_r_initial is VTS-coupled."""
    from omotion.ImageCapture import write_timing_profile
    from omotion.config import SWEEP_TIMING_PROFILE

    s = _FakeI2CSensor()
    assert write_timing_profile(s, cam=6, profile=SWEEP_TIMING_PROFILE) is True

    assert s.ops[0] == ("switch", 6)
    writes = [(op[2], op[3]) for op in s.ops[1:]]
    assert all(op[1] == 0x36 for op in s.ops[1:])   # OX02C1B device address
    assert writes == [
        (0x3208, 0x00),
        (0x380C, 0x96), (0x380D, 0x00),
        (0x380E, 0x05), (0x380F, 0x20),
        (0x3826, 0x05), (0x3827, 0x1C),
        (0x3501, 0x00), (0x3502, 0x01),
        (0x3208, 0x10),
        (0x3208, 0xA0),
    ]


def test_write_timing_profile_reports_failure():
    from omotion.ImageCapture import write_timing_profile
    from omotion.config import PRODUCTION_TIMING_PROFILE

    s = _FakeI2CSensor()
    s.camera_i2c_write = lambda packet: False
    assert write_timing_profile(s, cam=0, profile=PRODUCTION_TIMING_PROFILE) is False
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_image_capture.py -v -k timing_profile`
Expected: FAIL with `ImportError: cannot import name 'write_timing_profile' from 'omotion.ImageCapture'`

- [ ] **Step 3: Implement the writer**

Append to `omotion/ImageCapture.py`:

```python
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
```

- [ ] **Step 4: Run and verify all pass**

Run: `pytest tests/test_image_capture.py -v`
Expected: PASS (25 tests)

- [ ] **Step 5: Commit**

```bash
git add omotion/ImageCapture.py tests/test_image_capture.py
git commit -m "feat: atomic group-hold sweep/production timing writer (#<N>)"
```

---

### Task 8: Capture orchestrator

**Files:**
- Modify: `omotion/ImageCapture.py` (append)
- Test: `tests/test_image_capture.py` (append — retry-policy unit tests; the full orchestrator is HIL-verified per the spec's acceptance section)

- [ ] **Step 1: Write the failing tests for the retry policy**

Append to `tests/test_image_capture.py`:

```python
# ---------------------------------------------------------------------------
# Sweep retry policy (pure decision function driving the orchestrator loop)
# ---------------------------------------------------------------------------

def test_next_sweep_action_policy():
    """Strict attempts restart from line 0 on a fresh exposure (preserving the
    single-exposure guarantee); the last mixed_fill_sweeps attempts gap-fill
    from the first missing line; done/give_up terminate."""
    from omotion.ImageCapture import next_sweep_action

    # complete -> done regardless of attempt
    assert next_sweep_action([], attempt=1, max_sweeps=6, mixed_fill_sweeps=2) == ("done", None)
    # attempts 1..4 of 6 (2 reserved for fill): strict restart
    for a in (1, 2, 3, 4):
        assert next_sweep_action([9, 40], a, 6, 2) == ("restart", 0)
    # attempts 5..6: mixed gap-fill from first missing line
    assert next_sweep_action([9, 40], 5, 6, 2) == ("fill", 9)
    assert next_sweep_action([40], 6, 6, 2) == ("fill", 40)
    # beyond budget
    assert next_sweep_action([40], 7, 6, 2) == ("give_up", None)


def test_next_sweep_action_no_fill_budget():
    from omotion.ImageCapture import next_sweep_action

    assert next_sweep_action([3], 2, 2, 0) == ("restart", 0)
    assert next_sweep_action([3], 3, 2, 0) == ("give_up", None)
```

- [ ] **Step 2: Run and verify they fail**

Run: `pytest tests/test_image_capture.py -v -k next_sweep`
Expected: FAIL with `ImportError: cannot import name 'next_sweep_action' from 'omotion.ImageCapture'`

- [ ] **Step 3: Implement the policy function and orchestrator**

Append to `omotion/ImageCapture.py`:

```python
# ---------------------------------------------------------------------------
# Capture orchestration (graduates camera-fpga tools/full_frame_capture/)
# ---------------------------------------------------------------------------

import json
import queue as _queue
from pathlib import Path

from omotion.config import (
    PRODUCTION_TIMING_PROFILE,
    SWEEP_FSIN_HZ,
    SWEEP_TIMING_PROFILE,
)

# USB read size for the stream loop during an image session: the HISTO
# endpoint's max transfer (USB_HISTO_MAX_SIZE in sensor-fw usbd_histo.h).
# Image packets are 2420 B each; a single read may deliver one or several.
_STREAM_READ_SIZE = 32837

_SWEEP_PERIOD_S = 1.0 / SWEEP_FSIN_HZ   # 1.25 s per exposure at 0.8 Hz


def next_sweep_action(missing, attempt, max_sweeps, mixed_fill_sweeps):
    """Decide what the next sweep attempt should do for one camera.

    Returns (action, start_line):
      ("done", None)      — frame complete, stop.
      ("restart", 0)      — strict single-exposure retry: discard partial
                            assembly and re-sweep the whole frame.
      ("fill", first_gap) — mixed-exposure fallback for the last
                            ``mixed_fill_sweeps`` attempts: keep what we have,
                            re-sweep from the first missing line only.
      ("give_up", None)   — attempt budget exhausted.
    """
    if not missing:
        return ("done", None)
    if attempt > max_sweeps:
        return ("give_up", None)
    if attempt > max_sweeps - mixed_fill_sweeps:
        return ("fill", missing[0])
    return ("restart", 0)


@dataclass
class CameraCaptureResult:
    cam_id: int
    image: np.ndarray | None
    complete: bool
    mixed_exposure: bool
    missing_lines: list[int] = field(default_factory=list)
    frame_cnt: int | None = None
    overrun: bool = False
    rejected_lines: int = 0
    attempts: int = 0


def _collector_loop(image_queue, assemblers, stop_evt):
    """Drain the image queue into per-camera assemblers until stopped AND
    empty. Lines that fail CRC/framing are dropped and logged — the sweep
    retry policy re-requests whatever ends up missing."""
    while not stop_evt.is_set() or not image_queue.empty():
        try:
            pkt = image_queue.get(timeout=0.2)
        except _queue.Empty:
            continue
        try:
            line = parse_image_line_packet = parse_image_packet(pkt)
        except ImageLineError as exc:
            logger.warning("dropping bad image packet: %s", exc)
            continue
        asm = assemblers.get(line.cam_id)
        if asm is not None:
            asm.add(line)


def capture_full_frames(
    sensor,
    console,
    cams,
    out_dir=None,
    side: str = "left",
    max_sweeps: int = 6,
    mixed_fill_sweeps: int = 2,
    settle_timeout_s: float = 3.0,
) -> dict[int, CameraCaptureResult]:
    """Capture one full-frame single-exposure image from each camera in
    ``cams`` on one sensor module.

    Sequence (spec §4.5): enable histogram streaming (brings up the MIPI
    clock the FPGA control plane needs) -> enter image mode
    (OW_CAMERA_IMAGE_MODE; firmware suspends histograms and arms 2408-B line
    DMA) -> group-hold retime to the sweep profile -> slow FSIN to 0.8 Hz via
    the console trigger config (MotionConsole.set_trigger_json,
    TriggerFrequencyHz — the SDK's only FSIN-rate setter) -> collect with
    per-frame retry via the FPGA sweep start-line register -> restore timing
    and FSIN -> exit image mode -> discard the first (garbage) histogram
    frame accumulated across the session.

    ``console`` is required: SyncOut drives the sensors' FSIN on this
    hardware, and laser per-pulse parameters ride the existing trigger config
    untouched (only the repetition rate changes).

    Outputs (when ``out_dir`` is given): ``{side}_cam{c}.npy`` (uint16
    1280x1920) and, if PIL is importable (it ships transitively with the
    declared matplotlib dependency), a 16-bit ``{side}_cam{c}.png``; plus
    ``meta.json`` with per-camera capture status.
    """
    mask = 0
    for c in cams:
        mask |= 1 << c

    histo_if = sensor.uart.histo
    image_q: _queue.Queue = _queue.Queue()
    discard_q: _queue.Queue = _queue.Queue()   # stray histogram packets, dropped
    assemblers = {c: FrameAssembler() for c in cams}
    results: dict[int, CameraCaptureResult] = {}
    stop_evt = threading.Event()

    saved_trigger = console.get_trigger_json()
    if isinstance(saved_trigger, str):
        saved_trigger = json.loads(saved_trigger)

    histo_if.flush_stale_data(expected_size=_STREAM_READ_SIZE)
    histo_if.start_streaming(discard_q, _STREAM_READ_SIZE, image_queue=image_q)
    collector = threading.Thread(
        target=_collector_loop, args=(image_q, assemblers, stop_evt),
        daemon=True)
    collector.start()

    trigger_started = False
    image_mode_on = False
    try:
        if not sensor.enable_camera(mask):
            raise RuntimeError(f"{side}: enable_camera(0x{mask:02X}) failed")
        time.sleep(1.0)   # MIPI clock + FPGA control plane come up with streaming

        # Drop cameras whose FPGA is absent or not drip-scan capable.
        regs = {}
        for c in list(cams):
            r = FpgaRegs(sensor, c)
            if not r.check_id():
                logger.warning("[%s] cam%d: FPGA control plane not answering "
                               "— skipped", side, c)
                del assemblers[c]
                continue
            if not r.check_version():
                logger.warning("[%s] cam%d: FPGA register map < v2 (no "
                               "drip-scan) — skipped", side, c)
                del assemblers[c]
                continue
            regs[c] = r
        active = sorted(regs)
        if not active:
            raise RuntimeError(f"{side}: no drip-scan-capable cameras")

        if not sensor.set_camera_image_mode(True, mask):
            raise RuntimeError(f"{side}: OW_CAMERA_IMAGE_MODE enable failed")
        image_mode_on = True

        for c in active:
            if not write_timing_profile(sensor, c, SWEEP_TIMING_PROFILE):
                raise RuntimeError(f"{side}: cam{c} sweep retime failed")

        slow = dict(saved_trigger)
        slow["TriggerFrequencyHz"] = SWEEP_FSIN_HZ
        if not console.set_trigger_json(data=slow):
            raise RuntimeError("set_trigger_json (sweep rate) failed")
        if not sensor.enable_camera_fsin_ext():
            raise RuntimeError(f"{side}: enable_camera_fsin_ext failed")
        if not console.start_trigger():
            raise RuntimeError("start_trigger failed")
        trigger_started = True
        # One frame at the old timing may still be in flight; the group-hold
        # launches at its boundary. From here every FSIN is a sweep exposure.

        attempts = {c: 0 for c in active}
        pending = set(active)
        while pending:
            for c in sorted(pending):
                attempts[c] += 1
                action, start_line = next_sweep_action(
                    assemblers[c].missing(), attempts[c],
                    max_sweeps, mixed_fill_sweeps)
                if action == "restart":
                    assemblers[c].reset()
                    regs[c].arm_sweep(0)
                elif action == "fill":
                    assemblers[c].allow_mixed = True
                    regs[c].arm_sweep(start_line)
                elif action == "give_up":
                    logger.error("[%s] cam%d: incomplete after %d sweeps "
                                 "(%d lines missing)", side, c, max_sweeps,
                                 len(assemblers[c].missing()))
                    regs[c].stop_sweep()
                    pending.discard(c)
            if not pending:
                break
            # One exposure + full drain per attempt, with settle margin.
            deadline = time.monotonic() + 2 * _SWEEP_PERIOD_S + settle_timeout_s
            while time.monotonic() < deadline:
                time.sleep(0.25)
                if all(assemblers[c].complete for c in pending):
                    break
            for c in [c for c in pending if assemblers[c].complete]:
                regs[c].stop_sweep()
                pending.discard(c)

        for c in active:
            asm = assemblers[c]
            results[c] = CameraCaptureResult(
                cam_id=c,
                image=asm.image(),
                complete=asm.complete,
                mixed_exposure=asm.mixed_exposure,
                missing_lines=asm.missing(),
                frame_cnt=asm.frame_cnt,
                overrun=asm.overrun_seen,
                rejected_lines=asm.rejected_lines,
                attempts=attempts[c],
            )
    finally:
        # --- Restore, tolerating partial bring-up ------------------------
        try:
            for c in sorted(assemblers):
                write_timing_profile(sensor, c, PRODUCTION_TIMING_PROFILE)
            # Group launch happens at the next frame boundary — at the sweep
            # rate that is up to one 1.25 s period away.
            time.sleep(_SWEEP_PERIOD_S + 0.25)
            for c in sorted(assemblers):
                try:
                    FpgaRegs(sensor, c).exit_image_mode()
                except IOError:
                    pass
        except Exception:
            logger.exception("%s: timing/FPGA restore failed", side)
        if trigger_started:
            console.stop_trigger()
        try:
            console.set_trigger_json(data=saved_trigger)
        except Exception:
            logger.exception("restoring trigger config failed")
        if image_mode_on:
            sensor.set_camera_image_mode(False, mask)
        sensor.disable_camera_fsin_ext()
        sensor.disable_camera(mask)
        stop_evt.set()
        histo_if.stop_streaming()
        # The first histogram frame after an image session carries counts
        # accumulated across the whole session (spec §4.4) — drain and
        # discard anything already in flight so the next scan starts clean.
        try:
            discarded = histo_if.drain_final(expected_size=_STREAM_READ_SIZE)
            if discarded:
                logger.info("%s: discarded %d post-image-session chunk(s) "
                            "(first histogram frame after image mode is "
                            "garbage)", side, len(discarded))
        except Exception:
            pass
        collector.join(timeout=3.0)

    if out_dir is not None:
        _save_outputs(results, Path(out_dir), side)
    return results


def _save_outputs(results, out_dir: Path, side: str) -> None:
    """Write {side}_cam{c}.npy (+16-bit PNG when PIL is available) and merge
    per-camera status into meta.json — same shape as the retired
    tools/full_frame_capture/capture.py outputs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image          # transitively present via matplotlib
    except ImportError:                # pragma: no cover - env-dependent
        Image = None
        logger.warning("PIL not importable — writing .npy only (PNG skipped)")

    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update({
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "width": IMAGE_WIDTH, "height": IMAGE_HEIGHT, "bit_depth": 10,
        "scaling": "none — raw 10-bit sensor values 0..1023 in 16-bit files",
    })
    meta.setdefault("cameras", {})
    for c, res in sorted(results.items()):
        key = f"{side}_cam{c}"
        np.save(out_dir / f"{key}.npy", res.image)
        if Image is not None:
            Image.fromarray(res.image).save(out_dir / f"{key}.png")
        meta["cameras"][key] = {
            "complete": res.complete,
            "single_exposure": res.complete and not res.mixed_exposure,
            "mixed_exposure": res.mixed_exposure,
            "frame_cnt": res.frame_cnt,
            "missing_lines": res.missing_lines,
            "overrun": res.overrun,
            "rejected_lines": res.rejected_lines,
            "sweep_attempts": res.attempts,
        }
    meta_path.write_text(json.dumps(meta, indent=2))
```

Then fix the stray assignment in `_collector_loop` — the line must read exactly:

```python
            line = parse_image_packet(pkt)
```

(Also move the `import json`, `import queue as _queue`, `from pathlib import Path`, and the `from omotion.config import (...)` additions up into the module's existing import block rather than mid-file; Python allows mid-file imports but the repo keeps imports at top.)

- [ ] **Step 4: Run and verify all pass**

Run: `pytest tests/test_image_capture.py tests/test_stream_image_routing.py -v`
Expected: PASS (31 tests)

- [ ] **Step 5: Commit**

```bash
git add omotion/ImageCapture.py tests/test_image_capture.py
git commit -m "feat: drip-scan capture orchestrator with single-exposure sweep retry (#<N>)"
```

---

### Task 9: Docs, full software test run, PR, board update

**Files:**
- Modify: `docs/TestSuite.md` (add the two new test files to the layer overview)

- [ ] **Step 1: Update docs/TestSuite.md**

Open `docs/TestSuite.md`, find the section covering the transport/parsing layer (the file groups all tests by SDK layer), and add rows following the existing table/entry format for:

- `tests/test_image_capture.py` — drip-scan line parse/CRC, RAW10 bit layout (golden vectors), FrameAssembler single-exposure enforcement, sweep retiming group-hold sequence, retry policy. Software-only (`unit`).
- `tests/test_stream_image_routing.py` — StreamInterface TYPE_IMAGE framing/routing. Software-only (`unit`).

Match the surrounding entries' exact column/format conventions — do not restructure the document.

- [ ] **Step 2: Run the full software-only suite**

```bash
pytest tests/test_image_capture.py tests/test_stream_image_routing.py tests/test_pipeline/ tests/test_motion_processing_shim.py -v
```

Expected: all PASS, zero failures (the pipeline suite proves the StreamInterface change didn't disturb histogram consumers; the shim test pins the `_util_crc16` alias).

- [ ] **Step 3: Commit docs**

```bash
git add docs/TestSuite.md
git commit -m "docs: register drip-scan unit tests in TestSuite.md (#<N>)"
```

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feature/<N>-drip-scan-capture
gh pr create -R OpenwaterHealth/openmotion-sdk --base next \
  --title "feat: drip-scan full-frame single-exposure image capture" \
  --body "SDK side of the drip-scan feature (design spec 2026-07-19, camera-fpga#8).

- config.py: TYPE_IMAGE stream byte, OW_CAMERA_IMAGE_MODE=0x30, pinned sweep/production timing profiles
- StreamInterface: opt-in image queue; TYPE_IMAGE packets never reach histogram consumers; histogram hot path unchanged when no image queue is attached
- omotion/ImageCapture.py: CRC-verified line parser (util_crc16, byte-identical to sensor-fw utils.c, check value 0x29B1), vectorized RAW10 unpacker (pinned bit layout, hand-vector tested), FrameAssembler with frame_cnt single-exposure enforcement, FPGA map-v2 sweep registers, atomic group-hold retiming, capture orchestrator (graduates camera-fpga tools/full_frame_capture/)
- 31 new software-only unit tests with independently computed golden vectors (golden line CRC 0xA25E)

Depends on the sensor-fw (OW_CAMERA_IMAGE_MODE + line forwarding) and camera-fpga (register map v2) companions — HIL acceptance runs once all three land on their next branches.

Refs #<N>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Move the board card to In review and comment**

```bash
gh project item-edit --id <ITEM_ID> --project-id PVT_kwDOAif52c4BVgTu --field-id PVTSSF_lADOAif52c4BVgTuzhQ7qcU --single-select-option-id 5ef0dc97
gh issue comment <N> -R OpenwaterHealth/openmotion-sdk --body "PR up: <PR URL>. All 31 software unit tests green locally. HIL validation (bit-exact test-pattern sweep, laser-synced single-exposure frame, histogram-suite regression after a sweep session) is blocked on the sensor-fw and camera-fpga companion branches — per the matched-set policy this ticket stays In review until the set validates on the rig."
```

---

## Verification checklist for the reviewer (not steps — context)

- **Spec §4.5 coverage:** StreamInterface routing (Task 5), image reassembler with CRC/gap/frame_cnt (Tasks 3–4), capture orchestrator with retime→slow FSIN→collect/retry→restore→discard (Task 8). Spec §4.2 sensor retiming (Task 7, pinned values in Task 2).
- **Nothing invented beyond spec:** no viewfinder, no link-speed changes (spec §4.6 is FPGA/fw work; HTS stays the pinned 38400 profile), no per-camera timing variants.
- **Histogram regression safety:** with `image_queue=None` (every existing caller), `_stream_loop` is behavior-identical; `tests/test_pipeline/` passing confirms parser-side neutrality.
- **HIL items deliberately deferred to the rig** (spec §5): bit-exact test-pattern reconstruction, `frame_cnt` constancy on live speckle, ≤1.2 s single-camera capture, all-8 USB aggregate, histogram-suite pass after a sweep session.