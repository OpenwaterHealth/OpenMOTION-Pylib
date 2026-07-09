"""SPI-mirror probe: capture DAC SPI (D0=SCK, D1=MOSI, D2=SS) and MOD SCK
(D3) while exercising set_demod_config and gain writes in DIRECT mode.

Decodes SS-framed DAC SPI bursts from the raw digital export and reports
frame contents (expect 24-bit 0x31xxxx = cmd3 addr1 data16).
"""

import os
import sys
import threading
import time

import numpy as np
import pandas as pd
from saleae import automation

SEED_MUX, SEED_CHANNEL, SEED_I2C_ADDR = 1, 5, 0x41
REG_DDS_GAIN, REG_DDS_CL, REG_STATIC_CTRL = 0x02, 0x06, 0x20
PROD_CL = 864
D_SCK, D_MOSI, D_SS, D_MODSCK = 0, 1, 2, 3


def decode_dac_frames(df):
    """Return list of (t_start, nbits, value) for each SS-low burst."""
    t = df["Time [s]"].to_numpy()
    sck = df[[c for c in df.columns if f"Channel {D_SCK}" in c][0]].to_numpy().astype(int)
    mosi = df[[c for c in df.columns if f"Channel {D_MOSI}" in c][0]].to_numpy().astype(int)
    ss = df[[c for c in df.columns if f"Channel {D_SS}" in c][0]].to_numpy().astype(int)
    frames = []
    in_frame = False
    bits = []
    t0 = None
    prev_sck = sck[0] if len(sck) else 0
    for i in range(1, len(t)):
        if not in_frame and ss[i] == 0 and (ss[i-1] == 1):
            in_frame, bits, t0 = True, [], t[i]
        elif in_frame and ss[i] == 1 and ss[i-1] == 0:
            if bits:
                v = 0
                for b in bits:
                    v = (v << 1) | b
                frames.append((t0, len(bits), v))
            in_frame = False
        if in_frame and sck[i] == 1 and prev_sck == 0:
            bits.append(mosi[i])
        prev_sck = sck[i]
    return frames


def main() -> int:
    out = os.path.abspath(os.path.join(
        "scan_data", "demod-characterization",
        time.strftime("spi-probe-direct-%H%M%S")))
    os.makedirs(out, exist_ok=True)

    from omotion import MotionInterface
    mgr = automation.Manager.connect(port=10430)
    dev_cfg = automation.LogicDeviceConfiguration(
        enabled_digital_channels=[D_SCK, D_MOSI, D_SS],
        digital_sample_rate=125_000_000,
    )
    cap_cfg = automation.CaptureConfiguration(
        capture_mode=automation.TimedCaptureMode(duration_seconds=8.0))

    motion = MotionInterface(data_dir=None, scan_db_path=None,
                             operator_id="spi-probe")
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

        assert motion.apply_laser_power()
        w16(REG_DDS_CL, 1111)
        console.set_trigger_json(motion.default_trigger_config)
        console.start_trigger()
        w16(REG_STATIC_CTRL, 0x0003)

        stimulus_log = []

        def stimulus():
            time.sleep(1.0)
            stimulus_log.append(("set_demod_config", time.time()))
            console.set_demod_config({"DemodPulseInterval": 0,
                                      "ModulationFrequencyWord": 65535,
                                      "ModulationPhaseWord": 0})
            for wv in (600, 1060, 0):
                time.sleep(1.5)
                stimulus_log.append((f"gain<-{wv}", time.time()))
                w16(REG_DDS_GAIN, wv)

        th = threading.Thread(target=stimulus)
        with mgr.start_capture(device_configuration=dev_cfg,
                               capture_configuration=cap_cfg) as cap:
            th.start()
            cap.wait()
            cap.export_raw_data_csv(directory=out,
                                    digital_channels=[D_SCK, D_MOSI, D_SS])
        th.join()

        df = pd.read_csv(os.path.join(out, "digital.csv"))
        print("rows:", len(df), "cols:", list(df.columns))
        frames = decode_dac_frames(df)
        print(f"\nDAC SPI frames captured: {len(frames)}")
        for t0, nbits, v in frames[:20]:
            print(f"  t={t0:.4f}s  {nbits} bits  0x{v:0{max(1,(nbits+3)//4)}X}")
        # MOD SCK activity
        modcol = [c for c in df.columns if f"Channel {D_MODSCK}" in c][0]
        mod = df[modcol].to_numpy().astype(int)
        edges = int(np.sum(np.abs(np.diff(mod))))
        print(f"\nMOD_SCK (D3) edges in capture: {edges}")
        print("\nstimulus timeline:", stimulus_log)
        print("raw ->", out)
        return 0
    finally:
        try:
            w16(REG_DDS_GAIN, 0)
            w16(REG_STATIC_CTRL, 0x0000)
            w16(REG_DDS_CL, PROD_CL)
            console.stop_trigger()
        except Exception as e:
            print("restore warn:", e)
        motion.stop()
        mgr.close()


if __name__ == "__main__":
    sys.exit(main())
