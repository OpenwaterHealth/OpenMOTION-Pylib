# Packet-Consensus Frame-ID Recovery and Diagnostics

**Date:** 2026-08-11  
**Issues:** openmotion-sdk #220, #229  
**Target branch:** `fix/229-timestamp-repair-anchors`

## Problem

The 2026-08-06 field incident showed that one corrupted camera `frame_id` can
poison the per-camera 8-bit unwrapper and then be amplified by
`TimestampRepairStage`:

1. A high-bit-corrupted raw ID can look like a valid modular forward jump.
2. The unwrapper accepts the jump and creates a persistent absolute-ID offset.
3. Timestamp repair treats an in-packet ID disagreement as timestamp
   corruption, rewrites truthful timestamps, and inserts NaN rows for a gap
   that never existed.
4. Side averaging and terminal-dark matching consume the damaged absolute IDs,
   producing partial averages, non-monotonic plot time, and false terminal-dark
   failures.

The existing logs report the downstream repair window but omit the packet-level
wire evidence needed to distinguish a timestamp fault from a single-camera ID
fault.

Sensor firmware emits one coalesced histogram packet per capture. Individual
camera histograms are not independently buffered, so histograms sharing that
packet timestamp were captured together. When every valid histogram but one
agrees on the raw frame ID, the majority is authoritative for pipeline frame
identity. The outlier histogram remains scientifically valid at the packet's
original timestamp.

## Goals

- Prevent a single raw-ID outlier from corrupting unwrapper state.
- Accept the outlier histogram at its truthful packet timestamp.
- Preserve the observed wire ID for raw export and forensic logging.
- Capture suspicious inbound values in the ordinary log before any stage
  rewrites, substitutes, or fills them, so a raw CSV is not required to
  reconstruct the pipeline's decision.
- Keep downstream `abs_frame_ids` aligned for dark scheduling, terminal-dark
  matching, and side averaging.
- Emit self-contained diagnostics that make the fault root-causeable from an
  ordinary support log.
- Bound log volume during sustained corruption.
- Preserve the genuine timestamp-repair behavior on actual timestamp faults.

## Non-goals

- Guess between two cameras that disagree one-to-one.
- Repair packets with no strict majority or more than one outlier.
- Store frame-ID forensic examples in SQLite or `session_meta`.
- Change raw histogram contents or capture timestamps.
- Redesign the 8-bit firmware counter protocol.
- Log ordinary healthy packets or unbounded histogram payloads.

## Design

### 1. Keep coalesced packets atomic at the source boundary

`LiveUsbSource` currently flushes a batch as soon as its row count reaches the
configured threshold. That can split camera rows sharing one packet timestamp
across two `FrameBatch` objects.

The source will treat a same-side, same-timestamp row group as atomic:

- Rows are appended while the timestamp equals the current group's timestamp.
- A size or time flush becomes eligible at the threshold but occurs only when
  the next timestamp begins.
- The completed group is flushed before the first row of the next group is
  appended.
- The final partial group is flushed when the parser exits.

A batch may exceed `batch_size_frames` by at most one packet group. This adds no
meaningful latency beyond waiting for the next 40 Hz packet and preserves the
packet-consensus invariant for classification.

### 2. Derive a temporary effective raw ID by strict packet consensus

`FrameClassificationStage.process()` will group rows by exact
`(side_id, timestamp_s)`. Each group is validated before any unwrapper is
mutated.

A group is eligible for consensus correction only when all of these hold:

1. It contains at least three distinct camera IDs.
2. Camera IDs are unique within the group.
3. Exactly one row has a different raw `frame_id`.
4. Every other row has the same raw `frame_id` (a strict majority).
5. The outlier camera already has prior accepted unwrapper state.
6. The consensus raw ID is a valid forward continuation for the outlier
   camera's current unwrapper state.

For an eligible group, classification creates a temporary effective-ID vector:

- The outlier's effective raw ID is the consensus ID.
- Every other row's effective raw ID equals its observed raw ID.
- The outlier unwrapper consumes the effective consensus ID, so its
  `abs_frame_id` and future state stay aligned with its siblings.
- `batch.frame_ids` is never mutated. It remains the observed wire record used
  by the raw tee and raw CSV export.
