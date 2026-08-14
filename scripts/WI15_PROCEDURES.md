# WI-00015 supported procedures

The team-approved automated process defines four operator-facing procedures:

1. Single-Sensor Laser Calibration - implemented by
   `wi15_single_sensor_laser_calibration.py`.
2. Dual-Sensor Laser Calibration - implemented by
   `wi15_dual_sensor_laser_calibration.py`.
3. Safety Calibration - implemented by `wi15_safety_calibration.py`.
4. Measurement Calibration - implemented by
   `wi15_measurement_calibration.py` as a thin runner around the SDK
   calibration engine (one sensor per run; phantom attestation required).
   The auditable evidence workflow in the Measurement Calibration
   specification remains future work.

From the repository root, run the implemented procedure with:

```powershell
python -m scripts.wi15_single_sensor_laser_calibration
```

For the dual-sensor procedure, run:

```powershell
python -m scripts.wi15_dual_sensor_laser_calibration --output-dir C:\WI15_runs
```

For Safety Calibration, run:

```powershell
python -m scripts.wi15_safety_calibration --output-dir C:\WI15_runs
```

The safety script asks for the declared shipping topology before constructing
hardware; declare `console-only` to run without any sensor modules, in which
case the final normal scan is recorded as not applicable. For the other
declarations, connect exactly that topology for the final normal scan; extra
modules may remain connected during the console-only ADC portion. The script
does not use an external energy meter. The persistence check has no
confirmation gates: the script instructs the operator to power the console
off and back on, observes the disconnect and reconnect itself, and fails the
run if the measured off dwell is under 1 second (the console was cycled too
quickly for the dwell to be provable).

Keep the console plus both left and right sensor modules connected. The dual
script asks the operator to move the identified sensor module into the Ophir
0 cm fixture only when the required side changes. Consecutive measurements of
the same seated sensor do not repeat the placement prompt. Every prompt names
the side, serial number, and procedure phase for auditability.

The module form ensures the checkout's `omotion` package is used. Both scripts
collect required report metadata before hardware construction. Placement
confirmation occurs immediately before the associated measurement.

The authority for execution is the WI-00015 automated process addendum and its
linked procedure specifications. The historical combined runner is prototype
and reference code retained only on `feature/214-wi15-tuning-runner`; it is
not a supported procedure on this branch.
