#!/usr/bin/env python3
"""Demod-frame characterization harness.

Companion to console-fw ``feature/demod-frames`` and the test plan in
``openmotion-console-fw/docs/superpowers/specs/2026-06-11-demod-characterization-test-plan.md``.

Three subcommands, mapping to the plan's phases:

  sanity    Phase 0 — verify the Seed FPGA implements the modulation
            registers; write distinctive frequency/phase words via
            OW_CTRL_SET_DEMOD and read them back over raw I2C to confirm
            byte order on real hardware.

  collect   Phases 1-5 — run a capture with a given demod config (or with
            modulation held ON continuously for the reference floor) and
            write a per-frame, per-camera moments CSV + a run-metadata JSON.

  analyze   Classify frames by signature (dark / demod / normal), validate
            demod periodicity, and report the headline ratios:
              S = K(demod) / K(normal)          (suppression; want -> 0)
              M = mean(demod) / mean(normal)    (intensity;  want ~ 1)
              F = K(demod) / K(continuous ref)  (floor ratio; want ~ 1,
                                                 needs --reference)
            plus K distributions for demod-neighbor frames (ring-down probe).

Typical bench sequence (static phantom, see test plan):

  python scripts/demod_characterization.py sanity
  python scripts/demod_characterization.py collect --label baseline --duration 60
  python scripts/demod_characterization.py collect --label continuous --duration 60 \
      --freq-word 123456789 --phase-word 0 --continuous
  python scripts/demod_characterization.py collect --label interleave10 --duration 60 \
      --freq-word 123456789 --phase-word 0 --demod-interval 10
  python scripts/demod_characterization.py analyze scan_data/demod-characterization/interleave10 \
      --reference scan_data/demod-characterization/continuous

The ``analyze`` path is pure software (covered by
tests/test_demod_characterization.py); ``sanity`` and ``collect`` need the
bench and have not yet been hardware-validated.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger("demod_characterization")

# Seed FPGA location/registers (Unified Board FPGA Memory Map 700-00010,
# cross-checked against openmotion-seed-fpga src/registers.v)
SEED_MUX = 1
SEED_CHANNEL = 5
SEED_I2C_ADDR = 0x41
SEED_REG_PHASE = 0x00       # 2 bytes, PHASEREG[11:0], LSB first
SEED_REG_DDS_GAIN = 0x02    # 2 bytes, modulation amplitude DAC, 0.064 mA/step (POR: 0!)
SEED_REG_CW_GAIN = 0x04     # 2 bytes, CW current DAC, 0.0688 mA/step (POR: 140 mA)
SEED_REG_DDS_CL = 0x06      # 2 bytes, modulation current limit, 0.081 mA/step (POR: 80 mA)
SEED_REG_CW_CL = 0x08       # 2 bytes, CW current limit (POR: 160 mA)
SEED_REG_FREQ = 0x0A        # 4 bytes, FREQ[27:0], LSB first -> AD9833-class DDS
SEED_REG_STATUS = 0x12
SEED_REG_REVISION = 0x13
SEED_REG_MINOR = 0x14
SEED_REG_MAJOR = 0x15
SEED_REG_ID = 0x16          # expect 1
SEED_REG_STATIC_CTRL = 0x20  # D[0] = arm modulation (FPGA gates it per trigger pulse)
SEED_REG_DYNAMIC_CTRL = 0x22

# Default frequency word: 0x01000000 = 1.5625 MHz at the assumed 25 MHz DDS
# MCLK. Bits [23:16] are zero, so it lands exactly even on Seed FPGA images
# with the reg-0x0C write bug (registers.v <= rev 1.1.0).
DEFAULT_FREQ_WORD = 0x01000000
# Default modulation amplitude: ~20 mA (0.064 mA/step). The FPGA's POR value
# is ZERO - without writing this register, arming modulation does nothing.
DEFAULT_MOD_CURRENT_WORD = 312

_BIN_VALUES = np.arange(1024, dtype=np.float64)
_BIN_VALUES_SQ = _BIN_VALUES ** 2

CSV_HEADERS = ["abs_frame_id", "timestamp_s", "side", "cam",
               "total_counts", "mean_raw", "std_raw"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def hist_moments(hist: np.ndarray) -> tuple[float, float, float]:
    """(total_counts, mean, std) of one 1024-bin histogram.

    Same math as the pipeline's MomentsStage, kept inline so a collect run
    has no pipeline dependencies beyond the source.
    """
    counts = float(hist.sum())
    if counts <= 0:
        return 0.0, float("nan"), float("nan")
    u1 = float(np.dot(hist, _BIN_VALUES) / counts)
    u2 = float(np.dot(hist, _BIN_VALUES_SQ) / counts)
    var = max(u2 - u1 * u1, 0.0)
    return counts, u1, var ** 0.5


# ---------------------------------------------------------------------------
# sanity — Phase 0
# ---------------------------------------------------------------------------

def _wait_connected(motion, *, need_left=False, need_right=False,
                    timeout_s: float = 20.0) -> bool:
    """Connection happens on the hotplug monitor thread after start();
    poll until the devices we need are up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ok = motion.console.is_connected()
        if ok and need_left:
            ok = getattr(motion.left, "is_connected", lambda: False)()
        if ok and need_right:
            ok = getattr(motion.right, "is_connected", lambda: False)()
        if ok:
            return True
        time.sleep(0.5)
    return False


