# Connect/Disconnect Redesign — Manual Hardware Test Plan

**Date:** 2026-04-22
**Companion to:** `2026-04-21-connect-disconnect-redesign-design.md`
**Run against:** `feature/connection-redesign` branches in
`openmotion-sdk`, `openmotion-bloodflow-app`, and `openmotion-test-app`.

These tests exercise the new connection lifecycle on real hardware
(one console + two sensor modules). They cover the four scenarios
called out in the design spec plus a stress test, and validate that
mid-scan recovery (the 5 s grace window) works end-to-end.

## Setup

1. Restart the bloodflow app fresh so the log starts empty.
2. In a terminal, tail the latest log with a filter that hides per-camera
   firmware printf noise:

   ```bash
   tail -f $(ls -t app-logs/ow-bloodflowapp-*.log | head -1) \
     | grep -E 'state |Handle |USB read error|Streaming stopped|Read thread|UART console|MotionComposite|connect attempt|retry|grace|scan: '
   ```

3. Confirm both sensors and the console are CONNECTED in the UI before
   starting any test.

## Test 1 — Idle replug between scans

**Goal:** confirm a sensor unplug-replug between scans is fast and clean.

1. With everything CONNECTED and idle (no scan running), unplug the LEFT
   sensor USB cable.
2. Wait 3 seconds.
3. Plug it back in.
4. Repeat with the RIGHT sensor.
5. Repeat with the CONSOLE.

**Expect per cycle:**

- One WARNING line per sensor read thread (`USB read error errno=32`)
  on unplug for sensors. For console you'll see
  `state CONNECTED → DISCONNECTING (poll_gone)` instead.
- Three INFO state-machine lines: `… DISCONNECTING`, `… closed`,
  `… DISCONNECTED`.
- App UI: the sensor's controls grey out within ~250 ms of physical unplug.
- On replug: `state DISCONNECTED → CONNECTING (poll_arrived)` followed by
  `→ CONNECTED (ping_ok)` within ~3 s (longer if firmware boots slowly).
- App UI: controls re-enable.
- **No fan polls on the other side** during the unplugged cycle.

**Fail signals:** any ERROR line, any Exception, the handle stuck in
CONNECTING or DISCONNECTING for more than ~5 s, app UI not reflecting state.

## Test 2 — Power-cycle the whole enclosure

**Goal:** all three handles independently recover from simultaneous loss.

1. Power off the enclosure (kills console + both sensors at once).
2. Wait 5 seconds.
3. Power back on.

**Expect:**

- All three disconnect events in <50 ms of each other.
- After power-on, all three reach CONNECTED within ~3-5 s of devices
  showing up on USB. The first sensor to attempt connect typically eats
  one ~2 s ping timeout while firmware is still booting; the others
  connect in ~50 ms each.
- Total recovery time visible to the user: power-button-press to
  all-CONNECTED ≈ (firmware boot ~2 s) + (~3 s SDK) ≈ 5-6 s.

**Fail signals:** any handle stuck DISCONNECTED (the next 200 ms poll
should always retry); `connect_retry_exhausted` repeating more than
once or twice in a row.

## Test 3 — Rapid replug stress

**Goal:** state machine debouncing under fast successive events.

1. Pick one sensor cable.
2. Unplug → replug → unplug → replug → unplug → replug, as fast as you
   physically can (sub-second).
3. End with the cable plugged in. Wait 5 seconds.

**Expect:**

- Final state is CONNECTED.
- No "release failed" / "object deleted" warnings anywhere in the log.
- No half-init state (handle stuck in CONNECTING).
- Intermediate DISCONNECTING/CONNECTING transitions show monotonic
  state — no attempts to `_drive_connecting` while already CONNECTING
  (the queue serializes events).

**Fail signals:** handle ends in any state other than CONNECTED with
the cable plugged in. Repeated retry-exhausted entries.

## Test 4 — Hot replug during scan, **under 5 s** (recovery)

**Goal:** confirm the 5 s grace window restores streaming without
aborting the scan.

