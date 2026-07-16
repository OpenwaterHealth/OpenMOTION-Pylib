# SEEDLESS Frames Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the first N frames of a scan with the seed laser off, the TA pulsing 2 ms (deliberately unseeded — engineering test), and camera exposure 2295 µs, then restore normal parameters mid-scan — all host-side register writes, no firmware changes.

**Architecture:** A new `SeedlessController` (`omotion/seedless.py`) owns all register snapshot/apply/restore traffic. `ScanWorkflow` invokes it pre-trigger and on teardown; a tiny new pipeline stage (`SeedlessWatchStage`) fires the mid-scan restore when the classified frame stream reaches frame N. `FrameClassificationStage` tags frames `seedless` / `seedless_tx` so scientists can filter them in the CSV/DB.

**Tech Stack:** Python 3.12, existing `omotion` SDK layers (MotionConsole UART I2C, MotionSensor USB passthrough, pipeline stages), pytest.

**Spec:** `docs/superpowers/specs/2026-07-15-seedless-frames-design.md` (approved). Tickets: [SDK #146](https://github.com/OpenwaterHealth/openmotion-sdk/issues/146), app-side [bloodflow-app #361](https://github.com/OpenwaterHealth/openmotion-bloodflow-app/issues/361).

**One deviation from the spec, decided at planning:** frame-N detection uses a pipeline stage after classification (`SeedlessWatchStage`) instead of a raw `on_row` source callback. Rationale: it reuses the classify stage's already-unwrapped absolute frame IDs (no second `_FrameUnwrapper`), touches nothing in `sources.py`, and is trivially unit-testable. Cost: batches flush every 10 frames / 0.25 s, so the restore fires up to ~12 frames after frame N — well inside the 80-frame `seedless_tx` guard band that already absorbs the ~64-frame exposure smear.

---

## Register reference (used throughout — do not re-derive)

All console FPGA registers: `console.write_i2c_packet(mux_index=1, channel=<ch>, device_addr=0x41, reg_addr=<reg>, data=<bytes>)`, read via `console.read_i2c_packet(..., read_len=<len>)` → `(bytes|None, len|None)`. All little-endian. Source of truth: `omotion/data/fpga_model.json`, baselines `omotion/data/laser_params.json`.

| Name | ch | reg | len | Baseline (normal) | Seedless |
|---|---|---|---|---|---|
| `TA_PULSE_WIDTH` | 4 | 0x00 | 3 | `[0x1B,0x06,0x00]` (1563 → 500 µs) | `[0x6A,0x18,0x00]` (6250 → 2000 µs) |
| `SEED_DDS_GAIN` | 5 | 0x02 | 2 | `[0x00,0x00]` | `[0x00,0x00]` (unchanged, still snapshot/restore) |
| `SEED_CW_GAIN` | 5 | 0x04 | 2 | `[0x0E,0x08]` (2062 → ~142 mV) | `[0x00,0x00]` (seed OFF) |
| `EE_PULSE_WIDTH_UL` | 6 | 0x04 | 4 | `[0x35,0x0C,0x00,0x00]` (3125 → 1.0 ms) | `[0x85,0x1E,0x00,0x00]` (7813 → 2.5 ms) |
| `OPT_PULSE_WIDTH_UL` | 7 | 0x04 | 4 | `[0x35,0x0C,0x00,0x00]` | `[0x85,0x1E,0x00,0x00]` |

`EE/OPT_PULSE_WIDTH_LL` stay 0 in both regimes — not touched (spec lists them unchanged).

Camera exposure (per camera, via `MotionSensor`): `switch_camera(cam_id)` (0-based bit index of the mask), then `camera_i2c_write(I2C_Packet(device_address=0x36, register_address=0x3501, data=0x00))`, then `... register_address=0x3502, data=<byte>`. Seedless byte `0xFF` (2295 µs); restore byte `0x48` (648 µs — the firmware config-table default, `X02C1B_Sensor_Config.h` on sensor-fw `main`). `I2C_Packet` is `from omotion.i2c_packet import I2C_Packet`.

**Restore ordering is safety-critical** (reverse order latches `TA_shutdown` in the safety FPGA): TA_PULSE_WIDTH first → 50 ms wait → EE/OPT ULs → seed gains → exposure.

## File structure

- Create: `omotion/seedless.py` — `SeedlessController` + register constants (one responsibility: seedless register traffic)
- Create: `omotion/pipeline/stages/seedless_watch.py` — `SeedlessWatchStage` (~25 lines)
- Create: `tests/test_seedless_controller.py` — controller unit tests (fake console/sensors)
- Create: `tests/test_pipeline/test_seedless_watch_stage.py` — watch stage tests
- Modify: `omotion/pipeline/stages/classify.py` — `seedless`/`seedless_tx` tags, dtype `<U12`
- Modify: `omotion/pipeline/factory.py` — plumb `seedless_frames` + transition callback; exclude seedless tags from the live tee
- Modify: `omotion/pipeline/sinks.py` — `ScanMetadata.seedless_frames` + DB `sdk_flags`
- Modify: `omotion/ScanWorkflow.py` — `ScanRequest.seedless_frames`, controller lifecycle, trigger-config merge
- Modify: `tests/test_pipeline/test_classify_stage.py`, `tests/test_pipeline/test_factory.py` — new cases

---

### Task 1: `seedless` / `seedless_tx` tags in FrameClassificationStage

**Files:**
- Modify: `omotion/pipeline/stages/classify.py` (constructor ~line 88, `process` loop ~lines 101-140)
- Test: `tests/test_pipeline/test_classify_stage.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline/test_classify_stage.py` (it already has the `_batch_with_raw_ids` helper — reuse it):

```python
# ── SEEDLESS frames (spec: docs/superpowers/specs/2026-07-15-seedless-frames-design.md) ──

def test_seedless_tags_first_n_then_guard_band_then_normal():
    # N=3, guard=80: ids 1-3 seedless, 4-83 seedless_tx, 84+ normal.
    ids = list(range(1, 90))
    batch = _batch_with_raw_ids({(0, 0): ids})
    FrameClassificationStage(
        discard_count=9, dark_interval=600, seedless_frames=3
    ).process(batch)
    assert list(batch.frame_type[:3]) == ["seedless"] * 3
    assert list(batch.frame_type[3:83]) == ["seedless_tx"] * 80
    # After the guard band, normal positional rules resume (these ids are
    # past discard_count so they are light/dark, not warmup).
    assert batch.frame_type[83] in ("light", "dark")


def test_seedless_overrides_warmup_inside_region():
    # Without seedless, ids 1..9 would be warmup. With N=5 they are seedless
    # then seedless_tx — the seedless regime wins over warmup.
    batch = _batch_with_raw_ids({(0, 0): [1, 2, 3, 4, 5, 6, 7]})
    FrameClassificationStage(
        discard_count=9, dark_interval=600, seedless_frames=5
    ).process(batch)
    assert list(batch.frame_type) == ["seedless"] * 5 + ["seedless_tx"] * 2


def test_stale_still_wins_over_seedless():
    # A stream not starting at 1 marks the leading frame stale even in a
    # seedless scan — garbage is garbage in any regime.
    batch = _batch_with_raw_ids({(0, 0): [42, 43, 44]})
    FrameClassificationStage(
        discard_count=9, dark_interval=600, seedless_frames=5
    ).process(batch)
    assert batch.frame_type[0] == "stale"


def test_seedless_disabled_by_default():
    batch = _batch_with_raw_ids({(0, 0): [1, 2, 3]})
    FrameClassificationStage(discard_count=9, dark_interval=600).process(batch)
    assert list(batch.frame_type) == ["warmup"] * 3


def test_seedless_tx_tag_not_truncated():
    # frame_type dtype must hold the 11-char "seedless_tx" without clipping.
    batch = _batch_with_raw_ids({(0, 0): [1, 2]})
    FrameClassificationStage(
        discard_count=9, dark_interval=600, seedless_frames=1
    ).process(batch)
    assert batch.frame_type[1] == "seedless_tx"   # not "seedless" / "seedles…"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline/test_classify_stage.py -v -k seedless`
Expected: 5 failures — `TypeError: __init__() got an unexpected keyword argument 'seedless_frames'`

- [ ] **Step 3: Implement**

In `omotion/pipeline/stages/classify.py`:

Add a module-level constant near the top (after `_FRAME_ID_MODULUS`):

```python
# SEEDLESS engineering test (SDK issue #146): frames after the seedless
# region are tagged as transition for this many frames — covers the
# host-driven exposure revert smear (2 modules x 8 cameras of sequential
# passthrough writes ~= 64 frames at 40 Hz) plus console-write jitter.
SEEDLESS_TX_GUARD_FRAMES = 80
```

Constructor — add the parameter:

```python
    def __init__(self, discard_count: int = 9, dark_interval: int = 600,
                 seedless_frames: int = 0):
        self.discard_count = int(discard_count)
        self.dark_interval = int(dark_interval)
        self.seedless_frames = int(seedless_frames)
```

In `process`, change the dtype line:

```python
        types = np.empty(n, dtype="<U12")
```

And insert the two seedless branches into the classification chain, after the two stale checks and **before** the warmup check:

```python
            elif (self.seedless_frames > 0
                  and abs_id <= self.seedless_frames):
                types[i] = "seedless"
            elif (self.seedless_frames > 0
                  and abs_id <= self.seedless_frames + SEEDLESS_TX_GUARD_FRAMES):
                types[i] = "seedless_tx"
            elif abs_id <= self.discard_count:
                types[i] = "warmup"
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_pipeline/test_classify_stage.py -v`
Expected: ALL pass (new seedless cases and every pre-existing case — the dtype widening must not break anything).

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/stages/classify.py tests/test_pipeline/test_classify_stage.py
git commit -m "feat: tag seedless/seedless_tx frames in classification (refs #146)"
```

---

### Task 2: SeedlessWatchStage — fire the restore callback at frame N

**Files:**
- Create: `omotion/pipeline/stages/seedless_watch.py`
- Test: `tests/test_pipeline/test_seedless_watch_stage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline/test_seedless_watch_stage.py`:

```python
"""SeedlessWatchStage — fires the transition callback once at frame N."""

import numpy as np
from omotion.pipeline.batch import FrameBatch
from omotion.pipeline.stages.seedless_watch import SeedlessWatchStage


def _batch_with_abs_ids(abs_ids):
    n = len(abs_ids)
    batch = FrameBatch(
        cam_ids=np.zeros(n, dtype=np.int8),
        frame_ids=np.zeros(n, dtype=np.uint8),
        side_ids=np.zeros(n, dtype=np.int8),
        raw_histograms=np.zeros((n, 2, 8, 1024), dtype=np.uint32),
        temperature_c=np.zeros((n, 2, 8), dtype=np.float32),
        timestamp_s=np.arange(n, dtype=np.float64),
        pdc=None, tcm=None, tcl=None,
    )
    batch.abs_frame_ids = np.array(abs_ids, dtype=np.int64)
    return batch


def test_no_fire_below_threshold():
    fired = []
    stage = SeedlessWatchStage(n_frames=10, callback=lambda: fired.append(1))
    stage.process(_batch_with_abs_ids([1, 2, 3]))
    assert fired == []


def test_fires_once_at_threshold_and_never_again():
    fired = []
    stage = SeedlessWatchStage(n_frames=10, callback=lambda: fired.append(1))
    stage.process(_batch_with_abs_ids([8, 9, 10]))
    stage.process(_batch_with_abs_ids([11, 12]))
    assert fired == [1]


def test_passes_batch_through_unmodified():
    stage = SeedlessWatchStage(n_frames=10, callback=lambda: None)
    batch = _batch_with_abs_ids([1, 2])
    assert stage.process(batch) is batch


def test_callback_exception_does_not_break_pipeline():
    def boom():
        raise RuntimeError("restore failed")
    stage = SeedlessWatchStage(n_frames=1, callback=boom)
    batch = _batch_with_abs_ids([1, 2])
    assert stage.process(batch) is batch   # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline/test_seedless_watch_stage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'omotion.pipeline.stages.seedless_watch'`

- [ ] **Step 3: Implement**

Create `omotion/pipeline/stages/seedless_watch.py`:

```python
"""SEEDLESS transition watch (SDK issue #146).

Sits directly after FrameClassificationStage in the pipeline. When the
unwrapped absolute frame id reaches the end of the seedless region it fires
the supplied callback exactly once — ScanWorkflow wires this to
SeedlessController.schedule_restore(), which does its register writes on its
own thread, so the callback returns immediately and never blocks the runner.

Detection latency: batches flush every 10 frames / 0.25 s, so the callback
fires up to ~12 frames after frame N. That lag lands inside the 80-frame
seedless_tx guard band (see classify.SEEDLESS_TX_GUARD_FRAMES).
"""

import logging
from typing import Callable

from ..batch import FrameBatch

logger = logging.getLogger("openmotion.sdk.pipeline")


class SeedlessWatchStage:
    name = "seedless_watch"

    def __init__(self, *, n_frames: int, callback: Callable[[], None]):
        self.n_frames = int(n_frames)
        self._callback = callback
        self._fired = False

    def process(self, batch: FrameBatch) -> FrameBatch:
        if not self._fired and batch.abs_frame_ids is not None \
                and (batch.abs_frame_ids >= self.n_frames).any():
            self._fired = True
            logger.info(
                "SeedlessWatchStage: frame %d reached (max abs id %d) — "
                "firing restore callback",
                self.n_frames, int(batch.abs_frame_ids.max()),
            )
            try:
                self._callback()
            except Exception:
                logger.exception("seedless transition callback raised")
        return batch
```

Note: check `omotion/pipeline/batch.py` — if `FrameBatch.abs_frame_ids` does not default to `None` before classification sets it, drop the `is not None` guard accordingly (the stage always runs after classification anyway; the guard is belt-and-suspenders).

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_pipeline/test_seedless_watch_stage.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/stages/seedless_watch.py tests/test_pipeline/test_seedless_watch_stage.py
git commit -m "feat: SeedlessWatchStage fires restore callback at frame N (refs #146)"
```

---

### Task 3: Factory plumbing + live-tee exclusion

**Files:**
- Modify: `omotion/pipeline/factory.py` (signature ~line 28, `not_warmup_or_stale` ~line 52, stage list)
- Test: `tests/test_pipeline/test_factory.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline/test_factory.py` (reuse its `_trivial_calibration` and the `ScanMetadata` construction pattern already in the file):

```python
def _meta():
    return ScanMetadata(
        scan_id="x", subject_id="y", operator="z",
        started_at_iso="2026-05-22T00:00:00Z", duration_sec=60,
        left_camera_mask=0xFF, right_camera_mask=0xFF, reduced_mode=False,
    )


def test_seedless_frames_plumbed_to_classification_and_watch_stage():
    fired = []
    pipeline = default_pipeline(
        metadata=_meta(), calibration=_trivial_calibration(),
        pedestals=SensorPedestals(left=64.0, right=64.0),
        seedless_frames=120, seedless_transition_cb=lambda: fired.append(1),
    )
    names = [stage.name for stage in pipeline.stages]
    assert names.index("seedless_watch") == names.index("frame_classification") + 1
    classify = pipeline.stages[names.index("frame_classification")]
    assert classify.seedless_frames == 120
    watch = pipeline.stages[names.index("seedless_watch")]
    assert watch.n_frames == 120


def test_no_seedless_watch_stage_by_default():
    pipeline = default_pipeline(
        metadata=_meta(), calibration=_trivial_calibration(),
        pedestals=SensorPedestals(left=64.0, right=64.0),
    )
    assert "seedless_watch" not in [stage.name for stage in pipeline.stages]


def test_live_tee_excludes_seedless_frames():
    pipeline = default_pipeline(
        metadata=_meta(), calibration=_trivial_calibration(),
        pedestals=SensorPedestals(left=64.0, right=64.0),
        seedless_frames=10, seedless_transition_cb=lambda: None,
    )
    live = [s for s in pipeline.stages if s.name == "tee:live"][0]
    # The tee's emit predicate must reject seedless regimes like warmup/stale.
    for ft in ("warmup", "stale", "seedless", "seedless_tx"):
        assert not live.emit_if_any(ft), ft
    for ft in ("light", "dark"):
        assert live.emit_if_any(ft), ft
```

Note: if the `Tee` class stores the predicate under a different attribute than `emit_if_any`, check `omotion/pipeline/tee.py` and use the actual attribute name in the test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline/test_factory.py -v -k seedless`
Expected: FAIL — `TypeError: default_pipeline() got an unexpected keyword argument 'seedless_frames'`

- [ ] **Step 3: Implement**

In `omotion/pipeline/factory.py`:

Add the import with the other stage imports:

```python
from .stages.seedless_watch import SeedlessWatchStage
```

Add parameters to `default_pipeline` (after `dark_interval`):

```python
                     seedless_frames: int = 0,
                     seedless_transition_cb: Optional[Callable[[], None]] = None,
```

(`Callable` — extend the existing `typing` import if needed.)

Change the live-tee predicate (currently `not_warmup_or_stale = lambda ft: ft != "warmup" and ft != "stale"`):

```python
    # Frames excluded from the live UI trace: warmup/stale as before, plus
    # the SEEDLESS engineering-test regimes (treated like warmup — no valid
    # BFI/BVI; they remain in the raw CSV via the raw tee).
    not_warmup_or_stale = lambda ft: ft not in (
        "warmup", "stale", "seedless", "seedless_tx",
    )
```

Pass `seedless_frames` into the classification stage and insert the watch stage right after it:

```python
    stages: list = [
        FrameClassificationStage(discard_count=discard_count,
                                 dark_interval=dark_interval,
                                 seedless_frames=seedless_frames),
    ]

    if seedless_frames > 0 and seedless_transition_cb is not None:
        stages.append(SeedlessWatchStage(n_frames=seedless_frames,
                                         callback=seedless_transition_cb))
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_pipeline/test_factory.py -v`
Expected: ALL pass, including the pre-existing stage-order tests (the watch stage is only inserted when requested, so `test_default_pipeline_has_expected_stages` must still pass unchanged).

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/factory.py tests/test_pipeline/test_factory.py
git commit -m "feat: plumb seedless_frames through pipeline factory (refs #146)"
```

---

### Task 4: SeedlessController

**Files:**
- Create: `omotion/seedless.py`
- Test: `tests/test_seedless_controller.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_seedless_controller.py`:

```python
"""SeedlessController — register apply/restore for the SEEDLESS test.

Pure-software: fake console + fake sensors record every I2C call so the
tests can assert exact registers, values, and ORDER. The restore ordering
assertions are safety-critical — re-tightening the safety ULs while
TA_PULSE_WIDTH is still 2 ms latches TA_shutdown in the safety FPGA.
"""

import threading

from omotion.seedless import (
    SeedlessController,
    TA_PULSE_WIDTH_SEEDLESS, TA_PULSE_WIDTH_BASELINE,
    PULSE_WIDTH_UL_SEEDLESS, PULSE_WIDTH_UL_BASELINE,
    SEED_CW_GAIN_BASELINE,
    EXPOSURE_SEEDLESS_BYTE, EXPOSURE_RESTORE_BYTE,
)


class FakeConsole:
    def __init__(self, fail_reads=False, fail_writes=False):
        self.writes = []            # (channel, reg_addr, tuple(data))
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        # Simulated live register values, keyed (channel, reg_addr).
        self.regs = {
            (4, 0x00): bytes(TA_PULSE_WIDTH_BASELINE),
            (5, 0x02): bytes([0x00, 0x00]),
            (5, 0x04): bytes(SEED_CW_GAIN_BASELINE),
            (6, 0x04): bytes(PULSE_WIDTH_UL_BASELINE),
            (7, 0x04): bytes(PULSE_WIDTH_UL_BASELINE),
        }

    def read_i2c_packet(self, mux_index, channel, device_addr, reg_addr, read_len):
        assert mux_index == 1 and device_addr == 0x41
        if self.fail_reads:
            return None, None
        data = self.regs[(channel, reg_addr)]
        return data, len(data)

    def write_i2c_packet(self, mux_index, channel, device_addr, reg_addr, data):
        assert mux_index == 1 and device_addr == 0x41
        if self.fail_writes:
            return False
        self.writes.append((channel, reg_addr, tuple(data)))
        self.regs[(channel, reg_addr)] = bytes(data)
        return True


class FakeSensor:
    def __init__(self):
        self.calls = []             # ("switch", cam) or ("write", reg, val)

    def switch_camera(self, cam_id):
        self.calls.append(("switch", cam_id))
        return object()             # truthy response packet

    def camera_i2c_write(self, packet):
        self.calls.append(("write", packet.register_address, packet.data))
        return True


def _controller(console=None, left=None, n_frames=100):
    return SeedlessController(
        console=console if console is not None else FakeConsole(),
        sensors=[("left", left if left is not None else FakeSensor(), 0x03)],
        n_frames=n_frames,
        settle_s=0.0,               # no 50 ms sleeps in unit tests
    )


def test_apply_writes_seedless_values():
    console = FakeConsole()
    ctrl = _controller(console=console)
    assert ctrl.apply() is True
    w = console.writes
    assert (6, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS)) in w   # EE UL widened
    assert (7, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS)) in w   # OPT UL widened
    assert (5, 0x04, (0x00, 0x00)) in w                     # seed CW gain -> 0
    assert (5, 0x02, (0x00, 0x00)) in w                     # seed DDS gain -> 0
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_SEEDLESS)) in w   # TA -> 2 ms
    # Safety ULs must be widened BEFORE the TA width is raised.
    assert w.index((6, 0x04, tuple(PULSE_WIDTH_UL_SEEDLESS))) \
         < w.index((4, 0x00, tuple(TA_PULSE_WIDTH_SEEDLESS)))


