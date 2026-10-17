"""OS-keyring boundary for the single-user localhost runtime.

The local web state SQLite file never contains provider credentials or the
key used to blind login-rate-limit identifiers.  Secret Service is the only
supported Ubuntu backend; an unavailable or malformed keyring fails closed.
"""

from __future__ import annotations

import base64
import os
import threading
from datetime import UTC, datetime
from typing import Callable

from asset_assistant.credential_store import (
    ALLOWED_FIELDS,
    CredentialStore,
    CredentialStoreError,
    KeyringBackend,
    _backend_is_allowed,
    _load_secure_backend,
    valid_sec_user_agent,
)
from asset_assistant.domain import DomainValidationError, nonempty

from .repositories import CredentialStatus


LOCAL_SECRET_SERVICE = "org.long-term-asset-assistant.wealthy-local-web"
LOCAL_MASTER_KEY_ITEM = "login-fingerprint-key-v1"
_KEY_BYTES = 32


class LocalSecretStore:
    """Dedicated fixed-item keyring access with no file fallback."""

    def __init__(
        self,
        backend: KeyringBackend | None = None,
        *,
        trusted_test_backend: bool = False,
    ) -> None:
        self._backend = backend
        self._trusted_test_backend = trusted_test_backend

    def backend(self) -> KeyringBackend:
        if self._backend is None:
            self._backend = _load_secure_backend()
        elif not self._trusted_test_backend and not _backend_is_allowed(self._backend):
            raise CredentialStoreError("허용되지 않은 인증정보 저장소입니다.")
        return self._backend

    def provider_store(self) -> CredentialStore:
        backend = self.backend()
        return CredentialStore(
            backend,
            trusted_test_backend=self._trusted_test_backend,
        )

    def load_or_create_master_key(self) -> bytes:
        try:
            backend = self.backend()
            encoded = backend.get_password(LOCAL_SECRET_SERVICE, LOCAL_MASTER_KEY_ITEM)
            if encoded is None:
                generated = os.urandom(_KEY_BYTES)
                encoded = base64.urlsafe_b64encode(generated).decode("ascii")
                backend.set_password(LOCAL_SECRET_SERVICE, LOCAL_MASTER_KEY_ITEM, encoded)
                confirmed = backend.get_password(LOCAL_SECRET_SERVICE, LOCAL_MASTER_KEY_ITEM)
                if confirmed != encoded:
                    raise CredentialStoreError("보안 저장소 쓰기 결과를 확인할 수 없습니다.")
            if not isinstance(encoded, str) or len(encoded) > 256:
                raise ValueError
            key = base64.b64decode(
                encoded.strip().encode("ascii"), altchars=b"-_", validate=True
            )
            if len(key) != _KEY_BYTES:
                raise ValueError
            return key
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError(
                "로컬 보안 저장소의 앱 키를 사용할 수 없습니다. 평문 파일로 대체하지 않습니다."
            ) from None

    def status(self) -> tuple[bool, str]:
        try:
            backend = self.backend()
            # Reading the fixed, non-secret item is enough to prove the backend
            # is reachable without returning its value.
            backend.get_password(LOCAL_SECRET_SERVICE, LOCAL_MASTER_KEY_ITEM)
        except Exception:
            return False, "UNAVAILABLE"
        return True, "OS_KEYRING"


_PROVIDER_MAPPING: dict[tuple[str, str], str] = {
    ("bls", "default"): "bls",
    ("sec", "default"): "sec",
    ("opendart", "default"): "opendart",
    ("ecos", "default"): "ecos",
    ("kis", "paper"): "kis_paper",
    ("kis", "live"): "kis_live",
}


