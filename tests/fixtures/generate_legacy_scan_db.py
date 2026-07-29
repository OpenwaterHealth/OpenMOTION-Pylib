"""
Generate the legacy scan-database fixture used by tests/test_db_schema.py.

Run this script to (re)create the fixture:

    python tests/fixtures/generate_legacy_scan_db.py

Produces ``legacy_scans_v0.db`` — a scans.db as an OLD SDK left it, so the
migration runner is verified against a real file rather than one synthesized
inside the test:

- ``PRAGMA user_version = 0``   — written before schema versioning existed
- ``session_data`` has **no** ``frame_id`` and **no** ``quality`` column
  (pre-#92 Step F), so opening it exercises the ADD COLUMN branch of
  migration 1 rather than just the CREATE-IF-NOT-EXISTS branch
- only the two indexes that existed then (no ``idx_session_data_session_frame``)
- realistic-looking but entirely synthetic data — **no PHI**: the subject id is
  ``owTEST01`` and the BFI/BVI values are generated, not measured

Regenerate only if the historical schema shape needs to change. The point of the
fixture is that it is FROZEN: it represents what is actually on disk in the
field, so it must not be rewritten to match whatever the current schema is.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

OUT = Path(__file__).with_name("legacy_scans_v0.db")

# The schema exactly as it stood before frame_id/quality were added.
LEGACY_SCHEMA = """
CREATE TABLE sessions (
    id             INTEGER PRIMARY KEY,
    session_label  TEXT    NOT NULL,
    session_start  REAL    NOT NULL,
    session_end    REAL,
    session_notes  TEXT,
    session_meta   TEXT
);

CREATE TABLE session_data (
    id               INTEGER PRIMARY KEY,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    cam_id           INTEGER NOT NULL,
    side             INTEGER NOT NULL CHECK(side IN (0, 1)),
    timestamp_s      REAL    NOT NULL,
    bfi              REAL,
    bvi              REAL,
    contrast         REAL,
    mean             REAL
);

CREATE INDEX idx_session_data_session_time ON session_data(session_id, timestamp_s);
CREATE INDEX idx_session_data_session_cam
    ON session_data(session_id, side, cam_id, timestamp_s);

CREATE TABLE database_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# One scan: 2 sides x 4 cameras x 25 frames at 40 fps.
SIDES, CAMS, FRAMES = (0, 1), (1, 2, 5, 6), 25


def main() -> None:
    if OUT.exists():
        OUT.unlink()

    con = sqlite3.connect(OUT)
    try:
        con.executescript(LEGACY_SCHEMA)
        con.execute(
            "INSERT INTO sessions(id, session_label, session_start, session_end,"
            " session_notes, session_meta) VALUES(?,?,?,?,?,?)",
            (
                1,
                "20251217_160949_owTEST01",
                1_766_000_989.0,
                1_766_001_014.0,
                "legacy fixture - synthetic data, no PHI",
                '{"subject_id": "owTEST01", "fps": 40, "left_camera_mask": 102,'
                ' "right_camera_mask": 102}',
            ),
        )
        rows = []
        for frame in range(FRAMES):
            t = round(frame / 40.0, 6)
            for side in SIDES:
                for cam in CAMS:
                    # deterministic, obviously-synthetic values
                    bfi = round(1.0 + 0.01 * frame + 0.1 * cam + 0.5 * side, 6)
                    bvi = round(2.0 + 0.02 * frame + 0.1 * cam, 6)
                    rows.append((1, cam, side, t, bfi, bvi,
                                 round(0.30 + 0.001 * frame, 6),
                                 round(450.0 + frame, 3)))
        con.executemany(
            "INSERT INTO session_data(session_id, cam_id, side, timestamp_s,"
            " bfi, bvi, contrast, mean) VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
        con.execute("INSERT INTO database_settings(key, value) VALUES('sdk_version','1.5.8')")
        con.commit()

        # Explicitly pre-versioning: this is what the field actually has.
        assert con.execute("PRAGMA user_version").fetchone()[0] == 0
        cols = {r[1] for r in con.execute("PRAGMA table_info('session_data')")}
        assert "frame_id" not in cols and "quality" not in cols, cols
    finally:
        con.close()

    n = sqlite3.connect(OUT).execute("SELECT count(*) FROM session_data").fetchone()[0]
    print(f"wrote {OUT.name}: {n} session_data rows, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