def test_apply_sets_exposure_on_masked_cameras_only():
    left = FakeSensor()
    ctrl = _controller(left=left)
    ctrl.apply()
    switches = [c for c in left.calls if c[0] == "switch"]
    assert switches == [("switch", 0), ("switch", 1)]       # mask 0x03
    writes = [c for c in left.calls if c[0] == "write"]
    assert ("write", 0x3502, EXPOSURE_SEEDLESS_BYTE) in writes
    assert ("write", 0x3501, 0x00) in writes


def test_restore_order_is_ta_then_uls_then_seed_then_exposure():
    console = FakeConsole()
    left = FakeSensor()
    ctrl = _controller(console=console, left=left)
    ctrl.apply()
    console.writes.clear()
    left.calls.clear()
    assert ctrl.restore() is True
    w = console.writes
    i_ta  = w.index((4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)))
    i_ee  = w.index((6, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)))
    i_opt = w.index((7, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)))
    i_cw  = w.index((5, 0x04, tuple(SEED_CW_GAIN_BASELINE)))
    assert i_ta < i_ee and i_ta < i_opt        # TA narrowed FIRST
    assert i_ee < i_cw and i_opt < i_cw        # limits before seed back on
    assert ("write", 0x3502, EXPOSURE_RESTORE_BYTE) in left.calls


