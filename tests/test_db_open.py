"""Tests for omotion.db_open — content-based classification + fail-closed open.

Encrypted-path tests require sqlcipher3 (clinical-only dep) and never touch the
real keystore: db_key.get_key / require_encryption are monkeypatched.
"""
import sqlite3

import pytest

from omotion import db_key, db_open

FIXED_KEY = "a" * 64
MAGIC = b"SQLite format 3\x00"


def _first16(path):
    with open(path, "rb") as fh:
        return fh.read(16)


def _set_policy(monkeypatch, *, require, key=FIXED_KEY):
    monkeypatch.setattr(db_key, "require_encryption", lambda: require)
    if require:
        monkeypatch.setattr(db_key, "get_key", lambda *, create=False: key)


# --------------------------------------------------------------------------
# classify_file  (Task 4)
# --------------------------------------------------------------------------

def test_classify_missing_is_new(tmp_path):
    assert db_open.classify_file(tmp_path / "nope.db") == "new"


def test_classify_zero_byte_is_new(tmp_path):
    p = tmp_path / "empty.db"
    p.write_bytes(b"")
    assert db_open.classify_file(p) == "new"


def test_classify_truncated_is_new(tmp_path):
    p = tmp_path / "short.db"
    p.write_bytes(b"SQLite for")  # < 100 bytes
    assert db_open.classify_file(p) == "new"


def test_classify_plaintext_sqlite(tmp_path):
    p = tmp_path / "plain.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    assert db_open.classify_file(p) == "plaintext"


def test_classify_encrypted_looking(tmp_path):
    p = tmp_path / "enc.db"
    p.write_bytes(b"\x00\x11\x22\x33" * 40)  # >=100 bytes, not the magic
    assert db_open.classify_file(p) == "encrypted"


# --------------------------------------------------------------------------
# connect()  (Task 5)
# --------------------------------------------------------------------------

def test_research_creates_plaintext(monkeypatch, tmp_path):
    _set_policy(monkeypatch, require=False)
    p = tmp_path / "scans.db"
    con = db_open.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    assert _first16(p) == MAGIC


def test_clinical_creates_encrypted(monkeypatch, tmp_path):
    pytest.importorskip("sqlcipher3")
    _set_policy(monkeypatch, require=True)
    p = tmp_path / "scans.db"
    con = db_open.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    assert _first16(p) != MAGIC
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(p).execute("SELECT 1 FROM t")


def test_clinical_refuses_existing_plaintext(monkeypatch, tmp_path):
    p = tmp_path / "scans.db"
    # create plaintext under research policy
    _set_policy(monkeypatch, require=False)
    con = db_open.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    # now flip to clinical: opening a plaintext file must be refused, not opened
    _set_policy(monkeypatch, require=True)
    with pytest.raises(db_key.EncryptionUnavailable):
        db_open.connect(p)


def test_research_refuses_encrypted(monkeypatch, tmp_path):
    pytest.importorskip("sqlcipher3")
    p = tmp_path / "scans.db"
    _set_policy(monkeypatch, require=True)
    con = db_open.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    # research policy meets an encrypted file -> legible refusal, never get_key
    monkeypatch.setattr(db_key, "require_encryption", lambda: False)
    with pytest.raises(db_key.EncryptionUnavailable):
        db_open.connect(p)


def test_wrong_key_raises_key_error_not_corruption(monkeypatch, tmp_path):
    pytest.importorskip("sqlcipher3")
    p = tmp_path / "scans.db"
    _set_policy(monkeypatch, require=True)
    con = db_open.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.commit()
    con.close()
    # different key on the same file -> distinct key error via the probe read
    monkeypatch.setattr(db_key, "get_key", lambda *, create=False: "b" * 64)
    with pytest.raises(db_key.EncryptionKeyMissing):
        db_open.connect(p)


def test_create_ok_false_raises_on_missing(monkeypatch, tmp_path):
    _set_policy(monkeypatch, require=False)
    with pytest.raises(FileNotFoundError):
        db_open.connect(tmp_path / "nope.db", create_ok=False)


def test_pragma_key_is_first_statement(monkeypatch, tmp_path):
    """Regression guard: PRAGMA key must precede any other statement, else
    SQLCipher fails to decrypt. We assert by opening an existing encrypted DB
    (which only works if keying happened before the first page read)."""
    pytest.importorskip("sqlcipher3")
    _set_policy(monkeypatch, require=True)
    p = tmp_path / "scans.db"
    con = db_open.connect(p)
    con.execute("CREATE TABLE t(x)")
    con.execute("INSERT INTO t VALUES(7)")
    con.commit()
    con.close()
    con2 = db_open.connect(p)  # WAL pragma etc. run AFTER key inside connect()
    assert con2.execute("SELECT x FROM t").fetchone()[0] == 7
    con2.close()
