# WI-00015 rev 2 — redline suggestions from automation

Proposed edits to **WI-00015 Open-Motion Device Specific Parameter Tuning
rev 2 (ECO-000270)**, collected while building and hardware-validating the
automated runner (openmotion-sdk#214 / PR #215) on console QWW04Q10003,
2026-08-05/06. Interpretation rulings referenced below were given by Ethan
(ethan@openwater.cc) on 2026-08-05 and are recorded in
`scripts/WI15_RUNNER_README.md`.

Ordered by consequence, worst first.

## 1. Steps 30–31 — say the ADC value is in mA as displayed

**Current:** "Note the returned value, multiply it by 1.30 …" (step 30),
"… multiply it by 1.10 …" (step 31).

**Problem:** "the returned value" does not say whether it is the raw ADC
count or the scaled mA figure the TestApp displays. The TestApp shows
**scaled mA** (`rawValue × 1.86`) for ADC DATA; the User Configuration keys
are also in engineering units (verified by register round-trip: writing
`EE_DRIVE_CL: 4604` lands as raw 2475 = 4603.50 mA). An operator who reads
the sentence literally as "raw counts" programs `EE_DRIVE_CL ≈ 2476 mA`
against a ~4185 mA operating current — **the laser safety interlock trips on
the first pulse**. This misreading actually happened during automation
development and was caught only because the register math was checked.

**Proposed:** "Note the returned value **in mA as displayed by the FPGA I2C
Utility**, multiply it by …"

## 2. Section 4.4 — specify which module is seated during the ADC reads

**Current:** section 4.4 never says which Sensor Module (if any) should be in
the 0 cm detector while the Safety OPT/EE ADC values are read.

**Clarification (Ethan, 2026-08-06):** seating does NOT affect the readings
— there is one laser and one safety ADC, and the laser feeds both module
outputs continuously. (A ~5% Left/Right difference observed during
automation development was drift, not module dependence.) The WI therefore
needs no module-selection language for the ADC reads themselves; a module
must simply be seated somewhere for beam containment, and in practice it is
whichever module the section 4.2 adjustment branch used.

**Proposed:** state in step 29 that the ADC readings are independent of
which Sensor Module is in the detector, so operators do not perform an
unnecessary swap before section 4.4.

Additionally, consider **moving the section 4.4 ADC readings to immediately
after step 19's loop**, before the step 21–22 cross-check: the ADC values
depend only on the final tuned settings (frozen at loop exit) and the
cross-check changes no settings, so the readings are identical — and the
literal order costs two extra fixture insertions (seat higher, seat lower
for 21, re-seat higher for 4.4).

Also worth stating: the ADC values must be read **while the laser is
firing** — the register latches its last value after Stop Trigger, so a
late read looks plausible but is stale (reads 0 before the first fire).
The WI's step ordering implies this but never states the latching behavior.

## 3. Step 18 vs steps 23–26 — contradictory under-minimum routing

**Current:** step 18's expected result sends a module measuring below the
minimum "ahead to section 4.4", while steps 23–26 describe a pulse-width
escalation procedure for the same condition.

**Recommendation (Ethan, 2026-08-06, superseding the earlier informal
ruling):** the under-threshold case should route **through the step 23
escalation**, not skip to section 4.4. Step 18's "skip ahead to section
4.4" is the erroneous text — note its own instruction to "place it back
into the 0cm detector" already matches step 23's setup (lowest-energy
module seated), so the minimal fix is replacing "skip ahead to section 4.4"
with "proceed to step 23". The automated runner implements this routing.

**Executed live (2026-08-06, console WWW04Q40010):** with both modules
under-threshold (L 224.2 / R 219.6 µJ), the runner routed through step 23:
lowest module seated, 11 steps of +10 µs from 500 to 599 µs, energy rising
220.1 → 265.2 µJ — ceiling reached without meeting 300 µJ, unit dispositioned
NCR and the procedure ended per step 23's failure clause, with the default
configuration restored. Both the recommended routing and the ceiling-failure
clause are therefore proven executable exactly as proposed.

**Measured data for the decision** (both bench units, step-23 sweep executed
diagnostically): energy vs TA pulse width is cleanly linear — unit 1
(dim): 0.289 µJ/µs from a 163 µJ base, 600 µs ceiling reaches ~191 µJ;
unit 2 (console WWW04Q40010): 0.419 µJ/µs from a 219 µJ base, ceiling
reaches **260.6 µJ — still 13% under the 300 µJ floor** (extrapolated
~694 µs would be needed). The escalation path can therefore only rescue
units within roughly **14% of the floor (~260 µJ and up)**; anything dimmer
fails at the ceiling regardless. Whichever routing the WI settles on, it
should be chosen knowing the escalation's actual rescue band is this narrow.

**Proposed:** in step 18, replace "skip ahead to section 4.4 where the
laser safety settings are determined" with "proceed to step 23". Keep steps
23–26 as the mandatory under-threshold path; step 23's existing
ceiling-failure clause ("the unit fails … end the procedure") already
handles units the escalation cannot rescue.

## 4. Step 31 — rounding direction contradicts its own results column

**Current:** step text says "rounding to the nearest integer"; the Expected
Results column says "round down to the nearest integer".

**Ruling 3:** round **down** for the EE 1.1× value. (OPT 1.3×, step 30,
stays round-to-nearest.)

**Proposed:** make step 31's text say "round down"; leave step 30 as
nearest. One LSB on a safety limit, but a factory record will be audited
against one of the two.

