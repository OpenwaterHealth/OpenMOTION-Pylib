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
    "min_off_s": 10.0,
    "max_off_s": 3600.0,
    # Interleaved (cool_mode, cool_seconds) trial plan so every mode gets
    # boundary data early. Fans are assumed ON during normal operation
    # (Ethan), so heat phases default to fans on. Modes:
    #   mains_off        — whole system off via Shelly (original question)
    #   idle_fan_on      — production "wait between scans": cameras stay
    #                      powered, not streaming, fan on
    #   cams_off_fan_on  — aggressive powered cooldown: camera rails off, fan on
    #   cams_off_fan_off — isolates the fan's contribution (low priority)
    "trial_plan": [
        ["mains_off", 30], ["idle_fan_on", 120], ["cams_off_fan_on", 60],
        ["mains_off", 1800], ["idle_fan_on", 600], ["cams_off_fan_on", 300],
        ["mains_off", 300], ["idle_fan_on", 300], ["cams_off_fan_off", 300],
        ["mains_off", 900], ["cams_off_fan_on", 120], ["mains_off", 120],
    ],
}

COOL_MODES = ("mains_off", "cams_off_fan_on", "cams_off_fan_off", "idle_fan_on")


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


def _connect(connect_timeout_s: float, partial_after_s: float = 90.0):
    """Bring up MotionInterface; retry until console + both sensors connect.

    Proceeds with a partial sensor set after ``partial_after_s`` (a module
    that hasn't enumerated by then is missing, not slow — burning the full
    budget every cycle would waste the night). The interface is rebuilt at
    most ONCE (half budget), and a negative verdict is never returned from
    an interface younger than 15 s — a fresh rebuild needs time to ping.
    """
    from omotion import MotionInterface

    t0 = time.monotonic()
    deadline = t0 + connect_timeout_s
    partial_t = t0 + min(partial_after_s, connect_timeout_s)
    iface = MotionInterface()
    iface.start(wait=True, wait_timeout=5.0)
    iface_born = time.monotonic()
    rebuilt = False
    attempt = 0
    while True:
        attempt += 1
        con, left, right = iface.is_device_connected()
        logger.info("connect attempt %d: console=%s left=%s right=%s",
                    attempt, con, left, right)
        if con and left and right:
            return iface, {"console": True, "left": True, "right": True}
        now = time.monotonic()
        if con and (left or right) and now >= partial_t:
            logger.warning("proceeding with PARTIAL sensor set after %.0fs: "
                           "left=%s right=%s", now - t0, left, right)
            return iface, {"console": con, "left": left, "right": right}
        if now >= deadline and now - iface_born >= 15.0:
            logger.error("connect budget exhausted (console=%s left=%s right=%s)",
                         con, left, right)
            return iface, {"console": con, "left": left, "right": right}
        if not rebuilt and now >= t0 + connect_timeout_s / 2:
            logger.info("rebuilding MotionInterface once (stuck discovery?)")
            try:
                iface.stop()
            except Exception:
                logger.exception("iface.stop() during rebuild")
            iface = MotionInterface()
            iface.start(wait=True, wait_timeout=5.0)
            iface_born = time.monotonic()
            rebuilt = True
        time.sleep(3.0)


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


def _imu_bringup(handles) -> None:
    """IMU must be init'd + powered before imu_get_temperature returns real
    values (reads 0.0 otherwise — observed cycle 0, 02:51)."""
    for side, sensor in handles:
        try:
            ok1 = sensor.imu_init()
            ok2 = sensor.imu_on()
            logger.info("%s: imu init=%s on=%s", side, ok1, ok2)
        except Exception:
            logger.exception("%s: IMU bring-up failed", side)


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
        "cool_mode_before": args.cool_mode_before or None,
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
        _imu_bringup(handles)
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


# ═══════════════════════════════════════════════════════════════ cool mode

