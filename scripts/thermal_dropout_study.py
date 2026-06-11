#!/usr/bin/env python3
"""thermal_dropout_study.py — overnight camera thermal-dropout characterization.

Research question: when cameras (esp. 6/7) drop out under thermal load in
8-camera mode (the camera chip's power regulator gives out), how long must
the system stay powered OFF before the camera is usable again?

Design (agreed with Ethan, 2026-06-10):
  - Heat phase: full 8-camera scan, laser on, sensor fans OFF, until a
    camera drops out (per-camera frame watchdog on the "raw" pipeline
    channel) plus a soak, then stop.
  - Power off the WHOLE system (console + both sensors) via the Shelly
    outlet for a chosen off-time T, power back on, bring the system up
    and test which previously-dead cameras produce data again.
    "Usable again" == the camera actually outputs histogram frames.
  - Each recovery scan doubles as the next trial's heat phase.
  - T is swept adaptively (coarse ladder, then bisection on the boundary).

Architecture: the `campaign` mode (parent) owns the Shelly outlet and the
off-time schedule; each power-on session runs as a `cycle` subprocess so
USB-stack state can never rot across power cycles. The cycle process does
ALL hardware work for one session and writes a verdict JSON.

Modes:
    python scripts/thermal_dropout_study.py campaign --out labnotes/...id/data \
        --until 09:15
    python scripts/thermal_dropout_study.py cycle --out <dir> --cycle-index 0 \
        --off-time-before -1 [--fans off] [--prev-dropped left:6,left:7]
    python scripts/thermal_dropout_study.py restore     # fans on, idle state

The campaign reads <out>/control.json each loop iteration if present:
    {"stop": true}                      -> finish after current cycle
    {"next_off_times": [45, 90]}       -> overrides the adaptive scheduler
    {"max_heat_s": 1200, "post_dropout_soak_s": 240}   -> param overrides
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _REPO)  # this worktree's omotion, ahead of any PYTHONPATH copy

SHELLY_DRIVER_DIR = r"C:\Users\ethan\Projects\openmotion-bloodflow-app\tests"

logger = logging.getLogger("thermal_study")

SIDES = ("left", "right")
ALL_CAMS = list(range(8))

# ── defaults (overridable via CLI / control.json) ──────────────────────────
DEFAULTS = {
    "max_heat_s": 1500.0,          # give up waiting for a dropout after this
    "post_dropout_soak_s": 180.0,  # keep heating this long after 1st dropout
    "recovery_window_s": 120.0,    # camera must emit a frame within this after trigger
    "dropout_silence_s": 3.0,      # no frames for this long while scanning = dropped
    "boot_wait_s": 20.0,           # shelly-on -> first connection attempt
    "connect_timeout_s": 180.0,    # total budget to get console+sensors connected
    "cycle_timeout_s": 3600.0,     # hard kill for a wedged cycle subprocess
    "off_ladder": [30, 1800, 300, 900, 120, 600, 60, 2400],
    "min_off_s": 10.0,
    "max_off_s": 3600.0,
}


def _utcnow_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _append_jsonl(path: str, obj: dict) -> None:
    obj = dict(obj)
    obj.setdefault("t", _utcnow_iso())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


# ═══════════════════════════════════════════════════════════════ cycle mode
# Everything below `run_cycle` touches hardware and runs in the subprocess.

class WatchdogSink:
    """Pipeline sink on the "raw" channel: per-(side, cam) liveness tracker.

    "raw" is tee'd BEFORE TimestampRepairStage, so it never contains the
    synthetic nan-filled rows that would make a dead camera look alive.
    Rows with frame_type == "stale" (pre-scan USB leftovers) are skipped.
    """

    channels = {"raw"}
    critical = False

    def __init__(self):
        self.lock = threading.Lock()
        self.stats: dict[tuple[str, int], dict] = {}
        self._side_names = {0: "left", 1: "right"}

    def on_scan_start(self, meta):  # noqa: D401
        pass

    def consume(self, channel, batch):
        now = time.monotonic()
        with self.lock:
            for i, side_idx, cam_id, ftype in batch.iter_rows(exclude={"stale"}):
                side = self._side_names.get(side_idx, str(side_idx))
                key = (side, cam_id)
                st = self.stats.get(key)
                if st is None:
                    st = {"first": now, "last": now, "frames": 0,
                          "temp": None, "temp_max": None}
                    self.stats[key] = st
                st["last"] = now
                st["frames"] += 1
                try:
                    t_c = float(batch.temperature_c[i, side_idx, cam_id])
                    if t_c == t_c:  # not NaN
                        st["temp"] = t_c
                        if st["temp_max"] is None or t_c > st["temp_max"]:
                            st["temp_max"] = t_c
                except Exception:
                    pass

    def on_complete(self):
        pass

    def snapshot(self) -> dict[tuple[str, int], dict]:
        with self.lock:
            return {k: dict(v) for k, v in self.stats.items()}


def _setup_cycle_logging(out_dir: str) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(out_dir, "cycle.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def _connect(connect_timeout_s: float):
    """Bring up MotionInterface; retry until console + both sensors connect
    or the budget runs out. Returns (iface, connected_dict)."""
    from omotion import MotionInterface

    deadline = time.monotonic() + connect_timeout_s
    attempt = 0
    iface = None
    while True:
        attempt += 1
        if iface is None:
            iface = MotionInterface()
            iface.start(wait=True, wait_timeout=5.0)
        con, left, right = iface.is_device_connected()
        logger.info("connect attempt %d: console=%s left=%s right=%s",
                    attempt, con, left, right)
        if con and left and right:
            return iface, {"console": True, "left": True, "right": True}
        if time.monotonic() > deadline:
            logger.error("connect budget exhausted; proceeding with partial set")
            return iface, {"console": con, "left": left, "right": right}
        time.sleep(3.0)
        # Halfway through the budget, tear down and rebuild the interface in
        # case enumeration happened mid-discovery and the handles are stuck.
        if time.monotonic() > deadline - connect_timeout_s / 2 and attempt % 10 == 0:
            logger.info("rebuilding MotionInterface (stuck discovery?)")
            try:
                iface.stop()
            except Exception:
                logger.exception("iface.stop() during rebuild")
            iface = None


def _sensor_handles(iface, connected: dict) -> list[tuple[str, object]]:
    out = []
    for side in SIDES:
        if connected.get(side):
            out.append((side, getattr(iface, side)))
    return out


def _enable_debug_printf(sensor, side: str) -> int:
    from omotion.config import DEBUG_FLAG_USB_PRINTF
    try:
        cur = sensor.get_debug_flags()
        want = cur | DEBUG_FLAG_USB_PRINTF
        ok = sensor.set_debug_flags(want)
        after = sensor.get_debug_flags()
        logger.info("%s debug flags: 0x%02X -> 0x%02X (set ok=%s)", side, cur, after, ok)
        return after
    except Exception:
        logger.exception("%s: failed to set debug flags", side)
        return -1


def _configure_cameras_isolated(sensor, side: str) -> dict[int, dict]:
    """Per-camera configure with error isolation (the stock workflow aborts a
    whole side at the first dead camera, which would corrupt the verdict for
    the cameras after it). Mirrors ScanWorkflow._configure_side's primitives.
    """
    results: dict[int, dict] = {
        cam: {"power": None, "ready": None, "fpga": None, "registers": None,
              "status_after": None, "error": None}
        for cam in ALL_CAMS
    }
    try:
        ok = sensor.enable_camera_power(0xFF)
        logger.info("%s: enable_camera_power(0xFF) -> %s", side, ok)
        if not ok:
            time.sleep(1.0)
            ok = sensor.enable_camera_power(0xFF)
            logger.info("%s: enable_camera_power retry -> %s", side, ok)
        time.sleep(0.5)
        power = sensor.get_camera_power_status()
        logger.info("%s: camera power status: %s", side, power)
        for cam in ALL_CAMS:
            results[cam]["power"] = bool(power[cam]) if power and len(power) == 8 else None
    except Exception as e:
        logger.exception("%s: camera power-on failed", side)
        for cam in ALL_CAMS:
            results[cam]["error"] = f"power: {e}"
        return results

    for cam in ALL_CAMS:
        r = results[cam]
        mask = 1 << cam
        try:
            status_map = sensor.get_camera_status(mask)
            status = status_map.get(cam) if status_map else None
            ready = bool(status & 0x01) if status is not None else False
            if not ready:
                # one quick re-check; dead-regulator cameras stay not-READY
                time.sleep(2.0)
                status_map = sensor.get_camera_status(mask)
                status = status_map.get(cam) if status_map else None
                ready = bool(status & 0x01) if status is not None else False
            r["ready"] = ready
            if not ready:
                r["error"] = f"not READY (status={status})"
                logger.warning("%s cam%d: NOT READY (status=%s) — skipping", side, cam, status)
                continue

            t0 = time.monotonic()
            ok = sensor.program_fpga(camera_position=mask, manual_process=False)
            r["fpga"] = ok
            logger.info("%s cam%d: program_fpga -> %s (%.1fs)",
                        side, cam, ok, time.monotonic() - t0)
            if not ok:
                r["error"] = "program_fpga failed"
                continue

            time.sleep(0.1)
            ok = sensor.camera_configure_registers(camera_position=mask)
            r["registers"] = ok
            logger.info("%s cam%d: configure_registers -> %s", side, cam, ok)
            if not ok:
                r["error"] = "camera_configure_registers failed"
                continue

            status_map = sensor.get_camera_status(mask)
            r["status_after"] = status_map.get(cam) if status_map else None
        except Exception as e:
            logger.exception("%s cam%d: configure raised", side, cam)
            r["error"] = repr(e)
    return results


def _read_imu_temps(handles) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for side, sensor in handles:
        try:
            out[side] = round(float(sensor.imu_get_temperature()), 2)
        except Exception:
            logger.exception("%s: imu_get_temperature failed", side)
            out[side] = None
    return out


def _shelly_power_w() -> float | None:
    """Read live power draw from the Shelly (read-only, safe concurrently)."""
    try:
        import requests
        host = os.environ.get("SHELLY_IP_ADDRESS")
        if not host:
            return None
        r = requests.get(f"http://{host}/rpc/Switch.GetStatus?id=0", timeout=2)
        return float(r.json().get("apower"))
    except Exception:
        return None


def run_cycle(args) -> int:
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    _setup_cycle_logging(out_dir)
    params = {k: getattr(args, k) for k in
              ("max_heat_s", "post_dropout_soak_s", "recovery_window_s",
               "dropout_silence_s", "connect_timeout_s")}
    prev_dropped = set()
    if args.prev_dropped:
        prev_dropped = {tuple(p.split(":")) for p in args.prev_dropped.split(",") if p}
        prev_dropped = {(s, int(c)) for s, c in prev_dropped}

    verdict: dict = {
        "cycle": args.cycle_index,
        "off_time_before_s": args.off_time_before if args.off_time_before >= 0 else None,
        "started_iso": _utcnow_iso(),
        "params": params,
        "prev_dropped": sorted(f"{s}:{c}" for s, c in prev_dropped),
        "fans": args.fans,
        "connected": None,
        "identity": {},
        "imu_temp_at_start_c": {},
        "laser_ok": None,
        "configure": {},
        "scan": {},
        "cameras": {},
        "imu_temp_at_end_c": {},
        "end_reason": None,
        "error": None,
    }

    def _write_verdict():
        with open(os.path.join(out_dir, "verdict.json"), "w", encoding="utf-8") as f:
            json.dump(verdict, f, indent=2)

    iface = None
    scanning = False
    try:
        # ── 1. connect ────────────────────────────────────────────────
        iface, connected = _connect(params["connect_timeout_s"])
        verdict["connected"] = connected
        if not connected["console"]:
            verdict["end_reason"] = "bringup_failed"
            verdict["error"] = "console never connected"
            _write_verdict()
            return 2
        handles = _sensor_handles(iface, connected)
        if not handles:
            verdict["end_reason"] = "bringup_failed"
            verdict["error"] = "no sensors connected"
            _write_verdict()
            return 2

        # ── 2. identify ───────────────────────────────────────────────
        ident: dict = {}
        try:
            ident["console"] = {"fw": iface.console.get_version(),
                                "hwid": iface.console.get_hardware_id()}
        except Exception:
            logger.exception("console identity read failed")
        for side, sensor in handles:
            try:
                ident[side] = {"fw": sensor.get_version(),
                               "hwid": sensor.get_hardware_id()}
            except Exception:
                logger.exception("%s identity read failed", side)
        verdict["identity"] = ident
        logger.info("identity: %s", json.dumps(ident))

        # ── 3. debug printf + fans + module temps ────────────────────
        for side, sensor in handles:
            _enable_debug_printf(sensor, side)
        fans_on = args.fans == "on"
        for side, sensor in handles:
            try:
                ok = sensor.set_fan_control(fans_on)
                st = sensor.get_fan_control_status()
                logger.info("%s: fan -> %s (set ok=%s, readback=%s)",
                            side, "ON" if fans_on else "OFF", ok, st)
            except Exception:
                logger.exception("%s: fan control failed", side)
        verdict["imu_temp_at_start_c"] = _read_imu_temps(handles)
        logger.info("IMU temps at start: %s", verdict["imu_temp_at_start_c"])

        # ── 4. laser cold-start config ────────────────────────────────
        laser_ok = False
        for attempt in range(3):
            try:
                laser_ok = bool(iface.apply_laser_power())
            except Exception:
                logger.exception("apply_laser_power raised (attempt %d)", attempt + 1)
            if laser_ok:
                break
            time.sleep(2.0)
        verdict["laser_ok"] = laser_ok
        logger.info("apply_laser_power -> %s", laser_ok)

        # ── 5. per-camera configure with error isolation ──────────────
        cfg_results: dict[str, dict[int, dict]] = {}
        cfg_threads = []
        cfg_lock = threading.Lock()

        def _cfg(side, sensor):
            res = _configure_cameras_isolated(sensor, side)
            with cfg_lock:
                cfg_results[side] = res

        for side, sensor in handles:
            th = threading.Thread(target=_cfg, args=(side, sensor), daemon=True)
            th.start()
            cfg_threads.append(th)
        for th in cfg_threads:
            th.join(timeout=900)
        verdict["configure"] = {
            side: {str(cam): res for cam, res in side_res.items()}
            for side, side_res in cfg_results.items()
        }

        masks = {}
        for side, _ in handles:
            res = cfg_results.get(side, {})
            mask = 0
            for cam in ALL_CAMS:
                r = res.get(cam, {})
                if r.get("registers"):
                    mask |= 1 << cam
            masks[side] = mask
            logger.info("%s: configured-OK mask = 0x%02X", side, mask)

        if not any(masks.values()):
            verdict["end_reason"] = "no_cameras_configured"
            _finish_cameras(verdict, cfg_results, {}, prev_dropped, params, None)
            _write_verdict()
            return 0

        # ── 6. scan (heat phase / recovery test) ──────────────────────
        from omotion.ScanWorkflow import ScanRequest

        watchdog = WatchdogSink()
        request = ScanRequest(
            subject_id=f"thermal_c{args.cycle_index:03d}",
            duration_sec=int(params["max_heat_s"] + 900),
            left_camera_mask=masks.get("left", 0),
            right_camera_mask=masks.get("right", 0),
            write_corrected_csv=False,
            write_telemetry_csv=False,
            skip_default_storage=True,
            sinks=[watchdog],
        )
        started = iface.start_scan(request)
        logger.info("start_scan -> %s (left=0x%02X right=0x%02X)",
                    started, request.left_camera_mask, request.right_camera_mask)
        if not started:
            verdict["end_reason"] = "scan_refused"
            verdict["error"] = str(iface.scan_workflow.last_scan_error)
            _finish_cameras(verdict, cfg_results, {}, prev_dropped, params, None)
            _write_verdict()
            return 2
        scanning = True
        t_trigger = time.monotonic()
        verdict["scan"]["trigger_iso"] = _utcnow_iso()

        # 1 Hz observation loop
        samples_path = os.path.join(out_dir, "samples.csv")
        with open(samples_path, "w", encoding="utf-8") as f:
            f.write("t_s,side,cam,frames,silent_s,temp_c,temp_max_c\n")

        active = [(side, cam) for side in masks
                  for cam in ALL_CAMS if masks.get(side, 0) & (1 << cam)]
        dropouts: dict[tuple[str, int], dict] = {}
        first_dropout_t: float | None = None
        end_reason = None
        tick = 0

        while True:
            time.sleep(1.0)
            tick += 1
            now = time.monotonic()
            t_s = now - t_trigger
            snap = watchdog.snapshot()

            for key in active:
                side, cam = key
                st = snap.get(key)
                if st is None:
                    # never produced a frame: judged at recovery_window
                    if t_s > params["recovery_window_s"] and key not in dropouts:
                        dropouts[key] = {"kind": "never_streamed", "t_s": None}
                        logger.warning("DROPOUT-VERDICT %s cam%d: never streamed "
                                       "within %.0fs", side, cam,
                                       params["recovery_window_s"])
                    continue
                silent = now - st["last"]
                if silent > params["dropout_silence_s"] and key not in dropouts:
                    dropouts[key] = {
                        "kind": "dropout",
                        "t_s": round(t_s - silent, 1),
                        "temp_at_drop_c": st["temp"],
                        "frames": st["frames"],
                    }
                    if first_dropout_t is None:
                        first_dropout_t = now
                    logger.warning(
                        "DROPOUT %s cam%d at t=%.1fs (last temp %.1f C, %d frames)",
                        side, cam, t_s - silent,
                        st["temp"] if st["temp"] is not None else float("nan"),
                        st["frames"])

            if tick % 5 == 0:
                with open(samples_path, "a", encoding="utf-8") as f:
                    for (side, cam), st in sorted(snap.items()):
                        f.write(f"{t_s:.1f},{side},{cam},{st['frames']},"
                                f"{now - st['last']:.1f},"
                                f"{st['temp'] if st['temp'] is not None else ''},"
                                f"{st['temp_max'] if st['temp_max'] is not None else ''}\n")
                power_w = _shelly_power_w()
                alive = sum(1 for k in active
                            if k in snap and (now - snap[k]["last"]) < 2.0)
                temps = [f"{s}{c}:{snap[(s, c)]['temp']:.0f}"
                         for (s, c) in active
                         if (s, c) in snap and snap[(s, c)]["temp"] is not None]
                logger.info("t=%.0fs alive=%d/%d dropped=%d power=%sW temps=[%s]",
                            t_s, alive, len(active), len(dropouts),
                            power_w, " ".join(temps))
                status = {
                    "t_s": round(t_s, 1), "alive": alive, "active": len(active),
                    "dropouts": {f"{s}:{c}": d for (s, c), d in dropouts.items()},
                    "power_w": power_w,
                }
                with open(os.path.join(out_dir, "status.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(status, f, indent=2)

            # end conditions
            real_drops = [k for k, d in dropouts.items() if d["kind"] == "dropout"]
            if first_dropout_t is not None and \
                    now - first_dropout_t >= params["post_dropout_soak_s"]:
                end_reason = "dropout_plus_soak"
                break
            if t_s >= params["max_heat_s"]:
                end_reason = "max_heat_elapsed" if not real_drops else "max_heat_after_dropout"
                break
            if len(dropouts) == len(active):
                end_reason = "all_cameras_dead"
                break
            if not iface.scan_workflow.running:
                end_reason = "scan_worker_exited"
                logger.error("scan worker exited unexpectedly: %s",
                             iface.scan_workflow.last_scan_error)
                break

        verdict["scan"]["end_reason"] = end_reason
        verdict["scan"]["duration_s"] = round(time.monotonic() - t_trigger, 1)
        verdict["end_reason"] = end_reason
        logger.info("ending scan: %s after %.0fs", end_reason,
                    verdict["scan"]["duration_s"])

        try:
            iface.cancel_scan(join_timeout=15.0)
            iface.scan_workflow.await_complete(timeout_sec=30.0)
        except Exception:
            logger.exception("cancel_scan failed")
        scanning = False

        # ── 7. post-mortem ────────────────────────────────────────────
        final_snap = watchdog.snapshot()
        post_status: dict[str, dict] = {}
        for side, sensor in handles:
            try:
                sm = sensor.get_camera_status(0xFF) or {}
                post_status[side] = {str(c): s for c, s in sm.items()}
            except Exception:
                logger.exception("%s: post-scan camera status failed", side)
        verdict["scan"]["post_camera_status"] = post_status
        verdict["imu_temp_at_end_c"] = _read_imu_temps(handles)
        logger.info("IMU temps at end: %s", verdict["imu_temp_at_end_c"])

        _finish_cameras(verdict, cfg_results, final_snap, prev_dropped, params,
                        dropouts, t_trigger)
        _write_verdict()
        return 0

    except Exception as e:
        logger.exception("cycle failed")
        verdict["error"] = repr(e)
        verdict["end_reason"] = verdict.get("end_reason") or "exception"
        _write_verdict()
        return 1
    finally:
        if iface is not None:
            try:
                if scanning:
                    iface.cancel_scan(join_timeout=10.0)
            except Exception:
                pass
            if args.restore_fans:
                try:
                    for side in SIDES:
                        getattr(iface, side).set_fan_control(True)
                    logger.info("fans restored ON")
                except Exception:
                    logger.exception("fan restore failed")
            try:
                iface.stop()
            except Exception:
                logger.exception("iface.stop failed")


def _finish_cameras(verdict, cfg_results, final_snap, prev_dropped, params,
                    dropouts, t_trigger=None) -> None:
    """Assemble the per-camera verdict table."""
    dropouts = dropouts or {}
    cameras = {}
    for side in SIDES:
        cfg = cfg_results.get(side)
        for cam in ALL_CAMS:
            key = f"{side}:{cam}"
            r = (cfg or {}).get(cam, {})
            configured = bool(r.get("registers"))
            st = final_snap.get((side, cam)) if final_snap else None
            streamed = bool(st and st["frames"] > 0)
            drop = dropouts.get((side, cam))
            was_prev_dropped = (side, cam) in prev_dropped
            cameras[key] = {
                "configured": configured,
                "configure_error": r.get("error"),
                "streamed": streamed,
                "frames": st["frames"] if st else 0,
                "first_frame_s": (round(st["first"] - t_trigger, 1)
                                  if st and t_trigger else None),
                "dropout_t_s": drop.get("t_s") if drop else None,
                "dropout_kind": drop.get("kind") if drop else None,
                "temp_at_drop_c": drop.get("temp_at_drop_c") if drop else None,
                "temp_max_c": st["temp_max"] if st else None,
                "was_dropped_last_cycle": was_prev_dropped,
                # the headline metric — only meaningful if it dropped before:
                "recovered": (configured and streamed) if was_prev_dropped else None,
            }
    verdict["cameras"] = cameras


# ════════════════════════════════════════════════════════════ campaign mode

@dataclass
class TrialRecord:
    cycle: int
    off_time_s: float | None
    dropped: list[str] = field(default_factory=list)       # cameras dead by end
    prev_dropped: list[str] = field(default_factory=list)
    recovered: dict[str, bool] = field(default_factory=dict)
    end_reason: str | None = None


def _load_shelly():
    sys.path.insert(0, SHELLY_DRIVER_DIR)
    import shelly  # noqa: E402
    return shelly.default_outlet()


def _read_control(out_root: str) -> dict:
    path = os.path.join(out_root, "control.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("control.json unreadable; ignoring")
        return {}


def _next_off_time(history: list[TrialRecord], control: dict,
                   ladder_left: list[float]) -> float:
    """Pick the next power-off duration.

    Priority: control.json queue > coarse ladder > bisection of the
    recovery boundary (largest all-fail T vs smallest all-recover T).
    """
    q = control.get("next_off_times") or []
    if q:
        t = float(q.pop(0))
        control["next_off_times"] = q
        logger.info("off-time %ss from control.json queue", t)
        return t
    if ladder_left:
        t = float(ladder_left.pop(0))
        logger.info("off-time %ss from coarse ladder", t)
        return t
    fails, succs = [], []
    for tr in history:
        if tr.off_time_s is None or not tr.recovered:
            continue
        vals = [v for v in tr.recovered.values() if v is not None]
        if not vals:
            continue
        (succs if all(vals) else fails).append(tr.off_time_s)
    if fails and succs and min(succs) > max(f for f in fails):
        t = round((max(fails) + min(succs)) / 2.0, 0)
        logger.info("off-time %ss by bisection (fail<=%s, success>=%s)",
                    t, max(fails), min(succs))
    elif fails and not succs:
        t = min(DEFAULTS["max_off_s"], max(fails) * 2)
        logger.info("off-time %ss (no success yet; doubling)", t)
    elif succs and not fails:
        t = max(DEFAULTS["min_off_s"], min(succs) / 2)
        logger.info("off-time %ss (no failure yet; halving)", t)
    else:
        t = 300.0
        logger.info("off-time %ss (no boundary data; default)", t)
    return float(t)


def run_campaign(args) -> int:
    out_root = args.out
    os.makedirs(out_root, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(out_root, "campaign.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)

    events = os.path.join(out_root, "events.jsonl")
    until = _dt.datetime.strptime(args.until, "%H:%M").time()
    outlet = _load_shelly()
    _append_jsonl(events, {"type": "campaign_start", "until": args.until,
                           "shelly": os.environ.get("SHELLY_IP_ADDRESS")})

    ladder_left = list(args.off_ladder)
    history: list[TrialRecord] = []
    prev_dropped: list[str] = []
    cycle_idx = 0
    off_before: float = -1.0  # cycle 0: baseline (we power-cycle 30 s for a clean state)

    # Clean slate: brief power cycle so cycle 0 starts from a known reset.
    logger.info("initial 30 s power cycle for a clean baseline state")
    outlet.off()
    _append_jsonl(events, {"type": "power_off", "planned_off_s": 30})
    time.sleep(30)
    outlet.on()
    _append_jsonl(events, {"type": "power_on"})
    off_before = 30.0 if not history else off_before
    time.sleep(DEFAULTS["boot_wait_s"])

    while True:
        now = _dt.datetime.now()
        if now.time() >= until and now.hour < 12:
            logger.info("past --until %s; stopping", args.until)
            break

        control = _read_control(out_root)
        if control.get("stop"):
            logger.info("control.json stop=true; stopping")
            break

        cycle_dir = os.path.join(out_root, f"cycle_{cycle_idx:03d}")
        os.makedirs(cycle_dir, exist_ok=True)
        cmd = [sys.executable, os.path.abspath(__file__), "cycle",
               "--out", cycle_dir,
               "--cycle-index", str(cycle_idx),
               "--off-time-before", str(off_before),
               "--fans", "off"]
        if prev_dropped:
            cmd += ["--prev-dropped", ",".join(prev_dropped)]
        for k in ("max_heat_s", "post_dropout_soak_s"):
            if control.get(k):
                cmd += [f"--{k.replace('_', '-')}", str(control[k])]

        _append_jsonl(events, {"type": "cycle_start", "cycle": cycle_idx,
                               "off_time_before_s": off_before,
                               "prev_dropped": prev_dropped})
        logger.info("=== cycle %d (off-time before: %ss, prev dropped: %s) ===",
                    cycle_idx, off_before, prev_dropped or "none")
        try:
            proc = subprocess.run(cmd, timeout=DEFAULTS["cycle_timeout_s"])
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            logger.error("cycle %d timed out after %ss — killed",
                         cycle_idx, DEFAULTS["cycle_timeout_s"])
            rc = -9

        verdict = {}
        vpath = os.path.join(cycle_dir, "verdict.json")
        if os.path.exists(vpath):
            with open(vpath, encoding="utf-8") as f:
                verdict = json.load(f)
        _append_jsonl(events, {"type": "cycle_done", "cycle": cycle_idx,
                               "rc": rc, "end_reason": verdict.get("end_reason"),
                               "error": verdict.get("error")})

        cams = verdict.get("cameras", {})
        dropped_now = sorted(k for k, v in cams.items()
                             if v.get("dropout_kind") or
                             (not v.get("configured")) or (not v.get("streamed")))
        recovered = {k: v.get("recovered") for k, v in cams.items()
                     if v.get("was_dropped_last_cycle")}
        tr = TrialRecord(cycle=cycle_idx,
                         off_time_s=off_before if off_before >= 0 else None,
                         dropped=dropped_now, prev_dropped=list(prev_dropped),
                         recovered=recovered,
                         end_reason=verdict.get("end_reason"))
        history.append(tr)
        _append_jsonl(events, {"type": "trial", **tr.__dict__})
        logger.info("cycle %d result: end=%s dropped=%s recovered=%s",
                    cycle_idx, tr.end_reason, dropped_now, recovered)

        # next iteration
        prev_dropped = dropped_now
        cycle_idx += 1
        if rc != 0 and not verdict:
            logger.error("cycle produced no verdict; 60 s recovery power-cycle")
            off_before = 60.0
        else:
            off_before = _next_off_time(history, control, ladder_left)
            if control.get("next_off_times") is not None:
                with open(os.path.join(out_root, "control.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(control, f, indent=2)

        # projected end check: don't start a cycle we can't finish by ~until+45m
        proj = _dt.datetime.now() + _dt.timedelta(seconds=off_before + 2700)
        if proj.time() >= until and proj.hour < 12 and _dt.datetime.now().time() < until:
            pass  # informational only; the loop-top check enforces the stop

        logger.info("powering OFF for %ss", off_before)
        if not outlet.off():
            logger.error("Shelly OFF failed! retrying once")
            time.sleep(2)
            outlet.off()
        _append_jsonl(events, {"type": "power_off", "planned_off_s": off_before})
        time.sleep(off_before)
        if not outlet.on():
            logger.error("Shelly ON failed! retrying once")
            time.sleep(2)
            outlet.on()
        _append_jsonl(events, {"type": "power_on"})
        time.sleep(DEFAULTS["boot_wait_s"])

    # ── leave the bench in a sane state: power on, fans on ─────────────
    try:
        if not outlet.is_on():
            outlet.on()
            time.sleep(DEFAULTS["boot_wait_s"])
    except Exception:
        logger.exception("final power-on failed")
    logger.info("campaign done — restoring fans")
    restore_dir = os.path.join(out_root, "restore")
    os.makedirs(restore_dir, exist_ok=True)
    subprocess.run([sys.executable, os.path.abspath(__file__), "restore",
                    "--out", restore_dir], timeout=300)
    _append_jsonl(events, {"type": "campaign_end", "cycles": cycle_idx})
    return 0


def run_restore(args) -> int:
    os.makedirs(args.out, exist_ok=True)
    _setup_cycle_logging(args.out)
    from omotion import MotionInterface
    iface = MotionInterface()
    try:
        iface.start(wait=True, wait_timeout=10.0)
        time.sleep(2.0)
        for side in SIDES:
            try:
                sensor = getattr(iface, side)
                if sensor.is_connected():
                    ok = sensor.set_fan_control(True)
                    logger.info("%s: fan ON -> %s", side, ok)
            except Exception:
                logger.exception("%s fan restore failed", side)
        return 0
    finally:
        try:
            iface.stop()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════ main

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)

    pc = sub.add_parser("campaign", help="overnight orchestrator (owns the Shelly)")
    pc.add_argument("--out", required=True)
    pc.add_argument("--until", default="09:15", help="stop time HH:MM (default 09:15)")
    pc.add_argument("--off-ladder", type=lambda s: [float(x) for x in s.split(",")],
                    default=DEFAULTS["off_ladder"])

    py = sub.add_parser("cycle", help="one power-on session (hardware work)")
    py.add_argument("--out", required=True)
    py.add_argument("--cycle-index", type=int, required=True)
    py.add_argument("--off-time-before", type=float, default=-1.0)
    py.add_argument("--prev-dropped", default="",
                    help="comma list like left:6,right:7")
    py.add_argument("--fans", choices=["on", "off"], default="off")
    py.add_argument("--restore-fans", action="store_true")
    py.add_argument("--max-heat-s", type=float, dest="max_heat_s",
                    default=DEFAULTS["max_heat_s"])
    py.add_argument("--post-dropout-soak-s", type=float, dest="post_dropout_soak_s",
                    default=DEFAULTS["post_dropout_soak_s"])
    py.add_argument("--recovery-window-s", type=float, dest="recovery_window_s",
                    default=DEFAULTS["recovery_window_s"])
    py.add_argument("--dropout-silence-s", type=float, dest="dropout_silence_s",
                    default=DEFAULTS["dropout_silence_s"])
    py.add_argument("--connect-timeout-s", type=float, dest="connect_timeout_s",
                    default=DEFAULTS["connect_timeout_s"])

    pr = sub.add_parser("restore", help="fans back on; leave system idle-sane")
    pr.add_argument("--out", required=True)

    args = p.parse_args()
    if args.mode == "campaign":
        return run_campaign(args)
    if args.mode == "cycle":
        return run_cycle(args)
    if args.mode == "restore":
        return run_restore(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
