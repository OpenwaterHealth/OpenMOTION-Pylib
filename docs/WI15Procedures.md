# WI-00015 supported procedures

The team-approved automated process defines four operator-facing procedures,
implemented as runnable modules in `omotion/scripts/` and shipped inside the
`omotion` wheel — any environment that can `import omotion` can run them, no
SDK checkout required:

1. Single-Sensor Laser Calibration —
   `omotion.scripts.wi15_single_sensor_laser_calibration`.
2. Dual-Sensor Laser Calibration —
   `omotion.scripts.wi15_dual_sensor_laser_calibration`.
3. Safety Calibration — `omotion.scripts.wi15_safety_calibration`.
4. Measurement Calibration —
   `omotion.scripts.wi15_measurement_calibration`, a thin runner around the
   SDK calibration engine (one sensor per run; phantom attestation required).
   The auditable evidence workflow in the Measurement Calibration
   specification remains future work.

Run a procedure from any directory with:

```powershell
python -m omotion.scripts.wi15_single_sensor_laser_calibration
```

For the dual-sensor procedure, run:

```powershell
python -m omotion.scripts.wi15_dual_sensor_laser_calibration --output-dir C:\WI15_runs
```

For Safety Calibration, run:

```powershell
python -m omotion.scripts.wi15_safety_calibration --output-dir C:\WI15_runs
```

Evidence lands under `--output-dir` (default: `wi15_out` in the current
directory). The test-app's Procedures pane runs these same modules as
subprocesses and passes `--output-dir` explicitly.

The safety script is console-side only: it requires a connected, responsive
console and nothing else. It asks no topology question - sensor modules may
be attached or absent, they are not used, and the production-scan stage is
recorded as not applicable. The script does not use an external energy
meter. The persistence check has no confirmation gates: the script instructs
the operator to power the console off and back on, observes the disconnect
and reconnect itself, and fails the run if the measured off dwell is under
1 second (the console was cycled too quickly for the dwell to be provable).

Keep the console plus both left and right sensor modules connected. The dual
script asks the operator to move the identified sensor module into the Ophir
0 cm fixture only when the required side changes. Consecutive measurements of
the same seated sensor do not repeat the placement prompt. Every prompt names
the side, serial number, and procedure phase for auditability.

All scripts collect required report metadata before hardware construction.
Placement confirmation occurs immediately before the associated measurement.

The authority for execution is the WI-00015 automated process addendum and its
linked procedure specifications. The historical combined runner is prototype
and reference code retained only on `feature/214-wi15-tuning-runner`; it is
not a supported procedure on this branch.
