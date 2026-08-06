"""WI-00015 Device Specific Parameter Tuning - phase engine.

Importable implementation of the automated tuning flow; the CLI lives in
scripts/wi15_runner.py and a future test-app "tune" button can drive these
same phase functions directly (they communicate via the JSON state file).

Phased state machine mirroring the work instruction. Each phase is a
subcommand; results accumulate in a JSON state file so physical module swaps
can happen between invocations. See WI15_RUNNER_README.md for the deviation
log and the authoritative WI interpretation rulings this implements.

    python wi15_runner.py baseline  --side right          # module in fixture
    python wi15_runner.py baseline  --side left           # after swap
    python wi15_runner.py tune      --seated <higher>     # step 19 + section 4.4
    python wi15_runner.py crosscheck --seated <other>     # steps 21-22
    python wi15_runner.py finalize                        # EPROM + power cycle + PDF
    python wi15_runner.py diagnose-low --seated <side>    # step 23 sweep (diagnostic only)

LASER SAFETY, enforced in code:
  * stop_trigger() runs in a finally block on every exit path.
  * TA current only ever steps DOWN, with a hard floor (TA_CURRENT_FLOOR_MA).
  * TA pulse width is verified below the safety limits before every firing.
  * The diagnostic pulse-width sweep restores both the safety limits and the
    TA pulse width itself on every exit path.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import statistics
import sys
import time
import urllib.request

from omotion.MotionConsole import MotionConsole
from omotion.config import CONSOLE_MODULE_PID
from omotion.laser import FpgaMap, apply_laser_power

# =========================================================================
# CONFIGURATION - edit here, everything else derives from these.
# =========================================================================

# --- Acceptance window (SPEC-31) ---
# PRODUCTION values per WI-00015 rev 2 / SPEC-31. These are the defaults.
# For a dev rig with a dim unit, override per run at the baseline phase:
#     wi15_runner.py baseline --side right --window 75 125
# (or env WI15_WINDOW="75,125"). The chosen window is stored in the run state
# at baseline time and reloaded by every later phase, so one run can never
# mix windows.
SPEC31_MIN_UJ = 300.0
SPEC31_MAX_UJ = 400.0

# --- Measurement ---
MIN_PULSES = 25            # WI steps 14/17: meter Total must exceed 25
MAX_STDEV_UJ = 40.0        # WI steps 14/17
RATE_MIN_HZ, RATE_MAX_HZ = 39.0, 41.0
BASELINE_CAPTURE_S = 8.0   # per-measurement stream time (WI's is ~1 s; longer = better stats)
LOOP_CAPTURE_S = 4.0       # capture time inside tuning loops
SETTLE_S = 1.0             # discarded settling window after laser-on

# --- Meter setup (WI step 4) ---
METER_SETUP = {
    "range": "2.00mJ",
    "wavelength": "795",
    "pulse_length": "1.0ms",
    "threshold": "Min",
    "measurement_mode": "Energy",
}

# --- Laser / tuning ---
TRIGGER_FREQ_HZ = 40.0         # asserted (and set if wrong) before every firing
TA_CURRENT_STEP_MA = 50.0      # WI step 19 decrement
TA_CURRENT_FLOOR_MA = 2000.0   # sanity floor: abort rather than step below this
TUNE_MAX_STEPS = 40
TA_PULSE_WIDTH_CEILING_US = 600.0   # WI step 23 ceiling (diagnostic sweep)
TEMP_PULSE_WIDTH_UL_US = 660.0      # WI step 23 temporary safety relaxation
WI_PULSE_WIDTH_UL_US = 550.0        # WI default standing limit

# --- Section 4.4 safety-limit derivation ---
ADC_SAMPLES = 5            # ruling 9: average several reads (while firing)
ADC_SAMPLE_GAP_S = 0.15
EE_MULT = 1.10             # WI step 31; ruling 3: round DOWN
OPT_MULT = 1.30            # WI step 30; round to NEAREST per WI text
PW_UL_MULT = 1.10          # WI step 32; nearest

# --- Section 4.5 power-cycle verification ---
# Automated mains cycling uses a Shelly smart switch when SHELLY_IP_ADDRESS
# is set - the same env var the bloodflow-app HIL test infrastructure uses
# (tests/shelly.py, hil-tests.yml), so a bench configured for HIL runs is
# already configured for this. Without it - e.g. at the factory - the flow
# prompts the operator to flip the power switch instead, and then verifies
# via firmware uptime that the console actually rebooted. SHELLY_RELAY
# selects the relay index (default 0), also per the HIL convention.
SHELLY_HOST = os.environ.get("SHELLY_IP_ADDRESS", "")
SHELLY_RELAY = int(os.environ.get("SHELLY_RELAY", "0") or 0)
POWER_OFF_DWELL_S = 15.0       # ruling 9
RECONNECT_TIMEOUT_S = 90.0
MAX_UPTIME_AFTER_CYCLE_MS = 180_000   # console must report < 3 min uptime

# --- Files ---
# Reports and run state land here; override with the WI15_OUT_DIR env var.
OUT_DIR = os.environ.get("WI15_OUT_DIR", os.path.join(os.getcwd(), "wi15_out"))
STATE_FILE = os.path.join(OUT_DIR, "wi15_run_state.json")

WI_DOC = "WI-00015 rev 2 (ECO-000270)"
MOTION_VID = 0x0483

REG_UNITS = {
    "TA_PULSE_WIDTH": "us", "TA_CURRENT_DRV": "mA", "SEED_CW_GAIN": "mV",
    "EE_PULSE_WIDTH_UL": "us", "EE_DRIVE_CL": "mA", "EE_ADC_DATA": "mA",
    "OPT_PULSE_WIDTH_UL": "us", "OPT_DRIVE_CL": "mA", "OPT_ADC_DATA": "mA",
}

# =========================================================================


def set_window(lo_uj: float, hi_uj: float) -> None:
    """Set the SPEC-31 acceptance window for this process."""
    global SPEC31_MIN_UJ, SPEC31_MAX_UJ
    if not (0 < lo_uj < hi_uj):
        raise ValueError(f"invalid window {lo_uj}-{hi_uj} uJ")
    SPEC31_MIN_UJ, SPEC31_MAX_UJ = float(lo_uj), float(hi_uj)


def resolve_window(args, st: dict) -> None:
    """Baseline phase only: pick the window (CLI > env > production default)
    and pin it into the run state."""
    if "window" in st:
        set_window(*st["window"])
        return
    if getattr(args, "window", None):
        lo, hi = args.window
        src = "--window override"
    elif os.environ.get("WI15_WINDOW"):
        lo, hi = (float(x) for x in os.environ["WI15_WINDOW"].split(","))
        src = "WI15_WINDOW env override"
    else:
        lo, hi = 300.0, 400.0
        src = "production default (SPEC-31)"
    set_window(lo, hi)
    st["window"] = [lo, hi]
    st["window_source"] = src
    print(f"acceptance window: {lo:g}-{hi:g} uJ ({src})")


def apply_window(st: dict) -> None:
    """Later phases: reload the window pinned at baseline time."""
    if st.get("window"):
        set_window(*st["window"])


def sensor_inventory_from_iface(iface) -> dict:
    """WI step 8 inventory: SDK version + per-sensor identity, from an
    already-started MotionInterface."""
    import omotion as _omotion
    inv: dict = {"sdk_version": getattr(_omotion, "__version__", "?")}
    _, l_ok, r_ok = iface.is_device_connected()
    for side, dev, ok in (("left", iface.left, l_ok),
                          ("right", iface.right, r_ok)):
        if not ok:
            inv[side] = {"connected": False}
            continue
        entry = {"connected": True}
        for key, fn in (("serial", dev.read_serial_number),
                        ("firmware", dev.get_version),
                        ("hardware_id", dev.get_hardware_id)):
            try:
                entry[key] = fn()
            except Exception as e:
                entry[key] = f"unavailable ({e})"
        inv[side] = entry
    return inv


def collect_sensor_inventory() -> dict:
    """Short-lived MotionInterface session for the step-8 inventory. Used by
    the tuning flow, which otherwise talks to the console alone."""
    from omotion.MotionInterface import MotionInterface
    iface = MotionInterface(operator_id="wi15-inventory")
    iface.start()
    try:
        iface.wait_for_ready(console=True, sensors=2, timeout=20.0)
        return sensor_inventory_from_iface(iface)
    finally:
        iface.stop()


def read_fpga_revisions(session: "ConsoleSession") -> dict:
    """Console laser/safety FPGA versions (WI step 8's 'FPGA Firmware')."""
    out = {}
    for label, prefix in (("TA", "TA"), ("Seed", "SEED"),
                          ("Safety EE", "EE"), ("Safety OPT", "OPT")):
        try:
            major = session.read_reg(f"{prefix}_MAJOR")[0]
            minor = session.read_reg(f"{prefix}_MINOR")[0]
            rev = session.read_reg(f"{prefix}_REVISION")[0]
            out[label] = f"v{major}.{minor}.{rev}"
        except Exception as e:
            out[label] = f"unavailable ({e})"
    return out


def check_rows(mean_uj, stdev_uj, rate_hz, n):
    return [
        (f"Pulses captured >= {MIN_PULSES}", f"{n}", n >= MIN_PULSES),
        (f"Std. Dev < {MAX_STDEV_UJ:g} uJ", f"{stdev_uj:.2f} uJ", stdev_uj < MAX_STDEV_UJ),
        (f"Repetition rate {RATE_MIN_HZ:g}-{RATE_MAX_HZ:g} Hz", f"{rate_hz:.2f} Hz",
         RATE_MIN_HZ <= rate_hz <= RATE_MAX_HZ),
        (f"SPEC-31 energy {SPEC31_MIN_UJ:g}-{SPEC31_MAX_UJ:g} uJ", f"{mean_uj:.2f} uJ",
         SPEC31_MIN_UJ <= mean_uj <= SPEC31_MAX_UJ),
    ]


# --- state ---------------------------------------------------------------
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"created": dt.datetime.now().isoformat(timespec="seconds"),
            "phases": {}, "notes": []}


