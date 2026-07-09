"""E1 — gain staircase stimulus for the Saleae plateau-localization capture.

Companion to docs/superpowers/specs/2026-06-13-demod-hw-verification-plan.md
(addendum 2). Runs WITHOUT a scan: laser params -> CL raise (ordering!) ->
demod config 6.1 kHz -> trigger on -> arm -> step the gain-word ladder with a
fixed dwell, printing a wall-clock timestamp per step so the analog capture
can be aligned. Restores gain 0 / CL 864 / disarm / trigger off on any exit.

Usage:
  PYTHONPATH=<worktree> python scripts/demod_e1_staircase.py [--dwell 4]
      [--dds-cl 1111] [--freq-word 65535]
      [--ladder 0,200,400,600,800,860,880,920,1000,1060,1100]
"""

import argparse
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dwell", type=float, default=4.0)
    ap.add_argument("--dds-cl", type=lambda v: int(v, 0), default=1111)
    ap.add_argument("--freq-word", type=lambda v: int(v, 0), default=65535)
    ap.add_argument("--ladder", default="0,200,400,600,800,860,880,920,1000,1060,1100")
    args = ap.parse_args(argv)
    ladder = [int(x) for x in args.ladder.split(",")]
    if max(ladder) >= args.dds_cl:
        print(f"ERROR: ladder max {max(ladder)} >= DDS_CL {args.dds_cl}; the "
              f"gate would eat it", file=sys.stderr)
        return 2

    from omotion import MotionInterface

    motion = MotionInterface(data_dir=None, scan_db_path=None,
                             operator_id="demod-e1")
    motion.start()
    console = motion.console
    armed = trigger_on = False
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not console.is_connected():
            time.sleep(0.5)
        if not console.is_connected():
            print("ERROR: console did not connect", file=sys.stderr)
            return 1

        def w16(reg, val):
            ok = console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                          reg, bytes([val & 0xFF, (val >> 8) & 0xFF]))
            if not ok:
                raise RuntimeError(f"I2C write reg 0x{reg:02X} failed")

        def r16(reg):
            d, ln = console.read_i2c_packet(SEED_MUX, SEED_CHANNEL,
                                            SEED_I2C_ADDR, reg, 2)
            return int.from_bytes(bytes(d[:2]), "little") if d is not None and ln == 2 else None

        # 1. laser params FIRST (they clobber CL/gain), then the CL raise.
        if not motion.apply_laser_power():
            print("ERROR: apply_laser_power failed", file=sys.stderr)
            return 1
        w16(REG_DDS_CL, args.dds_cl)
        if r16(REG_DDS_CL) != args.dds_cl:
            print("ERROR: DDS_CL readback mismatch", file=sys.stderr)
            return 1
        print(f"DDS_CL = {args.dds_cl} (verified)")

        # 2. demod config (frequency), continuous-arm, trigger 40 Hz.
        cfg = console.set_demod_config({
            "DemodPulseInterval": 0,
            "ModulationFrequencyWord": args.freq_word,
            "ModulationPhaseWord": 0,
        })
        if cfg is None:
            print("ERROR: SET_DEMOD rejected", file=sys.stderr)
            return 1
        print(f"demod config: {cfg}")
        console.set_trigger_json(motion.default_trigger_config)
        if not console.start_trigger():
            print("ERROR: start_trigger failed", file=sys.stderr)
            return 1
        trigger_on = True
        w16(REG_STATIC_CTRL, 0x0001)
        armed = True
        print("trigger running, modulation armed")
        print()
        print(">>> START THE SALEAE CAPTURE NOW — ladder begins in 5 s <<<")
        time.sleep(5)

        # 3. the ladder.
        t0 = time.time()
        for word in ladder:
            w16(REG_DDS_GAIN, word)
            rb = r16(REG_DDS_GAIN)
            status = r16(REG_STATUS)
            t = time.time() - t0
            flag = "OK " if rb == word else f"MISMATCH rb={rb}"
            print(f"t={t:6.2f}s  word={word:5d} (~{word*0.081:5.1f} mA cmd)  "
                  f"readback {flag}  STATUS=0x{status:04X}")
            time.sleep(args.dwell)
        print(f"t={time.time()-t0:6.2f}s  ladder done")
        return 0
    finally:
        try:
            w16(REG_DDS_GAIN, 0)
            if armed:
                w16(REG_STATIC_CTRL, 0x0000)
            w16(REG_DDS_CL, PROD_CL)
            if trigger_on:
                console.stop_trigger()
            print(f"restored: gain 0, CL {PROD_CL}, disarmed, trigger off "
                  f"(readback gain={r16(REG_DDS_GAIN)}, cl={r16(REG_DDS_CL)})")
        except Exception as e:
            print(f"WARNING: restore incomplete: {e}", file=sys.stderr)
        motion.stop()


if __name__ == "__main__":
    sys.exit(main())
