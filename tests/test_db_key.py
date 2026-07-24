"""Tests for omotion.db_key — the sole keystore toucher + encryption policy.

None of these touch the real OS keyring: policy tests are pure, and key
get/create/export tests monkeypatch keyring.get_password/set_password.
"""
import importlib

import pytest

from omotion import db_key


@pytest.fixture(autouse=True)
def _reset_policy():
    # policy is module-global; reload to reset between tests
    importlib.reload(db_key)
    yield
    importlib.reload(db_key)


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------

def test_policy_defaults_to_plaintext():
    # unset MUST be False (backward-compat: existing callers never set it)
    assert db_key.require_encryption() is False


def test_set_policy_true(monkeypatch):
    monkeypatch.setattr(db_key, "_assert_backend", lambda: None)
    db_key.set_policy(require_encryption=True)
    assert db_key.require_encryption() is True


def test_set_policy_coerces_to_bool(monkeypatch):
    monkeypatch.setattr(db_key, "_assert_backend", lambda: None)
    db_key.set_policy(require_encryption=1)  # truthy
    assert db_key.require_encryption() is True


def test_set_policy_false_does_not_check_backend(monkeypatch):
    # backend must NOT be asserted when encryption is off (research/headless)
    def _boom():
        raise AssertionError("backend must not be checked when policy is off")
    monkeypatch.setattr(db_key, "_assert_backend", _boom)
    db_key.set_policy(require_encryption=False)
    assert db_key.require_encryption() is False


# --------------------------------------------------------------------------
# Key get / create against a fake keyring
# --------------------------------------------------------------------------

class _FakeKeyring:
    def __init__(self):
        self._store = {}

    def get_password(self, service, entry):
        return self._store.get((service, entry))

    def set_password(self, service, entry, value):
        self._store[(service, entry)] = value


@pytest.fixture
def fake_keyring(monkeypatch):
    import keyring
    kr = _FakeKeyring()
    monkeypatch.setattr(keyring, "get_password", kr.get_password)
    monkeypatch.setattr(keyring, "set_password", kr.set_password)
    return kr


def test_get_key_missing_without_create_raises(fake_keyring):
    with pytest.raises(db_key.EncryptionKeyMissing):
        db_key.get_key(create=False)


def test_get_key_creates_and_persists(fake_keyring):
    k1 = db_key.get_key(create=True)
    assert len(k1) == 64
    assert all(c in "0123456789abcdef" for c in k1)
    k2 = db_key.get_key(create=False)  # now exists
    assert k1 == k2


def test_generated_keys_are_random(fake_keyring):
    k1 = db_key.get_key(create=True)
    fake_keyring._store.clear()
    k2 = db_key.get_key(create=True)
    assert k1 != k2


def test_get_key_create_true_is_idempotent(fake_keyring):
    # create=True against an EXISTING key must return it unchanged, never
    # regenerate — regeneration would orphan every already-encrypted DB.
    k1 = db_key.get_key(create=True)
    k2 = db_key.get_key(create=True)
    assert k1 == k2


# --------------------------------------------------------------------------
# Backend pinning — the real _assert_backend (not stubbed)
# --------------------------------------------------------------------------

def test_set_policy_true_rejects_non_windows_backend(monkeypatch):
    import keyring

    class _PlaintextKeyring:  # name lacks 'WinVault'; module lacks 'Windows'
        pass

    monkeypatch.setattr(keyring, "get_keyring", lambda: _PlaintextKeyring())
    with pytest.raises(db_key.EncryptionUnavailable):
        db_key.set_policy(require_encryption=True)


def test_set_policy_true_accepts_winvault_backend(monkeypatch):
    import keyring

    class WinVaultKeyring:  # name contains 'WinVault'
        pass

    monkeypatch.setattr(keyring, "get_keyring", lambda: WinVaultKeyring())
    db_key.set_policy(require_encryption=True)  # must not raise
    assert db_key.require_encryption() is True


# --------------------------------------------------------------------------
# Export / import recovery seam
# --------------------------------------------------------------------------

def test_export_then_import_roundtrip(fake_keyring, tmp_path):
    original = db_key.get_key(create=True)
    out = tmp_path / "scan-db.key"
    db_key.export_key(out)
    fake_keyring._store.clear()                 # simulate a reimaged machine
    with pytest.raises(db_key.EncryptionKeyMissing):
        db_key.get_key(create=False)
    db_key.import_key(out)
    assert db_key.get_key(create=False) == original


def test_export_requires_existing_key(fake_keyring, tmp_path):
    with pytest.raises(db_key.EncryptionKeyMissing):
        db_key.export_key(tmp_path / "x.key")


def test_import_rejects_malformed_key(fake_keyring, tmp_path):
    bad = tmp_path / "bad.key"
    bad.write_text("not-a-64-hex-key")
    with pytest.raises(ValueError):
        db_key.import_key(bad)


def test_import_rejects_64_char_non_hex_key(fake_keyring, tmp_path):
    # 64 chars passes the length check and reaches the hex-char predicate
    bad = tmp_path / "bad64.key"
    bad.write_text("z" * 64)
    with pytest.raises(ValueError):
        db_key.import_key(bad)