## 5. Steps 24 / 26 — "drive current" should read "pulse width"

**Current:** "update the TA_PULSE_WIDTH value to the new **drive current**
found" (both steps).

**Proposed:** "… to the new **pulse width** found …" (copy-paste error; the
Figure Y line references already point at the pulse-width line).

## 6. Step 36 results table — wrong CSV filename

**Current:** the step text says `calibration-YYYYMMDD_HHMMSS.csv`; the
results-table headers say `test-YYYYMMDD_HHMMSS.csv`.

**Fact (from `omotion/CalibrationWorkflow.py`):** the calibration flow
writes `calibration-{ts}.csv`; `test-{ts}.csv` is produced by the separate
verification-test flow. **The step text is right; the table header is
wrong.**

## 7. Step 18 — "The Test is complete"

**Current:** "If both sensors measure between 300 and 400 µJ The Test is
complete."

**Proposed:** "…the **laser driver parameter determination (section 4.2)**
is complete; continue with section 4.3." Sections 4.4–4.6 still follow, so
"the Test is complete" invites an early stop.

## 8. Unspecified procedural parameters (rulings 9)

Worth pinning in the WI so every station behaves identically:

- **Power-cycle dwell** (steps 33/37): none specified → **15 seconds off**.
- **Trigger frequency**: step 10 checks 40 Hz but nothing says to *set* it
  if wrong → "confirm 40 Hz, correcting it if necessary".
- **ADC reads**: single read implied → "average several readings taken
  while the laser fires" (automation uses 5).
- **Step 28 quantization**: the note explains readback ≠ programmed value
  but gives no tolerance. Observed: 140 → 141.87 (1.3%). Suggest stating
  an acceptance tolerance (e.g. ±2%).

## 9. Both-directions failure is not covered

One module above maximum while the other is below minimum cannot be resolved
with the single shared TA drive. **Ruling 4:** NCR. Suggest adding a row to
the step 18 decision table saying so, rather than leaving the case to fall
through the sequential skip-aheads.

## 10. Figure Y omits TEC_TRIP — step 9 disarms the over-temp trip

**Current:** the default starting configuration (Figure Y, step 9) contains
only the nine laser keys. TEC_TRIP appears nowhere in the WI.

**Problem:** step 9 replaces the entire User Configuration. The console
firmware treats a missing/zero `TEC_TRIP` as **over-temp trip disabled**
(bloodflow-app `motion_config.py` guard-rail comments), and only the
bloodflow-app re-ensures the value on connect — the engineering/test app and
any rig executing this WI do not. A unit that completes this procedure and
never runs the clinical app therefore operates **without TEC over-temp
protection**. Observed live 2026-08-06: after the step-9 wipe on console
WWW04Q40010 nothing rewrote TEC_TRIP.

**Proposed:** add `"TEC_TRIP": 40` to Figure Y's default configuration (40 °C
is the fleet convention, bloodflow-app `tecTripTempC`), and to step 33/37's
expected final configuration. The automated runner already does this.

## 11. Steps 19/23 module selection needs a closeness margin

**Current:** step 19 seats "the Sensor Module with the highest energy
measured"; step 23 seats the lowest.

**Problem:** on a well-matched unit the ordering is not reproducible. Console
WWW04Q40010 measured L 216.0 / R 220.0 µJ on one run and L 224.2 / R 219.6 µJ
on the next — the "lowest" module flipped sides between insertions, because
the inter-module gap (~2%) is within insertion-to-insertion repeatability
(~±2–4%). Two compliant executions of the same WI on the same unit can
therefore seat different modules and record contradictory "lowest/highest"
designations. Physically this is harmless (one laser, one safety ADC — see
item 2), but the record inconsistency invites audit questions.

**Proposed:** add to steps 19/23: "If the two modules' measurements differ by
less than 5%, either module may be used; record which."

## 12. Step 8 should verify serial numbers are programmed, not just recorded

**Current:** step 8 says to record the System Serial Number and the modules'
Device IDs.

**Problem:** a unit arrived at this bench with **no console serial
programmed** (reads empty) and unprogrammed sensor-module serials — they had
to be written by the operator mid-procedure. "Record" silently produces a
blank field in the DHR; nothing in the WI catches the omission.

**Proposed:** step 8 add: "Verify the console and both Sensor Modules report
non-empty serial numbers. If any is missing, stop and program serials per
<applicable procedure> before continuing."

## 13. Step 9 should record the pre-existing configuration before deleting it

**Current:** step 9 says "Delete any data in the 'User Configuration' field
if present" — with no instruction to capture it first.

**Problem:** the deleted configuration can be the only record of a unit's
prior state. The same bench unit arrived carrying a foreign configuration at
write-sequence 66 (including `TA_PULSE_WIDTH: 600` — the WI ceiling); under
the WI as written that evidence would have been destroyed unexamined. The
automated runner preserves the pre-wipe configuration verbatim in its report
appendix.

**Proposed:** step 9 add, before the delete: "Copy the entire existing
contents of the User Configuration into the Actual Results cell for this
step, then delete."

## 14. Consider referencing the automated runner

If the automated rig (openmotion-sdk `scripts/wi15_runner.py` /
`wi15_guided.py`) is to become the official execution method, the WI needs a
revision naming it as an approved instrument (it currently names the
TestApp and bloodflow-app), or TP-00018 needs to bless the equivalence. The
runner's deviation log (`scripts/WI15_RUNNER_README.md`) enumerates every
difference from the WI as written to support that assessment.