1. Start a scan with both sensors enabled.
2. Wait until you see the scan plotting data normally (~5 s in).
3. Unplug the LEFT sensor cable.
4. Count "one-mississippi, two-mississippi, three-mississippi"
   (about 3 s).
5. Plug the LEFT sensor back in.
6. Let the scan run for another 10 s.
7. Stop the scan normally.

**Expect in log:**

- At scan start: three `scan: subscribed to <name> state changes`
  INFO lines (one per participating handle).
- On unplug: `scan: left disconnected mid-scan
  (usb_io_error:errno=32); 5.0 s grace started` (WARNING).
- `… left state CONNECTED → DISCONNECTING → DISCONNECTED`.
- After replug (~3 s gap): `… left state DISCONNECTED → CONNECTING →
  CONNECTED`.
- `scan: left recovered after 3.X s; resuming streaming` (INFO).
- Scan continues, completes normally (`Scan stopped`, not "Capture
  canceled").
- The right sensor's plot and CSV row count are unaffected (no gap in
  its data).
- The left sensor's CSV/plot has a temporal gap visible in the
  timestamps (~3 s).

**Fail signals:** scan aborts ("Capture canceled"); no `scan: left
recovered` line; left plot stops permanently; AttributeError on
`sensor.uart.histo` from the post-scan summary.

## Test 5 — Hot replug during scan, **over 5 s** (clean abort)

**Goal:** confirm the scan aborts cleanly when the grace window expires.

1. Start a scan with both sensors enabled.
2. Wait ~5 s into the scan.
3. Unplug the LEFT sensor cable.
4. Wait 8 seconds (past the 5 s grace).
5. Plug it back in (not strictly necessary but proves the late return
   is handled).

**Expect in log:**

- `scan: left disconnected mid-scan ...; 5.0 s grace started` (WARNING).
- 5 s later: `scan: left did not return within 5.0 s grace; aborting`
  (ERROR).
- Scan completes with reason `left did not return within 5 s grace
  window`.
- A *single* clean abort, not a flurry of retries or "release failed"
  warnings.
- After the late replug, normal CONNECTING → CONNECTED on left, with
  no scan-recovery messages (scan already aborted).

**Fail signals:** flurry of errors; scan hangs instead of aborting;
multiple grace-expired log entries.

## Test 6 — Console drop during scan

**Goal:** console drop kills the trigger but each sensor's grace window
applies independently. Console recovery within the grace window must
restart the trigger.

1. Start a scan with both sensors enabled.
2. Wait ~5 s.
3. Briefly unplug-replug the CONSOLE cable (under 5 s).

**Expect:**

- `scan: console disconnected mid-scan ...; 5.0 s grace started`
  (WARNING).
- Both sensor plots stop (no FSYNC trigger).
- Console reconnects within a few seconds.
- `scan: console recovered after X s; resuming streaming` (INFO).
- Console trigger is restarted automatically; sensor plots resume.
- Scan completes normally.

**Fail signals:** scan aborts on console drop alone; sensor plots don't
resume after console returns; trigger doesn't restart.

## What success looks like overall

After running all six tests, the log should contain:

- WARNING lines only for the read-thread `USB read error errno=32` on
  unplug, and for the `scan: <name> disconnected mid-scan ...; grace
  started` notices.
- ERROR lines only for the genuinely-aborted Test 5 case
  (`did not return within 5.0 s grace`).
- No `Unexpected error during X`, no `Serial error in send_packet`, no
  `Error getting fan control status`, no `release failed`, no
  `object deleted`, no AttributeErrors.
- Reconnect detection latency under ~250 ms in every case (poll
  fallback) or under ~50 ms (Win32 hotplug path).
- Mid-scan recovery within 5 s shows only as a timestamp gap in the
  output histogram CSV — no scan abort, no missing rows after the gap.

If anything fails, capture the relevant log section (with the filter
above) and the rough wall-clock timing of the physical action you
took, and iterate.
