"""One-time plaintext -> encrypted migration for scan-family databases.

Crash-atomic: exports to a temp file, fsyncs, atomically renames over the
original, verifies, and removes the stale plaintext WAL/SHM sidecars. Keeps a
``.pre-encryption.bak``. Called explicitly at app startup under the encryption
policy — never on open. See design doc §7.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from omotion import db_key, db_open


def _replace_with_retry(src: str, dst: str, *, attempts: int = 10, delay: float = 0.1) -> None:
    """os.replace with a bounded retry for transient Windows sharing violations
    (an AV scanner / indexer / a briefly-held handle can make replace raise
    PermissionError).

    A retry only rides out *transient* holders. A connection someone left open
    on the database never goes away, so after exhausting the attempts we
    re-raise with an actionable message instead of a bare ``WinError 5: Access
    is denied`` — that is the difference between a diagnosable failure and a
    support call during a clinical update.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            if attempt == attempts - 1:
                raise PermissionError(
                    f"could not replace {dst} with the encrypted copy — the file "
                    "is still open. Close every connection to it before migrating "
                    "(on Windows the atomic replace needs an unopened target). "
                    "In the app this usually means AuditLog or a ScanDatabase "
                    "handle was opened before the migration ran. The original "
                    f"database is untouched and {src} can be discarded."
                ) from exc
            time.sleep(delay)


def migrate_plaintext_to_encrypted(path: str | Path) -> bool:
    """Encrypt an existing plaintext scan DB in place.

    Returns True if a migration was performed, False if the file was already
    encrypted or absent (no-op). Leaves ``<name>.pre-encryption.bak`` next to the
    database; the operator removes it per SOP once the update is confirmed.

    Precondition: all connections to ``path`` must be closed before calling —
    the atomic replace needs an unopened target on Windows.
    """
    path = Path(path)
    if db_open.classify_file(path) != "plaintext":
        return False

    from sqlcipher3 import dbapi2 as sqlcipher

    key = db_key.get_key(create=True)
    backup = path.with_name(path.name + ".pre-encryption.bak")
    tmp = path.with_name(path.name + ".enc.tmp")

    if tmp.exists():
        tmp.unlink()  # clear a leftover temp from a prior crashed run

    # Fold any committed WAL into the main file BEFORE backing up, so the .bak
    # (the recovery artifact) contains every committed row and an empty WAL —
    # not just what was in the main file at copy time.
    check = sqlcipher.connect(str(path))
    try:
        try:
            check.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlcipher.DatabaseError:
            pass  # not in WAL mode — nothing to checkpoint
    finally:
        check.close()

    shutil.copy2(path, backup)

    # Open the plaintext source with NO key (SQLCipher reads unencrypted DBs when
    # unkeyed) and export the full logical DB into a freshly-keyed temp file.
    src = sqlcipher.connect(str(path))
    try:
        src.execute(
            "ATTACH DATABASE ? AS enc KEY \"x'%s'\"" % key, (str(tmp),)
        )
        src.execute("SELECT sqlcipher_export('enc')")
        src.execute("DETACH DATABASE enc")
    finally:
        src.close()
    del key  # unused hereafter; do not retain across fsync/replace/verify

    # Durably flush the encrypted copy before the atomic replace. The handle
    # must be writable — Windows os.fsync (_commit) rejects a read-only fd.
    fd = os.open(str(tmp), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

    _replace_with_retry(str(tmp), str(path))  # atomic on the same filesystem

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
