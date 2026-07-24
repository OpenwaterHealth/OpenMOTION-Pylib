"""Encryption key provisioning + the process-level encryption policy.

This is the ONLY module that touches the OS keystore. ``ScanDatabase`` (via
``db_open``) self-fetches the key here; no other class ever holds it.

The policy defaults to plaintext so every existing caller keeps working with no
change. A clinical build opts in exactly once by calling ``set_policy`` from its
signed build config; see docs/superpowers/specs/2026-07-23-sqlite-encryption-design.md
(§5.2).
"""
from __future__ import annotations

import secrets
from pathlib import Path

_SERVICE = "openmotion"
_ENTRY = "scan-db"

# None until set; require_encryption() collapses it to a bool (default False).
_require_encryption: bool | None = None


class EncryptionUnavailable(RuntimeError):
    """The encryption backend or dependency is missing where it is required,
    or a database's encryption state does not match the active policy."""


class EncryptionKeyMissing(RuntimeError):
    """An encrypted DB was opened but no usable key is available."""


def set_policy(*, require_encryption: bool) -> None:
    """Set the process-wide encryption policy. Called once by the app at startup.

    When ``require_encryption`` is True the keyring backend is verified now, so a
    misconfigured clinical machine fails at startup rather than at scan time.
    """
    global _require_encryption
    _require_encryption = bool(require_encryption)
    if _require_encryption:
        _assert_backend()


def require_encryption() -> bool:
    """True iff new/existing scan DBs must be encrypted. Default False (unset)."""
    return bool(_require_encryption)


def _assert_backend() -> None:
    import keyring

    kr = keyring.get_keyring()
    # Windows Credential Manager is the supported clinical backend.
    if "WinVault" not in type(kr).__name__ and "Windows" not in type(kr).__module__:
        raise EncryptionUnavailable(
            "encryption policy requires the Windows Credential Manager keyring, "
            f"got {type(kr).__module__}.{type(kr).__name__}"
        )


def get_key(*, create: bool = False) -> str:
    """Return the 64-hex-char (256-bit) DB key from the keystore.

    With ``create=True`` a fresh random key is generated and stored if none
    exists (used when creating a new encrypted DB). With ``create=False`` a
    missing key raises ``EncryptionKeyMissing`` (used when opening an existing
    encrypted DB). Never logs or echoes the key.
    """
    import keyring

    value = keyring.get_password(_SERVICE, _ENTRY)
    if value is None:
        if not create:
            raise EncryptionKeyMissing(
                "no scan-db encryption key in the keystore for this user/machine"
            )
        value = secrets.token_bytes(32).hex()
        keyring.set_password(_SERVICE, _ENTRY, value)
    return value


def export_key(path: str | Path) -> None:
    """Write the current key to ``path`` for planned recovery/migration.

    Raises ``EncryptionKeyMissing`` if there is no key to export. The caller is
    responsible for moving the file to secure storage (see design doc §6.1).
    """
    key = get_key(create=False)
    Path(path).write_text(key, encoding="ascii")


def import_key(path: str | Path) -> None:
    """Load a key exported by ``export_key`` back into the keystore."""
    import keyring

    key = Path(path).read_text(encoding="ascii").strip()
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key.lower()):
        raise ValueError("recovery key must be 64 hex characters")
    keyring.set_password(_SERVICE, _ENTRY, key)