class LocalCredentialRepository:
    """Web credential contract backed by the existing OS CredentialStore."""

    def __init__(
        self,
        store: CredentialStore,
        *,
        owner_guard: Callable[[str | None], None],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._owner_guard = owner_guard
        self._clock = clock
        self._lock = threading.RLock()
        self._primed = False
        self._configured: dict[tuple[str, str], bool] = {}
        self._sec_user_agent: str | None = None
        self._storage_available: bool | None = None
        self._storage_checked_at: datetime | None = None

    def _checked_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DomainValidationError("보안 저장소 상태 시각을 확인할 수 없습니다.")
        return value.astimezone(UTC)

    def _set_storage_state(self, available: bool) -> None:
        with self._lock:
            self._storage_available = available
            self._storage_checked_at = self._checked_now()

    def prime(self) -> None:
        """Read keyring state once before serving passive web/background reads.

        Secret Service access can display an unlock prompt.  Keeping the
        configured flags (and the SEC contact header needed by the background
        feed reader) in process memory prevents status polling from repeatedly
        touching the OS keyring.  A failed initial read is fail-closed by the
        local runtime rather than silently treating credentials as absent.
        """

        configured: dict[tuple[str, str], bool] = {}
        sec_user_agent: str | None = None
        try:
            for mapped in sorted(set(_PROVIDER_MAPPING.values())):
                for field in sorted(ALLOWED_FIELDS[mapped]):
                    value = self._store.get_secret(mapped, field)
                    configured[(mapped, field)] = value is not None
                    if mapped == "sec" and field == "user_agent":
                        sec_user_agent = value
        except Exception:
            self._set_storage_state(False)
            raise CredentialStoreError(
                "로컬 보안 저장소의 공급자 설정을 확인할 수 없습니다. 평문 파일로 대체하지 않습니다."
            ) from None
        with self._lock:
            self._configured = configured
            self._sec_user_agent = sec_user_agent
            self._primed = True
        self._set_storage_state(True)

    def _require_primed(self) -> None:
        if not self._primed:
            raise DomainValidationError("보안 저장소 상태를 확인할 수 없습니다.")

    def cached_sec_user_agent(self) -> str | None:
        """Return the startup/mutation-updated SEC value without keyring I/O."""

        with self._lock:
            self._require_primed()
            return self._sec_user_agent

    @staticmethod
    def _provider(provider: str, environment: str) -> str:
        try:
            return _PROVIDER_MAPPING[(provider, environment)]
        except (KeyError, TypeError):
            raise DomainValidationError("지원하지 않는 공급자 인증정보 항목입니다.") from None

    @staticmethod
    def _field(mapped: str, secret_name: str) -> None:
        if secret_name not in ALLOWED_FIELDS.get(mapped, frozenset()):
            raise DomainValidationError("지원하지 않는 공급자 인증정보 항목입니다.")

    def storage_status(self) -> dict[str, object]:
        self._owner_guard(None)
        with self._lock:
            available = self._storage_available
            checked_at = self._storage_checked_at
            if not self._primed and available is not False:
                self._require_primed()
        state = (
            "AVAILABLE_CONFIRMED"
            if available is True
            else "UNAVAILABLE_CONFIRMED"
            if available is False
            else "UNKNOWN"
        )
        return {
            "available": available,
            "confirmed": available is not None,
            "state": state,
            "backend": (
                "OS_KEYRING"
                if available is True
                else "UNAVAILABLE"
                if available is False
                else "UNKNOWN"
            ),
            "plaintext_fallback": False,
            "checked_at": checked_at.isoformat() if checked_at is not None else None,
            # Generic Secret Service implementations do not promise a
            # non-interactive availability probe.  Do not expose a button that
            # could cause repeated unlock dialogs from browser polling.
            "safe_recheck_allowed": False,
        }

    def status(
        self,
        owner_id: str,
        *,
        provider: str,
        environment: str,
        secret_name: str,
    ) -> CredentialStatus:
        self._owner_guard(owner_id)
        mapped = self._provider(provider, environment)
        self._field(mapped, secret_name)
        with self._lock:
            self._require_primed()
            configured = self._configured.get((mapped, secret_name), False)
        return CredentialStatus(
            provider=provider,
            environment=environment,
            secret_name=secret_name,
            configured=configured,
            updated_at=None,
            key_version="OS_KEYRING" if configured else None,
        )

    def upsert(
        self,
        owner_id: str,
        *,
        provider: str,
        environment: str,
        secret_name: str,
        plaintext: str,
        now: datetime | None = None,
    ) -> CredentialStatus:
        self._owner_guard(owner_id)
        del now
        mapped = self._provider(provider, environment)
        self._field(mapped, secret_name)
        value = nonempty(plaintext, field_name="secret")
        if len(value) > 8192:
            raise DomainValidationError("인증정보 길이가 허용 범위를 벗어났습니다.")
        if mapped == "sec" and secret_name == "user_agent" and not valid_sec_user_agent(value):
            raise DomainValidationError(
                "SEC User-Agent는 조직명과 연락 가능한 이메일 형식이어야 합니다."
            )
        try:
            self._store.set_secret(mapped, secret_name, value)
        except CredentialStoreError:
            self._set_storage_state(False)
            raise CredentialStoreError(
                "운영체제 보안 저장소를 사용할 수 없습니다. 평문 파일로 대체하지 않습니다."
            ) from None
        self._set_storage_state(True)
        with self._lock:
            self._require_primed()
            self._configured[(mapped, secret_name)] = True
            if mapped == "sec" and secret_name == "user_agent":
                self._sec_user_agent = value
        return CredentialStatus(
            provider=provider,
            environment=environment,
            secret_name=secret_name,
            configured=True,
            updated_at=None,
            key_version="OS_KEYRING",
        )

    def delete(
        self,
        owner_id: str,
        *,
        provider: str,
        environment: str,
        secret_name: str,
    ) -> bool:
        self._owner_guard(owner_id)
        mapped = self._provider(provider, environment)
        self._field(mapped, secret_name)
        with self._lock:
            self._require_primed()
            existed = self._configured.get((mapped, secret_name), False)
        try:
            self._store.delete_secret(mapped, secret_name)
        except CredentialStoreError:
            self._set_storage_state(False)
            raise CredentialStoreError(
                "운영체제 보안 저장소를 사용할 수 없습니다. 평문 파일로 대체하지 않습니다."
            ) from None
        self._set_storage_state(True)
        with self._lock:
            self._configured[(mapped, secret_name)] = False
            if mapped == "sec" and secret_name == "user_agent":
                self._sec_user_agent = None
        return existed

    def decrypt_for_provider_adapter(
        self,
        owner_id: str,
        *,
        provider: str,
        environment: str,
        secret_name: str,
    ) -> str:
        self._owner_guard(owner_id)
        mapped = self._provider(provider, environment)
        self._field(mapped, secret_name)
        try:
            value = self._store.get_secret(mapped, secret_name)
        except CredentialStoreError:
            self._set_storage_state(False)
            raise CredentialStoreError(
                "운영체제 보안 저장소를 사용할 수 없습니다. 평문 파일로 대체하지 않습니다."
            ) from None
        self._set_storage_state(True)
        if value is None:
            raise DomainValidationError("공급자 인증정보가 설정되지 않았습니다.")
        return value
