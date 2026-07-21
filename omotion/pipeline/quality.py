"""Per-frame quality vocabulary.

Single source of truth for the quality values and their severity ordering.
Import QUALITY_RANK — never re-declare it. `worse_of` treats an unrecognised
value as rank 0 ("ok"), so a stale copy of this table would rank a newer value
as the BEST quality rather than the worst, inverting its meaning.

Values, in increasing severity:
    ok             — nothing wrong
    ts_corrected   — timestamp replaced by re-anchoring interpolation
    nan_filled     — synthetic row standing in for a frame that never arrived
    wide_interval  — real measurement, but its dark baseline was interpolated
                     across an interval that spans one or more missing darks
"""

from __future__ import annotations


QUALITY_RANK: dict[str, int] = {
    "ok": 0,
    "ts_corrected": 1,
    "nan_filled": 2,
    "wide_interval": 3,
}


def worse_of(a: str, b: str) -> str:
    """Return whichever quality value is more severe (ties return `a`).

    If one value is unknown (not in QUALITY_RANK), return the known one.
    """
    a_in_map = a in QUALITY_RANK
    b_in_map = b in QUALITY_RANK

    # If one is known and one is unknown, return the known one
    if a_in_map and not b_in_map:
        return a
    if b_in_map and not a_in_map:
        return b

    # Both known or both unknown: use ranks (defaulting unknown to 0)
    return a if QUALITY_RANK.get(a, 0) >= QUALITY_RANK.get(b, 0) else b