def _read_seed(console, reg: int, n: int) -> bytes | None:
    data, n_read = console.read_i2c_packet(SEED_MUX, SEED_CHANNEL,
                                           SEED_I2C_ADDR, reg, n)
    if data is None or n_read != n:
        return None
    return bytes(data[:n])


def cmd_sanity(args) -> int:
    from omotion import MotionInterface

    motion = MotionInterface(data_dir=None, scan_db_path=None, operator_id="demod-sanity")
    motion.start()
    failures = 0
    try:
        if not _wait_connected(motion):
            print("ERROR: console did not connect", file=sys.stderr)
            return 1
        console = motion.console

        def check(name, ok, detail=""):
            nonlocal failures
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
            if not ok:
                failures += 1

        print("Phase 0 — Seed FPGA register sanity")

        ident = _read_seed(console, SEED_REG_ID, 1)
        check("Seed ID readable", ident is not None, f"got {ident!r}")
        if ident is not None:
            check("Seed ID == 1", ident[0] == 1, f"got {ident[0]}")

        rev = _read_seed(console, SEED_REG_REVISION, 3)
        check("Revision regs readable", rev is not None,
              f"rev/minor/major = {list(rev) if rev else None}")

        # Functional check with a word whose bits [23:16] are zero so it
        # lands exactly on both fixed and reg-0x0C-bug FPGA images.
        freq_word = args.freq_word if args.freq_word is not None else 0x0A00BEEF
        phase_word = args.phase_word if args.phase_word is not None else 0x0ABC
        resp = console.set_demod_config({
            "DemodPulseInterval": 0,
            "ModulationFrequencyWord": freq_word,
            "ModulationPhaseWord": phase_word,
        })
        check("SET_DEMOD accepted", resp is not None, f"resp={resp}")
        if resp is not None:
            check("GET echo: freq word",
                  resp.get("ModulationFrequencyWord") == freq_word, f"resp={resp}")
            check("GET echo: phase word",
                  resp.get("ModulationPhaseWord") == (phase_word & 0x0FFF), f"resp={resp}")

        raw = _read_seed(console, SEED_REG_FREQ, 4)
        expect = bytes((freq_word >> (8 * b)) & 0xFF for b in range(4))
        check("Freq regs 0x0A-0x0D readback (byte order!)", raw == expect,
              f"expect LSB-first {expect.hex()} got {raw.hex() if raw else None}")

        # Probe for the registers.v reg-0x0C write bug: write a word with
        # bits [23:16] set and see whether they survive. The firmware writes
        # bytes in 0x0C,0x0A,0x0B,0x0D order, so on a buggy image the rest of
        # the word still lands and only [23:16] reads back as zero.
        probe = 0x0DDC0FFE
        console.set_demod_config({"ModulationFrequencyWord": probe})
        raw = _read_seed(console, SEED_REG_FREQ, 4)
        if raw is not None:
            got = int.from_bytes(raw, "little")
            if got == probe:
                print("  [INFO] freq reg-0x0C write bug: FIXED on this image")
            elif got == (probe & 0xFF00FFFF):
                print("  [INFO] freq reg-0x0C write bug: PRESENT — restrict to "
                      "frequency words with bits [23:16] == 0")
            else:
                check("Freq bug probe readback recognizable", False,
                      f"wrote {probe:#010x} read {got:#010x}")
        # Restore the functional word
        console.set_demod_config({"ModulationFrequencyWord": freq_word})

        # Report drive/limit DAC words (POR: DDS gain = 0 -> modulation is
        # invisible until collect/--mod-current-word writes it).
        for name, reg, scale in (("DDS gain (mod amplitude)", SEED_REG_DDS_GAIN, 0.064),
                                 ("CW gain", SEED_REG_CW_GAIN, 0.0688),
                                 ("DDS current limit", SEED_REG_DDS_CL, 0.081),
                                 ("CW current limit", SEED_REG_CW_CL, 0.081)):
            raw = _read_seed(console, reg, 2)
            if raw is not None:
                word = int.from_bytes(raw, "little")
                print(f"  [INFO] {name}: word={word} (~{word * scale:.1f} mA)")

        raw = _read_seed(console, SEED_REG_PHASE, 2)
        pw = phase_word & 0x0FFF
        expect = bytes(((pw >> (8 * b)) & 0xFF for b in range(2)))
        check("Phase regs 0x00-0x01 readback", raw == expect,
              f"expect {expect.hex()} got {raw.hex() if raw else None}")

        ok = console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                      SEED_REG_STATIC_CTRL, bytes([0x01, 0x00]))
        raw = _read_seed(console, SEED_REG_STATIC_CTRL, 2)
        check("Static ctrl write ON + readback", ok and raw is not None and raw[0] & 1,
              f"got {raw.hex() if raw else None}")
        ok = console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                      SEED_REG_STATIC_CTRL, bytes([0x00, 0x00]))
        raw = _read_seed(console, SEED_REG_STATIC_CTRL, 2)
        check("Static ctrl write OFF + readback", ok and raw is not None and not (raw[0] & 1),
              f"got {raw.hex() if raw else None}")

        status = _read_seed(console, SEED_REG_STATUS, 1)
        check("Seed STATUS readable", status is not None,
              f"status=0x{status[0]:02X}" if status else "no response")

        print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'} — "
              "if ID/revision NACKed, the bench FPGA image predates the feature; "
              "sync with Henry before continuing (test-plan decision tree).")
    finally:
        motion.stop()
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# collect — Phases 1-5 capture
# ---------------------------------------------------------------------------