def save_state(st: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


# --- Ophir meter ---------------------------------------------------------
class OphirMeter:
    def __init__(self) -> None:
        import win32com.client  # deferred: only needed with a meter attached
        self.lm = win32com.client.Dispatch("OphirLMMeasurement.CoLMMeasurement")
        self.lm.StopAllStreams()
        self.lm.CloseAll()
        serials = self.lm.ScanUSB()
        if not serials:
            raise RuntimeError("no Ophir meter found on USB")
        self.serial = serials[0]
        self.handle = self.lm.OpenUSBDevice(self.serial)
        self.channel = 0
        if not self.lm.IsSensorExists(self.handle, self.channel):
            raise RuntimeError(f"meter {self.serial} reports no sensor on channel 0")

    def identity(self) -> dict:
        name, rom, serial = self.lm.GetDeviceInfo(self.handle)
        s_serial, s_type, s_name = self.lm.GetSensorInfo(self.handle, self.channel)
        return {
            "meter": f"{name} (firmware {rom})", "meter_serial": serial,
            "sensor": f"{s_name} ({s_type})", "sensor_serial": s_serial,
            "device_cal_due": str(self.lm.GetDeviceCalibrationDueDate(self.handle))[:10],
            "sensor_cal_due": str(
                self.lm.GetSensorCalibrationDueDate(self.handle, self.channel))[:10],
        }

    def _apply(self, label, getter, setter, wanted):
        index, options = getter(self.handle, self.channel)
        options = list(options)
        match = next((i for i, o in enumerate(options) if str(o).strip() == wanted), None)
        if match is None:
            match = next((i for i, o in enumerate(options) if wanted in str(o)), None)
        if match is None:
            return (label, wanted, f"NOT AVAILABLE ({', '.join(map(str, options))})", False)
        if index != match:
            setter(self.handle, self.channel, match)
            time.sleep(0.2)
        after_idx, after_opts = getter(self.handle, self.channel)
        after = str(list(after_opts)[after_idx])
        return (label, wanted, after, after.strip() == wanted or wanted in after)

    def configure_for_wi(self):
        lm = self.lm
        rows = [
            self._apply("Measurement mode", lm.GetMeasurementMode, lm.SetMeasurementMode,
                        METER_SETUP["measurement_mode"]),
            self._apply("Range", lm.GetRanges, lm.SetRange, METER_SETUP["range"]),
            self._apply("Wavelength", lm.GetWavelengths, lm.SetWavelength,
                        METER_SETUP["wavelength"]),
            self._apply("Pulse length", lm.GetPulseLengths, lm.SetPulseLength,
                        METER_SETUP["pulse_length"]),
            self._apply("Threshold", lm.GetThreshold, lm.SetThreshold,
                        METER_SETUP["threshold"]),
        ]
        if not all(ok for *_, ok in rows):
            raise RuntimeError(f"meter could not be configured per WI step 4: {rows}")
        return rows

    def measure(self, duration_s: float, settle_s: float = SETTLE_S) -> dict:
        """Stream and return statistics dict. Laser must be firing.

        Samples with a non-zero status word are sentinels, not measurements
        (~2% of the stream, values around 1e13 uJ, occasionally sharing a
        timestamp with a real pulse). They are discarded before statistics.
        """
        self.lm.StartStream(self.handle, self.channel)
        try:
            t_settle = time.time() + settle_s
            while time.time() < t_settle:
                time.sleep(0.05)
                self.lm.GetData(self.handle, self.channel)
            values, stamps, states = [], [], []
            t_end = time.time() + duration_s
            while time.time() < t_end:
                time.sleep(0.05)
                d = self.lm.GetData(self.handle, self.channel)
                if d and len(d[0]) > 0:
                    values.extend(d[0]); stamps.extend(d[1]); states.extend(d[2])
        finally:
            self.lm.StopStream(self.handle, self.channel)

        kept = [(v, t) for v, t, s in zip(values, stamps, states) if s == 0]
        discarded = sum(1 for s in states if s != 0)
        if len(kept) < 2:
            return {"n": len(kept), "discarded": discarded, "mean_uj": float("nan"),
                    "stdev_uj": float("nan"), "rate_hz": float("nan"),
                    "min_uj": float("nan"), "max_uj": float("nan"),
                    "duration_s": duration_s}
        vals = [v for v, _ in kept]
        ts = [t for _, t in kept]
        span = (ts[-1] - ts[0]) / 1000.0
        return {
            "n": len(kept), "discarded": discarded,
            "mean_uj": statistics.fmean(vals) * 1e6,
            "stdev_uj": statistics.pstdev(vals) * 1e6,
            "rate_hz": (len(kept) - 1) / span if span > 0 else float("nan"),
            "min_uj": min(vals) * 1e6, "max_uj": max(vals) * 1e6,
            "duration_s": duration_s,
        }

    def close(self):
        try:
            self.lm.StopAllStreams(); self.lm.CloseAll()
        except Exception:
            pass


# --- console -------------------------------------------------------------
class ConsoleSession:
    def __init__(self) -> None:
        self.console = MotionConsole(vid=MOTION_VID, pid=CONSOLE_MODULE_PID)
        self.console._drive_connecting("wi15 runner")
        if not self.console.is_connected():
            raise RuntimeError(
                "could not connect to the console - close the TestApp/bloodflow-app first")
        self.map = FpgaMap()

    def identity(self) -> dict:
        return {"console_serial": self.console.read_serial_number(),
                "console_firmware": self.console.get_version(),
                "port": self.console.uart.port}

    def _entry(self, friendly):
        e = self.map.get_entry_by_friendly_name(friendly)
        if e is None:
            raise KeyError(friendly)
        return e

    def read_reg(self, friendly):
        e = self._entry(friendly)
        nbytes = int(e["data_size"].rstrip("B")) // 8
        raw, _ = self.console.read_i2c_packet(
            mux_index=e["mux_idx"], channel=e["channel"], device_addr=e["i2c_addr"],
            reg_addr=e["start_address"], read_len=nbytes)
        if raw is None:
            raise RuntimeError(f"I2C read failed for {friendly}")
        value = int.from_bytes(bytes(raw[:nbytes]),
                               "big" if e.get("isMsbFirst") else "little")
        scale = e.get("scale")
        return value, (value * scale if scale else None), REG_UNITS.get(friendly)

    def write_scaled(self, friendly, scaled_value):
        e = self._entry(friendly)
        nbytes = int(e["data_size"].rstrip("B")) // 8
        scale = e.get("scale") or 1.0
        raw = max(0, min((1 << (nbytes * 8)) - 1, int(round(scaled_value / scale))))
        data = bytearray(raw.to_bytes(nbytes, "big" if e.get("isMsbFirst") else "little"))
        if not self.console.write_i2c_packet(
                mux_index=e["mux_idx"], channel=e["channel"], device_addr=e["i2c_addr"],
                reg_addr=e["start_address"], data=data):
            raise RuntimeError(f"I2C write failed for {friendly}")

    def assert_trigger_40hz(self) -> float:
        """Ruling 9: assume/enforce 40 Hz. Sets it if the console disagrees."""
        cfg = self.console.get_trigger_json() or {}
        freq = cfg.get("TriggerFrequencyHz")
        if freq != TRIGGER_FREQ_HZ:
            cfg["TriggerFrequencyHz"] = TRIGGER_FREQ_HZ
            self.console.set_trigger_json(cfg)
            cfg = self.console.get_trigger_json() or {}
            freq = cfg.get("TriggerFrequencyHz")
            if freq != TRIGGER_FREQ_HZ:
                raise RuntimeError(f"could not set trigger frequency to "
                                   f"{TRIGGER_FREQ_HZ} Hz (reads {freq})")
        return freq

    def preflight(self) -> None:
        """Verify pulse width sits below both safety limits before firing."""
        ta_pw = self.read_reg("TA_PULSE_WIDTH")[1]
        ee_ul = self.read_reg("EE_PULSE_WIDTH_UL")[1]
        opt_ul = self.read_reg("OPT_PULSE_WIDTH_UL")[1]
        if ta_pw >= min(ee_ul, opt_ul):
            raise RuntimeError(
                f"ABORT: TA pulse width {ta_pw:.1f} us not below safety limits "
                f"(EE {ee_ul:.1f}, OPT {opt_ul:.1f}) - firing would trip the interlock")

    def close(self):
        try:
            self.console.stop_trigger()
        except Exception:
            pass
        try:
            self.console.telemetry.stop()
        except Exception:
            pass
        self.console.uart.close()


def fire_and_measure(session: ConsoleSession, meter: OphirMeter,
                     duration_s: float) -> dict:
    session.assert_trigger_40hz()
    session.preflight()
    session.console.start_trigger()
    try:
        return meter.measure(duration_s)
    finally:
        session.console.stop_trigger()


def bringup(session: ConsoleSession, notes: list) -> None:
    """Cold-start laser bring-up + WI standing limits.

    apply_laser_power() programs 1000 us pulse-width upper limits from the
    SDK's bundled laser_params.json; the WI's standing default is 550 us, so
    they are tightened immediately after (laser is not firing yet).
    """
    session.console.stop_trigger()
    if not apply_laser_power(session.console):
        raise RuntimeError("apply_laser_power failed")
    for reg in ("EE_PULSE_WIDTH_UL", "OPT_PULSE_WIDTH_UL"):
        session.write_scaled(reg, WI_PULSE_WIDTH_UL_US)


# --- phases --------------------------------------------------------------
def phase_baseline(args) -> int:
    st = load_state()
    resolve_window(args, st)
    if "inventory" not in st:
        print("collecting sensor inventory (one-time, ~15s) ...")
        try:
            st["inventory"] = collect_sensor_inventory()
        except Exception as e:
            st["inventory"] = {"error": str(e)}
        save_state(st)
    meter = OphirMeter()
    session = ConsoleSession()
    try:
        st.setdefault("meter", meter.identity())
        st["meter_settings"] = [list(r) for r in meter.configure_for_wi()]
        st.setdefault("console", session.identity())
        if "prior_config" not in st:
            cfg = session.console.read_config()
            st["prior_config"] = cfg.json_data if cfg else None

        bringup(session, st["notes"])
        if "console_fpga" not in st.get("inventory", {}):
            st.setdefault("inventory", {})["console_fpga"] = \
                read_fpga_revisions(session)
        regs = {}
        for name in ("TA_PULSE_WIDTH", "TA_CURRENT_DRV", "SEED_CW_GAIN",
                     "EE_PULSE_WIDTH_UL", "OPT_PULSE_WIDTH_UL"):
            raw, scaled, unit = session.read_reg(name)
            regs[name] = {"raw": raw, "scaled": scaled, "unit": unit}
            print(f"  {name:<20} raw={raw:<8} {scaled:>9.2f} {unit}")

        print(f"\n*** FIRING LASER ({args.side}, {BASELINE_CAPTURE_S:g}s) ***")
        m = fire_and_measure(session, meter, BASELINE_CAPTURE_S)
        print("*** laser stopped ***")
        print(f"  {m['mean_uj']:.2f} uJ  sd {m['stdev_uj']:.2f}  "
              f"{m['rate_hz']:.2f} Hz  n={m['n']} (-{m['discarded']})")
        for crit, val, ok in check_rows(m["mean_uj"], m["stdev_uj"],
                                        m["rate_hz"], m["n"]):
            print(f"  [{'PASS' if ok else 'FAIL'}] {crit}: {val}")

        st["phases"].setdefault("baseline", {})[args.side] = {
            "when": dt.datetime.now().isoformat(timespec="seconds"),
            "registers": regs, "measurement": m,
        }
        save_state(st)

        done = sorted(st["phases"]["baseline"].keys())
        print(f"\nbaselines recorded: {done}")
        if len(done) == 2:
            e = {s: st["phases"]["baseline"][s]["measurement"]["mean_uj"] for s in done}
            higher = max(e, key=e.get)
            st["higher_side"] = higher
            save_state(st)
            over = [s for s in done if e[s] > SPEC31_MAX_UJ]
            under = [s for s in done if e[s] < SPEC31_MIN_UJ]
            print(f"energies: {e} -> higher side: {higher}")
            if over:
                print(f"NEXT: over-max ({over}) -> seat the '{higher}' module and run: tune")
            elif under:
                print(f"NEXT: under-min ({under}) -> per ruling 1, skip ahead: seat "
                      f"'{higher}' and run: tune (no adjustment) then finalize")
            else:
                print(f"NEXT: both in window -> seat '{higher}' and run: tune")
        return 0
    finally:
        session.close()
        meter.close()


def phase_tune(args) -> int:
    """WI steps 18-19 branch + section 4.4 ADC capture, higher module seated."""
    st = load_state()
    apply_window(st)
    base = st.get("phases", {}).get("baseline", {})
    if len(base) < 2:
        print("FAIL: need both baselines first")
        return 1
    higher = st["higher_side"]
    if args.seated != higher:
        print(f"FAIL: WI ruling 2 requires the higher-power module ('{higher}') "
              f"seated for tuning and the section 4.4 ADC reads; you said "
              f"'{args.seated}'. Swap and re-run.")
        return 1

    energies = {s: base[s]["measurement"]["mean_uj"] for s in base}
    meter = OphirMeter()
    session = ConsoleSession()
    try:
        meter.configure_for_wi()
        bringup(session, st["notes"])
        tune = {"branch": None, "steps": [], "outcome": ""}

        if any(e > SPEC31_MAX_UJ for e in energies.values()):
            # WI step 19: reduce TA current 50 mA at a time until below max.
            tune["branch"] = "reduce-current (step 19)"
            cur = session.read_reg("TA_CURRENT_DRV")[1]
            print(f"=== step 19: reducing TA current from {cur:.0f} mA ===")
            for i in range(1, TUNE_MAX_STEPS + 1):
                m = fire_and_measure(session, meter, LOOP_CAPTURE_S)
                actual = session.read_reg("TA_CURRENT_DRV")[1]
                tune["steps"].append({"index": i, "ta_current_ma": actual,
                                      "mean_uj": m["mean_uj"],
                                      "stdev_uj": m["stdev_uj"], "n": m["n"]})
                print(f"  step {i:2d}: {actual:7.1f} mA -> {m['mean_uj']:7.2f} uJ "
                      f"(sd {m['stdev_uj']:.2f}, n={m['n']})")
                if m["mean_uj"] < SPEC31_MAX_UJ:
                    tune["outcome"] = (f"MET: {m['mean_uj']:.2f} uJ < "
                                       f"{SPEC31_MAX_UJ:g} uJ at {actual:.1f} mA")
                    break
                nxt = actual - TA_CURRENT_STEP_MA
                if nxt < TA_CURRENT_FLOOR_MA:
                    tune["outcome"] = (f"FLOOR: refusing to step below "
                                       f"{TA_CURRENT_FLOOR_MA:g} mA - NCR")
                    break
                session.write_scaled("TA_CURRENT_DRV", nxt)
            else:
                tune["outcome"] = f"MAX STEPS ({TUNE_MAX_STEPS}) reached - NCR"
        elif any(e < SPEC31_MIN_UJ for e in energies.values()):
            # Ruling 1: under-min does NOT escalate pulse width in the
            # production flow - it skips ahead to section 4.4. The unit's
            # SPEC-31 line will show FAIL; diagnose-low exists for the sweep.
            tune["branch"] = "under-min: skip ahead to 4.4 (ruling 1)"
            tune["outcome"] = "no adjustment made"
            print("under-min -> skipping ahead to section 4.4 per ruling 1")
        else:
            tune["branch"] = "in window: no tuning required"
            tune["outcome"] = "no adjustment made"
            print("both modules in window - no tuning needed")

        print(f"  outcome: {tune['outcome']}")

        # --- Section 4.4: safety ADC at FINAL tuned values, higher module
        # seated (ruling 2), averaged over several reads (ruling 9), sampled
        # while the laser fires (the register latches when stopped).
        print(f"\n=== section 4.4: safety ADC capture ({ADC_SAMPLES} reads) ===")
        session.assert_trigger_40hz()
        session.preflight()
        adc = {"EE_ADC_DATA": [], "OPT_ADC_DATA": []}
        session.console.start_trigger()
        try:
            time.sleep(SETTLE_S)
            for _ in range(ADC_SAMPLES):
                for name in adc:
                    adc[name].append(session.read_reg(name)[1])
                time.sleep(ADC_SAMPLE_GAP_S)
        finally:
            session.console.stop_trigger()
        ee_mean = statistics.fmean(adc["EE_ADC_DATA"])
        opt_mean = statistics.fmean(adc["OPT_ADC_DATA"])
        final = {
            "adc_reads": adc,
            "ee_adc_mean_ma": ee_mean, "opt_adc_mean_ma": opt_mean,
            # Ruling 3: EE 1.1x rounds DOWN. OPT 1.3x rounds NEAREST (WI text).
            "EE_DRIVE_CL": int(ee_mean * EE_MULT),
            "OPT_DRIVE_CL": round(opt_mean * OPT_MULT),
            "seated": args.seated,
        }
        ta_pw = session.read_reg("TA_PULSE_WIDTH")[1]
        ta_cur = session.read_reg("TA_CURRENT_DRV")[1]
        final["TA_PULSE_WIDTH"] = round(ta_pw)
        final["TA_CURRENT_DRV"] = round(ta_cur)
        final["PW_UL"] = round(final["TA_PULSE_WIDTH"] * PW_UL_MULT)  # step 32, nearest
        print(f"  EE  ADC {[f'{v:.1f}' for v in adc['EE_ADC_DATA']]} "
              f"-> mean {ee_mean:.1f} mA -> EE_DRIVE_CL {final['EE_DRIVE_CL']}")
        print(f"  OPT ADC {[f'{v:.1f}' for v in adc['OPT_ADC_DATA']]} "
              f"-> mean {opt_mean:.1f} mA -> OPT_DRIVE_CL {final['OPT_DRIVE_CL']}")
        print(f"  TA {final['TA_PULSE_WIDTH']} us / {final['TA_CURRENT_DRV']} mA "
              f"-> PW_UL {final['PW_UL']} us")

        st["phases"]["tune"] = {"when": dt.datetime.now().isoformat(timespec="seconds"),
                                **tune}
        st["phases"]["sec44"] = final
        save_state(st)
        other = [s for s in ("left", "right") if s != higher][0]
        print(f"\nNEXT: seat the '{other}' module and run: crosscheck --seated {other}")
        return 0
    finally:
        session.close()
        meter.close()


def phase_crosscheck(args) -> int:
    """WI steps 21-22: re-measure the other module at the final settings."""
    st = load_state()
    apply_window(st)
    if "tune" not in st.get("phases", {}):
        print("FAIL: run tune first")
        return 1
    higher = st["higher_side"]
    other = [s for s in ("left", "right") if s != higher][0]
    if args.seated != other:
        print(f"FAIL: crosscheck wants the '{other}' module seated")
        return 1

    meter = OphirMeter()
    session = ConsoleSession()
    try:
        meter.configure_for_wi()
        bringup(session, st["notes"])
        # Re-assert the tuned current (bringup rewrites laser_params defaults,
        # then user-config overrides - but the tuned value is not in EPROM
        # yet, so program it explicitly from state).
        tuned = st["phases"]["sec44"]["TA_CURRENT_DRV"]
        session.write_scaled("TA_CURRENT_DRV", tuned)
        print(f"TA current re-asserted to tuned {tuned} mA")

        print(f"\n*** FIRING LASER ({other}, {BASELINE_CAPTURE_S:g}s) ***")
        m = fire_and_measure(session, meter, BASELINE_CAPTURE_S)
        print("*** laser stopped ***")
        in_window = SPEC31_MIN_UJ <= m["mean_uj"] <= SPEC31_MAX_UJ
        print(f"  {m['mean_uj']:.2f} uJ  sd {m['stdev_uj']:.2f}  "
              f"{m['rate_hz']:.2f} Hz  n={m['n']}")
        print(f"  window {SPEC31_MIN_UJ:g}-{SPEC31_MAX_UJ:g}: "
              f"{'PASS' if in_window else 'FAIL (NCR per WI step 22)'}")

        st["phases"]["crosscheck"] = {
            "when": dt.datetime.now().isoformat(timespec="seconds"),
            "side": other, "measurement": m, "in_window": in_window,
        }
        save_state(st)
        print("\nNEXT: run finalize   (writes EPROM, power-cycles via Shelly, "
              "verifies persistence, emits the PDF)")
        return 0
    finally:
        session.close()
        meter.close()


def shelly_power(on: bool) -> None:
    if not SHELLY_HOST:
        raise RuntimeError("no SHELLY_IP_ADDRESS configured")
    url = (f"http://{SHELLY_HOST}/rpc/Switch.Set?id={SHELLY_RELAY}"
           f"&on={'true' if on else 'false'}")
    with urllib.request.urlopen(url, timeout=10) as r:
        r.read()


def _print_utf8(text: str) -> None:
    """Print text that may contain emoji without dying on cp1252 consoles.

    Windows pipes default Python's stdout to the locale encoding; try to
    upgrade it to UTF-8 once, and if printing still fails, degrade the text
    rather than crash a hardware procedure over a pictogram.
    """
    try:
        print(text)
        return
    except UnicodeEncodeError:
        pass
    try:
        import sys as _sys
        if hasattr(_sys.stdout, "reconfigure"):
            _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)
    except Exception:
        print(text.encode("ascii", "replace").decode())


