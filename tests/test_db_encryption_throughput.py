"""Insert-throughput check for the encrypted scan database.

SQLCipher adds per-page AES + HMAC-SHA512 on every read and write, so the one
deployment risk that unit tests do not surface is whether an encrypted
``scans.db`` can absorb the live scan rate. A scan produces

    40 fps x 16 cameras = 640 session_data rows/second

sustained, written by ``ScanDBSink`` in batches of 200. This measures the real
``ScanDatabase`` write path (not raw sqlite3) on both backends and fails if the
encrypted path cannot clear the scan rate with a wide margin.

Thresholds are deliberately loose — this is a "did encryption make writes
pathologically slow" guard for CI, not a micro-benchmark. Actual numbers are
printed so a regression is visible even when the assertion passes.
"""
import time

import pytest

from omotion import db_key
from omotion.ScanDatabase import ScanDatabase

FIXED_KEY = "a" * 64

SCAN_ROWS_PER_SEC = 40 * 16          # 640 — live scan rate
BATCH = 200                          # ScanDBSink batch size
BATCHES = 25                         # 5000 rows ~= 7.8 s of scanning
REQUIRED_MARGIN = 4                  # must sustain >= 4x the live rate


def _rows(session_id, start, n=BATCH):
    return [
        {
            "session_id": session_id,
            "cam_id": (start + i) % 8,
            "side": (start + i) % 2,
            "frame_id": start + i,
            "timestamp_s": (start + i) / 40.0,
            "bfi": 1.5,
            "bvi": 2.5,
            "contrast": 0.3,
            "mean": 450.0,
        }
        for i in range(n)
    ]


def _measure(db_path):
    """Returns (rows_per_sec, total_rows) for the real ScanDatabase write path."""
    db = ScanDatabase(db_path=str(db_path))
    try:
        sid = db.create_session("BENCH", 0.0)
        # warm up: first batch pays connection/page-cache costs
        db.insert_session_data_rows(_rows(sid, 0))
        t0 = time.perf_counter()
        for b in range(1, BATCHES + 1):
            db.insert_session_data_rows(_rows(sid, b * BATCH))
        elapsed = time.perf_counter() - t0
        written = BATCHES * BATCH
        assert db._connection().execute(
            "SELECT count(*) FROM session_data"
        ).fetchone()[0] == written + BATCH
        return written / elapsed, written
    finally:
        db.close()


def test_encrypted_insert_throughput_clears_scan_rate(tmp_path, monkeypatch, capsys):
    pytest.importorskip("sqlcipher3")

    monkeypatch.setattr(db_key, "require_encryption", lambda: False)
    plain_rps, n = _measure(tmp_path / "plain.db")

    monkeypatch.setattr(db_key, "require_encryption", lambda: True)
    monkeypatch.setattr(db_key, "get_key", lambda *, create=False: FIXED_KEY)
    enc_rps, _ = _measure(tmp_path / "enc.db")

    overhead = (plain_rps / enc_rps - 1) * 100 if enc_rps else float("inf")
    with capsys.disabled():
        print(
            f"\n  {n} rows | plaintext {plain_rps:,.0f} rows/s | "
            f"encrypted {enc_rps:,.0f} rows/s | encryption overhead {overhead:+.0f}% | "
            f"scan rate {SCAN_ROWS_PER_SEC} rows/s "
            f"({enc_rps / SCAN_ROWS_PER_SEC:.0f}x headroom)"
        )

    assert enc_rps > SCAN_ROWS_PER_SEC * REQUIRED_MARGIN, (
        f"encrypted scan DB sustains only {enc_rps:,.0f} rows/s; a live scan "
        f"produces {SCAN_ROWS_PER_SEC} rows/s and this must hold "
        f"{REQUIRED_MARGIN}x margin"
    )


def test_encrypted_read_throughput_is_usable(tmp_path, monkeypatch, capsys):
    """Playback / History read the DB back; per-page HMAC is verified on read
    too, so confirm a full-scan read stays interactive."""
    pytest.importorskip("sqlcipher3")
    monkeypatch.setattr(db_key, "require_encryption", lambda: True)
    monkeypatch.setattr(db_key, "get_key", lambda *, create=False: FIXED_KEY)

    path = tmp_path / "enc.db"
    db = ScanDatabase(db_path=str(path))
    sid = db.create_session("BENCH", 0.0)
    for b in range(BATCHES):
        db.insert_session_data_rows(_rows(sid, b * BATCH))
    db.close()

    db = ScanDatabase(db_path=str(path))
    try:
        t0 = time.perf_counter()
        n = sum(1 for _ in db.iter_session_data(sid))
        elapsed = time.perf_counter() - t0
    finally:
        db.close()

    with capsys.disabled():
        print(f"  read {n:,} rows in {elapsed * 1000:.0f} ms "
              f"({n / elapsed:,.0f} rows/s)")
    assert n == BATCHES * BATCH
    assert elapsed < 2.0, f"reading {n} encrypted rows took {elapsed:.2f}s"
