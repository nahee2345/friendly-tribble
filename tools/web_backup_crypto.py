"""Small authenticated streaming format for encrypted PostgreSQL dumps."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"WEALTHY-PGDUMP-AES256-GCM-V1\n"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


class BackupCryptoError(ValueError):
    """Raised without embedding paths, keys, or decrypted contents."""


def load_key(path: Path) -> bytes:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BackupCryptoError("백업 키 파일 형식이 안전하지 않습니다.")
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            raise BackupCryptoError("백업 키 파일은 소유자 전용 권한이어야 합니다.")
        encoded = path.read_bytes().strip()
        key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except BackupCryptoError:
        raise
    except (OSError, ValueError):
        raise BackupCryptoError("백업 키를 안전하게 읽을 수 없습니다.") from None
    if len(key) != 32:
        raise BackupCryptoError("백업 키는 별도 생성한 256비트 키여야 합니다.")
    return key


def encrypt_stream_to_temporary(
    source: BinaryIO, destination: Path, key: bytes
) -> tuple[Path, str, int]:
    """Encrypt *source* to a sibling temporary file; caller commits atomically."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(NONCE_SIZE)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    digest = hashlib.sha256()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{destination.name}.",
            suffix=".partial",
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            prefix = MAGIC + nonce
            output.write(prefix)
            digest.update(prefix)
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                encrypted = encryptor.update(chunk)
                output.write(encrypted)
                digest.update(encrypted)
            final = encryptor.finalize()
            if final:
                output.write(final)
                digest.update(final)
            output.write(encryptor.tag)
            digest.update(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
            size = output.tell()
        return temporary, digest.hexdigest(), size
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        raise


def _parts(path: Path) -> tuple[bytes, bytes, int, int]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BackupCryptoError("암호화 백업 파일 형식이 안전하지 않습니다.")
        minimum = len(MAGIC) + NONCE_SIZE + TAG_SIZE
        if info.st_size <= minimum:
            raise BackupCryptoError("암호화 백업 파일이 완전하지 않습니다.")
        with path.open("rb") as source:
            if source.read(len(MAGIC)) != MAGIC:
                raise BackupCryptoError("지원하지 않는 암호화 백업 형식입니다.")
            nonce = source.read(NONCE_SIZE)
            source.seek(-TAG_SIZE, os.SEEK_END)
            tag = source.read(TAG_SIZE)
        ciphertext_start = len(MAGIC) + NONCE_SIZE
        ciphertext_size = info.st_size - ciphertext_start - TAG_SIZE
        return nonce, tag, ciphertext_start, ciphertext_size
    except BackupCryptoError:
        raise
    except OSError:
        raise BackupCryptoError("암호화 백업 파일을 안전하게 읽을 수 없습니다.") from None


def verify_encrypted_file(path: Path, key: bytes) -> str:
    """Authenticate the complete file before a restore creates a database."""

    nonce, tag, start, remaining = _parts(path)
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(MAGIC)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                digest.update(chunk)
            source.seek(start)
            while remaining:
                chunk = source.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise BackupCryptoError("암호화 백업 파일이 완전하지 않습니다.")
                remaining -= len(chunk)
                decryptor.update(chunk)
            decryptor.finalize()
    except InvalidTag:
        raise BackupCryptoError("백업 인증에 실패했습니다. 복원을 시작하지 않았습니다.") from None
    except BackupCryptoError:
        raise
    except OSError:
        raise BackupCryptoError("암호화 백업 파일을 안전하게 검사할 수 없습니다.") from None
    return digest.hexdigest()


def decrypt_verified_file_to_sink(path: Path, key: bytes, sink: BinaryIO) -> None:
    """Decrypt an already authenticated file directly to a process pipe."""

    nonce, tag, start, remaining = _parts(path)
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(MAGIC)
    try:
        with path.open("rb") as source:
            source.seek(start)
            while remaining:
                chunk = source.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise BackupCryptoError("암호화 백업 파일이 완전하지 않습니다.")
                remaining -= len(chunk)
                plain = decryptor.update(chunk)
                if plain:
                    sink.write(plain)
            final = decryptor.finalize()
            if final:
                sink.write(final)
            sink.flush()
    except InvalidTag:
        raise BackupCryptoError("복원 중 백업 인증 상태가 변경되었습니다.") from None
    except (BrokenPipeError, OSError):
        raise BackupCryptoError("복원 프로세스에 백업을 안전하게 전달할 수 없습니다.") from None
