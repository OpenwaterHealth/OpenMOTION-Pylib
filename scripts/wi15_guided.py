"""WI-00015 operator-guided runner - the full work instruction in one command.

Walks an operator through every phase of WI-00015 (tuning sections 4.1-4.5,
then calibration section 4.6) with explicit prompts at each physical step:
module swaps in the 0 cm fixture, phantom placement, and power-cycle warnings.
Wraps the same phase functions as scripts/wi15_runner.py (omotion.tuning) and
scripts/wi15_calibration.py - no separate logic, just sequencing and prompts.

    python scripts/wi15_guided.py                      # full WI, production window
    python scripts/wi15_guided.py --window 75 125      # dev-rig override
    python scripts/wi15_guided.py --fresh              # discard previous state
    python scripts/wi15_guided.py --skip-calibration   # laser tuning only (4.1-4.5)
    python scripts/wi15_guided.py --skip-tuning        # calibration only (4.6)

The laser fires during measurement, tuning, and calibration phases; the
console mains power is cycled twice (persistence checks). Every prompt must be
answered with 'y' to continue; anything else aborts safely (trigger stopped,
nothing further written).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from types import SimpleNamespace

# Emoji in operator instructions must survive Windows pipes, where Python
# otherwise defaults to the cp1252 locale encoding and dies on first print.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from omotion.tuning import (
    STATE_FILE as TUNE_STATE,
    load_state as tune_state,
    phase_baseline,
    phase_crosscheck,
    phase_finalize,
    phase_tune,
    save_state as save_tune_state,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wi15_calibration as cal  # noqa: E402


def gate(msg: str, simple: str | None = None) -> bool:
    """Operator confirmation. Returns False (= abort) unless 'y'.

    ``simple`` is a short plain-language version of the instruction, emitted
    as an ``@@SIMPLE``-tagged line. Front ends showing a reduced (factory)
    view display the simple line instead of the detailed prompt; it is also
    the string set to translate for non-English operators.
    """
    if simple:
        print(f"@@SIMPLE {simple}")
    try:
        return input(f"\n>>> {msg}\n    [y to continue, anything else aborts] "
                     ).strip().lower() == "y"
    except EOFError:
        return False


def ask_side(msg: str, simple: str | None = None) -> str | None:
    if simple:
        print(f"@@SIMPLE {simple}")
    try:
        s = input(f"\n>>> {msg} [left/right] ").strip().lower()
    except EOFError:
        return None
    return s if s in ("left", "right") else None


def run(step: str, fn, args) -> int:
    print(f"\n{'=' * 62}\n  {step}\n{'=' * 62}")
    return fn(args)


def main() -> int:
    logging.disable(logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", nargs=2, type=float, metavar=("MIN_UJ", "MAX_UJ"),
                    help="SPEC-31 window override (default: production 300 400)")
    ap.add_argument("--fresh", action="store_true",
                    help="discard any previous run state first")
    ap.add_argument("--allow-dim", action="store_true",
                    help="consent to below-threshold calibration writes")
    ap.add_argument("--thresholds-json", default=None,
                    help="calibration thresholds JSON (default: factory)")
    ap.add_argument("--bench-thresholds", action="store_true",
                    help="calibration: disable absolute mean/contrast gates "
                         "(dim dev bench)")
    ap.add_argument("--skip-calibration", action="store_true",
                    help="run only the tuning sections (4.1-4.5)")
    ap.add_argument("--skip-tuning", action="store_true",
                    help="run only the calibration section (4.6)")
    a = ap.parse_args()

    if a.skip_calibration and a.skip_tuning:
        print("nothing to do: both --skip-calibration and --skip-tuning given")
        return 1

    if a.fresh:
        # Only clear the state belonging to the flows this run executes.
        clear = []
        if not a.skip_tuning:
            clear.append(TUNE_STATE)
        if not a.skip_calibration:
            clear.append(cal.STATE_FILE)
        for f in clear:
            if os.path.exists(f):
                os.remove(f)
        print("previous run state cleared")

    print("WI-00015 Device Specific Parameter Tuning - guided run")
    print("The laser will fire during measurements; mains power will be "
          "cycled for persistence checks.")
    bench = ("console powered, both sensor modules connected via USB, "
             "TestApp/bloodflow-app CLOSED")
    if not a.skip_tuning:
        bench = "Ophir meter connected, " + bench
    if not gate(f"Bench ready: {bench}. Continue?",
                simple="✅ Get the machine ready. ⚠️ Laser will be on. Close other programs. Then press Continue ▶️"):
        return 1

    if not a.skip_tuning:
        # --- Section 4.2: baselines --------------------------------------
        first = ask_side("Seat a sensor module in the 0 cm fixture, optics "
                         "up, cable perpendicular with minimal bend (WI "
                         "Figure F). TIP: if you know which module reads "
                         "lower, seat it FIRST - the higher one must be "
                         "seated for the later safety-ADC step, so ending "
                         "on it saves a swap. Which side is seated?",
                         simple="📥 Put ONE sensor in the metal holder, glass side up ⬆️. Then press the button for that sensor: ⬅️ Left or Right ➡️")
        if first is None:
            return 1
        if run(f"Baseline - {first}", phase_baseline,
               SimpleNamespace(side=first, window=a.window)) != 0:
            return 1

        other = "right" if first == "left" else "left"
        if not gate(f"Swap: seat the {other.upper()} module in the fixture "
                    f"(same orientation rules). Ready?",
                    simple=f"🔄 Take the sensor out. Put in the {other.upper()} sensor, glass side up ⬆️. Then press Continue ▶️"):
            return 1
        if run(f"Baseline - {other}", phase_baseline,
               SimpleNamespace(side=other, window=a.window)) != 0:
            return 1

        higher = tune_state().get("higher_side")
        if higher is None:
            print("ABORT: baselines did not produce a higher side")
            return 1

        # --- Tuning + section 4.4 (higher module seated) -----------------
        if higher != other:
            if not gate(f"Swap: seat the {higher.upper()} module (higher "
                        f"power) for tuning and the safety-ADC reads. Ready?",
                        simple=f"🔄 Take the sensor out. Put in the {higher.upper()} sensor, glass side up ⬆️. Then press Continue ▶️"):
                return 1
        if run("Tune + section 4.4", phase_tune,
               SimpleNamespace(seated=higher)) != 0:
            print("Tuning did not converge - NCR per the WI. Stopping.")
            return 1

        # Cross-check (WI steps 21-22) exists only inside the adjustment
        # branches: it re-measures the OTHER module after the shared drive
        # changed. If tuning made no adjustment, that module's baseline was
        # already taken at these exact settings - skip the extra insertion.
        tuned = bool(tune_state().get("phases", {})
                     .get("tune", {}).get("steps"))
        if not tuned:
            print("\nNo tuning adjustment was made - the other module's "
                  "baseline already reflects the final settings; "
                  "cross-check re-measure not required (WI step 18).")
            st = tune_state()
            st.setdefault("notes", []).append(
                "Cross-check (WI steps 21-22) not performed: no tuning "
                "adjustment was made, so both baselines were taken at the "
                "final settings (WI step 18 - both in window).")
            save_tune_state(st)
        else:
            lower = "right" if higher == "left" else "left"
            if not gate(f"Swap: seat the {lower.upper()} module for the "
                        f"cross-check. Ready?",
                        simple=f"🔄 Take the sensor out. Put in the {lower.upper()} sensor, glass side up ⬆️. Then press Continue ▶️"):
                return 1
            cc_rc = run("Cross-check", phase_crosscheck,
                        SimpleNamespace(seated=lower))
            if cc_rc != 0:
                if not gate("Cross-check FAILED its window (NCR per WI "
                            "step 22). Continue anyway (dev bench only)?",
                            simple="❌ The test FAILED. 🧑‍🔧 Ask a technician before you continue."):
                    return 1

        # --- Sections 4.3/4.5: EPROM + power cycle -----------------------
        auto_cycle = bool(os.environ.get("SHELLY_IP_ADDRESS"))
        if not gate("Finalize will write the console EPROM and verify it "
                    "across a mains power cycle"
                    + (" (automatic, 15 s off)" if auto_cycle
                       else " - you will be asked to flip the power switch")
                    + ". Ready?",
                    simple=("⚡ The machine will turn off and on by itself. 🚫✋ Do not touch anything. Press Continue ▶️"
                            if auto_cycle else
                            "⚡ Soon you will turn the machine off and on. Get ready. Press Continue ▶️")):
            return 1
        if run("Finalize (EPROM + power cycle + PDF)", phase_finalize,
               SimpleNamespace()) != 0:
            return 1

    if a.skip_calibration:
        print("\nTuning sections complete (calibration skipped).")
        return 0

    # --- Section 4.6: calibration ----------------------------------------
    cal_first = ask_side("Remove the module from the 0 cm fixture. Place a "
                         "module on the STATIC PHANTOM with the included "
                         "weight (WI Figure H), covers removed. Which side "
                         "is on the phantom?",
                         simple="📥 Put ONE sensor on the white test block ⬜. Put the weight on top. Then press the button for that sensor: ⬅️ Left or Right ➡️")
    if cal_first is None:
        return 1
    if not gate("Confirm: module is on the phantom, weight on top, and the "
                "setup will NOT be touched or bumped during calibration. "
                "The laser will fire. Ready?",
                simple="✅ Check: sensor on the white block ⬜, weight on top. 🚫✋ Do not touch the table after this. Press Continue ▶️"):
        return 1
    ns = SimpleNamespace(side=cal_first, phantom_confirmed=True,
                         two_phantoms=False, allow_dim=a.allow_dim,
                         thresholds_json=a.thresholds_json,
                         bench_thresholds=a.bench_thresholds)
    if run(f"Calibration - {cal_first}", cal.phase_calibrate, ns) != 0:
        return 1

    cal_other = "right" if cal_first == "left" else "left"
    if not gate(f"Swap: place the {cal_other.upper()} module on the phantom "
                f"(weight on, hands off after). Ready?",
                simple=f"🔄 Take the sensor off the block. Put the {cal_other.upper()} sensor on the block ⬜. Weight on top. Then press Continue ▶️"):
        return 1
    ns = SimpleNamespace(side=cal_other, phantom_confirmed=True,
                         two_phantoms=False, allow_dim=a.allow_dim,
                         thresholds_json=a.thresholds_json,
                         bench_thresholds=a.bench_thresholds)
    if run(f"Calibration - {cal_other}", cal.phase_calibrate, ns) != 0:
        return 1

    auto_cycle = bool(os.environ.get("SHELLY_IP_ADDRESS"))
    if not gate("Verify will check persistence across another mains power "
                "cycle"
                + (" (automatic)" if auto_cycle
                   else " - you will be asked to flip the power switch")
                + ". Ready?",
                simple=("⚡ The machine will turn off and on again by itself. 🚫✋ Do not touch anything. Press Continue ▶️"
                        if auto_cycle else
                        "⚡ Soon you will turn the machine off and on again. Get ready. Press Continue ▶️")):
        return 1
    rc = run("Calibration verify (power cycle + PDF)", cal.phase_verify,
             SimpleNamespace())
    if rc == 0:
        print("\nWI-00015 COMPLETE - see the two PDF records in the output "
              "directory. Remove modules and replace their covers "
              "(WI steps 33/35).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
