from __future__ import annotations

import unittest

from asset_assistant.credential_store import (
    CredentialStore,
    CredentialStoreError,
    _backend_is_allowed,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


class CredentialStoreTests(unittest.TestCase):
    def test_register_replace_read_and_delete_use_backend_only(self) -> None:
        backend = FakeKeyring()
        store = CredentialStore(backend, trusted_test_backend=True)
        store.set_secret("opendart", "api_key", "first-secret")
        self.assertTrue(store.has_secret("opendart", "api_key"))
        store.set_secret("opendart", "api_key", "replacement-secret")
        self.assertEqual(store.get_secret("opendart", "api_key"), "replacement-secret")
        store.delete_secret("opendart", "api_key")
        self.assertFalse(store.has_secret("opendart", "api_key"))

    def test_unapproved_backend_fails_closed(self) -> None:
        with self.assertRaises(CredentialStoreError):
            CredentialStore(FakeKeyring())

    def test_secure_backend_allowlist_requires_exact_module_and_class(self) -> None:
        exact = type("Keyring", (), {"__module__": "keyring.backends.SecretService"})()
        prefixed = type(
            "Keyring", (), {"__module__": "keyring.backends.SecretService.untrusted"}
        )()
        wrong_class = type(
            "OtherKeyring", (), {"__module__": "keyring.backends.SecretService"}
        )()
        self.assertTrue(_backend_is_allowed(exact))
        self.assertFalse(_backend_is_allowed(prefixed))
        self.assertFalse(_backend_is_allowed(wrong_class))

    def test_invalid_sec_user_agent_is_rejected_before_backend_write(self) -> None:
        backend = FakeKeyring()
        store = CredentialStore(backend, trusted_test_backend=True)
        marker = "private-456789"
        with self.assertRaises(CredentialStoreError) as raised:
            store.set_secret("sec", "user_agent", f"Example admin@example.com\r\nX: {marker}")
        self.assertEqual(backend.values, {})
        self.assertNotIn(marker, str(raised.exception))
        store.set_secret("sec", "user_agent", "Example Company admin@example.com")
        self.assertTrue(store.has_secret("sec", "user_agent"))

    def test_error_does_not_contain_secret(self) -> None:
        class FailingKeyring(FakeKeyring):
            def set_password(self, service: str, username: str, password: str) -> None:
                raise RuntimeError(f"failed for {password}")

        secret = "sensitive-tail-456789"
        store = CredentialStore(FailingKeyring(), trusted_test_backend=True)
        with self.assertRaises(CredentialStoreError) as raised:
            store.set_secret("ecos", "api_key", secret)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("456789", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
