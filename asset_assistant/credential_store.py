"""공급자 인증정보를 운영체제 보안 저장소에만 보관한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .domain import DomainValidationError, nonempty


SERVICE_NAME = "org.long-term-asset-assistant.market-data"
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "bls": frozenset({"registration_key"}),
    "sec": frozenset({"user_agent"}),
    "opendart": frozenset({"api_key"}),
    "ecos": frozenset({"api_key"}),
    "kis_live": frozenset({"app_key", "app_secret"}),
    "kis_paper": frozenset({"app_key", "app_secret"}),
}
_SECURE_BACKEND_TYPES = frozenset(
    {
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.macOS", "Keyring"),
    }
)
_SEC_USER_AGENT = re.compile(
    r"(?=.{8,256}\Z)[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?\s+"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z"
)


class CredentialStoreError(DomainValidationError):
    """보안 저장소가 없거나 인증정보 작업이 실패한 경우."""


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class CredentialStatus:
    available: bool
    backend: str
    message: str


def _item_name(provider: str, field: str) -> str:
    if provider not in ALLOWED_FIELDS or field not in ALLOWED_FIELDS[provider]:
        raise CredentialStoreError("지원하지 않는 공급자 인증정보 항목입니다.")
    return f"{provider}:{field}"


def _backend_is_allowed(backend: object) -> bool:
    backend_type = type(backend)
    return (backend_type.__module__, backend_type.__name__) in _SECURE_BACKEND_TYPES


def valid_sec_user_agent(value: object) -> bool:
    """SEC 헤더로 전송 가능한 조직명과 연락 이메일만 허용한다."""

    return isinstance(value, str) and _SEC_USER_AGENT.fullmatch(value) is not None


def _load_secure_backend() -> KeyringBackend:
    try:
        import keyring
        from keyring.backend import get_all_keyring
    except (ImportError, RuntimeError):
        raise CredentialStoreError(
            "운영체제 보안 저장소 구성요소를 사용할 수 없습니다. 평문 저장은 하지 않습니다."
        ) from None

    current = keyring.get_keyring()
    candidates = (current, *get_all_keyring())
    for candidate in candidates:
        if not _backend_is_allowed(candidate):
            continue
        try:
            priority = float(candidate.priority)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            continue
        if priority <= 0:
            continue
        keyring.set_keyring(candidate)
        return candidate
    raise CredentialStoreError(
        "지원되는 운영체제 보안 저장소를 사용할 수 없습니다. 평문 저장은 하지 않습니다."
    )


class CredentialStore:
    """Secret Service/Windows Credential Manager/macOS Keychain만 허용한다."""

    def __init__(
        self,
        backend: KeyringBackend | None = None,
        *,
        trusted_test_backend: bool = False,
    ) -> None:
        if backend is not None and not trusted_test_backend and not _backend_is_allowed(backend):
            raise CredentialStoreError("허용되지 않은 인증정보 저장소입니다.")
        self._backend = backend
        self._trusted_test_backend = trusted_test_backend

    def _get_backend(self) -> KeyringBackend:
        if self._backend is None:
            self._backend = _load_secure_backend()
        return self._backend

    def status(self) -> CredentialStatus:
        try:
            backend = self._get_backend()
        except CredentialStoreError as exc:
            return CredentialStatus(False, "UNAVAILABLE", str(exc))
        backend_name = type(backend).__name__
        return CredentialStatus(True, backend_name, "운영체제 보안 저장소를 사용할 수 있습니다.")

    def set_secret(self, provider: str, field: str, secret: str) -> None:
        item = _item_name(provider, field)
        value = nonempty(secret, field_name="secret")
        if len(value) > 8192:
            raise CredentialStoreError("인증정보 길이가 허용 범위를 벗어났습니다.")
        if provider == "sec" and field == "user_agent" and not valid_sec_user_agent(value):
            raise CredentialStoreError(
                "SEC User-Agent는 조직명과 연락 가능한 이메일 형식이어야 합니다."
            )
        try:
            self._get_backend().set_password(SERVICE_NAME, item, value)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError(
                "인증정보를 운영체제 보안 저장소에 등록하지 못했습니다."
            ) from None

    def get_secret(self, provider: str, field: str) -> str | None:
        item = _item_name(provider, field)
        try:
            value = self._get_backend().get_password(SERVICE_NAME, item)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError(
                "인증정보를 운영체제 보안 저장소에서 읽지 못했습니다."
            ) from None
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise CredentialStoreError("저장된 인증정보 상태를 확인할 수 없습니다.")
        return value

    def has_secret(self, provider: str, field: str) -> bool:
        return self.get_secret(provider, field) is not None

    def delete_secret(self, provider: str, field: str) -> None:
        item = _item_name(provider, field)
        try:
            backend = self._get_backend()
            if backend.get_password(SERVICE_NAME, item) is None:
                return
            backend.delete_password(SERVICE_NAME, item)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError(
                "인증정보를 운영체제 보안 저장소에서 삭제하지 못했습니다."
            ) from None

    def configured_fields(self, provider: str) -> frozenset[str]:
        if provider not in ALLOWED_FIELDS:
            raise CredentialStoreError("지원하지 않는 공급자입니다.")
        configured: set[str] = set()
        for field in ALLOWED_FIELDS[provider]:
            if self.has_secret(provider, field):
                configured.add(field)
        return frozenset(configured)