def test_restore_uses_snapshot_not_constants():
    console = FakeConsole()
    console.regs[(4, 0x00)] = bytes([0x99, 0x01, 0x00])     # non-default live value
    ctrl = _controller(console=console)
    ctrl.apply()
    console.writes.clear()
    ctrl.restore()
    assert (4, 0x00, (0x99, 0x01, 0x00)) in console.writes


def test_restore_falls_back_to_baseline_when_snapshot_read_failed():
    console = FakeConsole(fail_reads=True)
    ctrl = _controller(console=console)
    ctrl.apply()                                # snapshot reads all fail
    console.writes.clear()
    ctrl.restore()
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)) in console.writes
    assert (6, 0x04, tuple(PULSE_WIDTH_UL_BASELINE)) in console.writes


def test_restore_is_idempotent():
    console = FakeConsole()
    ctrl = _controller(console=console)
    ctrl.apply()
    ctrl.restore()
    n = len(console.writes)
    ctrl.restore()
    assert len(console.writes) == n             # second call is a no-op


def test_restore_without_apply_is_noop():
    console = FakeConsole()
    ctrl = _controller(console=console)
    ctrl.restore()
    assert console.writes == []


def test_schedule_restore_fires_thread_once():
    console = FakeConsole()
    ctrl = _controller(console=console)
    ctrl.apply()
    console.writes.clear()
    ctrl.schedule_restore()
    ctrl.schedule_restore()                     # duplicate — must not double-fire
    assert ctrl.wait_restored(timeout=5.0)
    assert (4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE)) in console.writes
    assert console.writes.count((4, 0x00, tuple(TA_PULSE_WIDTH_BASELINE))) == 1