- The histogram and `batch.timestamp_s` are unchanged and remain valid.

No new per-row batch field is required. The effective ID exists only during
classification; the emitted diagnostic carries the observed and substituted
values.

If a group is ambiguous, classification does not substitute an ID. Existing
conservative classification and timestamp-repair handling continue, augmented
with a packet-anomaly diagnostic.

### 3. Compare classified absolute IDs in timestamp repair

After classification, `abs_frame_ids` are the authoritative pipeline identity.
`TimestampRepairStage` will therefore evaluate in-packet disagreement using
`batch.abs_frame_ids`, not the preserved raw `batch.frame_ids`.

Consequences:

- A consensus-corrected packet has matching absolute IDs and is not treated as
  timestamp corruption.
- It receives no timestamp rewrite and no NaN fill.
- A non-correctable disagreement remains visible because its classified
  absolute IDs still disagree.
- Condition 1, which detects genuine timestamp deviation from cadence, remains
  unchanged apart from the anchor corrections already present on #229.

### 4. Log suspicious input before mutation

The observability rule is: **log the evidence first, then mutate**. A future
investigation must be able to reconstruct why the pipeline acted even when the
raw CSV was disabled or is no longer available.

Every anomaly log begins with the explicit marker `INBOUND DATA ANOMALY` and
states whether the associated histogram is being accepted, corrected,
NaN-filled, or left for conservative downstream handling. Values in these
records are captured before timestamp rewriting, frame-ID substitution, or
synthetic-row insertion.

Four diagnostics-channel event types describe suspicious inbound data:

- `FrameIdConsensusCorrection`: one outlier was safely substituted by strict
  packet consensus.
- `FrameIdPacketAnomaly`: the packet disagreed but was ambiguous and was not
  changed.
- `TimestampRepairInputAnomaly`: a frame was flagged for timestamp repair by
  cadence deviation, unresolved absolute-ID disagreement, or both.
- `FrameGapFillAnomaly`: an accepted absolute-ID gap will cause synthetic NaN
  rows to be inserted.

A consensus-correction event contains:

- side and packet timestamp;
- outlier camera ID;
- observed raw ID and consensus raw ID;
- previous accepted raw and absolute IDs for the outlier camera;
- corrected absolute ID;
- complete packet snapshot as ordered `(camera_id, observed_raw_id)` pairs;
- an explicit action stating that the histogram was accepted at the original
  packet timestamp and only its pipeline frame identity was normalized.

An ambiguous-anomaly event contains the side, packet timestamp, complete packet
snapshot, prior per-camera unwrap context where available, and the reason no
substitution was made.

A timestamp-input event contains:

- side, camera, observed raw ID, classified absolute ID, and original timestamp;
- the previous genuine anchor's raw ID, absolute ID, and timestamp;
- the nominal period, frame-ID gap, expected timestamp, signed residual, and
  configured tolerance;
- all rows in the same side/timestamp group as ordered
  `(camera_id, observed_raw_id, abs_frame_id)` triples;
- which detector fired (`timestamp_deviation`, `frame_id_disagreement`, or
  both); and
- the action the stage will take.

A gap-fill event contains:

- side and camera;
- the preceding and current observed raw IDs, classified absolute IDs, and
  original timestamps;
- the inferred missing-frame count and synthetic absolute-ID range; and
- the action stating that NaN rows will be inserted because the accepted
  absolute IDs indicate a gap.

These events contain metadata only. Histogram bins are not copied into the log.
The raw histogram remains available through the existing optional raw CSV, but
the CSV is not necessary to see the IDs, timestamps, anchors, residuals, or
pipeline decision.

`DiagnosticsLogSink` will:

- log the first eight events of each inbound-anomaly type in full;
- show raw IDs in decimal and hexadecimal;
- emit one suppression notice when the per-type limit is exceeded;
- continue counting suppressed events; and
- include per-type totals in the scan-completion summary.

The eight-example cap applies per event type per scan. It gives complete
evidence for the three-frame #220 burst while bounding a sustained fault.

`ScanDBSink` will explicitly ignore all four inbound-anomaly event types. No
details, counts, or examples are added to SQLite `session_meta`; the ordinary
application log is the forensic record.

