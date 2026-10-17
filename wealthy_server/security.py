"""Password, session-token, and encrypted credential primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Mapping

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from asset_assistant.domain import DomainValidationError


def _nonempty_text(value: str, *, label: str, limit: int = 100) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DomainValidationError(f"{label} 형식을 확인할 수 없습니다.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DomainValidationError(f"{label} 형식을 확인할 수 없습니다.")
    return value.strip()


class PasswordManager:
    """Argon2id password hashing with a deliberately generic verify result."""

    def __init__(self) -> None:
        self._hasher = Argon2PasswordHasher(type=Type.ID)

    def hash(self, password: str) -> str:
        if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
            raise DomainValidationError("비밀번호는 12~1024자여야 합니다.")
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except (InvalidHashError, TypeError):
            return True


def hash_token(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise DomainValidationError("인증 토큰 형식을 확인할 수 없습니다.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, expected_hash: str) -> bool:
    try:
        candidate = hash_token(token)
    except DomainValidationError:
        return False
    return hmac.compare_digest(candidate, expected_hash)


@dataclass(frozen=True)
class IssuedSessionTokens:
    session_token: str
    csrf_token: str
    session_token_hash: str
    csrf_token_hash: str


def issue_session_tokens() -> IssuedSessionTokens:
    session_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    return IssuedSessionTokens(
        session_token=session_token,
        csrf_token=csrf_token,
        session_token_hash=hash_token(session_token),
        csrf_token_hash=hash_token(csrf_token),
    )


@dataclass(frozen=True, repr=False)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: str

    def __repr__(self) -> str:
        return f"EncryptedSecret(key_version={self.key_version!r}, ciphertext=<redacted>)"


class SecretVault:
    """AES-256-GCM vault whose key material is supplied outside the database.

    ``decrypt_for_provider_adapter`` is intentionally the only plaintext read
    path.  API response objects should only use credential status metadata.
    """

    def __init__(self, keys: Mapping[str, bytes], *, active_key_version: str) -> None:
        active = _nonempty_text(active_key_version, label="암호화 키 버전", limit=40)
        copied: dict[str, bytes] = {}
        for version, key in keys.items():
            normalized = _nonempty_text(version, label="암호화 키 버전", limit=40)
            if not isinstance(key, bytes) or len(key) != 32:
                raise DomainValidationError("암호화 마스터 키는 32바이트여야 합니다.")
            copied[normalized] = bytes(key)
        if active not in copied:
            raise DomainValidationError("활성 암호화 키를 찾을 수 없습니다.")
        self._keys = copied
        self._active_key_version = active

    def __repr__(self) -> str:
        return f"SecretVault(active_key_version={self._active_key_version!r}, keys=<redacted>)"

    @staticmethod
    def generate_key() -> bytes:
        return AESGCM.generate_key(bit_length=256)

    @staticmethod
    def encode_key_for_file(key: bytes) -> str:
        if not isinstance(key, bytes) or len(key) != 32:
            raise DomainValidationError("암호화 마스터 키는 32바이트여야 합니다.")
        return base64.urlsafe_b64encode(key).decode("ascii")

    @staticmethod
    def decode_key_from_file(encoded: str) -> bytes:
        try:
            key = base64.b64decode(encoded.strip().encode("ascii"), altchars=b"-_", validate=True)
        except (ValueError, UnicodeError):
            raise DomainValidationError("암호화 마스터 키 파일을 확인할 수 없습니다.") from None
        if len(key) != 32:
            raise DomainValidationError("암호화 마스터 키 파일을 확인할 수 없습니다.")
        return key

    @staticmethod
    def _aad(owner_id: str, provider: str, environment: str, secret_name: str) -> bytes:
        parts = (
            _nonempty_text(owner_id, label="사용자 식별자", limit=100),
            _nonempty_text(provider, label="공급자", limit=40).lower(),
            _nonempty_text(environment, label="공급자 환경", limit=20).lower(),
            _nonempty_text(secret_name, label="인증정보 이름", limit=60),
        )
        return "\x1f".join(parts).encode("utf-8")

    def encrypt(
        self,
        plaintext: str,
        *,
        owner_id: str,
        provider: str,
        environment: str,
        secret_name: str,
    ) -> EncryptedSecret:
        if not isinstance(plaintext, str) or not plaintext or len(plaintext) > 16384:
            raise DomainValidationError("인증정보 값을 확인할 수 없습니다.")
        nonce = secrets.token_bytes(12)
        aad = self._aad(owner_id, provider, environment, secret_name)
        ciphertext = AESGCM(self._keys[self._active_key_version]).encrypt(
            nonce, plaintext.encode("utf-8"), aad
        )
        return EncryptedSecret(ciphertext, nonce, self._active_key_version)

    def decrypt_for_provider_adapter(
        self,
        encrypted: EncryptedSecret,
        *,
        owner_id: str,
        provider: str,
        environment: str,
        secret_name: str,
    ) -> str:
        key = self._keys.get(encrypted.key_version)
        if key is None:
            raise DomainValidationError("인증정보를 복호화할 수 없습니다.")
        aad = self._aad(owner_id, provider, environment, secret_name)
        try:
            plaintext = AESGCM(key).decrypt(encrypted.nonce, encrypted.ciphertext, aad)
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError):
            raise DomainValidationError("인증정보를 복호화할 수 없습니다.") from None

    def rotate(
        self,
        encrypted: EncryptedSecret,
        *,
        owner_id: str,
        provider: str,
        environment: str,
        secret_name: str,
    ) -> EncryptedSecret:
        plaintext = self.decrypt_for_provider_adapter(
            encrypted,
            owner_id=owner_id,
            provider=provider,
            environment=environment,
            secret_name=secret_name,
        )
        return self.encrypt(
            plaintext,
            owner_id=owner_id,
            provider=provider,
            environment=environment,
            secret_name=secret_name,
        )

