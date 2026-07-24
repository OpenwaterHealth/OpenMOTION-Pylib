"""The single choke point for opening a scan-family database.

Classifies a file by content, consults the process encryption policy
(``db_key``), selects the driver, and opens fail-closed. Reused by
``ScanDatabase`` and (in the app) ``AuditLog`` so the encryption invariant holds
uniformly. See docs/superpowers/specs/2026-07-23-sqlite-encryption-design.md
(§5.1/§5.2).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from omotion import db_key

_SQLITE_MAGIC = b"SQLite format 3\x00"   # 16 bytes; SQLCipher encrypts its header
_MIN_DB_SIZE = 100                       # a full SQLite header is 100 bytes

_STD_PRAGMAS = (
    "journal_mode=WAL",
    "synchronous=NORMAL",
    "foreign_keys=ON",
    "temp_store=MEMORY",
    "busy_timeout=5000",
)


def classify_file(path: str | Path) -> str:
    """Return 'new', 'plaintext', or 'encrypted' for ``path``.

    A missing / 0-byte / <100-byte file is 'new' (no real header yet) so a
    killed pre-flight that left a 0-byte file is re-created rather than treated
    as encrypted. Otherwise the first 16 bytes decide: the SQLite magic means
    plaintext; anything else means encrypted.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return "new"
    if size < _MIN_DB_SIZE:
        return "new"
    with open(path, "rb") as fh:
        head = fh.read(16)
    return "plaintext" if head == _SQLITE_MAGIC else "encrypted"


def connect(path: str | Path, *, create_ok: bool = True):
    """Open a DB-API connection to a scan-family DB, fail-closed under the policy.

    Returns a connection with ``row_factory`` set to the driver-matched Row and
    the standard PRAGMAs applied. Raises rather than ever silently opening
    plaintext under the encryption policy. Never rewrites in place — a plaintext
    file under the policy is refused; migration is a separate step
    (``db_migrate``).
    """
    path = str(path)
    kind = classify_file(path)
    require = db_key.require_encryption()

    if not create_ok and kind == "new":
        raise FileNotFoundError(f"scan database not found: {path}")

    if require:
        if kind == "plaintext":
            raise db_key.EncryptionUnavailable(
                f"encryption policy is on but {path} is a plaintext database; "
                "run db_migrate.migrate_plaintext_to_encrypted() first"
            )
        key = db_key.get_key(create=(kind == "new"))
        return _open_encrypted(path, key)

    # policy off (research / default)
    if kind == "encrypted":
        raise db_key.EncryptionUnavailable(
            f"{path} is an encrypted database; open it on a build with the "
            "encryption policy enabled"
        )
    return _open_plain(path)


def _apply_standard_pragmas(conn) -> None:
    for pragma in _STD_PRAGMAS:
        conn.execute(f"PRAGMA {pragma}")


def _open_plain(path: str):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_standard_pragmas(conn)
    return conn


def _open_encrypted(path: str, key: str):
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError as exc:
        raise db_key.EncryptionUnavailable(
            "encryption policy is on but sqlcipher3 is not installed"
        ) from exc

    conn = sqlcipher.connect(path, check_same_thread=False)
    # PRAGMA key MUST be the first statement, before any page is touched.
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.row_factory = sqlcipher.Row          # driver-matched Row, NOT sqlite3.Row
    try:
        # Probe read: SQLCipher validates the key lazily, so force a page-1 read
        # now to surface a wrong/rotated key as a key error rather than as an
        # opaque "file is not a database" deep in _init_schema.
        conn.execute("SELECT count(*) FROM sqlite_master")
    except sqlcipher.DatabaseError as exc:
        conn.close()
        raise db_key.EncryptionKeyMissing(
            f"the key did not decrypt {path} (wrong or rotated key)"
        ) from exc
    _apply_standard_pragmas(conn)
    return conn
