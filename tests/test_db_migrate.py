"""Tests for omotion.db_migrate — plaintext -> encrypted migration."""
import sqlite3

import pytest

from omotion import db_key, db_migrate, db_open

pytest.importorskip("sqlcipher3")

FIXED_KEY = "a" * 64
MAGIC = b"SQLite format 3\x00"


@pytest.fixture
def clinical(monkeypatch):
    monkeypatch.setattr(db_key, "require_encryption", lambda: True)
    monkeypatch.setattr(db_key, "get_key", lambda *, create=False: FIXED_KEY)


def _make_plaintext(path):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE sessions(id INTEGER PRIMARY KEY, session_label TEXT);"
        "CREATE INDEX ix ON sessions(session_label);"
        "CREATE TABLE logs(id INTEGER PRIMARY KEY, ev TEXT);"
    )
    con.execute("INSERT INTO sessions(session_label) VALUES('OLD-PHI')")
    con.execute("INSERT INTO logs(ev) VALUES('e')")
    con.commit()
    con.close()


def test_migration_encrypts_and_preserves(clinical, tmp_path):
    p = tmp_path / "scans.db"
    _make_plaintext(p)
    assert db_migrate.migrate_plaintext_to_encrypted(p) is True

    with open(p, "rb") as fh:                    # opaque now
        assert fh.read(16) != MAGIC

    con = db_open.connect(p)                       # readable via keyed helper
    assert con.execute("SELECT session_label FROM sessions").fetchone()[0] == "OLD-PHI"
    assert con.execute("SELECT count(*) FROM logs").fetchone()[0] == 1
    idx = con.execute(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    assert idx == 1
    con.close()

    assert (tmp_path / "scans.db.pre-encryption.bak").exists()   # backup kept
    with open(tmp_path / "scans.db.pre-encryption.bak", "rb") as fh:
        assert fh.read(16) == MAGIC                # backup is the plaintext original


def test_migration_noop_on_already_encrypted(clinical, tmp_path):
    p = tmp_path / "scans.db"
    con = db_open.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    assert db_migrate.migrate_plaintext_to_encrypted(p) is False
    con = db_open.connect(p)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 0
    con.close()


def test_migration_noop_on_missing(clinical, tmp_path):
    assert db_migrate.migrate_plaintext_to_encrypted(tmp_path / "nope.db") is False


def test_migration_preserves_wal_committed_data(clinical, tmp_path):
    """A real scans.db is WAL-mode; committed rows in an uncheckpointed -wal
    must survive migration (not be lost when stale sidecars are removed)."""
    p = tmp_path / "scans.db"
    con = sqlite3.connect(p)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY, label TEXT)")
    con.execute("INSERT INTO sessions(label) VALUES('WAL-ROW')")
    con.commit()
    con.close()  # may leave scans.db-wal alongside
    assert db_migrate.migrate_plaintext_to_encrypted(p) is True
    con = db_open.connect(p)
    assert con.execute("SELECT label FROM sessions").fetchone()[0] == "WAL-ROW"
    con.close()


def test_schema_migration_runs_on_encrypted_db(clinical, tmp_path):
    """An app-update schema bump (_init_schema ALTER/CREATE INDEX on every open)
    must run cleanly on an encrypted DB and preserve data. See §7.1."""
    from omotion import ScanDatabase

    path = str(tmp_path / "scans.db")
    db = ScanDatabase(db_path=path)               # creates encrypted + schema
    sid = db.create_session("S", 1.0)
    db.insert_session_data(sid, cam_id=0, side=0, timestamp_s=1.0, bfi=1.5)
    db.close()
    # reopen — _init_schema re-runs ADD COLUMN / CREATE INDEX IF NOT EXISTS:
    db2 = ScanDatabase(db_path=path)
    rows = list(db2.iter_session_data(sid))
    db2.close()
    assert rows[0]["bfi"] == 1.5
