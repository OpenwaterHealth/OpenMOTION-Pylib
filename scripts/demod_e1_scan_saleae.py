"""E1 ladder DURING A REAL SCAN, with integrated Saleae capture.

Same as demod_e1_saleae.py but laser bring-up happens via the production
ScanWorkflow (start_scan), which is the only path known to fully enable the
analog rail family / laser power sequence. Ladder steps run mid-scan.

Per gain-word step: write word -> settle -> 1 s Logic 2 capture on analog
ch 8/9/10/11 -> export -> measure. Prints the transfer table live.

Channel map (bench hookup 2026-07-09):
  ch8  = A0  VOUTA (R31 DAC-side)      -> DC staircase, mV
  ch9  = A1  DDS VOUT (R7 pad)         -> source triangle, pk-pk (sanity)
  ch10 = A2  TP1 = CW_MODULATE (W)     -> modulation amplitude at 6.1 kHz
  ch11 = A3  MAX4372 OUT (0.5 V/A)     -> DELIVERED current modulation

Usage:
  PYTHONPATH=<worktree> python scripts/demod_e1_saleae.py
      [--dwell-settle 0.4] [--capture-s 1.0] [--dds-cl 1111]
      [--freq-word 65535] [--sample-rate 625000]
      [--ladder 0,200,400,600,800,860,880,920,1000,1060,1100]
"""

import argparse
import math
import os
import sys
import time

SEED_MUX = 1
SEED_CHANNEL = 5
SEED_I2C_ADDR = 0x41
REG_DDS_GAIN = 0x02
REG_DDS_CL = 0x06
REG_STATUS = 0x12
REG_STATIC_CTRL = 0x20
PROD_CL = 864

CH_VOUTA, CH_DDS, CH_W, CH_CUR = 8, 9, 10, 11


