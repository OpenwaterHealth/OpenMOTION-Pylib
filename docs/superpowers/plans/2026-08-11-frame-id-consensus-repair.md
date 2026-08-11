# Frame-ID Consensus Repair — Implementation Plan

**Goal:** Prevent one corrupt histogram frame ID from poisoning timestamp repair, while leaving enough bounded log evidence to diagnose unusual inbound data without raw CSV or SQLite metadata.

## 1. Lock down the behavior with tests

- Keep a coalesced `(side, timestamp)` histogram packet in one source batch.
- Recover exactly one frame-ID outlier only when the rest of a packet agrees and the corrected ID is a valid continuation for that camera.
- Preserve the raw wire frame ID and log ambiguous/corrected packets before mutation.
- Make timestamp repair compare absolute IDs and emit detailed pre-repair and gap-fill anomaly events.
- Cap detailed anomaly logging per event type and keep these events out of SQLite.
- Reproduce issue #220 end-to-end and assert no repair cascade or NaN insertion.

## 2. Implement the smallest production changes

- Add packet-aware batching in `LiveUsbSource`.
- Add a strict packet-consensus pass ahead of frame-ID unwrapping.
- Add explicit anomaly event types and bounded human-readable log formatting.
- Simplify timestamp-repair state into named records and small detector helpers while changing its packet consistency check to absolute IDs.

## 3. Verify

- Run focused tests after every red/green cycle.
- Run the complete software pipeline suite.
- Review the final diff for raw-ID preservation, pre-mutation logging, bounded output, and accidental scope creep.
