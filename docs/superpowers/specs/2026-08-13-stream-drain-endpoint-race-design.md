# Stream Drain Endpoint Race Repair Design

**Date:** 2026-08-13  
**Scope:** Sensor histogram stream shutdown only

## Problem

`StreamInterface.drain_final()` verifies that `self.ep_in` is present and then
dereferences `self.ep_in.bEndpointAddress` inside its read loop. Sensor
connection cleanup may concurrently release the USB interface and set
`self.ep_in` to `None` after the initial check. The drain then raises an
`AttributeError` during otherwise normal shutdown, and `LiveUsbSource` logs an
exception traceback even though scan evidence and sensor reconnection can be
valid.

The repair must not perform hardware discovery or communicate with attached
devices during development or verification.

## Approved Design

At `drain_final()` entry, after confirming that an endpoint is claimed, copy
the endpoint address into a local immutable value. Every read in that drain
operation uses the local address instead of dereferencing the mutable
`self.ep_in` attribute again.

The snapshot provides a consistent address for the bounded drain operation.
If concurrent release or physical removal makes the underlying device read
fail, existing USB-error handling remains responsible for ending the drain.
Known device-loss and pipe-loss errors during this post-stop drain remain a
graceful empty/end-of-drain outcome. Unexpected USB errors remain visible as
warnings. Non-USB programming errors are not hidden.

No locking will be added around USB reads and interface release. No broad
exception catch will be added to `LiveUsbSource`, and no connection-state or
scan-acceptance behavior will change.

## Test Strategy

Add deterministic unit coverage using a fake USB device. The fake clears the
stream's `ep_in` during a successful first read and supplies a second outcome.
The regression proves that:

1. later reads continue using the captured endpoint address rather than
   dereferencing `None`;
2. recovered final chunks remain intact;
3. a subsequent expected USB timeout terminates the drain normally; and
4. the repair needs no live hardware.

Run the focused stream/source tests, the scan-workflow tests, the WI-00015
Safety Calibration suite, Ruff, Python compilation, and Git whitespace checks.

## Acceptance Criteria

- `drain_final()` cannot raise an endpoint-`None` `AttributeError` merely
  because concurrent cleanup clears `self.ep_in` after entry.
- Expected post-stop USB loss still terminates the drain without a traceback.
- Unexpected errors retain diagnostic visibility.
- Existing recovered-chunk behavior and WI-00015 scan acceptance remain
  unchanged.
- All verification is fake-driven and offline; no hardware access occurs.
