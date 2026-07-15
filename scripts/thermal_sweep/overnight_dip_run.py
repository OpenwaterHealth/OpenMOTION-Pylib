#!/usr/bin/env python3
"""Overnight power-cycled long-scan orchestrator for the warm-up intensity-dip
investigation.

Wraps bench/drift_scan.py (Keysight illumination + Thorlabs photodiode + 8-camera
streaming) in a repeat loop so the cold-start warm-up dip is witnessed once per
scan across a night.

KEY LESSON (2026-07-14): the dip only appears from a genuinely cold ASSEMBLY, not
just a cold die. A 2 h scan dumps enough heat into the board/heatsink that a
30 min cooldown with the sensor board STILL POWERED leaves the die floor ~40 C
(near equilibrium) and the dip is suppressed (DIP2H_02: 40 C start, no dip). So
cooldowns here power the rig fully OFF via the Shelly plug — the whole assembly
cools, and each scan also gets a fresh cold boot (which independently clears the
TIM5 telemetry-freeze wrap and the "3rd streaming session crashes enable_camera"
hazard). An initial off-period cools the assembly before the first scan.

The Keysight PSU and Thorlabs meter are on separate mains (NOT the Shelly plug),
so they survive the power-off and drift_scan re-discovers them each iteration.

Drop a file named STOP in the data dir to end the loop cleanly after the current
iteration. Analysis (analyze_drift_scan.py + dip tooling) is run separately.
"""
import argparse
import datetime
import subprocess
import sys
import time
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
DRIFT = WORKTREE / "bench" / "drift_scan.py"
SHELLY_DIR = r"C:\Users\openwater\Projects\openmotion-bloodflow-app\tests"
sys.path.insert(0, SHELLY_DIR)
sys.path.insert(0, str(WORKTREE / "bench" / "keysight-psu"))
from shelly import ShellyOutlet  # noqa: E402

SUPPLY_CH, CONTROL_CH = 2, 3  # Keysight channels: illumination supply + control voltage


def log(msg: str) -> None:
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} ORCH {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--scan-minutes", type=float, default=120.0)
    ap.add_argument("--cooldown-minutes", type=float, default=30.0,
                    help="rig-OFF cool-down between scans (assembly cools)")
    ap.add_argument("--initial-cool-minutes", type=float, default=0.0,
                    help="rig-OFF cool-down before the FIRST scan")
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--schedule", default=None,
                    help="comma list of scanmin:coolmin pairs, e.g. '30:55,30:180,120:0'. "
                         "Each pair is one iteration; the cool follows its scan and "
                         "conditions the NEXT scan. Overrides --iters/--scan-minutes/"
                         "--cooldown-minutes.")
    ap.add_argument("--subject", default="COLDDIP")
    ap.add_argument("--control-voltage", type=float, default=2.0)
    ap.add_argument("--shelly-host", default="192.168.1.79")
    ap.add_argument("--enum-wait", type=float, default=60.0)
    ap.add_argument("--start-index", type=int, default=1)
    ap.add_argument("--leave-source-on", action="store_true",
                    help="Keep the Keysight illumination source ON between scans and from "
                         "startup (instead of powering it off each cooldown). Tests whether the "
                         "dip is source-side (a thermally-stable source removes it) or "
                         "camera-side (it persists). The PSU is on separate mains from the "
                         "Shelly rig plug, so it survives rig power-cycles.")
    a = ap.parse_args()

    data_dir = Path(a.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    stop_file = data_dir / "STOP"
    outlet = ShellyOutlet(a.shelly_host)

    def rig(on: bool):
        for attempt in range(1, 4):
            try:
                return outlet.on() if on else outlet.off()
            except Exception as e:
                log(f"shelly {'on' if on else 'off'} attempt {attempt} failed: {e}")
                time.sleep(5)
        log(f"shelly {'on' if on else 'off'} failed 3x")
        return None

    def source_on():
        """Turn on the Keysight illumination (separate mains from the rig plug), then
        release the VISA handle so drift_scan can reconnect. Outputs persist after close."""
        try:
            from keysight_psu import KeysightE36300
            psu = KeysightE36300.connect()
            psu.set_current_limit(SUPPLY_CH, 2.0); psu.set_voltage(SUPPLY_CH, 24.0)
            psu.set_current_limit(CONTROL_CH, 0.5); psu.set_voltage(CONTROL_CH, a.control_voltage)
            psu.set_output(SUPPLY_CH, True); psu.set_output(CONTROL_CH, True)
            psu.close()
            log(f"illumination source ON (24 V supply, {a.control_voltage} V control) — kept on between scans")
        except Exception as e:
            log(f"source_on failed (continuing): {e}")

    if a.leave_source_on:
        source_on()

    if a.schedule:
        plan = []
        for tok in a.schedule.split(","):
            s, c = tok.split(":")
            plan.append((float(s), float(c)))
    else:
        plan = [(a.scan_minutes, a.cooldown_minutes)] * a.iters

    log(f"start: {len(plan)} iters, schedule "
        f"{[f'{s:g}m scan + {c:g}m off' for s, c in plan]} @ {a.control_voltage} V, "
        f"initial cool {a.initial_cool_minutes:.0f} min, shelly {a.shelly_host} -> {data_dir}")

    # Initial assembly cool-down: rig OFF.
    if a.initial_cool_minutes > 0:
        rig(False)
        log(f"initial cool-down {a.initial_cool_minutes:.0f} min (rig OFF)")
        time.sleep(a.initial_cool_minutes * 60)

    last = a.start_index + len(plan) - 1
    for k, (scan_min, cool_min) in enumerate(plan):
        i = a.start_index + k
        if stop_file.exists():
            log(f"STOP file present -> exiting before iteration {i}")
            break
        log(f"=== iteration {i}/{last} ({scan_min:g} min scan, then {cool_min:g} min off) ===")

        rig(True)
        log(f"rig ON (cold boot); waiting {a.enum_wait:.0f}s for USB enumeration")
        time.sleep(a.enum_wait)

        subject = f"{a.subject}_{i:02d}"
        cmd = [sys.executable, "-u", str(DRIFT),
               "--duration-sec", str(scan_min * 60.0),
               "--control-voltage", str(a.control_voltage),
               "--subject-id", subject, "--data-dir", str(data_dir)]
        if a.leave_source_on:
            cmd.append("--leave-source-on")
        log(f"launch drift_scan {subject}: {scan_min:g} min @ {a.control_voltage} V")
        t0 = time.time()
        try:
            rc = subprocess.run(cmd, cwd=str(WORKTREE)).returncode
        except Exception as e:
            rc = -999
            log(f"drift_scan subprocess raised: {e}")
        log(f"drift_scan {subject} exited rc={rc} after {(time.time() - t0) / 60:.1f} min")

        # Cool-down: rig OFF so the whole assembly cools before the next cold start.
        rig(False)
        if i < last and cool_min > 0 and not stop_file.exists():
            log(f"cooldown {cool_min:g} min (rig OFF, assembly cooling)")
            time.sleep(cool_min * 60)

    log("=== orchestrator complete (rig left OFF) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