## Error handling and safety rules

- A two-camera disagreement is ambiguous and is never guessed.
- A 2-vs-2 or other no-majority packet is never guessed.
- Multiple outliers are never guessed.
- Duplicate camera IDs invalidate consensus for that timestamp group.
- A consensus ID that is not a valid forward continuation for the outlier's
  existing unwrapper state is not applied.
- A first-seen outlier camera has no history for validation and is not corrected.
- Ambiguous cases retain current downstream behavior and gain detailed logging.
- Anomaly events preserve observed raw IDs and pre-mutation timestamps as
  immutable evidence even when a later stage repairs `batch.timestamp_s`.
- An anomaly is logged even when consensus correction makes the resulting data
  scientifically usable; successful healing never makes the input anomaly
  invisible.

## Data flow

```text
coalesced USB histogram packet
  -> LiveUsbSource keeps timestamp group atomic
  -> FrameClassificationStage
       observed raw IDs retained in batch.frame_ids
       strict-majority outlier gets temporary effective consensus ID
       effective ID feeds that camera's unwrapper
       packet diagnostic appended to batch.events
  -> TimestampRepairStage compares abs_frame_ids
       corrected consensus group passes unchanged
       genuine/ambiguous divergence remains detectable
       timestamp deviations and inferred gaps emit pre-mutation evidence
  -> dark correction and side averaging consume aligned abs_frame_ids
  -> DiagnosticsLogSink writes bounded forensic detail
  -> ScanDBSink ignores inbound-data anomaly diagnostics
```

## Testing strategy

### Source batching

- A batch threshold reached midway through a timestamp group does not split the
  group.
- The next timestamp triggers the pending flush.
- Parser shutdown flushes the final complete group.

### Classification

- Four-camera packet with one corrupted ID uses the three-camera consensus.
- `batch.frame_ids` retains the corrupt observed byte.
- All four `abs_frame_ids` match after classification.
- The outlier camera's next genuine frame unwraps normally.
- The correction event contains every required field.
- Two-camera, 2-vs-2, duplicate-camera, multiple-outlier, first-seen, and
  cadence-inconsistent cases are not corrected and emit an ambiguity event.

### Timestamp repair

- Condition 2 compares `abs_frame_ids`.
- A consensus-corrected group produces no timestamp correction or NaN fill.
- A genuine absolute-ID disagreement remains detected.
- A genuine cadence deviation emits its original timestamp, genuine anchor,
  expected timestamp, residual, tolerance, and packet context before repair.
- A missing-ID gap emits its bounding observations and proposed synthetic range
  before any NaN rows are inserted.
- Existing condition-1 timestamp burst, terminal artifact, re-anchor, and
  NaN-gap tests continue to pass.

### End-to-end #220 regression

Replay the real four-camera, 40 Hz high-bit-corruption signature through
classification, timestamp repair, and side averaging. Assert:

- no real frames become stale;
- no timestamps are rewritten;
- no NaN rows are fabricated;
- the timeline remains monotonic;
- every side average contains all enabled cameras;
- each outlier histogram is retained at its packet timestamp; and
- the expected correction diagnostics identify the wire IDs and consensus.

### Logging and persistence

- Every suspicious-input line begins with `INBOUND DATA ANOMALY`.
- Frame-ID lines contain side, timestamp, camera, observed/consensus IDs in
  decimal and hex, prior state, corrected absolute ID, packet snapshot, and
  action.
- Timestamp lines contain the original value, genuine anchor, expected value,
  residual, tolerance, detector, packet snapshot, and action.
- Gap-fill lines contain both bounding observations, missing count, synthetic
  range, and action.
- The ninth same-type event produces one suppression notice, not a ninth full
  record.
- Completion reports full per-type anomaly totals.
- `ScanDBSink` writes no inbound-anomaly diagnostic entry into `session_meta`.

## Compatibility and performance

- Public scan APIs and persisted science schemas do not change.
- Raw CSV frame IDs remain wire-authentic.
- Corrected CSV/DB science output continues to use downstream absolute frame
  identity as before.
- Consensus work is linear in each small packet group (at most eight cameras).
- Packet-aligned batching can increase a batch by at most one packet and adds at
  most one packet interval of flush latency.