def request_power_cycle() -> str:
    """Cycle console mains: automated when WI15_SHELLY_HOST is set, otherwise
    instruct the operator (same prompt protocol as the guided runner, so GUI
    hosts surface a Continue button and a simple-language instruction).
    Returns a description for the run record."""
    if SHELLY_HOST:
        print(f"power-cycling via Shelly {SHELLY_HOST} "
              f"({POWER_OFF_DWELL_S:g}s dwell) ...")
        shelly_power(False)
        time.sleep(POWER_OFF_DWELL_S)
        shelly_power(True)
        return f"Shelly {SHELLY_HOST}, {POWER_OFF_DWELL_S:g}s dwell"
    _print_utf8("@@SIMPLE \U0001f50c Turn the machine OFF with the power "
                "switch. Count to 15. Turn it ON again. Then press "
                "Continue ▶️")
    try:
        answer = input(
            f"\n>>> MANUAL POWER CYCLE: switch the console mains OFF, wait "
            f"{POWER_OFF_DWELL_S:g} seconds, switch it back ON, then "
            f"confirm.\n    [y to continue, anything else aborts] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "y":
        raise RuntimeError("operator aborted the manual power cycle")
    return "manual operator power cycle (SHELLY_IP_ADDRESS not set)"


def console_uptime_ms(session: "ConsoleSession") -> int | None:
    """Firmware uptime of the connected console, from telemetry timestamps.
    Used to prove a power cycle actually happened (uptime resets at boot)."""
    try:
        samples = session.console.get_temperatures(return_all=True)
        if samples:
            return int(samples[-1].timestamp_ms)
    except Exception:
        pass
    return None


def phase_finalize(args) -> int:
    """Write EPROM (4.3/4.5), power-cycle + verify persistence, emit the PDF."""
    st = load_state()
    apply_window(st)
    sec44 = st.get("phases", {}).get("sec44")
    if not sec44:
        print("FAIL: run tune first")
        return 1

    session = ConsoleSession()
    try:
        prior = st.get("prior_config") or {}
        new_cfg = {
            "TA_PULSE_WIDTH": sec44["TA_PULSE_WIDTH"],
            "TA_CURRENT_DRV": sec44["TA_CURRENT_DRV"],
            "SEED_CW_GAIN": 140,
            "EE_PULSE_WIDTH_UL": sec44["PW_UL"],
            "EE_RATE_LL": 23125,
            "EE_DRIVE_CL": sec44["EE_DRIVE_CL"],
            "OPT_PULSE_WIDTH_UL": sec44["PW_UL"],
            "OPT_RATE_LL": 23125,
            "OPT_DRIVE_CL": sec44["OPT_DRIVE_CL"],
        }
        # Ruling 7: factory-new EPROM is empty; extra keys are permitted in the
        # final config. Preserve calibration/TEC_TRIP when present; drop legacy
        # EE/OPT THRESH+GAIN (laser.py prefers them over DRIVE_CL - keeping
        # them would silently defeat the tuned limits).
        for keep in ("calibration", "TEC_TRIP"):
            if keep in prior:
                new_cfg[keep] = prior[keep]
        dropped = [k for k in ("EE_THRESH", "EE_GAIN", "OPT_THRESH", "OPT_GAIN")
                   if k in prior]
        if dropped:
            st["notes"].append(f"Dropped legacy override keys: {dropped}")

        print("writing User Configuration to EPROM ...")
        session.console.write_config_json(json.dumps(new_cfg))
        time.sleep(0.5)

        session.close()
        cycle_desc = request_power_cycle()
        print("waiting for console ...")

        deadline = time.time() + RECONNECT_TIMEOUT_S
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
        print(f"console back on {session.console.uart.port}")

        # Prove the cycle happened: firmware uptime resets at boot. Catches
        # an operator confirming without flipping the switch (and a Shelly
        # misfire) - without a real power loss, persistence is unproven.
        uptime = console_uptime_ms(session)
        rebooted = uptime is not None and uptime < MAX_UPTIME_AFTER_CYCLE_MS
        if uptime is None:
            print("! could not read console uptime - reboot unconfirmed")
        elif not rebooted:
            print(f"FAIL: console uptime is {uptime/1000:.0f}s - it did NOT "
                  f"power cycle; persistence is not proven")
        else:
            print(f"reboot confirmed (uptime {uptime/1000:.0f}s)")

        back = session.console.read_config()
        readback = back.json_data if back else {}
        verify = []
        for k, v in new_cfg.items():
            got = readback.get(k)
            ok = got == v
            shown_v = "(preserved block)" if k == "calibration" else str(v)
            shown_g = "(present, match)" if k == "calibration" and ok else str(got) \
                if k != "calibration" else "(MISMATCH)"
            verify.append([k, shown_v, shown_g, ok])
            print(f"  {k:<20} {'OK' if ok else 'MISMATCH'}")
        st["phases"]["finalize"] = {
            "when": dt.datetime.now().isoformat(timespec="seconds"),
            "written": {k: v for k, v in new_cfg.items() if k != "calibration"},
            "verify": verify,
            "power_cycle": cycle_desc,
            "uptime_after_ms": uptime,
            "reboot_confirmed": rebooted,
            "all_ok": all(v[3] for v in verify) and rebooted,
        }
        save_state(st)

        out = os.path.join(OUT_DIR, f"WI-00015_{dt.datetime.now():%Y%m%d_%H%M%S}_full.pdf")
        build_pdf(st, out)
        print(f"\nreport: {out}")
        return 0 if st["phases"]["finalize"]["all_ok"] else 1
    finally:
        if session is not None:
            session.close()


def phase_diagnose_low(args) -> int:
    """DIAGNOSTIC ONLY - WI step 23 pulse-width sweep. Not in the production
    flow (ruling 1: under-min skips ahead). Restores safety limits AND the TA
    pulse width itself on every exit path; persists nothing."""
    meter = OphirMeter()
    session = ConsoleSession()
    try:
        meter.configure_for_wi()
        bringup(session, [])
        orig_pw = session.read_reg("TA_PULSE_WIDTH")[1]
        orig_ul = session.read_reg("EE_PULSE_WIDTH_UL")[1]
        print(f"sweep from {orig_pw:.1f} us; limits {orig_ul:.1f} -> "
              f"{TEMP_PULSE_WIDTH_UL_US:g} us (temporary)")
        for reg in ("EE_PULSE_WIDTH_UL", "OPT_PULSE_WIDTH_UL"):
            session.write_scaled(reg, TEMP_PULSE_WIDTH_UL_US)
        try:
            target = orig_pw
            while target <= TA_PULSE_WIDTH_CEILING_US:
                session.write_scaled("TA_PULSE_WIDTH", target)
                m = fire_and_measure(session, meter, LOOP_CAPTURE_S)
                actual = session.read_reg("TA_PULSE_WIDTH")[1]
                print(f"  {actual:6.2f} us -> {m['mean_uj']:7.2f} uJ "
                      f"(sd {m['stdev_uj']:.2f}, n={m['n']})")
                if m["mean_uj"] >= SPEC31_MIN_UJ:
                    print(f"  target {SPEC31_MIN_UJ:g} uJ met at {actual:.2f} us "
                          f"(diagnostic only - NOT persisted)")
                    break
                target += 10.0
            else:
                print(f"  ceiling {TA_PULSE_WIDTH_CEILING_US:g} us reached "
                      f"without meeting {SPEC31_MIN_UJ:g} uJ")
        finally:
            session.write_scaled("TA_PULSE_WIDTH", orig_pw)
            for reg in ("EE_PULSE_WIDTH_UL", "OPT_PULSE_WIDTH_UL"):
                session.write_scaled(reg, orig_ul)
            print(f"restored: TA pulse width {orig_pw:.1f} us, limits {orig_ul:.1f} us")
        return 0
    finally:
        session.close()
        meter.close()


# --- PDF -----------------------------------------------------------------
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
    GRID = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP")]

    def table(rows, widths):
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle(GRID))
        return t

    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=0.75 * inch,
                            rightMargin=0.75 * inch, topMargin=0.75 * inch,
                            bottomMargin=0.75 * inch,
                            title="WI-00015 tuning record")
    el = []
    win = st.get("window", [SPEC31_MIN_UJ, SPEC31_MAX_UJ])
    win_src = st.get("window_source", "production default (SPEC-31)")
    el.append(Paragraph("Open-Motion Device Specific Parameter Tuning", h1))
    el.append(Paragraph(
        f"Automated tuning record - {WI_DOC}. Acceptance window in effect: "
        f"{win[0]:g}-{win[1]:g} uJ ({win_src}; production is 300-400 uJ).",
        body))
    el.append(Spacer(1, 10))

    con, met = st.get("console", {}), st.get("meter", {})
    el.append(Paragraph("1. Identification and equipment", h2))
    el.append(table([["Item", "Value"],
                     ["Run started", st.get("created", "?")],
                     ["Console serial / firmware",
                      f"{con.get('console_serial')} / {con.get('console_firmware')}"],
                     ["Energy meter", f"{met.get('meter')} s/n {met.get('meter_serial')}"],
                     ["Sensor head", f"{met.get('sensor')} s/n {met.get('sensor_serial')}"],
                     ["Calibration due (meter / head)",
                      f"{met.get('device_cal_due')} / {met.get('sensor_cal_due')}"]],
                    (2.3 * inch, 4.4 * inch)))
    el.append(Spacer(1, 10))

    inv = st.get("inventory") or {}
    if inv and "error" not in inv:
        el.append(Paragraph("1b. Device inventory (WI step 8)", h2))
        rows = [["Item", "Value"], ["SDK version", inv.get("sdk_version", "?")]]
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
        el.append(table(rows, (2.3 * inch, 4.4 * inch)))
        el.append(Spacer(1, 10))

    ms = st.get("meter_settings")
    if ms:
        el.append(Paragraph("2. Meter configuration (WI step 4)", h2))
        rows = [["Setting", "Required", "Applied", "OK"]]
        rows += [[a, b, c, "PASS" if d else "FAIL"] for a, b, c, d in ms]
        el.append(table(rows, (1.5 * inch, 1.2 * inch, 3.2 * inch, 0.6 * inch)))
        el.append(Spacer(1, 10))

    base = st.get("phases", {}).get("baseline", {})
    if base:
        el.append(Paragraph("3. Baseline energy (WI steps 12-17)", h2))
        rows = [["Side", "Energy", "Std dev", "Rate", "Pulses", "Discarded", "In window"]]
        for side, b in sorted(base.items()):
            m = b["measurement"]
            inw = SPEC31_MIN_UJ <= m["mean_uj"] <= SPEC31_MAX_UJ
            rows.append([side.capitalize(), f"{m['mean_uj']:.2f} uJ",
                         f"{m['stdev_uj']:.2f} uJ", f"{m['rate_hz']:.2f} Hz",
                         str(m["n"]), str(m["discarded"]), "yes" if inw else "NO"])
        el.append(table(rows, (0.8 * inch, 1.2 * inch, 1.0 * inch, 1.0 * inch,
                               0.8 * inch, 0.9 * inch, 0.9 * inch)))
        el.append(Spacer(1, 10))

    tune = st.get("phases", {}).get("tune")
    if tune:
        el.append(Paragraph("4. Tuning (WI steps 18-19)", h2))
        el.append(Paragraph(f"Branch: {tune.get('branch')}", body))
        if tune.get("steps"):
            rows = [["#", "TA current", "Energy", "Std dev", "Pulses"]]
            for s in tune["steps"]:
                rows.append([str(s["index"]), f"{s['ta_current_ma']:.1f} mA",
                             f"{s['mean_uj']:.2f} uJ", f"{s['stdev_uj']:.2f} uJ",
                             str(s["n"])])
            el.append(table(rows, (0.5 * inch, 1.4 * inch, 1.4 * inch,
                                   1.2 * inch, 0.9 * inch)))
        el.append(Paragraph(f"<b>Outcome:</b> {tune.get('outcome')}", body))
        el.append(Spacer(1, 10))

    sec44 = st.get("phases", {}).get("sec44")
    if sec44:
        el.append(Paragraph("5. Safety values (WI section 4.4)", h2))
        el.append(Paragraph(
            f"ADC sampled while firing at the final tuned parameters with the "
            f"'{sec44.get('seated')}' (higher-power) module seated; "
            f"{ADC_SAMPLES} reads averaged.", body))
        ee = ", ".join(f"{v:.1f}" for v in sec44["adc_reads"]["EE_ADC_DATA"])
        opt = ", ".join(f"{v:.1f}" for v in sec44["adc_reads"]["OPT_ADC_DATA"])
        el.append(table([
            ["Quantity", "Value"],
            ["EE ADC reads (mA)", ee],
            ["EE ADC mean", f"{sec44['ee_adc_mean_ma']:.1f} mA"],
            [f"EE_DRIVE_CL = floor({EE_MULT:g} x mean)", f"{sec44['EE_DRIVE_CL']} mA"],
            ["OPT ADC reads (mA)", opt],
            ["OPT ADC mean", f"{sec44['opt_adc_mean_ma']:.1f} mA"],
            [f"OPT_DRIVE_CL = round({OPT_MULT:g} x mean)", f"{sec44['OPT_DRIVE_CL']} mA"],
            ["Final TA pulse width / current",
             f"{sec44['TA_PULSE_WIDTH']} us / {sec44['TA_CURRENT_DRV']} mA"],
            [f"Pulse-width UL = round({PW_UL_MULT:g} x PW)", f"{sec44['PW_UL']} us"]],
            (3.2 * inch, 3.5 * inch)))
        el.append(Spacer(1, 10))

    cc = st.get("phases", {}).get("crosscheck")
    if cc:
        m = cc["measurement"]
        el.append(Paragraph("6. Cross-check (WI steps 21-22)", h2))
        el.append(table([
            ["Side", "Energy", "Std dev", "Rate", "Pulses", "Window", "Result"],
            [cc["side"].capitalize(), f"{m['mean_uj']:.2f} uJ",
             f"{m['stdev_uj']:.2f} uJ", f"{m['rate_hz']:.2f} Hz", str(m["n"]),
             f"{SPEC31_MIN_UJ:g}-{SPEC31_MAX_UJ:g} uJ",
             "PASS" if cc["in_window"] else "FAIL (NCR)"]],
            (0.7 * inch, 1.1 * inch, 1.0 * inch, 0.9 * inch, 0.7 * inch,
             1.1 * inch, 1.1 * inch)))
        el.append(Spacer(1, 10))

    fin = st.get("phases", {}).get("finalize")
    if fin:
        el.append(Paragraph("7. EPROM write and power-cycle verification "
                            "(WI sections 4.3/4.5)", h2))
        up = fin.get("uptime_after_ms")
        reboot_txt = ("reboot confirmed by firmware uptime "
                      f"({up/1000:.0f} s)" if fin.get("reboot_confirmed")
                      else "REBOOT NOT CONFIRMED - persistence unproven")
        el.append(Paragraph(
            f"Power cycle: {fin['power_cycle']}; {reboot_txt}.", body))
        rows = [["Key", "Written", "Read back after cycle", "Result"]]
        for k, wrote, got, ok in fin["verify"]:
            rows.append([k, wrote, got, "OK" if ok else "MISMATCH"])
        el.append(table(rows, (1.9 * inch, 1.7 * inch, 1.9 * inch, 0.9 * inch)))
        el.append(Spacer(1, 6))
        el.append(Paragraph(
            f"<b>Persistence: {'VERIFIED' if fin['all_ok'] else 'FAILED'}</b>", body))
        el.append(Spacer(1, 10))

    if st.get("notes"):
        el.append(Paragraph("8. Notes", h2))
        for n in st["notes"]:
            el.append(Paragraph(f"&bull; {n}", body))

    el.append(PageBreak())
    el.append(Paragraph("Appendix A. Console User Configuration prior to this run", h2))
    el.append(Paragraph(
        "Verbatim EPROM contents as read before any modification, retained "
        "for reference.", body))
    el.append(Spacer(1, 8))
    text = json.dumps(st.get("prior_config"), indent=2) \
        if st.get("prior_config") is not None else "(could not be read)"
    for line in text.splitlines():
        el.append(Preformatted(line if line.strip() else " ", mono))
    doc.build(el)