def run_cool(args) -> int:
    """Powered cooldown: camera PCBA rails off, sensor fan per --fan, hold
    for --duration-s while logging the IMU-temperature cooling curve — the
    only module-temperature observable while cameras are unpowered (the
    camera temps come from the sensor die itself and vanish with the rail).
    Falls back to a plain wait if hardware is unreachable; cooling happens
    regardless.
    """
    os.makedirs(args.out, exist_ok=True)
    _setup_cycle_logging(args.out)
    t_end = time.monotonic() + args.duration_s
    curve_path = os.path.join(args.out, "cooling_curve.csv")
    with open(curve_path, "w", encoding="utf-8") as f:
        f.write("t_s,side,imu_temp_c,power_w\n")
    iface = None
    try:
        iface, connected = _connect(min(60.0, max(20.0, args.duration_s / 2)))
        handles = _sensor_handles(iface, connected)
        _imu_bringup(handles)
        fan_on = args.fan == "on"
        for side, sensor in handles:
            if args.cams == "off":
                try:
                    power = sensor.get_camera_power_status()
                    mask = 0
                    if power and len(power) == 8:
                        mask = sum(1 << i for i in range(8) if power[i])
                    if mask:
                        ok = sensor.disable_camera_power(mask)
                        logger.info("%s: disable_camera_power(0x%02X) -> %s",
                                    side, mask, ok)
                    else:
                        logger.info("%s: cameras already unpowered", side)
                except Exception:
                    logger.exception("%s: camera power-off failed", side)
            else:
                logger.info("%s: leaving camera power as-is (idle mode)", side)
            try:
                ok = sensor.set_fan_control(fan_on)
                logger.info("%s: fan -> %s (set ok=%s)", side,
                            "ON" if fan_on else "OFF", ok)
            except Exception:
                logger.exception("%s: fan control failed", side)
        t0 = time.monotonic()
        while time.monotonic() < t_end:
            time.sleep(min(10.0, max(0.5, t_end - time.monotonic())))
            row_t = time.monotonic() - t0
            power_w = _shelly_power_w()
            with open(curve_path, "a", encoding="utf-8") as f:
                for side, sensor in handles:
                    try:
                        temp = round(float(sensor.imu_get_temperature()), 2)
                    except Exception:
                        temp = ""
                    f.write(f"{row_t:.0f},{side},{temp},"
                            f"{power_w if power_w is not None else ''}\n")
        logger.info("cooldown complete (%.0fs, fan %s)", args.duration_s, args.fan)
        return 0
    except Exception:
        logger.exception("cool phase error; falling back to plain wait")
        remaining = t_end - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        return 0
    finally:
        if iface is not None:
            try:
                iface.stop()
            except Exception:
                pass


# ════════════════════════════════════════════════════════════ campaign mode

@dataclass
class TrialRecord:
    cycle: int
    cool_mode: str | None          # cooling applied BEFORE this cycle
    cool_s: float | None
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


