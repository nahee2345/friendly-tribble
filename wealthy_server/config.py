"""Configuration boundaries for the private Wealthy web service.

Production values are read from root-owned/container secret files.  A
PostgreSQL password or encryption key is never accepted on a command line.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from asset_assistant.domain import DomainValidationError


_MAX_SECRET_FILE_BYTES = 16_384


def _read_secret_file(environment_name: str) -> bytes:
    raw_path = os.environ.get(environment_name)
    if not raw_path:
        raise DomainValidationError("필수 서버 보안 파일 설정이 없습니다.")
    try:
        path = Path(raw_path)
        payload = path.read_bytes()
    except (OSError, ValueError):
        raise DomainValidationError("서버 보안 파일을 안전하게 읽을 수 없습니다.") from None
    if not payload or len(payload) > _MAX_SECRET_FILE_BYTES or b"\x00" in payload:
        raise DomainValidationError("서버 보안 파일 형식을 확인할 수 없습니다.")
    return payload.strip()


def load_database_url() -> str:
    """Read the DB URL from a mounted secret file.

    The only environment-value exception is an explicit SQLite test/developer
    URL.  Network database passwords must therefore not be placed in ``.env``.
    """

    if os.environ.get("WEALTHY_DATABASE_URL_FILE"):
        try:
            value = _read_secret_file("WEALTHY_DATABASE_URL_FILE").decode("utf-8")
        except UnicodeDecodeError:
            raise DomainValidationError("데이터베이스 보안 설정 형식을 확인할 수 없습니다.") from None
    else:
        value = os.environ.get("WEALTHY_DATABASE_URL", "")
        if value and not value.startswith("sqlite"):
            raise DomainValidationError("네트워크 데이터베이스 주소는 보안 파일로 제공해야 합니다.")
    if not value:
        raise DomainValidationError("데이터베이스 연결 설정이 없습니다.")
    if len(value) > 4096 or "\x00" in value:
        raise DomainValidationError("데이터베이스 연결 설정 형식을 확인할 수 없습니다.")
    return value


def load_master_key() -> bytes:
    """Load one 256-bit AES key from a file outside the database."""

    encoded = _read_secret_file("WEALTHY_MASTER_KEY_FILE")
    try:
        key = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
    except (ValueError, TypeError):
        raise DomainValidationError("서버 암호화 키 형식을 확인할 수 없습니다.") from None
    if len(key) != 32:
        raise DomainValidationError("서버 암호화 키 형식을 확인할 수 없습니다.")
    return key


@dataclass(frozen=True)
class ServerSettings:
    database_url: str
    master_key: bytes
    secure_cookies: bool = True
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    session_hours: int = 12
    upload_limit_bytes: int = 1_048_576
    public_origin: str | None = None
    vapid_public_key: str | None = None
    vapid_private_key: bytes | None = None

    @classmethod
    def from_environment(cls) -> "ServerSettings":
        hosts = tuple(
            value.strip()
            for value in os.environ.get(
                "WEALTHY_ALLOWED_HOSTS", "localhost,127.0.0.1"
            ).split(",")
            if value.strip()
        )
        if not hosts or len(hosts) > 20:
            raise DomainValidationError("허용 호스트 설정을 확인할 수 없습니다.")
        secure = os.environ.get("WEALTHY_SECURE_COOKIES", "1") != "0"
        public_origin = os.environ.get("WEALTHY_PUBLIC_ORIGIN") or None
        if public_origin is not None:
            public_origin = public_origin.rstrip("/")
            if (
                len(public_origin) > 512
                or not public_origin.startswith("https://")
                or any(character.isspace() for character in public_origin)
            ):
                raise DomainValidationError("공개 서비스 origin 설정을 확인할 수 없습니다.")
        vapid_public: str | None = None
        vapid_private: bytes | None = None
        public_file = os.environ.get("WEALTHY_VAPID_PUBLIC_KEY_FILE")
        private_file = os.environ.get("WEALTHY_VAPID_PRIVATE_KEY_FILE")
        if bool(public_file) != bool(private_file):
            raise DomainValidationError("Web Push 키 파일은 공개키와 비밀키를 함께 설정해야 합니다.")
        if public_file and private_file:
            try:
                vapid_public = _read_secret_file("WEALTHY_VAPID_PUBLIC_KEY_FILE").decode(
                    "ascii"
                )
            except UnicodeDecodeError:
                raise DomainValidationError("Web Push 공개키 형식을 확인할 수 없습니다.") from None
            vapid_private = _read_secret_file("WEALTHY_VAPID_PRIVATE_KEY_FILE")
            if (
                len(vapid_public) < 32
                or len(vapid_public) > 512
                or not all(character.isalnum() or character in "-_=" for character in vapid_public)
                or len(vapid_private) < 32
            ):
                raise DomainValidationError("Web Push 키 형식을 확인할 수 없습니다.")
        return cls(
            database_url=load_database_url(),
            master_key=load_master_key(),
            secure_cookies=secure,
            allowed_hosts=hosts,
            public_origin=public_origin,
            vapid_public_key=vapid_public,
            vapid_private_key=vapid_private,
        )
