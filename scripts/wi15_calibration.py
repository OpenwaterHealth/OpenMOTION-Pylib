"""WI-00015 section 4.6 - BFI/BVI calibration runner (steps 34-37).

Deliberately a SEPARATE flow from the laser-tuning runner
(scripts/wi15_runner.py / omotion.tuning) so tuning and calibration stay
segmented in software; shared bench plumbing (console session, Shelly mains
control, output locations) is imported from omotion.tuning.

    python scripts/wi15_calibration.py calibrate --side left  --phantom-confirmed
    python scripts/wi15_calibration.py calibrate --side right --phantom-confirmed
    python scripts/wi15_calibration.py verify          # step 37: power cycle + EPROM check + PDF
    python scripts/wi15_calibration.py reset-state

Runs the same SDK calibration engine the bloodflow-app's Calibrate button
uses (CalibrationWorkflow via MotionInterface.start_calibration): laser-on
collection scan, per-camera mean/contrast computation, console EEPROM write,
validation scan. Emits the WI step-36 table (side / cam / mean / avg_contrast)
per run and a PDF record.

LASER SAFETY: the calibration scan fires the laser. The module must be on the
static phantom with the included weight (WI Figure H) - never run this with a
module in the 0 cm energy-meter fixture; with permissive thresholds the engine
would write a garbage calibration block over a good one. The
--phantom-confirmed attestation is mandatory for every calibrate invocation.

WI step 35 warning, enforced here: --side both additionally requires
--two-phantoms, because calibrating "both" with only one module properly
placed improperly recalibrates the other module.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import threading
import time

from omotion import CalibrationRequest, CalibrationThresholds
from omotion.MotionInterface import MotionInterface
from omotion.tuning import (
    MAX_UPTIME_AFTER_CYCLE_MS,
    OUT_DIR,
    ConsoleSession,
    console_uptime_ms,
    read_fpga_revisions,
    request_power_cycle,
    sensor_inventory_from_iface,
)

# =========================================================================
# CONFIGURATION
# =========================================================================
# Scan parameters mirror the bloodflow-app's defaults (motion_connector cfg
# keys calibration_scan_duration_sec / calibration_scan_delay_sec /
# max_calibration_time_sec).
CAL_SCAN_DURATION_SEC = 5
CAL_SCAN_DELAY_SEC = 1
CAL_MAX_DURATION_SEC = 600

# Per-camera acceptance thresholds. Defaults are the FACTORY values,
# mirroring the bloodflow-app's live config (config/app_config.json ft_*
# keys, read 2026-08-06): absolute-brightness minimums per camera (corner
# cameras 40, inner 80), contrast 0.25, SPEC-69 BFI/BVI, dark <= 3.0.
#
# The SPEC-69 BFI/BVI gates alone are nearly self-fulfilling right after
# calibration (i_max/c_max are normalized to the just-measured values, so
# the validation scan reads BVI ~5 / BFI ~0 by construction). The
# mean/contrast minimums are the only ABSOLUTE-brightness gates - without
# them, "passed" says nothing about signal level. BFI bounds MUST straddle
# zero: on a static phantom BFI legitimately reads slightly negative.
#
# On a dim dev bench use --bench-thresholds (disables the mean/contrast
# gates, loudly) or --thresholds-json for custom values.
FACTORY_THRESHOLDS = {
    "min_mean_per_camera": [40.0, 80.0, 80.0, 80.0, 80.0, 80.0, 80.0, 40.0],
    "min_contrast_per_camera": [0.25] * 8,
    "min_bfi_per_camera": [-0.5] * 8,   # SPEC-69 BFI Min
    "max_bfi_per_camera": [0.5] * 8,    # SPEC-69 BFI Max
    "min_bvi_per_camera": [4.5] * 8,    # SPEC-69 BVI Min
    "max_bvi_per_camera": [5.5] * 8,    # SPEC-69 BVI Max
    "max_dark_per_camera": [3.0] * 8,
}
BENCH_THRESHOLDS = {
    **FACTORY_THRESHOLDS,
    "min_mean_per_camera": [0.0] * 8,
    "min_contrast_per_camera": [0.0] * 8,
}

READY_TIMEOUT_S = 20.0
STATE_FILE = os.path.join(OUT_DIR, "wi15_cal_state.json")
WI_DOC = "WI-00015 rev 2 (ECO-000270), section 4.6"

# =========================================================================


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"created": dt.datetime.now().isoformat(timespec="seconds"),
            "runs": [], "notes": []}


def save_state(st: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


def build_thresholds(path: str | None, bench: bool) -> tuple[CalibrationThresholds, str]:
    """Resolve thresholds. Returns (thresholds, source-label-for-the-record)."""
    if path:
        data = dict(FACTORY_THRESHOLDS)
        with open(path, "r", encoding="utf-8") as f:
            data.update(json.load(f))
        return CalibrationThresholds(**data), f"custom ({os.path.basename(path)})"
    if bench:
        return (CalibrationThresholds(**BENCH_THRESHOLDS),
                "BENCH: mean/contrast gates DISABLED - passed does not "
                "certify signal level")
    return (CalibrationThresholds(**FACTORY_THRESHOLDS),
            "factory (mean 40/80, contrast 0.25, SPEC-69 BFI/BVI, dark 3.0)")


def phase_calibrate(args) -> int:
    if not args.phantom_confirmed:
        print("REFUSED: pass --phantom-confirmed to attest that the module is on the")
        print("static phantom with the included weight (WI Figure H), protective")
        print("covers removed, and that the setup will not be touched or bumped")
        print("while calibration runs. NEVER run this with a module in the 0 cm")
        print("energy-meter fixture.")
        return 1
    if args.side == "both" and not args.two_phantoms:
        print("REFUSED: --side both additionally requires --two-phantoms (WI step 35:")
        print("calibrating 'both' with one module improperly recalibrates the other).")
        return 1

    st = load_state()
    thresholds, thresholds_label = build_thresholds(
        args.thresholds_json, getattr(args, "bench_thresholds", False))
    print(f"thresholds: {thresholds_label}")
    if getattr(args, "bench_thresholds", False):
        print("*** WARNING: bench mode - a PASSED result does NOT certify "
              "image brightness ***")
    output_dir = os.path.join(OUT_DIR, "calibrations")
    os.makedirs(output_dir, exist_ok=True)

    iface = MotionInterface(data_dir=os.path.join(OUT_DIR, "cal_scans"),
                            operator_id="wi15-calibration")
    iface.start()
    try:
        if not iface.wait_for_ready(console=True, sensors=0,
                                    timeout=READY_TIMEOUT_S):
            print("FAIL: console not ready - close the TestApp/bloodflow-app first")
            return 1
        # Give the sensor side(s) a moment, then require exactly what the
        # chosen target needs.
        deadline = time.time() + READY_TIMEOUT_S
        need_left = args.side in ("left", "both")
        need_right = args.side in ("right", "both")
        while time.time() < deadline:
            _, l_ok, r_ok = iface.is_device_connected()
            if (not need_left or l_ok) and (not need_right or r_ok):
                break
            time.sleep(1.0)
        _, l_ok, r_ok = iface.is_device_connected()
        if (need_left and not l_ok) or (need_right and not r_ok):
            print(f"FAIL: required sensor not connected "
                  f"(left={l_ok}, right={r_ok}, target={args.side})")
            return 1

        # Cold-start prerequisite: after any power cycle the laser-driver
        # registers are cleared. This also applies the tuned EPROM overrides
        # (TA_CURRENT_DRV etc.) written by the tuning flow.
        if not iface.apply_laser_power():
            print("FAIL: apply_laser_power failed")
            return 1

        cfg = iface.console.read_config()
        cfg_data = (cfg.json_data or {}) if cfg else {}
        if "prior_config" not in st:
            st["prior_config"] = cfg_data
        if "inventory" not in st:
            st["inventory"] = sensor_inventory_from_iface(iface)
            st["inventory"]["console"] = {
                "serial": iface.console.read_serial_number(),
                "firmware": iface.console.get_version(),
            }
        laser_point = {k: cfg_data.get(k) for k in
                       ("TA_PULSE_WIDTH", "TA_CURRENT_DRV")}
        print(f"tuned laser point from EPROM: {laser_point}")

        left_mask = 0xFF if need_left else 0x00
        right_mask = 0xFF if need_right else 0x00
        req = CalibrationRequest(
            operator_id="wi15-calibration",
            output_dir=output_dir,
            left_camera_mask=left_mask,
            right_camera_mask=right_mask,
            thresholds=thresholds,
            duration_sec=CAL_SCAN_DURATION_SEC,
            scan_delay_sec=CAL_SCAN_DELAY_SEC,
            max_duration_sec=CAL_MAX_DURATION_SEC,
            notes=f"WI-00015 4.6 automated run, side={args.side}",
        )

        done = threading.Event()
        holder: dict = {}

        def on_complete(result) -> None:
            holder["result"] = result
            done.set()

        def on_progress(stage: str) -> None:
            print(f"  [{dt.datetime.now():%H:%M:%S}] {stage}")

        confirm_fn = None
        if args.allow_dim:
            def confirm_fn(rows) -> bool:
                print("  below-threshold gate fired; --allow-dim consents to write")
                return True

        print(f"\n*** CALIBRATION STARTING (side={args.side}, laser will fire; "
              f"do not touch the setup) ***")
        if not iface.start_calibration(req, on_complete_fn=on_complete,
                                       on_progress_fn=on_progress,
                                       on_confirm_fn=confirm_fn):
            print("FAIL: start_calibration refused (already running?)")
            return 1
        if not done.wait(CAL_MAX_DURATION_SEC + 60):
            print("FAIL: calibration did not complete within the watchdog window")
            iface.cancel_calibration()
            return 1

        r = holder["result"]
        outcome = getattr(r.outcome, "value", str(r.outcome))
        print(f"\noutcome: {outcome}"
              + (f"  error: {r.error}" if r.error else ""))
        rows_rec = []
        if r.rows:
            print(f"  {'side':<6} {'cam':>3} {'mean':>10} {'avg_contrast':>13}")
            for row in r.rows:
                print(f"  {row.side:<6} {row.cam_id:>3} {row.mean:>10.3f} "
                      f"{row.avg_contrast:>13.4f}")
                rows_rec.append({"side": row.side, "cam": row.cam_id,
                                 "mean": row.mean,
                                 "avg_contrast": row.avg_contrast,
                                 "bfi": row.bfi, "bvi": row.bvi,
                                 "dark": row.dark})
        if r.csv_path:
            print(f"  csv: {r.csv_path}")

        # Snapshot the EPROM calibration block right after the write so the
        # verify phase can prove persistence across a power cycle.
        cfg_after = iface.console.read_config()
        cal_block = ((cfg_after.json_data or {}).get("calibration")
                     if cfg_after else None)

        st["runs"].append({
            "when": dt.datetime.now().isoformat(timespec="seconds"),
            "side": args.side,
            "outcome": outcome,
            "error": r.error,
            "csv_path": r.csv_path,
            "json_path": r.json_path,
            "rows": rows_rec,
            "laser_point": laser_point,
            "thresholds_source": thresholds_label,
            "eprom_calibration_after": cal_block,
        })
        save_state(st)
        passed = outcome == "passed"
        print(f"\ncalibration {'PASSED' if passed else 'DID NOT PASS'} for "
              f"side={args.side}; state saved")
        if not passed:
            return 1
        print("NEXT: calibrate the other side (swap phantom), or run: verify")
        return 0
    finally:
        try:
            iface.stop()
        except Exception:
            pass


def phase_verify(args) -> int:
    """WI step 37: power cycle, confirm the full EPROM config (tuning keys +
    calibration block) survives, emit the PDF record."""
    st = load_state()
    if not st.get("runs"):
        print("FAIL: no calibration runs recorded - run calibrate first")
        return 1

    session = ConsoleSession()
    try:
        cfg = session.console.read_config()
        before = (cfg.json_data or {}) if cfg else {}
        if not before.get("calibration"):
            print("FAIL: no calibration block present in EPROM")
            return 1

        session.close()
        cycle_desc = request_power_cycle()
        print("waiting for console ...")
        deadline = time.time() + 90
        session = None
        while time.time() < deadline:
            time.sleep(5)
            try:
                session = ConsoleSession()
                break
            except Exception:
                session = None
        if session is None:
            print("FAIL: console did not come back after power cycle")
            return 1

        uptime = console_uptime_ms(session)
        rebooted = uptime is not None and uptime < MAX_UPTIME_AFTER_CYCLE_MS
        if not rebooted:
            print(f"FAIL: console uptime "
                  f"{'unknown' if uptime is None else f'{uptime/1000:.0f}s'}"
                  f" - power cycle not confirmed; persistence unproven")
        else:
            print(f"reboot confirmed (uptime {uptime/1000:.0f}s)")

        if "console_fpga" not in st.get("inventory", {}):
            st.setdefault("inventory", {})["console_fpga"] = \
                read_fpga_revisions(session)

        cfg2 = session.console.read_config()
        after = (cfg2.json_data or {}) if cfg2 else {}
        verify = []
        for key in sorted(set(before) | set(after)):
            ok = before.get(key) == after.get(key)
            shown = "(block match)" if isinstance(before.get(key), (dict, list)) \
                else str(before.get(key))
            verify.append([key, shown, ok])
            print(f"  {key:<20} {'OK' if ok else 'MISMATCH'}")
        all_ok = all(v[2] for v in verify)

        st["verify"] = {
            "when": dt.datetime.now().isoformat(timespec="seconds"),
            "power_cycle": cycle_desc,
            "uptime_after_ms": uptime,
            "reboot_confirmed": rebooted,
            "keys": verify,
            "all_ok": all_ok and rebooted,
            "config_after": after,
        }
        save_state(st)

        out = os.path.join(
            OUT_DIR, f"WI-00015-46_{dt.datetime.now():%Y%m%d_%H%M%S}_calibration.pdf")
        build_pdf(st, out)
        verified = all_ok and rebooted
        print("\npersistence: " + ("VERIFIED" if verified else "FAILED")
              + ("" if rebooted else " (reboot not confirmed)"))
        print(f"report: {out}")
        return 0 if verified else 1
    finally:
        if session is not None:
            session.close()


def build_pdf(st: dict, path: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Preformatted, PageBreak)

    ss = getSampleStyleSheet()
    h1, h2, body = ss["Heading1"], ss["Heading2"], ss["BodyText"]
    mono = ParagraphStyle("mono", parent=ss["Code"], fontSize=7, leading=8.5)
    GRID = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ])

    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=0.75 * inch,
                            rightMargin=0.75 * inch, topMargin=0.75 * inch,
                            bottomMargin=0.75 * inch,
                            title="WI-00015 section 4.6 calibration record")
    el = [Paragraph("Open-Motion BFI/BVI Calibration", h1),
          Paragraph(f"Automated calibration record - {WI_DOC}.", body),
          Spacer(1, 10)]

    inv = st.get("inventory") or {}
    if inv:
        el.append(Paragraph("Device inventory (WI step 8)", h2))
        rows = [["Item", "Value"], ["SDK version", inv.get("sdk_version", "?")]]
        con = inv.get("console") or {}
        if con:
            rows.append(["Console",
                         f"s/n {con.get('serial')} - fw {con.get('firmware')}"])
        for side in ("left", "right"):
            s = inv.get(side) or {}
            if s.get("connected"):
                rows.append([f"{side.capitalize()} sensor module",
                             f"s/n {s.get('serial')} - fw {s.get('firmware')} "
                             f"- hw {s.get('hardware_id')}"])
            else:
                rows.append([f"{side.capitalize()} sensor module",
                             "not connected at inventory time"])
        for label, ver in (inv.get("console_fpga") or {}).items():
            rows.append([f"Console FPGA - {label}", ver])
        t = Table(rows, colWidths=(2.3 * inch, 4.4 * inch), hAlign="LEFT")
        t.setStyle(GRID)
        el.append(t)
        el.append(Spacer(1, 10))

    for i, run in enumerate(st.get("runs", []), start=1):
        el.append(Paragraph(
            f"{i}. Calibration run - side {run['side']} ({run['when']})", h2))
        el.append(Paragraph(
            f"Outcome: <b>{run['outcome'].upper()}</b>"
            + (f" - {run['error']}" if run.get("error") else "")
            + f". Laser point (EPROM): {run.get('laser_point')}. "
            f"Thresholds: {run.get('thresholds_source')}.", body))
        if run.get("rows"):
            rows = [["side", "cam", "mean", "avg_contrast", "BFI", "BVI", "dark"]]
            for r in run["rows"]:
                rows.append([r["side"], str(r["cam"]), f"{r['mean']:.3f}",
                             f"{r['avg_contrast']:.4f}", f"{r['bfi']:.3f}",
                             f"{r['bvi']:.3f}", f"{r['dark']:.2f}"])
            t = Table(rows, colWidths=(0.7 * inch, 0.5 * inch, 1.0 * inch,
                                       1.2 * inch, 0.9 * inch, 0.9 * inch,
                                       0.9 * inch), hAlign="LEFT")
            t.setStyle(GRID)
            el.append(t)
        if run.get("csv_path"):
            el.append(Paragraph(f"CSV: {run['csv_path']}", body))
        el.append(Spacer(1, 10))

    ver = st.get("verify")
    if ver:
        el.append(Paragraph("Persistence verification (WI step 37)", h2))
        up = ver.get("uptime_after_ms")
        reboot_txt = ("reboot confirmed by firmware uptime "
                      f"({up/1000:.0f} s)" if ver.get("reboot_confirmed")
                      else "REBOOT NOT CONFIRMED - persistence unproven")
        el.append(Paragraph(
            f"Power cycle: {ver['power_cycle']}; {reboot_txt}.", body))
        rows = [["Key", "Value before cycle", "Result"]]
        for key, shown, ok in ver["keys"]:
            rows.append([key, shown, "OK" if ok else "MISMATCH"])
        t = Table(rows, colWidths=(2.0 * inch, 3.4 * inch, 1.0 * inch),
                  hAlign="LEFT")
        t.setStyle(GRID)
        el.append(t)
        el.append(Spacer(1, 6))
        el.append(Paragraph(
            f"<b>Persistence: {'VERIFIED' if ver['all_ok'] else 'FAILED'}</b>",
            body))

    el.append(PageBreak())
    el.append(Paragraph("Appendix A. Console User Configuration prior to "
                        "calibration", h2))
    text = json.dumps(st.get("prior_config"), indent=2) \
        if st.get("prior_config") is not None else "(not captured)"
    for line in text.splitlines():
        el.append(Preformatted(line if line.strip() else " ", mono))
    doc.build(el)


def main() -> int:
    logging.disable(logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("calibrate")
    p.add_argument("--side", required=True, choices=["left", "right", "both"])
    p.add_argument("--phantom-confirmed", action="store_true",
                   help="attest the module is on the static phantom with weight "
                        "(WI Figure H) and will not be touched")
    p.add_argument("--two-phantoms", action="store_true",
                   help="required with --side both: both modules are each on "
                        "their own phantom")
    p.add_argument("--allow-dim", action="store_true",
                   help="consent to write a below-threshold calibration "
                        "(dim laser) if the pre-write gate fires")
    p.add_argument("--bench-thresholds", action="store_true",
                   help="disable the absolute mean/contrast gates (dim dev "
                        "bench). PASSED then does NOT certify signal level.")
    p.add_argument("--thresholds-json", default=None,
                   help="JSON file of CalibrationThresholds overrides; "
                        "default: factory values")
    sub.add_parser("verify")
    sub.add_parser("reset-state")
    args = ap.parse_args()

    if args.cmd == "reset-state":
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("state cleared")
        return 0
    return {"calibrate": phase_calibrate, "verify": phase_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
