"""Tests for omotion.ScanDatabase (the re-homed scan_db.py)."""

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omotion import ScanDatabase


@pytest.fixture
def tmp_db(tmp_path: Path) -> ScanDatabase:
    db = ScanDatabase(db_path=str(tmp_path / "test.db"))
    yield db
    db.close()


def test_create_and_get_session(tmp_db: ScanDatabase) -> None:
    sid = tmp_db.create_session(
        session_label="20260414_000000_owTEST",
        session_start=1744600000.0,
        session_notes="unit test",
        session_meta={"subject_id": "owTEST", "fps": 40},
    )
    assert sid > 0

    session = tmp_db.get_session(sid)
    assert session is not None
    assert session["session_label"] == "20260414_000000_owTEST"
    assert session["session_start"] == 1744600000.0
    assert session["session_notes"] == "unit test"
    assert session["session_meta"] == {"subject_id": "owTEST", "fps": 40}


def test_no_raw_frame_api_or_table(tmp_path: Path) -> None:
    """Raw histograms are not stored in the DB (raw CSVs are the only raw
    record). New databases have no session_raw table and the class exposes
    no raw-frame API."""
    db = ScanDatabase(db_path=str(tmp_path / "noraw.db"))
    try:
        tables = {
            r[0] for r in db._connection().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "session_raw" not in tables
        assert not hasattr(db, "insert_raw_frame")
        assert not hasattr(db, "insert_raw_frames")
    finally:
        db.close()


def test_insert_session_data_and_stream(tmp_db: ScanDatabase) -> None:
    sid = tmp_db.create_session(session_label="s", session_start=0.0)
    tmp_db.insert_session_data(
        sid, cam_id=3, side=0, timestamp_s=1.0,
        bfi=0.25, bvi=0.5, contrast=0.1, mean=511.0,
    )
    rows = [r for batch in tmp_db.stream_session_data(sid) for r in batch]
    assert len(rows) == 1
    assert rows[0]["cam_id"] == 3
    assert rows[0]["side"] == 0
    assert rows[0]["bfi"] == 0.25


def test_session_meta_survives_unicode_and_none(tmp_db: ScanDatabase) -> None:
    meta = {"subject": "öwtëst", "ops": None, "nested": {"a": [1, 2, 3]}}
    sid = tmp_db.create_session(
        session_label="u", session_start=0.0, session_meta=meta,
    )
    session = tmp_db.get_session(sid)
    assert session["session_meta"] == meta


# ---------------------------------------------------------------------------
# assert_writable — write-probe so a read-only DB fails the scan preflight
# synchronously (before the laser fires) instead of asynchronously on the
# first INSERT inside the scan worker thread. See bloodflow-app issue #213.
# ---------------------------------------------------------------------------

def test_assert_writable_raises_on_readonly_db(tmp_path: Path) -> None:
    """A read-only DB file opens fine (open never writes) but a write fails.
    assert_writable must force that write and surface the OperationalError."""
    p = tmp_path / "ro.db"
    ScanDatabase(db_path=str(p)).close()  # create + schema while writable
    os.chmod(p, stat.S_IREAD)             # Windows read-only attribute
    db = ScanDatabase(db_path=str(p))     # reopen: succeeds, no write yet
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.assert_writable()
    finally:
        db.close()
        os.chmod(p, stat.S_IWRITE)


def test_assert_writable_noop_on_writable_db(tmp_db: ScanDatabase) -> None:
    """On a writable DB the probe returns None and persists nothing."""
    settings_before = tmp_db._connection().execute(
        "SELECT COUNT(*) FROM database_settings"
    ).fetchone()[0]
    version_before = tmp_db._connection().execute(
        "PRAGMA user_version"
    ).fetchone()[0]

    assert tmp_db.assert_writable() is None

    settings_after = tmp_db._connection().execute(
        "SELECT COUNT(*) FROM database_settings"
    ).fetchone()[0]
    version_after = tmp_db._connection().execute(
        "PRAGMA user_version"
    ).fetchone()[0]
    assert settings_after == settings_before
    assert version_after == version_before


# ==========================================================================
# Encryption (SQLCipher) — clinical policy. These monkeypatch db_key so they
# never touch the real keystore; encrypted-path tests skip without sqlcipher3.
# ==========================================================================

from omotion import db_key  # noqa: E402

FIXED_KEY = "a" * 64
MAGIC = b"SQLite format 3\x00"


@pytest.fixture
def clinical_policy(monkeypatch):
    pytest.importorskip("sqlcipher3")
    monkeypatch.setattr(db_key, "require_encryption", lambda: True)
    monkeypatch.setattr(db_key, "get_key", lambda *, create=False: FIXED_KEY)


def test_scan_database_encrypted_roundtrip(clinical_policy, tmp_path):
    path = str(tmp_path / "scans.db")
    db = ScanDatabase(db_path=path)
    sid = db.create_session("SUBJ", 1.0)
    db.insert_session_data(sid, cam_id=0, side=0, timestamp_s=1.0, bfi=1.5)
    rows = list(db.iter_session_data(sid))
    db.close()
    assert rows[0]["bfi"] == 1.5
    with open(path, "rb") as fh:                     # on-disk proof of encryption
        assert fh.read(16) != MAGIC


def test_encrypted_row_access_parity(clinical_policy, tmp_path):
    db = ScanDatabase(db_path=str(tmp_path / "scans.db"))
    sid = db.create_session("SUBJ", 1.0)
    row = db.get_session(sid)                          # dict(row) path
    assert row["session_label"] == "SUBJ"
    raw = db._connection().execute(                    # SessionPlayback pattern
        "SELECT id, session_label FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    assert raw[1] == "SUBJ"                             # positional
    assert raw["session_label"] == "SUBJ"              # by-name
    db.close()


def test_encrypted_db_repr_and_attrs_have_no_key(clinical_policy, tmp_path):
    db = ScanDatabase(db_path=str(tmp_path / "scans.db"))
    try:
        assert FIXED_KEY not in repr(db)
        # no instance attribute holds the raw key
        assert not any(
            isinstance(v, str) and v == FIXED_KEY for v in vars(db).values()
        )
    finally:
        db.close()


@pytest.fixture(params=["plaintext", "encrypted"])
def any_db(request, tmp_path, monkeypatch):
    if request.param == "encrypted":
        pytest.importorskip("sqlcipher3")
        monkeypatch.setattr(db_key, "require_encryption", lambda: True)
        monkeypatch.setattr(db_key, "get_key", lambda *, create=False: FIXED_KEY)
    else:
        monkeypatch.setattr(db_key, "require_encryption", lambda: False)
    db = ScanDatabase(db_path=str(tmp_path / "scans.db"))
    yield db
    db.close()


def test_crud_both_backends(any_db):
    sid = any_db.create_session("S", 1.0)
    any_db.insert_session_data_rows([
        {"session_id": sid, "cam_id": 0, "side": 0, "timestamp_s": 1.0, "bfi": 1.0},
        {"session_id": sid, "cam_id": 1, "side": 1, "timestamp_s": 2.0, "bfi": 2.0},
    ])
    got = list(any_db.iter_session_data(sid))
    assert len(got) == 2
    assert any_db.get_session(sid)["session_label"] == "S"