def tone_amplitude(x, fs, f0, halfwidth_hz=25.0):
    """Single-sided amplitude of the strongest bin within f0±halfwidth."""
    import numpy as np
    n = len(x)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft((x - x.mean()) * win))
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    sel = (freqs >= f0 - halfwidth_hz) & (freqs <= f0 + halfwidth_hz)
    if not sel.any():
        return float("nan")
    # amplitude correction for Hann window: 2/sum(win)
    return float(spec[sel].max() * 2.0 / win.sum())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell-settle", type=float, default=0.4)
    ap.add_argument("--capture-s", type=float, default=1.0)
    ap.add_argument("--dds-cl", type=lambda v: int(v, 0), default=1111)
    ap.add_argument("--freq-word", type=lambda v: int(v, 0), default=65535)
    ap.add_argument("--sample-rate", type=int, default=625_000)
    ap.add_argument("--ladder", default="0,200,400,600,800,860,880,920,1000,1060,1100")
    ap.add_argument("--static-ctrl", type=lambda v: int(v, 0), default=0x0001,
                    help="STATIC_CTRL word: D0=arm, D1=laser_active (0x0003 for both)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    ladder = [int(x) for x in args.ladder.split(",")]
    if max(ladder) >= args.dds_cl:
        print("ERROR: ladder exceeds DDS_CL", file=sys.stderr)
        return 2

    import numpy as np
    import pandas as pd
    from saleae import automation
    from omotion import MotionInterface

    f_mod = args.freq_word * 25e6 / 2**28
    out_root = os.path.abspath(args.out or os.path.join(
        "scan_data", "demod-characterization",
        time.strftime("e1-saleae-%Y%m%d_%H%M%S")))
    os.makedirs(out_root, exist_ok=True)

    mgr = automation.Manager.connect(port=10430)
    dev_cfg = automation.LogicDeviceConfiguration(
        enabled_analog_channels=[CH_VOUTA, CH_DDS, CH_W, CH_CUR],
        analog_sample_rate=args.sample_rate,
    )
    cap_cfg = automation.CaptureConfiguration(
        capture_mode=automation.TimedCaptureMode(duration_seconds=args.capture_s))

    motion = MotionInterface(data_dir=None, scan_db_path=None,
                             operator_id="demod-e1-saleae")
    motion.start()
    console = motion.console
    armed = trigger_on = False
    rows = []
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not console.is_connected():
            time.sleep(0.5)
        if not console.is_connected():
            print("ERROR: console did not connect", file=sys.stderr)
            return 1

        def w16(reg, val):
            if not console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                            reg, bytes([val & 0xFF, (val >> 8) & 0xFF])):
                raise RuntimeError(f"I2C write reg 0x{reg:02X} failed")

        def r16(reg):
            d, ln = console.read_i2c_packet(SEED_MUX, SEED_CHANNEL,
                                            SEED_I2C_ADDR, reg, 2)
            return int.from_bytes(bytes(d[:2]), "little") if d is not None and ln == 2 else None

        if not motion.apply_laser_power():
            print("ERROR: apply_laser_power failed", file=sys.stderr)
            return 1
        w16(REG_DDS_CL, args.dds_cl)
        assert r16(REG_DDS_CL) == args.dds_cl, "DDS_CL readback mismatch"
        print(f"DDS_CL = {args.dds_cl} (verified); f_mod = {f_mod:.1f} Hz")

        if console.set_demod_config({
                "DemodPulseInterval": 0,
                "ModulationFrequencyWord": args.freq_word,
                "ModulationPhaseWord": 0}) is None:
            print("ERROR: SET_DEMOD rejected", file=sys.stderr)
            return 1
        from omotion.ScanWorkflow import ScanRequest
        scan_len = int(len(ladder) * (args.dwell_settle + args.capture_s + 4.0) + 30)
        req = ScanRequest(subject_id="e1-ladder", duration_sec=scan_len,
                          left_camera_mask=0, right_camera_mask=0x0F,
                          write_corrected_csv=False)
        if not motion.start_scan(req):
            print("ERROR: start_scan refused", file=sys.stderr)
            return 1
        trigger_on = True
        time.sleep(6.0)  # let the scan's laser-on sequence complete
        w16(REG_STATIC_CTRL, args.static_ctrl)
        armed = True
        print(f"SCAN running ({scan_len}s), modulation armed\n")

        hdr = (f"{'word':>5} {'cmd mA':>7} {'rb':>4} {'STATUS':>6} "
               f"{'VOUTA mV':>9} {'DDSpkpk mV':>10} "
               f"{'W@f mV':>8} {'I@f mV':>8} {'I@f mA':>8}")
        print(hdr)
        print("-" * len(hdr))

        for word in ladder:
            w16(REG_DDS_GAIN, word)
            rb = r16(REG_DDS_GAIN)
            status = r16(REG_STATUS)
            time.sleep(args.dwell_settle)

            step_dir = os.path.join(out_root, f"word_{word:05d}")
            os.makedirs(step_dir, exist_ok=True)
            with mgr.start_capture(device_configuration=dev_cfg,
                                   capture_configuration=cap_cfg) as cap:
                cap.wait()
                cap.export_raw_data_csv(
                    directory=step_dir,
                    analog_channels=[CH_VOUTA, CH_DDS, CH_W, CH_CUR])

            df = pd.read_csv(os.path.join(step_dir, "analog.csv"))
            cols = {c: c for c in df.columns}
            def col(ch):
                for c in df.columns:
                    if str(ch) in c and "Time" not in c:
                        return df[c].to_numpy()
                raise KeyError(ch)
            t = df[df.columns[0]].to_numpy()
            fs = 1.0 / float(np.median(np.diff(t[:10000])))

            vouta_mv = 1e3 * float(np.mean(col(CH_VOUTA)))
            dds_pkpk_mv = 1e3 * float(np.percentile(col(CH_DDS), 99.9)
                                      - np.percentile(col(CH_DDS), 0.1))
            w_amp_mv = 1e3 * tone_amplitude(col(CH_W), fs, f_mod)
            i_amp_mv = 1e3 * tone_amplitude(col(CH_CUR), fs, f_mod)
            i_amp_ma = i_amp_mv / 0.5  # MAX4372F 50 V/V x 10 mOhm = 0.5 V/A

            flag = "" if rb == word else "  <-- READBACK MISMATCH"
            print(f"{word:>5} {word*0.081:>7.1f} {('OK' if rb==word else str(rb)):>4} "
                  f"0x{status:04X} {vouta_mv:>9.2f} {dds_pkpk_mv:>10.1f} "
                  f"{w_amp_mv:>8.2f} {i_amp_mv:>8.2f} {i_amp_ma:>8.2f}{flag}")
            rows.append(dict(word=word, readback=rb, status=status,
                             vouta_mv=vouta_mv, dds_pkpk_mv=dds_pkpk_mv,
                             w_amp_mv=w_amp_mv, i_amp_mv=i_amp_mv,
                             i_amp_ma=i_amp_ma, fs=fs))

        pd.DataFrame(rows).to_csv(os.path.join(out_root, "transfer.csv"), index=False)
        motion.cancel_scan()
        print(f"\ntransfer table -> {os.path.join(out_root, 'transfer.csv')}")
        print(f"raw captures  -> {out_root}")
        return 0
    finally:
        try:
            motion.cancel_scan()
        except Exception:
            pass
        try:
            w16(REG_DDS_GAIN, 0)
            if armed:
                w16(REG_STATIC_CTRL, 0x0000)
            w16(REG_DDS_CL, PROD_CL)
            if trigger_on:
                console.stop_trigger()
            print(f"restored: gain 0, CL {PROD_CL}, disarmed, trigger off")
        except Exception as e:
            print(f"WARNING: restore incomplete: {e}", file=sys.stderr)
        motion.stop()
        mgr.close()


if __name__ == "__main__":
    sys.exit(main())
