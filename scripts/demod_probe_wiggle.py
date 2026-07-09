"""Probe-seating helper: toggle the DDS gain word 0 <-> 1060 at ~1 Hz so a
correctly-seated A0 (VOUTA) probe shows an ~80 mV square in Logic 2's live
view. Laser chain brought up the same way as demod_e1_saleae. Runs for
--minutes then restores (also restores on Ctrl-C / kill).
"""

import argparse
import sys
import time

SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR = 1, 5, 0x41
REG_DDS_GAIN, REG_DDS_CL, REG_STATIC_CTRL = 0x02, 0x06, 0x20
PROD_CL = 864


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument("--hi-word", type=int, default=1060)
    ap.add_argument("--dds-cl", type=int, default=1111)
    args = ap.parse_args(argv)

    from omotion import MotionInterface
    motion = MotionInterface(data_dir=None, scan_db_path=None,
                             operator_id="demod-wiggle")
    motion.start()
    console = motion.console
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not console.is_connected():
            time.sleep(0.5)
        if not console.is_connected():
            print("ERROR: console did not connect", file=sys.stderr)
            return 1

        def w16(reg, val):
            console.write_i2c_packet(SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR,
                                     reg, bytes([val & 0xFF, (val >> 8) & 0xFF]))

        if not motion.apply_laser_power():
            print("ERROR: apply_laser_power failed", file=sys.stderr)
            return 1
        w16(REG_DDS_CL, args.dds_cl)
        console.set_demod_config({"DemodPulseInterval": 0,
                                  "ModulationFrequencyWord": 65535,
                                  "ModulationPhaseWord": 0})
        console.set_trigger_json(motion.default_trigger_config)
        console.start_trigger()
        w16(REG_STATIC_CTRL, 0x0003)
        print(f"wiggling gain 0 <-> {args.hi_word} for {args.minutes} min — "
              f"watch ch8 for an ~{args.hi_word*0.0763:.0f} mV square")
        end = time.monotonic() + args.minutes * 60
        hi = False
        while time.monotonic() < end:
            hi = not hi
            w16(REG_DDS_GAIN, args.hi_word if hi else 0)
            time.sleep(1.0)
        return 0
    finally:
        try:
            w16(REG_DDS_GAIN, 0)
            w16(REG_STATIC_CTRL, 0x0000)
            w16(REG_DDS_CL, PROD_CL)
            console.stop_trigger()
            print("restored: gain 0, CL 864, disarmed, trigger off")
        except Exception as e:
            print(f"WARNING: restore incomplete: {e}", file=sys.stderr)
        motion.stop()


if __name__ == "__main__":
    sys.exit(main())
