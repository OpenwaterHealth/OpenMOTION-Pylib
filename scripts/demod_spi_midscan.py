"""Decode DAC SPI frames DURING a production scan.

collect runs in a subprocess with --rearm-interval 2 (static+gain re-written
every 2 s); we capture digital ch12 (SCK) + ch13 (MOSI) mid-scan and decode
frames by burst-gap framing, sampling MOSI on SCK FALLING edges (the FPGA
shifts MOSI on the rising edge; the AD5689R samples on falling).
"""

import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd
from saleae import automation

WORD = sys.argv[1] if len(sys.argv) > 1 else "1060"
WT = r"C:/Users/ethan/Projects/.worktrees/sdk-demod-config"
LABEL = f"spimid-a{WORD}-{time.strftime('%H%M%S')}"

env = dict(os.environ, PYTHONPATH=WT)
collect = subprocess.Popen(
    [sys.executable, "scripts/demod_characterization.py", "collect",
     "--label", LABEL, "--duration", "50", "--continuous",
     "--freq-word", "65535", "--phase-word", "0",
     "--mod-current-word", WORD, "--dds-cl", "1111",
     "--left-mask", "0", "--right-mask", "0x0F",
     "--rearm-interval", "2.0"],
    cwd=WT, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
print(f"collect launched (word {WORD}); capturing at t+40 s...")
time.sleep(40.0)

out = os.path.abspath(os.path.join("scan_data", "demod-characterization",
                                   f"{LABEL}-saleae"))
os.makedirs(out, exist_ok=True)
mgr = automation.Manager.connect(port=10430)
dev_cfg = automation.LogicDeviceConfiguration(
    enabled_digital_channels=[12, 13], digital_sample_rate=125_000_000)
cap_cfg = automation.CaptureConfiguration(
    capture_mode=automation.TimedCaptureMode(duration_seconds=10.0))
with mgr.start_capture(device_configuration=dev_cfg,
                       capture_configuration=cap_cfg) as cap:
    cap.wait()
    cap.export_raw_data_csv(directory=out, digital_channels=[12, 13])
mgr.close()
print("capture done; waiting for collect...")
tail = collect.communicate()[0].splitlines()
for ln in tail:
    if any(k in ln for k in ("static ctrl", "re-written", "rearm t=", "ERROR",
                             "wrote", "scan reported")):
        print("collect:", ln)

df = pd.read_csv(os.path.join(out, "digital.csv"))
t = df[df.columns[0]].to_numpy()
sck = df["Channel 12"].to_numpy().astype(int)
mosi = df["Channel 13"].to_numpy().astype(int)

falls = [(t[i], mosi[i]) for i in range(1, len(t)) if sck[i] == 0 and sck[i-1] == 1]
print(f"\nSCK falling edges in 10 s mid-scan window: {len(falls)}")
frames, cur = [], []
for tt, bit in falls:
    if cur and tt - cur[-1][0] > 10e-6:
        frames.append(cur)
        cur = []
    cur.append((tt, bit))
if cur:
    frames.append(cur)
print(f"frames: {len(frames)}")
for f in frames[:15]:
    bits = [b for _, b in f]
    v = 0
    for b in bits:
        v = (v << 1) | b
    print(f"  t={f[0][0]:.4f}s  {len(bits)} clks  raw=0x{v:06X}")