def convert_raw_csv(raw_csv: Path, side: int, writer) -> int:
    """Convert one CsvSink raw-histogram CSV into frames.csv rows.

    Raw schema: cam_id, frame_id, timestamp_s, type, 0..1023, temperature,
    sum, tcm, tcl, pdc. Moments are recomputed from the bins so frames.csv
    is self-consistent with the synthetic-test path.
    """
    n = 0
    with open(raw_csv, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        idx = {name: i for i, name in enumerate(header)}
        bin0 = idx["0"]
        for row in r:
            hist = np.asarray(row[bin0:bin0 + 1024], dtype=np.float64)
            counts, mean, std = hist_moments(hist)
            writer.writerow([row[idx["frame_id"]], row[idx["timestamp_s"]],
                             side, row[idx["cam_id"]],
                             int(counts), f"{mean:.4f}", f"{std:.4f}",
                             row[idx["type"]]])
            n += 1
    return n


def cmd_collect(args) -> int:
    """Run a scan via the standard ScanWorkflow and write frames.csv.

    Uses MotionInterface.start_scan so camera bring-up, trigger config and
    start/stop, and raw-CSV storage all follow the production scan path.
    The demod config (and optional continuous-modulation override) is
    applied just before the scan starts.
    """
    from omotion import MotionInterface
    from omotion.ScanWorkflow import ScanRequest, ConfigureRequest

    out_dir = Path(args.out) / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    motion = MotionInterface(data_dir=str(out_dir), scan_db_path=None,
                             operator_id="demod-char")
    motion.start()
    continuous_on = False
    try:
        if not _wait_connected(motion, need_left=args.left_mask != 0,
                               need_right=args.right_mask != 0):
            print("ERROR: console/sensors did not connect", file=sys.stderr)
            return 1
        console = motion.console

        # Camera configuration (FPGA/sensor init) — required once per sensor
        # power-on before start_scan's camera-enable works. Idempotent, so
        # run it every collect rather than tracking power-cycle state.
        cfg_done = threading.Event()
        cfg_result: dict = {}

        def _cfg_complete(res):
            cfg_result["ok"] = getattr(res, "ok", False)
            cfg_result["error"] = getattr(res, "error", "")
            cfg_done.set()

        if not motion.start_configure_camera_sensors(
                ConfigureRequest(left_camera_mask=args.left_mask,
                                 right_camera_mask=args.right_mask),
                on_complete_fn=_cfg_complete):
            print("ERROR: camera configure refused (already running?)", file=sys.stderr)
            return 1
        if not cfg_done.wait(timeout=180.0):
            print("ERROR: camera configure timed out", file=sys.stderr)
            return 1
        if not cfg_result.get("ok"):
            print(f"ERROR: camera configure failed: {cfg_result.get('error')}",
                  file=sys.stderr)
            return 1
        print("camera configure OK")

        # Cold-start requirement: laser driver registers are cleared at
        # power-up; without this the trigger fires but no light is emitted.
        if not motion.apply_laser_power():
            print("ERROR: apply_laser_power failed", file=sys.stderr)
            return 1

        demod_payload = {
            "DemodPulseInterval": 0 if args.continuous else args.demod_interval,
            "ModulationFrequencyWord": args.freq_word,
            "ModulationPhaseWord": args.phase_word,
        }
        demod_cfg = console.set_demod_config(demod_payload)
        print(f"demod config: {demod_cfg}")
        if demod_cfg is None:
            print("ERROR: SET_DEMOD rejected — is the console running the "
                  "feature/demod-frames firmware?", file=sys.stderr)
            return 1

        # Modulation amplitude DAC: POR value is 0 mA — without this write,
        # armed modulation has no effect. Auto-increment write 0x02 then
        # 0x03 latches the value into the DAC on the upper-byte write.
        mc = args.mod_current_word
        if not console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                        SEED_REG_DDS_GAIN,
                                        bytes([mc & 0xFF, (mc >> 8) & 0xFF])):
            print("ERROR: could not set modulation amplitude", file=sys.stderr)
            return 1
        print(f"modulation amplitude word: {mc} (~{mc * 0.064:.1f} mA)")

        if args.continuous:
            # Phase 2 reference: hold modulation ON for the whole run,
            # bypassing the firmware interleave on purpose.
            if not console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                            SEED_REG_STATIC_CTRL, bytes([0x01, 0x00])):
                print("ERROR: could not switch modulation ON", file=sys.stderr)
                return 1
            continuous_on = True

        seed_rev = _read_seed(console, SEED_REG_REVISION, 3)

        req = ScanRequest(
            subject_id=args.label,
            duration_sec=int(args.duration),
            left_camera_mask=args.left_mask,
            right_camera_mask=args.right_mask,
            write_corrected_csv=False,
            raw_save_max_duration_s=float(args.duration) + 30.0,
            trigger_config=({"TriggerFrequencyHz": args.trigger_freq}
                            if args.trigger_freq is not None else None),
        )
        if not motion.start_scan(req):
            print("ERROR: start_scan refused", file=sys.stderr)
            return 1

        probe = {}
        if args.probe:
            # Mid-scan electrical probe over UART (independent of the USB
            # histogram stream): is the arm bit still set, did the amplitude
            # survive, is the trigger reaching the unified board?
            time.sleep(min(15.0, args.duration / 3))

            def _rd(ch, reg, n):
                d, ln = console.read_i2c_packet(SEED_MUX, ch, SEED_I2C_ADDR, reg, n)
                return int.from_bytes(bytes(d[:n]), "little") if d is not None and ln == n else None

            ta_count_0 = _rd(4, 0x10, 4)
            probe["seed_static_ctrl"] = _rd(5, SEED_REG_STATIC_CTRL, 2)
            probe["seed_dds_gain"] = _rd(5, SEED_REG_DDS_GAIN, 2)
            probe["seed_freq_word"] = _rd(5, SEED_REG_FREQ, 4)
            probe["seed_adc_current_0"] = _rd(5, 0x0E, 2)
            time.sleep(2.0)
            ta_count_1 = _rd(4, 0x10, 4)
            probe["seed_adc_current_1"] = _rd(5, 0x0E, 2)
            probe["ta_trigger_count_delta_2s"] = (
                ta_count_1 - ta_count_0
                if ta_count_0 is not None and ta_count_1 is not None else None)
            print(f"mid-scan probe: {probe}")

        deadline = time.monotonic() + args.duration + 120.0
        while motion.scan_workflow.running and time.monotonic() < deadline:
            time.sleep(1.0)
        if motion.scan_workflow.running:
            print("WARNING: scan still running at deadline; cancelling", file=sys.stderr)
            motion.cancel_scan()
        err = getattr(motion.scan_workflow, "last_scan_error", None)
        if err:
            print(f"ERROR: scan reported failure: {err}", file=sys.stderr)
            return 1

        raw_csvs = sorted(out_dir.glob("*_raw.csv"))
        if not raw_csvs:
            print(f"ERROR: no *_raw.csv produced in {out_dir}", file=sys.stderr)
            return 1
        n_rows = 0
        with open(out_dir / "frames.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(CSV_HEADERS + ["fw_type"])
            for raw in raw_csvs:
                side = 0 if "_left_" in raw.name else 1
                n_rows += convert_raw_csv(raw, side, w)

        meta = {
            "label": args.label,
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duration_sec": args.duration,
            "continuous_modulation": args.continuous,
            "demod_config": demod_cfg,
            "mod_current_word": args.mod_current_word,
            "trigger_config": console.get_trigger_json(),
            "seed_fpga_rev": list(seed_rev) if seed_rev else None,
            "raw_csvs": [r.name for r in raw_csvs],
            "mid_scan_probe": probe,
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        print(f"wrote {n_rows} rows -> {out_dir / 'frames.csv'}")
    finally:
        if continuous_on:
            motion.console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                            SEED_REG_STATIC_CTRL, bytes([0x00, 0x00]))
        motion.stop()
    return 0


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

@dataclass
class CamSeries:
    frame_ids: list[int] = field(default_factory=list)
    means: list[float] = field(default_factory=list)
    stds: list[float] = field(default_factory=list)
    fw_types: list[str] = field(default_factory=list)  # "" when not recorded


@dataclass
class CamResult:
    side: int
    cam: int
    n_frames: int
    pedestal: float
    n_dark: int
    n_demod: int
    demod_interval_mode: int | None
    demod_interval_frac: float
    k_normal: float          # median contrast, normal light frames
    k_demod: float           # median contrast, demod frames (nan if none)
    k_neighbor: float        # median contrast, demod±1 frames (nan if none)
    mean_normal: float
    mean_demod: float
    demod_frame_ids: list[int]

    @property
    def S(self) -> float:
        return self.k_demod / self.k_normal if self.k_normal else float("nan")

    @property
    def M(self) -> float:
        return self.mean_demod / self.mean_normal if self.mean_normal else float("nan")


def load_run(run_dir: Path) -> dict[tuple[int, int], CamSeries]:
    series: dict[tuple[int, int], CamSeries] = defaultdict(CamSeries)
    with open(run_dir / "frames.csv", newline="") as f:
        for row in csv.DictReader(f):
            s = series[(int(row["side"]), int(row["cam"]))]
            s.frame_ids.append(int(row["abs_frame_id"]))
            s.means.append(float(row["mean_raw"]))
            s.stds.append(float(row["std_raw"]))
            s.fw_types.append(row.get("fw_type", "") or "")
    return dict(series)


def analyze_cam(side: int, cam: int, s: CamSeries,
                dark_frac: float = 0.5, demod_k_frac: float = 0.5) -> CamResult:
    """Classify one camera's frames by signature and compute population stats.

    dark:   mean below ``dark_frac`` x median(mean)  (laser off)
    demod:  light frame whose contrast K = std/(mean-pedestal) is below
            ``demod_k_frac`` x median K of light frames
    normal: every other light frame, excluding demod neighbors
    """
    fid = np.asarray(s.frame_ids)
    mean = np.asarray(s.means)
    std = np.asarray(s.stds)
    ftype = np.asarray(s.fw_types if len(s.fw_types) == len(s.frame_ids)
                       else [""] * len(s.frame_ids))
    order = np.argsort(fid)
    fid, mean, std, ftype = fid[order], mean[order], std[order], ftype[order]

    if (ftype != "").any():
        # The pipeline's frame classification is authoritative when present:
        # exclude warmup/stale frames entirely; darks by label.
        keep = (ftype == "dark") | (ftype == "light")
        fid, mean, std, ftype = fid[keep], mean[keep], std[keep], ftype[keep]
        dark = ftype == "dark"
    else:
        med_mean = float(np.nanmedian(mean))
        dark = mean < dark_frac * med_mean
    pedestal = float(np.nanmedian(mean[dark])) if dark.any() else 0.0

    light = ~dark & np.isfinite(mean) & np.isfinite(std)
    denom = mean - pedestal
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(light & (denom > 0), std / denom, np.nan)

    k_light_med = float(np.nanmedian(k[light])) if light.any() else float("nan")
    demod = light & (k < demod_k_frac * k_light_med)

    demod_ids = fid[demod]
    diffs = np.diff(demod_ids)
    if diffs.size:
        mode, mode_n = Counter(diffs.tolist()).most_common(1)[0]
        interval_mode, interval_frac = int(mode), mode_n / diffs.size
    else:
        interval_mode, interval_frac = None, 0.0

    # Neighbors: light frames whose id is demod_id ± 1 (ring-up/down probe)
    demod_set = set(demod_ids.tolist())
    neighbor = light & ~demod & np.array(
        [(f - 1 in demod_set) or (f + 1 in demod_set) for f in fid])
    normal = light & ~demod & ~neighbor

    def med(arr, sel):
        return float(np.nanmedian(arr[sel])) if sel.any() else float("nan")

    return CamResult(
        side=side, cam=cam, n_frames=int(fid.size),
        pedestal=pedestal, n_dark=int(dark.sum()), n_demod=int(demod.sum()),
        demod_interval_mode=interval_mode, demod_interval_frac=interval_frac,
        k_normal=med(k, normal), k_demod=med(k, demod), k_neighbor=med(k, neighbor),
        mean_normal=med(mean, normal), mean_demod=med(mean, demod),
        demod_frame_ids=demod_ids.tolist(),
    )


def cmd_analyze(args) -> int:
    run_dir = Path(args.run_dir)
    series = load_run(run_dir)

    ref_k: dict[tuple[int, int], float] = {}
    if args.reference:
        for key, s in load_run(Path(args.reference)).items():
            r = analyze_cam(*key, s, args.dark_frac, args.demod_k_frac)
            # Continuous run: every light frame is modulated -> the "normal"
            # population IS the steady-state floor. Demod classification will
            # find nothing (no contrast bimodality), so k_normal is the floor.
            ref_k[key] = r.k_normal

    results = [analyze_cam(*key, s, args.dark_frac, args.demod_k_frac)
               for key, s in sorted(series.items())]

    lines = [f"# Demod characterization — {run_dir.name}", ""]
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        lines += ["```json", meta_path.read_text().strip(), "```", ""]
    hdr = ("side cam frames dark demod interval(frac)   pedestal "
           "K_norm  K_demod   S      M      K_nbr   F")
    lines += [hdr, "-" * len(hdr)]
    for r in results:
        f_ratio = (r.k_demod / ref_k[(r.side, r.cam)]
                   if (r.side, r.cam) in ref_k and ref_k[(r.side, r.cam)] else float("nan"))
        interval = (f"{r.demod_interval_mode}({r.demod_interval_frac:.2f})"
                    if r.demod_interval_mode is not None else "-")
        lines.append(
            f"{r.side:4d} {r.cam:3d} {r.n_frames:6d} {r.n_dark:4d} {r.n_demod:5d} "
            f"{interval:>14s} {r.pedestal:10.2f} "
            f"{r.k_normal:6.4f} {r.k_demod:8.4f} {r.S:6.3f} {r.M:6.3f} "
            f"{r.k_neighbor:7.4f} {f_ratio:6.3f}")
    lines += [
        "",
        "S = K(demod)/K(normal): want -> 0 (contrast collapse).",
        "M = mean(demod)/mean(normal): want ~1.00 (intensity unchanged).",
        "F = K(demod)/K(continuous reference): ~1 -> ring-up completes within "
        "the firmware's lead time; >> 1 -> single-frame flash is under-modulated.",
        "K_nbr vs K_norm: elevated suppression in demod±1 frames -> ring-down "
        "bleeding into neighbors.",
        "interval(frac): modal spacing of detected demod frames and the "
        "fraction of spacings that match — want the configured interval at ~1.00.",
    ]
    report = "\n".join(lines)
    print(report)
    (run_dir / "report.txt").write_text(report + "\n")

    if args.plot:
        _plot_run(run_dir, series, results, args)
    return 0


def _plot_run(run_dir, series, results, args) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(results)
    fig, axes = plt.subplots(n, 1, figsize=(12, 2.2 * n), sharex=True, squeeze=False)
    for ax, r in zip(axes[:, 0], results):
        s = series[(r.side, r.cam)]
        fid = np.asarray(s.frame_ids)
        mean = np.asarray(s.means)
        std = np.asarray(s.stds)
        denom = mean - r.pedestal
        with np.errstate(divide="ignore", invalid="ignore"):
            k = np.where(denom > 0, std / denom, np.nan)
        ax.plot(fid, k, ".", ms=2, color="0.6", label="K")
        dm = np.isin(fid, r.demod_frame_ids)
        ax.plot(fid[dm], k[dm], "o", ms=4, color="crimson", label="demod")
        ax.set_ylabel(f"s{r.side}c{r.cam} K")
        ax.legend(loc="upper right", fontsize=7)
    axes[-1, 0].set_xlabel("abs frame id")
    out = Path(run_dir) / "contrast_vs_frame.png"
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print(f"plot -> {out}")


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("sanity", help="Phase 0 Seed FPGA register checks")
    ps.add_argument("--freq-word", type=lambda v: int(v, 0), default=None,
                    help="frequency word for the functional check "
                         "(default: distinctive bug-immune test pattern)")
    ps.add_argument("--phase-word", type=lambda v: int(v, 0), default=None)
    ps.set_defaults(fn=cmd_sanity)

    pc = sub.add_parser("collect", help="run a capture, write per-frame moments CSV")
    pc.add_argument("--label", required=True, help="run name (output subdirectory)")
    pc.add_argument("--duration", type=float, default=60.0)
    pc.add_argument("--demod-interval", type=int, default=0,
                    help="every Nth laser cycle is a demod frame; 0 = disabled")
    pc.add_argument("--freq-word", type=lambda v: int(v, 0), default=DEFAULT_FREQ_WORD,
                    help="raw 28-bit DDS word (default 0x01000000 = 1.5625 MHz "
                         "@ 25 MHz MCLK, immune to the reg-0x0C FPGA bug)")
    pc.add_argument("--phase-word", type=lambda v: int(v, 0), default=0)
    pc.add_argument("--mod-current-word", type=lambda v: int(v, 0),
                    default=DEFAULT_MOD_CURRENT_WORD,
                    help="SEED_DDS_GAIN word, 0.064 mA/step (default ~20 mA; "
                         "FPGA POR value is 0 = no modulation; limit POR is 80 mA)")
    pc.add_argument("--continuous", action="store_true",
                    help="hold modulation ON for the whole run (Phase 2 reference)")
    pc.add_argument("--trigger-freq", type=float, default=None,
                    help="override trigger frequency in Hz (Phase 4b sweeps)")
    pc.add_argument("--left-mask", type=lambda v: int(v, 0), default=0x0F,
                    help="left camera bitmask (default 0x0F = 4 cameras)")
    pc.add_argument("--right-mask", type=lambda v: int(v, 0), default=0)
    pc.add_argument("--out", default="scan_data/demod-characterization")
    pc.add_argument("--probe", action="store_true",
                    help="mid-scan register probe over UART (arm bit, gain, "
                         "TA trigger count, seed ADC current)")
    pc.set_defaults(fn=cmd_collect)

    pa = sub.add_parser("analyze", help="classify frames and report S/F/M ratios")
    pa.add_argument("run_dir", help="directory produced by collect")
    pa.add_argument("--reference", default=None,
                    help="continuous-modulation run dir (enables the F ratio)")
    pa.add_argument("--dark-frac", type=float, default=0.5,
                    help="dark if mean < frac * median(mean)")
    pa.add_argument("--demod-k-frac", type=float, default=0.5,
                    help="demod if K < frac * median K of light frames")
    pa.add_argument("--plot", action="store_true",
                    help="write contrast_vs_frame.png (needs matplotlib)")
    pa.set_defaults(fn=cmd_analyze)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