def _next_trial(history: list[TrialRecord], control: dict,
                plan_left: list) -> tuple[str, float]:
    """Pick the next (cool_mode, cool_seconds).

    Priority: control.json "next_trials" queue > interleaved default plan >
    adaptive (least-sampled mode, bisection of its recovery boundary).
    """
    q = control.get("next_trials") or []
    if q:
        e = q.pop(0)
        control["next_trials"] = q
        mode, t = str(e["mode"]), float(e["off_s"])
        logger.info("trial (%s, %ss) from control.json queue", mode, t)
        return mode, t
    if plan_left:
        mode, t = plan_left.pop(0)
        logger.info("trial (%s, %ss) from default plan", mode, t)
        return str(mode), float(t)

    counts = {m: 0 for m in COOL_MODES}
    for tr in history:
        if tr.cool_mode in counts and tr.recovered:
            counts[tr.cool_mode] += 1
    mode = min(counts, key=lambda m: counts[m])
    fails, succs = [], []
    for tr in history:
        if tr.cool_mode != mode or tr.cool_s is None or not tr.recovered:
            continue
        vals = [v for v in tr.recovered.values() if v is not None]
        if not vals:
            continue
        (succs if all(vals) else fails).append(tr.cool_s)
    if fails and succs and min(succs) > max(fails):
        t = round((max(fails) + min(succs)) / 2.0, 0)
        logger.info("trial (%s, %ss) by bisection (fail<=%s, success>=%s)",
                    mode, t, max(fails), min(succs))
    elif fails and not succs:
        t = min(DEFAULTS["max_off_s"], max(fails) * 2)
        logger.info("trial (%s, %ss): no success yet; doubling", mode, t)
    elif succs and not fails:
        t = max(DEFAULTS["min_off_s"], min(succs) / 2)
        logger.info("trial (%s, %ss): no failure yet; halving", mode, t)
    else:
        t = 300.0
        logger.info("trial (%s, %ss): no boundary data; default", mode, t)
    return mode, float(t)


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

    plan_left = [list(x) for x in DEFAULTS["trial_plan"]]
    history: list[TrialRecord] = []
    prev_dropped: list[str] = []
    cycle_idx = 0

    # Clean slate: brief power cycle so cycle 0 starts from a known reset.
    logger.info("initial 30 s power cycle for a clean baseline state")
    outlet.off()
    _append_jsonl(events, {"type": "power_off", "planned_off_s": 30})
    time.sleep(30)
    outlet.on()
    _append_jsonl(events, {"type": "power_on"})
    cool_mode, cool_s = "mains_off", 30.0   # what preceded cycle 0
    force_fans_off = False
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

        heat_fans = "off" if force_fans_off else str(control.get("heat_fans", "on"))
        if force_fans_off:
            logger.info("previous heat phase tripped nothing — forcing fans OFF "
                        "this cycle to generate a dropout")
        cycle_dir = os.path.join(out_root, f"cycle_{cycle_idx:03d}")
        os.makedirs(cycle_dir, exist_ok=True)
        cmd = [sys.executable, os.path.abspath(__file__), "cycle",
               "--out", cycle_dir,
               "--cycle-index", str(cycle_idx),
               "--off-time-before", str(cool_s),
               "--cool-mode-before", cool_mode,
               "--fans", heat_fans]
        if prev_dropped:
            cmd += ["--prev-dropped", ",".join(prev_dropped)]
        for k in ("max_heat_s", "post_dropout_soak_s"):
            if control.get(k):
                cmd += [f"--{k.replace('_', '-')}", str(control[k])]

        _append_jsonl(events, {"type": "cycle_start", "cycle": cycle_idx,
                               "cool_mode_before": cool_mode,
                               "cool_s_before": cool_s,
                               "prev_dropped": prev_dropped})
        logger.info("=== cycle %d (cooled %ss via %s, prev dropped: %s) ===",
                    cycle_idx, cool_s, cool_mode, prev_dropped or "none")
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
        connected_v = verdict.get("connected") or {}

        def _on_connected_side(key: str) -> bool:
            # A module absent from USB (left, tonight) must not be counted
            # as "dropped" — it would poison the no-dropout fallback and
            # fabricate failed-recovery data points.
            return bool(connected_v.get(key.split(":")[0]))

        dropped_now = sorted(k for k, v in cams.items()
                             if _on_connected_side(k) and
                             (v.get("dropout_kind") or
                              (not v.get("configured")) or (not v.get("streamed"))))
        recovered = {k: v.get("recovered") for k, v in cams.items()
                     if v.get("was_dropped_last_cycle") and _on_connected_side(k)}
        tr = TrialRecord(cycle=cycle_idx,
                         cool_mode=cool_mode, cool_s=cool_s,
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
        force_fans_off = False
        if (rc != 0 and not verdict) or verdict.get("end_reason") == "bringup_failed":
            logger.error("cycle failed bring-up or produced no verdict; "
                         "60 s recovery power-cycle")
            cool_mode, cool_s = "mains_off", 60.0
        elif not dropped_now:
            # Nothing tripped: no recovery to test, so don't burn a planned
            # cooling trial. Roll straight into another heat phase (cameras
            # stay warm) and force fans off once to generate a dropout.
            logger.info("no dropouts this cycle — minimal cool, fans off next heat")
            cool_mode, cool_s = "idle_fan_on", 15.0
            force_fans_off = True
        else:
            cool_mode, cool_s = _next_trial(history, control, plan_left)
            if control.get("next_trials") is not None:
                with open(os.path.join(out_root, "control.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(control, f, indent=2)

        # ── cooling phase ────────────────────────────────────────────
        _append_jsonl(events, {"type": "cool_start", "mode": cool_mode,
                               "planned_s": cool_s})
        logger.info("cooling: %s for %ss", cool_mode, cool_s)
        if cool_mode == "mains_off":
            if not outlet.off():
                logger.error("Shelly OFF failed! retrying once")
                time.sleep(2)
                outlet.off()
            time.sleep(cool_s)
            if not outlet.on():
                logger.error("Shelly ON failed! retrying once")
                time.sleep(2)
                outlet.on()
            time.sleep(DEFAULTS["boot_wait_s"])
        else:
            fan = "on" if cool_mode.endswith("fan_on") else "off"
            cams = "leave" if cool_mode == "idle_fan_on" else "off"
            cool_dir = os.path.join(out_root, f"cool_{cycle_idx:03d}")
            os.makedirs(cool_dir, exist_ok=True)
            try:
                subprocess.run(
                    [sys.executable, os.path.abspath(__file__), "cool",
                     "--out", cool_dir, "--duration-s", str(cool_s),
                     "--fan", fan, "--cams", cams],
                    timeout=cool_s + 300)
            except subprocess.TimeoutExpired:
                logger.error("cool subprocess timed out — continuing")
        _append_jsonl(events, {"type": "cool_done", "mode": cool_mode})

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

    py = sub.add_parser("cycle", help="one power-on session (hardware work)")
    py.add_argument("--out", required=True)
    py.add_argument("--cycle-index", type=int, required=True)
    py.add_argument("--off-time-before", type=float, default=-1.0)
    py.add_argument("--cool-mode-before", default="", choices=("", *COOL_MODES))
    py.add_argument("--prev-dropped", default="",
                    help="comma list like left:6,right:7")
    py.add_argument("--fans", choices=["on", "off"], default="on")
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

    pl = sub.add_parser("cool", help="powered cooldown: cams off, fan on/off, "
                                     "log IMU cooling curve")
    pl.add_argument("--out", required=True)
    pl.add_argument("--duration-s", type=float, required=True, dest="duration_s")
    pl.add_argument("--fan", choices=["on", "off"], required=True)
    pl.add_argument("--cams", choices=["off", "leave"], default="off",
                    help="off = disable camera power rails; leave = idle mode")

    pr = sub.add_parser("restore", help="fans back on; leave system idle-sane")
    pr.add_argument("--out", required=True)

    args = p.parse_args()
    if args.mode == "campaign":
        return run_campaign(args)
    if args.mode == "cycle":
        return run_cycle(args)
    if args.mode == "cool":
        return run_cool(args)
    if args.mode == "restore":
        return run_restore(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
