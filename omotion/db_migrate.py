"""One-time plaintext -> encrypted migration for scan-family databases.

Crash-atomic: exports to a temp file, fsyncs, atomically renames over the
original, verifies, and removes the stale plaintext WAL/SHM sidecars. Keeps a
``.pre-encryption.bak``. Called explicitly at app startup under the encryption
policy — never on open. See design doc §7.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from omotion import db_key, db_open


def migrate_plaintext_to_encrypted(path: str | Path) -> bool:
    """Encrypt an existing plaintext scan DB in place.

    Returns True if a migration was performed, False if the file was already
    encrypted or absent (no-op). Leaves ``<name>.pre-encryption.bak`` next to the
    database; the operator removes it per SOP once the update is confirmed.
    """
    path = Path(path)
    if db_open.classify_file(path) != "plaintext":
        return False

    from sqlcipher3 import dbapi2 as sqlcipher

    key = db_key.get_key(create=True)
    backup = path.with_name(path.name + ".pre-encryption.bak")
    tmp = path.with_name(path.name + ".enc.tmp")

    shutil.copy2(path, backup)
    if tmp.exists():
        tmp.unlink()

    # Open the plaintext source with NO key (SQLCipher reads unencrypted DBs when
    # unkeyed). Fold any committed WAL back into the main file first so nothing
    # committed is lost when the stale sidecars are removed below, then export
    # the full logical DB into a freshly-keyed temp file.
    src = sqlcipher.connect(str(path))
    try:
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlcipher.DatabaseError:
            pass  # not in WAL mode — nothing to checkpoint
        src.execute(
            "ATTACH DATABASE ? AS enc KEY \"x'%s'\"" % key, (str(tmp),)
        )
        src.execute("SELECT sqlcipher_export('enc')")
        src.execute("DETACH DATABASE enc")
    finally:
        src.close()

    # Durably flush the encrypted copy before the atomic replace. The handle
    # must be writable — Windows os.fsync (_commit) rejects a read-only fd.
    fd = os.open(str(tmp), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(str(tmp), str(path))  # atomic on the same filesystem

    # The old plaintext sidecars belong to the pre-migration file and would be
    # misapplied to the new encrypted DB — remove them (their committed content
    # was folded in above).
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            side.unlink()

    # Verify the encrypted result is readable with the key before returning.
    con = db_open.connect(path)
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        con.close()
    return True