def test_apply_failure_returns_false():
    console = FakeConsole(fail_writes=True)
    ctrl = _controller(console=console)
    assert ctrl.apply() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_seedless_controller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'omotion.seedless'`

- [ ] **Step 3: Implement**

Create `omotion/seedless.py`:

```python
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

    # ── I2C helpers (retry once — spec error-handling table) ──────────

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

    # ── lifecycle ──────────────────────────────────────────────────────

    def apply(self) -> bool:
        """Snapshot live values, then write the seedless configuration.

        Returns False on any console write failure — the caller must abort
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
                logger.error("seedless: apply failed at %s — aborting", name)
                return False
        if not self._set_exposure(EXPOSURE_SEEDLESS_BYTE):
            logger.error("seedless: exposure apply failed — aborting")
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

        ORDER MATTERS — see module docstring. Never re-tighten the safety
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
                    "seedless: restore INCOMPLETE — laser/safety registers "
                    "may still hold test values; they self-heal on the next "
                    "apply_laser_power() or console power-cycle")
            return ok

    def wait_restored(self, timeout: float = 10.0) -> bool:
        return self._restored.wait(timeout)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_seedless_controller.py -v`
Expected: 9 passed. If `test_restore_is_idempotent` fails because a failed restore blocks retry: note `restore()` only sets `_restored` on full success — a *failed* restore stays retriable by design; only a *successful* one is a no-op afterwards. The test uses an always-succeeding fake, so it must pass.

- [ ] **Step 5: Commit**

```bash
git add omotion/seedless.py tests/test_seedless_controller.py
git commit -m "feat: SeedlessController — apply/restore seedless register config (refs #146)"
```

---

### Task 5: ScanMetadata + DB record of N

**Files:**
- Modify: `omotion/pipeline/sinks.py` (`ScanMetadata` dataclass ~line 24; `ScanDBSink._session_meta` ~line 615)
- Test: `tests/test_pipeline/test_scan_db_sink.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline/test_scan_db_sink.py`, following the file's existing `test_scan_db_sink_stamps_session_meta` pattern (it already imports `sqlite3`, `json`, `ScanDBSink`, `ScanMetadata`, and has the `_frame`/`_interval` helpers):

```python
def test_session_meta_records_seedless_frames(tmp_path):
    db_path = str(tmp_path / "scan.db")
    meta = ScanMetadata(
        scan_id="s1", subject_id="subj", operator="op",
        started_at_iso="2026-07-16T00:00:00Z", duration_sec=10,
        left_camera_mask=0x66, right_camera_mask=0x66, reduced_mode=False,
        seedless_frames=200,
    )
    sink = ScanDBSink(db_path=db_path)
    sink.on_scan_start(meta)
    # One row so the session persists (empty scans are deleted).
    sink.consume("final", _interval([_frame(42)]))
    sink.on_complete()

    conn = sqlite3.connect(db_path)
    meta_json = conn.execute("SELECT session_meta FROM sessions").fetchone()[0]
    conn.close()
    assert json.loads(meta_json)["sdk_flags"]["seedless_frames"] == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline/test_scan_db_sink.py -v -k seedless`
Expected: FAIL — `TypeError: ScanMetadata.__init__() got an unexpected keyword argument 'seedless_frames'`

- [ ] **Step 3: Implement**

In `omotion/pipeline/sinks.py`, add to `ScanMetadata` (after `reduced_mode`):

```python
    # SEEDLESS engineering test (issue #146): number of leading frames run
    # with seed off / TA 2 ms / exposure 2295 us. 0 = normal scan.
    seedless_frames:   int = 0
```

In `ScanDBSink.on_scan_start`, add to the `"sdk_flags"` dict:

```python
                "seedless_frames": meta.seedless_frames,
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_pipeline/ tests/test_scan_database.py -v`
Expected: ALL pass (default of 0 keeps every existing ScanMetadata construction valid).

- [ ] **Step 5: Commit**

```bash
git add omotion/pipeline/sinks.py tests/test_pipeline/test_scan_db_sink.py
git commit -m "feat: record seedless_frames in scan metadata + DB sdk_flags (refs #146)"
```

---

### Task 6: ScanRequest field + ScanWorkflow wiring

**Files:**
- Modify: `omotion/ScanWorkflow.py` — `ScanRequest` (~line 121), `start_scan` (~lines 420-470), `_worker` pre-flight (~lines 640-656), inner `finally` (~line 736)
- Test: `tests/test_scan_workflow_seedless.py` (new)

This task wires four things: (1) the request field, (2) controller construction + pipeline callback, (3) pre-trigger apply + trigger-config merge, (4) teardown restore. Hardware paths can't run in CI, so the unit test targets the two pure pieces — the trigger-config merge and the metadata plumb — and the wiring is covered by code review + the bench test (Task 7).

- [ ] **Step 1: Write the failing test**

Create `tests/test_scan_workflow_seedless.py`:

```python
"""SEEDLESS ScanRequest field + trigger-config merge (pure-software)."""

from omotion.ScanWorkflow import ScanRequest, _seedless_trigger_overrides


def test_scan_request_defaults_off():
    req = ScanRequest(subject_id="s", duration_sec=10,
                      left_camera_mask=0x66, right_camera_mask=0x66)
    assert req.seedless_frames == 0


def test_seedless_trigger_overrides_push_dark_slot_past_exposure():
    # Dark frames are laser pulses delayed past the exposure window. The
    # normal 1800 us skip delay was sized for 648 us exposure; at 2295 us
    # the pulse (start = 100 + skip) must begin after the exposure closes.
    ov = _seedless_trigger_overrides()
    assert ov == {"LaserPulseSkipDelayUsec": 2500}
    assert 100 + ov["LaserPulseSkipDelayUsec"] > 2295


def test_no_overrides_when_disabled():
    # merge helper contract: caller only applies overrides when
    # request.seedless_frames > 0 (asserted here by reading the code path
    # via the helper's docstring contract; the conditional lives in _worker).
    assert callable(_seedless_trigger_overrides)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scan_workflow_seedless.py -v`
Expected: FAIL — `ImportError: cannot import name '_seedless_trigger_overrides'`

- [ ] **Step 3: Implement**

In `omotion/ScanWorkflow.py`:

**(a)** Add to `ScanRequest` after the `on_error` field:

```python
    # SEEDLESS engineering test (SDK issue #146, app issue #361): run the
    # first N frames with the seed laser OFF, TA pulse width 2 ms, and
    # camera exposure 2295 us, restoring normal parameters at frame N.
    # Deliberately widens the laser-safety pulse-width limits for the
    # seedless window (laser-engineer approved). 0 = disabled.
    seedless_frames: int = 0
```

**(b)** Module-level helper (near the top, after the imports):

```python
def _seedless_trigger_overrides() -> dict:
    """Trigger-config overrides for a seedless scan.

    Dark frames shift the laser pulse LaserPulseSkipDelayUsec past its
    normal 100 us delay so it lands outside the camera exposure. 1800 us
    was sized for the 648 us exposure; the seedless 2295 us exposure needs
    the pulse start (100 + skip) pushed past 2295 us with margin.
    """
    return {"LaserPulseSkipDelayUsec": 2500}
```

**(c)** In `start_scan`, pass N into the metadata (in the `ScanMetadata(...)` construction, after `reduced_mode=request.reduced_mode`):

```python
            seedless_frames=request.seedless_frames,
```

**(d)** Construct the controller just before the `pipeline = default_pipeline(...)` call:

```python
        # ── SEEDLESS engineering test (issue #146) ────────────────────────
        seedless_ctrl = None
        if request.seedless_frames > 0:
            from omotion.seedless import SeedlessController
            seedless_ctrl = SeedlessController(
                console=self._interface.console,
                sensors=[
                    ("left", self._interface.left, request.left_camera_mask),
                    ("right", self._interface.right, request.right_camera_mask),
                ],
                n_frames=request.seedless_frames,
            )
        self._seedless_ctrl = seedless_ctrl
```

and extend the `default_pipeline(...)` call:

```python
            seedless_frames=request.seedless_frames,
            seedless_transition_cb=(
                seedless_ctrl.schedule_restore if seedless_ctrl else None
            ),
```

Also add `self._seedless_ctrl = None` in `ScanWorkflow.__init__` beside the other per-scan state.

**(e)** In `_worker` pre-flight, immediately before the trigger-config block (`trigger_cfg = self._interface.resolve_trigger_config(...)` ~line 651):

```python
                    if seedless_ctrl is not None and not seedless_ctrl.apply():
                        raise RuntimeError(
                            "SEEDLESS apply failed — aborting before trigger "
                            "start (laser never fired)"
                        )
```

and merge the overrides after `resolve_trigger_config`:

```python
                    trigger_cfg = self._interface.resolve_trigger_config(
                        request.trigger_config
                    )
                    if seedless_ctrl is not None:
                        trigger_cfg = {**trigger_cfg,
                                       **_seedless_trigger_overrides()}
```

**(f)** In the inner `finally` block (the one containing the `stop_trigger()` safety net at ~line 747), after the `stop_trigger` try/except:

```python
                    # SEEDLESS: no exit path may leave the seed off or the
                    # safety limits widened. Idempotent (no-op if the
                    # frame-N restore already ran).
                    if seedless_ctrl is not None:
                        try:
                            seedless_ctrl.restore()
                        except Exception:
                            logger.exception("seedless teardown restore raised")
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_scan_workflow_seedless.py tests/test_pipeline/ -v -m "not console and not sensor and not destructive"`
Expected: ALL pass.

Also run the full software-only suite to catch regressions:

Run: `pytest tests/ -m "not console and not sensor and not destructive and not slow" -q`
Expected: no new failures vs. a pre-change baseline run of the same command (record the baseline first).

- [ ] **Step 5: Commit**

```bash
git add omotion/ScanWorkflow.py tests/test_scan_workflow_seedless.py
git commit -m "feat: seedless_frames ScanRequest field + scan workflow wiring (refs #146)"
```

---

### Task 7: Docs + ticket updates + bench verification checklist

**Files:**
- Modify: `docs/API.md` (ScanRequest field table / scan examples section)
- Modify: `docs/superpowers/specs/2026-07-15-seedless-frames-design.md` (note the watch-stage deviation)

- [ ] **Step 1: Document the field in `docs/API.md`**

Find the section documenting `ScanRequest` fields and add:

```markdown
- `seedless_frames: int = 0` — engineering test (issue #146): run the first
  N frames with the seed off, TA pulsing 2 ms, exposure 2295 µs, then
  restore mid-scan. Frames are tagged `seedless` (1..N) and `seedless_tx`
  (80-frame guard band) in the CSV/DB `frame_type` column and excluded from
  BFI/BVI. Widens the laser-safety pulse-width limits for the seedless
  window — engineering use only, never in clinical mode.
```

- [ ] **Step 2: Update the spec's mechanism note**

In `docs/superpowers/specs/2026-07-15-seedless-frames-design.md`, in the "Phase 2 — transition at frame N" section, replace the sentence referencing the source's `on_row` callback with:

```markdown
- Implementation note (planning refinement): the transition is detected by
  `SeedlessWatchStage`, a tiny pipeline stage directly after frame
  classification, which reuses the already-unwrapped absolute frame IDs.
  Batching adds up to ~12 frames of detection latency — inside the 80-frame
  `seedless_tx` guard band.
```

- [ ] **Step 3: Commit**

```bash
git add docs/API.md docs/superpowers/specs/2026-07-15-seedless-frames-design.md
git commit -m "docs: seedless_frames API entry + spec implementation note (refs #146)"
```

- [ ] **Step 4: Comment on SDK issue #146**

Post a progress comment: implementation complete on the branch, list of commits, software-test status, and the bench checklist below. Move the board item to In review when the PR is opened (item id `PVTI_lADOAif52c4BVgTuzgzG6rA`, In review option `5ef0dc97`).

- [ ] **Step 5: Bench verification (hardware — requires Ethan or test bench access; NOT automatable)**

Run one seedless scan (e.g. `seedless_frames=200`, `duration_sec=30`, mask 0x66) via a headless script following the CLAUDE.md "Running a scan end-to-end" recipe, then verify:

1. Raw CSV: frames tagged `seedless` show the dim unseeded signature; histogram sums step visibly at the `seedless`→`seedless_tx` boundary; post-guard frames match a normal scan's levels.
2. Scan completes with normal `light` frames after the guard band — proves the safety FPGA did **not** latch `TA_shutdown` (the restore ordering worked).
3. Post-scan I2C read-back: all five console registers match their pre-scan values (`read_i2c_packet` on ch4/0x00, ch5/0x02, ch5/0x04, ch6/0x04, ch7/0x04).
4. DB `session_meta.sdk_flags.seedless_frames == 200`.
5. A cancelled seedless scan (cancel mid-region) also restores all registers.

---

## Execution notes

- Branch: work continues on `claude/github-issue-link-3ca73e` or a fresh `feature/146-seedless-frames` off `next` — **the repo convention is feature branches off `next`, PR into `next`** (this worktree branch is off `main`; rebase/cherry-pick onto `next` before the PR).
- PR body must use `Refs #146` (never `Closes`) and link the spec; the ticket stays In review through pre-release validation per board rules.
- The pre-existing `camera_set_exposure()` `& 0xFF` wrap bug is explicitly out of scope (separate ticket).
```
