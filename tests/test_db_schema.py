"""Tests for omotion.db_schema — versioned scan-database migrations.

Covers the upgrade path a real app update takes: an older database opened by
newer code is migrated in place, existing rows survive, and the version is
tracked. Also covers atomicity, idempotency, the downgrade guard, and that all
of it works on an ENCRYPTED database.
"""
import sqlite3

import pytest

from omotion import db_key, db_schema
from omotion.ScanDatabase import ScanDatabase

FIXED_KEY = "a" * 64


@pytest.fixture
def clinical(monkeypatch):
    """Encryption policy on, with an injected key (never touches the keystore)."""
    pytest.importorskip("sqlcipher3")
    monkeypatch.setattr(db_key, "require_encryption", lambda: True)
    monkeypatch.setattr(db_key, "get_key", lambda *, create=False: FIXED_KEY)


def _legacy_v1_db(path):
    """A database as an older SDK left it: v1 schema, real rows, and
    user_version 0 because versioning did not exist yet."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, session_label TEXT NOT NULL,"
        " session_start REAL NOT NULL, session_end REAL, session_notes TEXT,"
        " session_meta TEXT)"
    )
    con.execute(
        "CREATE TABLE session_data (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL,"
        " cam_id INTEGER NOT NULL, side INTEGER NOT NULL CHECK(side IN (0,1)),"
        " frame_id INTEGER NOT NULL DEFAULT -1, timestamp_s REAL NOT NULL, bfi REAL,"
        " bvi REAL, contrast REAL, mean REAL, quality TEXT DEFAULT 'ok')"
    )
    con.execute(
        "CREATE TABLE database_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    con.execute("INSERT INTO sessions(session_label, session_start) VALUES('OLD-SCAN', 1.0)")
    con.execute(
        "INSERT INTO session_data(session_id, cam_id, side, timestamp_s, bfi)"
        " VALUES(1, 3, 1, 2.5, 1.75)"
    )
    con.commit()
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    con.close()


# ---------------------------------------------------------------------------
# Fresh databases
# ---------------------------------------------------------------------------

def test_fresh_db_is_created_at_current_version(tmp_path):
    db = ScanDatabase(db_path=str(tmp_path / "scans.db"))
    try:
        conn = db._connection()
        assert db_schema.current_version(conn) == db_schema.SCHEMA_VERSION
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "session_annotations" in tables            # migration 002 new table
        cols = {r[1] for r in conn.execute("PRAGMA table_info('sessions')")}
        assert "operator_id" in cols                       # migration 002 ALTER
    finally:
        db.close()


def test_reopening_does_not_re_run_migrations(tmp_path):
    path = str(tmp_path / "scans.db")
    db = ScanDatabase(db_path=path)
    sid = db.create_session("S", 1.0)
    db.close()
    db2 = ScanDatabase(db_path=path)          # second open: no-op upgrade
    try:
        assert db_schema.current_version(db2._connection()) == db_schema.SCHEMA_VERSION
        assert db2.get_session(sid)["session_label"] == "S"
    finally:
        db2.close()


# ---------------------------------------------------------------------------
# The update path: old database opened by new code
# ---------------------------------------------------------------------------

def test_legacy_db_is_upgraded_and_data_survives(tmp_path):
    """The scenario an app update actually hits."""
    path = tmp_path / "scans.db"
    _legacy_v1_db(path)

    db = ScanDatabase(db_path=str(path))       # opening runs the migrations
    try:
        conn = db._connection()
        assert db_schema.current_version(conn) == db_schema.SCHEMA_VERSION

        # pre-existing rows survived, untouched
        assert db.get_session(1)["session_label"] == "OLD-SCAN"
        row = next(iter(db.iter_session_data(1)))
        assert row["bfi"] == 1.75
        assert row["cam_id"] == 3

        # the new schema is present and usable
        assert "operator_id" in {r[1] for r in conn.execute("PRAGMA table_info('sessions')")}
        conn.execute(
            "INSERT INTO session_annotations(session_id, timestamp_s, label)"
            " VALUES(1, 4.2, 'cuff inflated')"
        )
        conn.commit()
        assert conn.execute(
            "SELECT label FROM session_annotations WHERE session_id=1"
        ).fetchone()[0] == "cuff inflated"
    finally:
        db.close()


def test_upgrade_from_scratch_reports_version(tmp_path):
    con = sqlite3.connect(tmp_path / "bare.db")
    try:
        assert db_schema.upgrade(con) == db_schema.SCHEMA_VERSION
        assert db_schema.upgrade(con) == db_schema.SCHEMA_VERSION   # idempotent
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Safety: atomicity, downgrade guard, no-write when current
# ---------------------------------------------------------------------------

def test_failed_migration_rolls_back_and_keeps_version(tmp_path, monkeypatch):
    """A migration that raises must leave NO partial DDL and must not bump the
    version, so the next open retries from a known-good state."""
    def _boom(conn):
        conn.execute("CREATE TABLE half_applied(x)")
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(
        db_schema, "MIGRATIONS",
        [(1, "baseline", db_schema._migration_001_baseline), (2, "boom", _boom)],
    )
    con = sqlite3.connect(tmp_path / "scans.db")
    try:
        with pytest.raises(RuntimeError, match="simulated migration failure"):
            db_schema.upgrade(con)
        assert db_schema.current_version(con) == 1        # stopped at last good
        tables = {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "half_applied" not in tables               # rolled back
        assert "sessions" in tables                       # migration 1 kept
    finally:
        con.close()


def test_db_from_a_newer_sdk_is_refused(tmp_path):
    con = sqlite3.connect(tmp_path / "future.db")
    try:
        con.execute(f"PRAGMA user_version = {db_schema.SCHEMA_VERSION + 5}")
        with pytest.raises(db_schema.SchemaTooNewError):
            db_schema.upgrade(con)
    finally:
        con.close()


def test_up_to_date_db_performs_no_write(tmp_path, monkeypatch):
    """An up-to-date DB must not write, so opening a read-only or archived
    database still works."""
    path = tmp_path / "scans.db"
    ScanDatabase(db_path=str(path)).close()

    con = sqlite3.connect(path)
    try:
        def _fail(*_a, **_k):
            raise AssertionError("upgrade() must not BEGIN a transaction when current")

        monkeypatch.setattr(db_schema, "MIGRATIONS", [(1, "x", _fail), (2, "y", _fail)])
        assert db_schema.upgrade(con) == db_schema.SCHEMA_VERSION
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Encrypted databases
# ---------------------------------------------------------------------------

def test_migrations_run_on_encrypted_db(clinical, tmp_path):
    db = ScanDatabase(db_path=str(tmp_path / "scans.db"))
    try:
        conn = db._connection()
        assert db_schema.current_version(conn) == db_schema.SCHEMA_VERSION
        conn.execute(
            "INSERT INTO sessions(session_label, session_start, operator_id)"
            " VALUES('S', 1.0, 'ethan')"
        )
        conn.commit()
        assert conn.execute("SELECT operator_id FROM sessions").fetchone()[0] == "ethan"
    finally:
        db.close()
    with open(tmp_path / "scans.db", "rb") as fh:          # still encrypted
        assert fh.read(16) != b"SQLite format 3\x00"


def test_legacy_encrypted_db_is_upgraded(clinical, tmp_path):
    """An encrypted DB written by an older SDK upgrades on open — the clinical
    equivalent of the plaintext update path above."""
    from sqlcipher3 import dbapi2 as sqlcipher

    path = str(tmp_path / "scans.db")
    con = sqlcipher.connect(path)
    con.execute(f"PRAGMA key = \"x'{FIXED_KEY}'\"")
    con.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, session_label TEXT NOT NULL,"
        " session_start REAL NOT NULL, session_end REAL, session_notes TEXT,"
        " session_meta TEXT)"
    )
    con.execute(
        "CREATE TABLE session_data (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL,"
        " cam_id INTEGER NOT NULL, side INTEGER NOT NULL CHECK(side IN (0,1)),"
        " frame_id INTEGER NOT NULL DEFAULT -1, timestamp_s REAL NOT NULL, bfi REAL,"
        " bvi REAL, contrast REAL, mean REAL, quality TEXT DEFAULT 'ok')"
    )
    con.execute("INSERT INTO sessions(session_label, session_start) VALUES('OLD-ENC', 1.0)")
    con.commit()
    con.close()

    db = ScanDatabase(db_path=path)
    try:
        assert db_schema.current_version(db._connection()) == db_schema.SCHEMA_VERSION
        assert db.get_session(1)["session_label"] == "OLD-ENC"
        assert "session_annotations" in {
            r[0] for r in db._connection().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        db.close()
