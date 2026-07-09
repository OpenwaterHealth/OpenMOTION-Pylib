"""Launch a production collect run and take a Saleae capture mid-scan.

The collect subprocess owns the console; Logic 2 is external, so the capture
can run concurrently. Capture is delayed into the scan window and analyzed
for the DDS triangle / multiplier output / seed-current modulation.
"""

import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd
from saleae import automation

WORD = sys.argv[1] if len(sys.argv) > 1 else "1060"
EXTRA = sys.argv[2:]  # extra collect args, e.g. --trigger-json {...}
LABEL = f"e1mid-a{WORD}-{time.strftime('%H%M%S')}"
WT = r"C:/Users/ethan/Projects/.worktrees/sdk-demod-config"
F_MOD = 65535 * 25e6 / 2**28

env = dict(os.environ, PYTHONPATH=WT)
collect = subprocess.Popen(
    [sys.executable, "scripts/demod_characterization.py", "collect",
     "--label", LABEL, "--duration", "50", "--continuous",
     "--freq-word", "65535", "--phase-word", "0",
     "--mod-current-word", WORD, "--dds-cl", "1111",
     "--left-mask", "0", "--right-mask", "0x0F", "--rearm-interval", "2.0"] + EXTRA,
    cwd=WT, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

print(f"collect launched (word {WORD}); waiting 25 s for the scan window...")
time.sleep(40.0)

out = os.path.abspath(os.path.join("scan_data", "demod-characterization",
                                   f"{LABEL}-saleae"))
os.makedirs(out, exist_ok=True)
mgr = automation.Manager.connect(port=10430)
dev_cfg = automation.LogicDeviceConfiguration(
    enabled_analog_channels=[8, 9, 10, 11], analog_sample_rate=781_250)
cap_cfg = automation.CaptureConfiguration(
    capture_mode=automation.TimedCaptureMode(duration_seconds=10.0))
with mgr.start_capture(device_configuration=dev_cfg,
                       capture_configuration=cap_cfg) as cap:
    cap.wait()
    cap.export_raw_data_csv(directory=out, analog_channels=[8, 9, 10, 11])
mgr.close()
print("capture done; waiting for collect to finish...")
tail = collect.communicate()[0].splitlines()
for ln in tail:
    if any(k in ln for k in ("DDS_CL in effect", "modulation amplitude", "static ctrl", "re-written", "rearm t=", "mid-scan probe",
                             "ERROR", "wrote", "scan reported")):
        print("collect:", ln)

df = pd.read_csv(os.path.join(out, "analog.csv"))
t = df[df.columns[0]].to_numpy()
fs = 1.0 / np.median(np.diff(t[:10000]))


def tone(x, f0, hw=25):
    n = len(x)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft((x - x.mean()) * win))
    freqs = np.fft.rfftfreq(n, 1 / fs)
    sel = (freqs >= f0 - hw) & (freqs <= f0 + hw)
    return spec[sel].max() * 2 / win.sum() if sel.any() else float("nan")


print(f"\nmid-scan capture, word {WORD}, fs={fs/1e3:.0f} kS/s")
print(f"{'ch':12s} {'DC mV':>9} {'pkpk mV':>9} {'6.1k mV':>9}")
for c in df.columns[1:]:
    x = df[c].to_numpy()
    pkpk = (np.percentile(x, 99.9) - np.percentile(x, 0.1)) * 1e3
    print(f"{c:12s} {x.mean()*1e3:>9.2f} {pkpk:>9.1f} {tone(x, F_MOD)*1e3:>9.3f}")
