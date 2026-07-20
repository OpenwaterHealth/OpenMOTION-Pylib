# Drip-scan: full-frame single-exposure image readout — design

**Issue:** [openmotion-camera-fpga#8](https://github.com/OpenwaterHealth/openmotion-camera-fpga/issues/8) ·
**Builds on:** `feature/5-i2c-line-readout` (issue #5, PR #7) ·
**Date:** 2026-07-19

## 1. Goal

Capture a complete 1920×1280 RAW10 image from **one global-shutter exposure** of the
OX02C1B, laser-synced with production pulse parameters, and deliver it to the host in
~1 s. The existing line-readout feature composites an image from 1280 *different*
frames over ~32 s; this feature makes every line come from the *same* exposure.

Primary use is speckle/focus inspection, so per-pixel fidelity is mandatory:
no lossy processing anywhere, full 10-bit depth end to end.

Out of scope (phase 2, separate spec): 40 Hz live viewfinder via sensor ROI windowing.

## 2. Why this shape

The chip cannot buffer a frame (24.6 Mbit vs ~184 kbit total EBR) and speckle is
near-incompressible, so the only route is pacing the *source*: the OX02C1B is
global-shutter — exposure completes in one instant, and readout afterward merely
drains stored charge. Stretching **HTS** (the between-lines blanking) slows readout
so each row fits through the FPGA→MCU link before the next row lands. The FPGA
only ever holds two lines.

Two facts verified from the shipped config + datasheet make this safe:

- **MIPI clock lane is continuous** (`0x4800 = 0x14`, `gate_sc_en` = 0): the FPGA's
  PLL reference — and therefore the SPI serializer — survives arbitrarily long
  blanking.
- **Power-save auto-engage is off** (`0x3400 = 0x0C`, `psv_auto_dis` = 1): the
  VTS-row-counter threshold that would sleep the sensor never fires.
  (`psv_mode_en` bit2 is set — read as capability-enable; scope-verify on bench.)

## 3. Verified system facts (basis for all numbers)

| Fact | Value | Source |
|---|---|---|
| Sensor | OX02C1B, global shutter, 1920×1280 RAW10 | `OX02C1S_OX02C1B_a-CSP_DS_1.0.pdf` |
| Production timing | HTS 432, VTS 2768, row 9.032 µs, frame 25 ms, exposure 72 rows ≈ 650 µs | `X02C1B_Sensor_Config.h` |
| HTS timing unit | 9.032 µs / 432 ≈ 20.9 ns (16-bit reg, max 65535 ≈ 1.37 ms/row) | derived |
| FPGA→MCU link | 33.2 MHz SCLK burst, ~286 ns/byte sustained (~3.5 MB/s) | `histo_serializer.v`, `spi_master.v` |
| FPGA memory | LIF-MD6000: 20 EBR; 9 used by histogram path, +3 line RAM on feature/5 | map report |
| MCU links | 4 cameras SPI-slave, 4 USART-sync-slave, dedicated per camera, DMA one-shot | sensor-fw `main.c`/`msp.c` |
| USB headroom | HISTO endpoint uses 1.3 MB/s of ~30–40 MB/s practical HS bulk | sensor-fw |
| FSIN | Broadcast, host-settable rate (console SyncOut or MCU TIM4); sensor slave regs `0x3826/27` = VTS−4 | sensor-fw, datasheet p43 |

## 4. Design

### 4.1 Wire format — packed RAW10 line push (2408 B)

The 4100-B histogram envelope is retired for image data (its 2-px-per-4-B packing
wastes 37% of the link). Each row is one fixed-size push:

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | Magic `0xB6` (same marker as feature/5) |
| 1 | 1 | Format version `0x01` |
| 2 | 1 | `line[7:0]` |
| 3 | 1 | `{flags[3:0], line[11:8]}` — flag bit0: overrun occurred since sweep start (line dropped: host misprogrammed sensor timing); flag bit1: pusher-watchdog wedge occurred since sweep start (a push aborted after ~15.8 ms with no serializer progress: electrical/SEU event mid-push). A wedge sets both bits; a plain overrun sets only bit0 |
| 4 | 1 | `frame_cnt[7:0]` (free-running fv counter; must be constant across one image) |
| 5 | 1 | Reserved `0x00` |
| 6 | 2400 | 1920 px packed RAW10, 4 px → 5 B: pixel *k* (k=0..3, in readout order) occupies bits [10k+9 : 10k] of a 40-bit little-endian group; groups transmit low byte first (matches the link's LSB-first byte convention) |
| 2406 | 2 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, MSB-first fold, no reflection, no final XOR — byte-identical to sensor-fw `util_crc16`), transmitted high byte first, computed over bytes 0–2405 |

Drain time at current link: 2408 × 286 ns ≈ **0.69 ms**. The MCU forwards blind;
the **host** verifies CRC (the MCU stays out of the per-line hot path).

### 4.2 Sensor retiming (host-side I2C, group-hold latched)

Sweep profile, written via the existing `OW_CAMERA_SWITCH` + `OW_I2C_PASSTHRU`
path using sensor group-hold so it latches atomically at a frame boundary:

| Register | Production | Sweep | Why |
|---|---|---|---|
| HTS `0x380C/D` | 432 (9.03 µs) | **38,400** (~0.80 ms) | ≥16% margin over 0.69 ms drain |
| VTS `0x380E/F` | 2768 | **1312** | Don't pay for 1488 blank rows at stretched pace |
| `tc_r_initial 0x3826/27` | 0x0000 (shipped) | 1308 (=VTS−4) | FSIN slave timing is VTS-coupled. NB: the shipped production config writes tc_r=0, not VTS−4; the sweep value uses the datasheet FSIN-slave VTS−4 recommendation — confirm FSIN sync on the bench (§5). Restore writes the real shipped 0x0000. |
| Exposure `0x3501/02` | 72 rows | **1 row** (~0.80 ms) | Row unit stretched; shutter window minimal |
| FSIN rate (console/TIM4) | 40 Hz | **0.8 Hz** | Period ≥ frame readout 1312 × 0.80 ms ≈ 1.05 s |

Laser: per-pulse peak, width, and FSIN-relative firing time are **unchanged**; only
the repetition rate drops with FSIN. The safety interlock (peak/width/rate) sees a
lower rate — the safe direction. Speckle integration time is governed by the laser
pulse, not the shutter window; the 0.80 ms window admits ~1.24× the ambient of the
production 650 µs window (negligible for NIR-filtered dark-room use; quantified in
verification).

### 4.3 FPGA (delta on feature/5 modules)

- **Register map (I2C 0x5A), VERSION 0x01 → 0x02:**
  - `CTRL` bit1 = **SWEEP** (valid only with bit0 image-mode; sampled at fv boundary
    like the existing mode bit).
  - `LINE_L/H` reused as **sweep start line** — a retry re-runs the sweep from the
    first missing line (0 for a fresh capture).
  - `STATUS` bit2 = overrun latch, bit3 = wedge latch (pusher-watchdog abort —
    distinguishes an electrical/SEU wedge from a timing overrun). Both latches
    clear on sweep arm and on any re-arm publish (consumed at the next frame
    boundary), so each retry starts with clean tripwires.
- **`line_capture` becomes double-buffered:** second 1024×24 EBR instance (ping/pong,
  +3 EBR → 15/20 total). In sweep mode every line ≥ start_line is captured; a
  completed buffer immediately queues to the serializer. If a buffer completes while
  the previous is still draining (should be impossible by construction — row period
  0.80 ms > drain 0.69 ms — but HTS is host-programmed and can be wrong): drop the
  new line, set the overrun latch and header flag. Open-loop timing with a tripwire,
  same philosophy as the rest of the design.
- **Packer/serializer:** 40-bit accumulator (two 20-bit pixel pairs) → 5 output
  bytes; CRC-16 computed as bytes stream; header from registers/counters. The
  histogram serializer and envelope are untouched — histogram mode behavior is
  bit-identical (regression guarantee).

### 4.4 Sensor-fw

- **Image receive mode** (new command, opcode assigned at implementation; carries a
  camera bitmask): per enabled camera, arm a fixed **2408-B** DMA one-shot; on
  RxCplt, forward and immediately re-arm (data-paced, not FSIN-paced). Line timeout
  ~2 ms → abort/re-arm and count a gap (host retries via sweep start line).
- **USB forwarding:** per-line packet on the HISTO endpoint with stream type
  **`0x03`** (`OW_IMAGE_PACKET` — reserved in the SDK since forever, never used):
  existing header/SOH framing conventions, cam_id + 2408-B line payload.
  ISR budget: 8 cameras × ~1.25 kHz line rate = 10 k light ISRs/s on a 480 MHz H7 —
  fine; the camera mask is the derating lever if USB aggregate (~24 MB/s all-8)
  disagrees on the bench.
- Histogram streaming is suspended while image mode is active (exclusive, as on
  feature/5); first histogram frame after exit is garbage and must be discarded
  (existing caveat, unchanged).

### 4.5 SDK (`omotion`)

- `StreamInterface`: accept type `0x03`, route to an image queue (today unknown
  types are logged and dropped).
- New image reassembler: CRC check per line, ordered fill by `line`, gap list,
  `frame_cnt` consistency check (all lines of an image must share it — the
  single-exposure proof).
- Capture orchestrator (graduating `tools/full_frame_capture/`): enable image
  mode → group-hold retime → slow FSIN → collect/retry until complete → restore
  timing → restore FSIN → exit image mode → discard first histogram frame.
  Output: uint16 1920×1280 `.npy` + 16-bit PNG per camera, as on feature/5.

### 4.6 Link-speed experiments (accelerator, same feature)

Adopt the fastest configuration that passes HIL on **both** link types (4 cameras
are USART-sync-slave — the likely ceiling):

1. Serializer inter-byte gap trim (~38 → ~33 clk/byte): ~15% sustained gain, no
   SCLK rating change. Low risk.
2. `CLKS_PER_HALF_BIT` 2 → 1: SCLK 33.2 → 66.4 MHz, drain 0.69 → ~0.35 ms →
   row ~0.45 ms → frame ~0.6 s. Test per link type before adopting.

HTS is recomputed from the adopted rate and pinned as one profile in the SDK config
(no per-camera timing variants).

## 5. Verification & acceptance

Bench items first (cheap, kill-or-confirm):

1. Scope the MIPI clock lane through a stretched line (confirms §2 clock facts).
2. Capped-lens dark sweep: storage-node decay vs row index over the ~1 s readout,
   reported in DN. If the gradient is objectionable, the fix is §4.6 (shorter
   readout), not redesign.
3. Per-link-type max SCLK matrix for §4.6.

Acceptance:

- Sensor test-pattern sweep reconstructs **bit-exact** (all 1280 lines, CRC clean)
  on at least one SPI-linked and one USART-linked camera.
- Live laser-synced speckle full frame at production pulse parameters, with
  `frame_cnt` constant across all lines (single-exposure proof).
- Single-camera capture ≤ 1.2 s at the shipped link rate; ≥ 2 cameras concurrent;
  all-8 result documented (pass, or camera-mask derating decision).
- Existing histogram HIL suite passes after a sweep session (mode exclusivity and
  exit path leave streaming intact).
- Icarus testbench for double-buffered sweep + packer/CRC (extend the feature/5
  integration TB).

## 6. Risks

| Risk | Exposure | Mitigation |
|---|---|---|
| Storage-node dark-current gradient over ~1 s readout | Image quality (later rows brighter) | Measure first (verification #2); shorten readout via link speed if needed |
| USART-slave SCLK ceiling blocks 66 MHz experiment | Capture stays ~1.0 s | Gap-trim still applies; 1.0 s already meets the goal |
| USB aggregate all-8 (~24 MB/s) | Concurrent-capture scope | Camera mask derates to 4- or 2-at-a-time |
| FSIN slave regs coupled to VTS | Sync glitch on retime | `0x3826/27` written in the same group-hold as VTS (§4.2) |
| `psv_mode_en` bit2 set in shipped config | Sensor sleeps mid-sweep | Scope check in verification #1; pin PSV regs in sweep profile if needed |
| Byte-slip on USART links mid-stream | Corrupt line | Per-line CRC + fixed-size DMA + timeout resync + host retry |

## 7. Tracking

- FPGA work: this repo, issue #8, branch `feature/8-drip-scan-single-frame`
  (based on `feature/5-i2c-line-readout`; rebase to `main` when PR #7 merges).
- sensor-fw and SDK companion issues: filed when implementation starts in those
  repos (per the cross-repo board process), linked back to #8.
