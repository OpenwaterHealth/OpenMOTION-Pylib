# NO_TA Frames — Design

**Date:** 2026-07-15
**Ticket:** [openmotion-bloodflow-app#361](https://github.com/OpenwaterHealth/openmotion-bloodflow-app/issues/361)
**Requested by:** @bahartl — engineering testing only
**Status:** draft for review

## Purpose

For the first **N** frames of a scan, deliberately drive the TA unseeded (the
test *intends* to induce TA self-lasing) with a long pulse and a long camera
exposure, then revert to normal scan parameters and let the scan continue.

Per-frame regime for frames 1..N:

| Parameter | Normal | NO_TA |
|---|---|---|
| Seed | CW, `SEED_CW_GAIN` ≈ 142 mV | **off** (`SEED_CW_GAIN=0`, `SEED_DDS_GAIN=0`) |
| TA pulse width | 500 µs (raw 1563) | **2 ms** (raw 6250) |
| Camera exposure | 648 µs (`0x3502=0x48`) | **2295 µs** (`0x3502=0xFF`) |
| Safety `EE/OPT_PULSE_WIDTH_UL` | 1.000 ms (raw 3125) | **widened** to clear 2 ms |
| Safety `EE/OPT_PULSE_WIDTH_LL` | 0 | 0 (unchanged) |

Notes on the numbers:

- 2295 µs (255 rows × 9 µs) is the max the single-byte `0x3502` write can
  express; Ethan accepted it in lieu of 2.5 ms. No `0x3501` write needed.
- The 1 ms safety UL is the normal fault-trip mechanism
  (`laser_params_fault.json` differs from the normal set *only* by UL).
  Widening it is a deliberate, Ethan-authorized safety change, **bounded to
  the NO_TA scan** and restored on every exit path.
- Unseeded TA at 5 A: the *goal* is self-lasing. 2 ms at 40 Hz is 8 % duty
  vs the normal 2 % — thermal watch is a bench responsibility, not a
  software control.

## Why all host-side (no firmware change anywhere)

The TA FPGA is an **edge-triggered internal one-shot**: on the rising edge of
the trigger line it emits a pulse sized by its own `TA_PULSE_WIDTH` I2C
register (`openmotion-safety-fpga/src/driver_control.v:143-182`; register
decode confirmed against `fpga_register_map.c:15`). The STM32 trigger line's
duration is irrelevant beyond the edge, so changing the pulse width mid-scan
is **just an I2C write** — no `Trigger_SetConfig`, no trigger restart (which
would reset the fsync counter and is why mid-scan trigger reconfig is
impossible; `openmotion-console-fw/Core/Src/trigger.c:180-196`).

The seed has no emission-gate bit in the compiled bitstream
(`laser_control.v` exists but is not instantiated; `SEED_STATIC_CTRL[1]` only
drives an LED). Zeroing the gains is the only off-switch, also an I2C write.

Exposure is a camera I2C passthrough write the SDK already performs.

Therefore every parameter is host-writable at runtime and the feature lives
entirely in the SDK, plus a small bloodflow-app UI toggle.

## Control surface

`ScanRequest` gains one field:

```python
no_ta_frames: int = 0   # >0: run the first N frames in NO_TA mode (issue
                        # bloodflow-app#361, engineering test only). 0 = off.
```

- Headless scripts set it directly.
- bloodflow-app (separate ticket/PR): engineering-mode checkbox + frame-count
  spinner mapped to this field. Not exposed in clinical mode.

## Architecture

New module **`omotion/no_ta.py`** — class `NoTaController` — owning all
NO_TA register traffic and state. `ScanWorkflow` calls it at three defined
points; nothing else in the pipeline knows the registers exist.

```
ScanWorkflow worker                      NoTaController
────────────────────                     ─────────────────────────────
pre-flight (before start_trigger) ────▶  snapshot() + apply_no_ta()
source on_row(frame_id) ──(abs id ≥ N)─▶ schedule restore (worker thread)
finally / cancel path ────────────────▶  restore(force=True)  [idempotent]
```

### Phase 1 — pre-scan (before `start_trigger()`, `ScanWorkflow.py` ~line 650)

Only when `request.no_ta_frames > 0`:

1. **Snapshot** current values of `SEED_CW_GAIN`, `SEED_DDS_GAIN`,
   `TA_PULSE_WIDTH`, `EE/OPT_PULSE_WIDTH_UL/LL` via console I2C read-back
   (`OW_CTRL_I2C_RD`). If any read fails, fall back to the bundled
   `laser_params.json` values and log a warning — restore must never be
   blocked by a failed read.
2. **Write NO_TA config** (console I2C, order is free — laser not firing yet):
   widen `EE_PULSE_WIDTH_UL` and `OPT_PULSE_WIDTH_UL` to raw 7813 (≈2.5 ms,
   25 % margin over the 2 ms pulse), zero seed gains, set `TA_PULSE_WIDTH`
   raw 6250.
3. **Set exposure 2295 µs** on all active cameras on both modules via the
   existing camera I2C passthrough path.
4. Any write failure here **aborts the scan before the trigger starts** and
   runs the restore. Never start the laser in a half-applied NO_TA state.

### Trigger-config adjustment (dark frames must stay dark)

"Dark" frames are laser pulses delayed by `LaserPulseSkipDelayUsec` (1800 µs)
to fall outside the exposure window — sized for 648 µs exposure. With
2295 µs exposure, a pulse starting at 1900 µs lands **inside** the window,
and the console schedules the first 10 frames dark (`NUM_DARK_FRAMES_AT_START`,
`trigger.h:18`) — inside the NO_TA region.

When `no_ta_frames > 0`, the resolved per-scan trigger config sets
`LaserPulseSkipDelayUsec = 2500` (pulse start ≥ 2600 µs > 2295 µs exposure).
This is a normal pre-scan `set_trigger_json` merge — no mid-scan change —
and remains valid after the transition (2600 µs ≫ 648 µs). Pulse *rate* is
unchanged, so the `EE_RATE_LL` safety window is undisturbed.

### Phase 2 — transition at frame N (frame-ID-driven, not wall-clock)

- The scan source's per-frame `on_row` callback (`pipeline/sources.py:353`)
  reports raw frame IDs as packets arrive. `NoTaController` watches unwrapped
  IDs; when **any** camera reaches abs frame ID ≥ N, it fires the restore
  exactly once (`threading.Event` latch).
- Restore runs on its **own worker thread** — never on the USB reader thread
  (console UART writes + 16 camera passthrough writes take ~1 s; blocking the
  reader would drop frames).
- **Restore order is safety-critical** (wrong order latches `TA_shutdown`
  via `pulse_upper_limit_fail`, killing the TA for the rest of the scan):
  1. `TA_PULSE_WIDTH` → snapshot value (500 µs)
  2. wait ≥ 2 frame periods (50 ms) so no in-flight 2 ms pulse can be
     measured against restored limits
  3. `EE/OPT_PULSE_WIDTH_UL/LL` → snapshot values
  4. seed gains → snapshot values
  5. camera exposure → 0x48 (648 µs), all active cameras
- Expected imprecision (accepted): console writes land within ±1–2 frames of
  N; exposure revert smears over ~32 frames (2 modules × 8 cameras ×
  per-register USB round-trips). All of it falls inside the tagged guard
  band (below).

### Phase 3 — teardown (always)

The scan worker's `finally` and the `cancel_scan` path call
`controller.restore(force=True)`. Idempotent (the Event latch makes a
double-restore a no-op). **A NO_TA scan can never exit — complete, cancelled,
or crashed — with the seed off or the safety limits widened.** If a restore
write fails, retry once, then log at ERROR and surface via the scan's
`on_error` callback; the values also self-heal on the next
`apply_laser_power()` (cold-start path), which rewrites the full baseline.

## Output: how scientists identify the frames

`FrameClassificationStage` (`pipeline/stages/classify.py`) gains
`no_ta_frames: int = 0` (plumbed from `ScanRequest` through `factory.py`).
When > 0, positional tagging on unwrapped abs IDs — same mechanism as
`warmup`/`dark`/`light` today:

| abs frame ID | `frame_type` |
|---|---|
| 1 .. N | `no_ta` |
| N+1 .. N+80 | `no_ta_tx` (guard band: register writes landing, exposure smear) |
| > N+80 | normal classification (`warmup`/`dark`/`light`/`stale`) |

- Tags are ≤ 8 chars (the column is dtype `<U8`).
- `stale` still wins over `no_ta` (a stale frame is garbage in any regime).
- Guard band is fixed at 80 frames (2 s): worst case for the exposure revert
  is 2 modules × 8 cameras of sequential passthrough writes (~64 frames at
  40 Hz), plus console-write jitter, with margin.
- Downstream stages treat `no_ta`/`no_ta_tx` like `warmup`: excluded from
  BFI/BVI computation and dark correction; present in the raw CSV with their
  tags. Scientists filter `frame_type == "no_ta"`.
- The boundary is also **recorded**: N goes into scan metadata (DB scan row
  + log line), and the controller logs the abs frame ID at which each restore
  step completed.

## Error handling summary

| Failure | Behavior |
|---|---|
| Snapshot read fails | Warn, fall back to bundled baseline values for restore |
| NO_TA apply write fails | Abort scan pre-trigger, restore, surface error |
| Restore write fails mid-scan | Retry once, ERROR log + `on_error`; teardown retries again |
| Scan cancelled during NO_TA region | `finally`-path restore (idempotent) |
| Host crash mid-NO_TA | Registers left NO_TA until next `apply_laser_power()` rewrites baseline; console power-cycle also clears (registers are volatile) |

## Testing

- **Unit (no hardware):** `NoTaController` with a mock console/sensors —
  ordering assertions (UL widened before TA width raised is not required
  pre-trigger, but restore order 1→5 is asserted strictly), latch
  idempotency, snapshot-fallback path. `FrameClassificationStage` tag tests
  in `tests/test_pipeline/` (no_ta / no_ta_tx / boundary / stale-wins).
- **Bench (with hardware):** one NO_TA scan; verify in the raw CSV that
  (a) frames tagged `no_ta` show the dim unseeded signature, (b) exposure
  step is visible in histogram sums at the tagged boundary, (c) post-guard
  frames match a normal scan's levels, (d) safety FPGA did not latch
  (`TA_shutdown` clear — scan completes with light frames). Verify via
  I2C read-back after the scan that all six registers match baseline.

## Explicitly out of scope

- Wire-format frame tagging (no spare bits; FPGA counter uses all 8).
- Sensor-fw frame-accurate exposure switching.
- Console-fw frame-accurate seed/TA switching (`FSYNC_PeriodElapsedCallback`
  third slot) — revisit only if ±1–2 frame laser precision proves
  insufficient for the science.
- Fixing `camera_set_exposure()`'s `& 0xFF` silent wrap (real bug, separate
  ticket — not blocking since 255 is exactly the value we want).
- Clinical-mode UI exposure. Engineering mode only.

## Tickets / repos touched

| Repo | Work | Ticket |
|---|---|---|
| openmotion-sdk | `no_ta.py`, `ScanRequest`, classify stage, factory plumbing, tests | new SDK issue, refs bloodflow-app#361 |
| openmotion-bloodflow-app | engineering-mode checkbox + N spinner → `no_ta_frames` | #361 |
| console-fw / sensor-fw / FPGAs | **none** | — |
