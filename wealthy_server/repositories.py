"""Owner-scoped repositories for the Wealthy server.

Every financial-data lookup takes an explicit ``owner_id``.  The only lookup
that derives ownership is session authentication with an unguessable hashed
bearer token.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from asset_assistant.domain import DomainValidationError

from .models import (
    CollectionRun,
    JobAttempt,
    JobRun,
    LoginAttempt,
    MarketRecord,
    ProviderSecret,
    PushSubscription,
    ReportSnapshot,
    ScenarioSnapshot,
    StrategyTargetVersion,
    User,
    WebSession,
    utc_now,
)
from .security import EncryptedSecret, SecretVault
from .strategy_targets import TargetStrategyMix


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SQLITE_LOGIN_CLAIM_LOCK = threading.RLock()


def _text(value: str, *, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DomainValidationError(f"{label} 형식을 확인할 수 없습니다.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DomainValidationError(f"{label} 형식을 확인할 수 없습니다.")
    return value.strip()


def _owner(owner_id: str) -> str:
    return _text(owner_id, label="사용자 식별자", limit=100)


def _document(value: str, *, label: str, limit: int) -> str:
    """Validate a multiline document without altering its stored bytes."""

    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DomainValidationError(f"{label} 형식을 확인할 수 없습니다.")
    if any(
        (ord(character) < 32 and character not in {"\n", "\r", "\t"})
        or ord(character) == 127
        for character in value
    ):
        raise DomainValidationError(f"{label} 형식을 확인할 수 없습니다.")
    return value


def _aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{label}에는 시간대가 필요합니다.")
    return value.astimezone(UTC)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, Mapping):
        raise DomainValidationError("저장 자료 형식을 확인할 수 없습니다.")
    try:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        normalized = json.loads(rendered)
    except (TypeError, ValueError, RecursionError):
        raise DomainValidationError("저장 자료 형식을 확인할 수 없습니다.") from None
    return normalized, _sha256_text(rendered)


class _Repository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory


class AuthRepository(_Repository):
    def create_user(
        self, username: str, password_hash: str, *, is_admin: bool = True
    ) -> User:
        normalized = _text(username, label="사용자 이름", limit=100).casefold()
        hashed = _text(password_hash, label="비밀번호 해시", limit=1024)
        user = User(username=normalized, password_hash=hashed, is_admin=bool(is_admin))
        try:
            with self._sessions.begin() as session:
                session.add(user)
                session.flush()
        except IntegrityError:
            raise DomainValidationError("사용자 계정을 생성할 수 없습니다.") from None
        return user

    def get_user_by_id(self, owner_id: str) -> User | None:
        with self._sessions() as session:
            return session.scalar(select(User).where(User.id == _owner(owner_id)))

    def get_user_by_username(self, username: str) -> User | None:
        normalized = _text(username, label="사용자 이름", limit=100).casefold()
        with self._sessions() as session:
            return session.scalar(select(User).where(User.username == normalized))

    def change_password(self, owner_id: str, password_hash: str, *, changed_at: datetime) -> bool:
        with self._sessions.begin() as session:
            user = session.scalar(select(User).where(User.id == _owner(owner_id)))
            if user is None:
                return False
            user.password_hash = _text(password_hash, label="비밀번호 해시", limit=1024)
            user.password_changed_at = _aware(changed_at, label="비밀번호 변경 시각")
            return True

    def record_attempt(
        self,
        *,
        username_fingerprint: str,
        client_fingerprint: str,
        succeeded: bool,
        attempted_at: datetime,
        owner_id: str | None = None,
    ) -> LoginAttempt:
        if _SHA256.fullmatch(username_fingerprint) is None or _SHA256.fullmatch(client_fingerprint) is None:
            raise DomainValidationError("로그인 시도 식별자 형식을 확인할 수 없습니다.")
        attempt = LoginAttempt(
            owner_id=_owner(owner_id) if owner_id is not None else None,
            username_fingerprint=username_fingerprint,
            client_fingerprint=client_fingerprint,
            succeeded=bool(succeeded),
            attempted_at=_aware(attempted_at, label="로그인 시도 시각"),
        )
        with self._sessions.begin() as session:
            session.add(attempt)
            session.flush()
        return attempt

    def claim_login_attempt(
        self,
        *,
        username_fingerprint: str,
        client_fingerprint: str,
        attempted_at: datetime,
        since: datetime,
        prune_before: datetime,
        max_pair_failures: int,
        max_client_failures: int,
    ) -> int | None:
        """Atomically reserve one login attempt within both failure budgets.

        The reservation starts as a failure and is changed to success only
        after password verification.  PostgreSQL serializes reservations for
        the same client with a transaction advisory lock.  SQLite is used only
        by the single-process test/developer path and uses a process lock.
        """

        if (
            _SHA256.fullmatch(username_fingerprint) is None
            or _SHA256.fullmatch(client_fingerprint) is None
            or isinstance(max_pair_failures, bool)
            or isinstance(max_client_failures, bool)
            or max_pair_failures < 1
            or max_client_failures < max_pair_failures
        ):
            raise DomainValidationError("로그인 제한 설정을 확인할 수 없습니다.")
        attempted = _aware(attempted_at, label="로그인 시도 시각")
        window_start = _aware(since, label="로그인 제한 기준 시각")
        retention_start = _aware(prune_before, label="로그인 기록 보존 기준 시각")
        if retention_start > window_start or window_start > attempted:
            raise DomainValidationError("로그인 제한 시각을 확인할 수 없습니다.")

        def reserve() -> int | None:
            with self._sessions.begin() as session:
                if session.get_bind().dialect.name == "postgresql":
                    lock_key = int(client_fingerprint[:16], 16)
                    if lock_key >= 2**63:
                        lock_key -= 2**64
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": lock_key},
                    )
                session.execute(
                    delete(LoginAttempt).where(
                        LoginAttempt.attempted_at < retention_start
                    )
                )
                pair_failures = int(
                    session.scalar(
                        select(func.count(LoginAttempt.id)).where(
                            LoginAttempt.username_fingerprint == username_fingerprint,
                            LoginAttempt.client_fingerprint == client_fingerprint,
                            LoginAttempt.succeeded.is_(False),
                            LoginAttempt.attempted_at >= window_start,
                        )
                    )
                    or 0
                )
                client_failures = int(
                    session.scalar(
                        select(func.count(LoginAttempt.id)).where(
                            LoginAttempt.client_fingerprint == client_fingerprint,
                            LoginAttempt.succeeded.is_(False),
                            LoginAttempt.attempted_at >= window_start,
                        )
                    )
                    or 0
                )
                if (
                    pair_failures >= max_pair_failures
                    or client_failures >= max_client_failures
                ):
                    return None
                attempt = LoginAttempt(
                    owner_id=None,
                    username_fingerprint=username_fingerprint,
                    client_fingerprint=client_fingerprint,
                    succeeded=False,
                    attempted_at=attempted,
                )
                session.add(attempt)
                session.flush()
                return int(attempt.id)

        if self._sessions.kw.get("bind") is not None and self._sessions.kw[
            "bind"
        ].dialect.name == "sqlite":
            with _SQLITE_LOGIN_CLAIM_LOCK:
                return reserve()
        return reserve()

    def mark_login_attempt_succeeded(
        self, attempt_id: int, *, owner_id: str
    ) -> None:
        if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id < 1:
            raise DomainValidationError("로그인 시도 식별자를 확인할 수 없습니다.")
        with self._sessions.begin() as session:
            attempt = session.get(LoginAttempt, attempt_id)
            if attempt is None or attempt.succeeded:
                raise DomainValidationError("로그인 시도 상태를 확인할 수 없습니다.")
            attempt.succeeded = True
            attempt.owner_id = _owner(owner_id)

    def failed_attempt_count(
        self, *, username_fingerprint: str, client_fingerprint: str, since: datetime
    ) -> int:
        if _SHA256.fullmatch(username_fingerprint) is None or _SHA256.fullmatch(client_fingerprint) is None:
            raise DomainValidationError("로그인 시도 식별자 형식을 확인할 수 없습니다.")
        with self._sessions() as session:
            return int(
                session.scalar(
                    select(func.count(LoginAttempt.id)).where(
                        LoginAttempt.username_fingerprint == username_fingerprint,
                        LoginAttempt.client_fingerprint == client_fingerprint,
                        LoginAttempt.succeeded.is_(False),
                        LoginAttempt.attempted_at >= _aware(since, label="로그인 제한 기준 시각"),
                    )
                )
                or 0
            )


class SessionRepository(_Repository):
    def create(
        self,
        owner_id: str,
        *,
        token_hash: str,
        csrf_hash: str,
        expires_at: datetime,
        created_at: datetime | None = None,
    ) -> WebSession:
        if _SHA256.fullmatch(token_hash) is None or _SHA256.fullmatch(csrf_hash) is None:
            raise DomainValidationError("세션 토큰 해시 형식을 확인할 수 없습니다.")
        created = _aware(created_at or utc_now(), label="세션 생성 시각")
        expires = _aware(expires_at, label="세션 만료 시각")
        if expires <= created:
            raise DomainValidationError("세션 만료 시각을 확인할 수 없습니다.")
        web_session = WebSession(
            owner_id=_owner(owner_id),
            token_hash=token_hash,
            csrf_hash=csrf_hash,
            created_at=created,
            last_seen_at=created,
            expires_at=expires,
        )
        try:
            with self._sessions.begin() as session:
                session.add(web_session)
                session.flush()
        except IntegrityError:
            raise DomainValidationError("세션을 생성할 수 없습니다.") from None
        return web_session

    def get(self, owner_id: str, token_hash: str, *, now: datetime) -> WebSession | None:
        with self._sessions() as session:
            return session.scalar(
                select(WebSession).where(
                    WebSession.owner_id == _owner(owner_id),
                    WebSession.token_hash == token_hash,
                    WebSession.revoked_at.is_(None),
                    WebSession.expires_at > _aware(now, label="세션 조회 시각"),
                )
            )

    def authenticate(self, token_hash: str, *, now: datetime) -> WebSession | None:
        """Derive the owner only after possession of a random session token."""

        if _SHA256.fullmatch(token_hash) is None:
            return None
        with self._sessions() as session:
            return session.scalar(
                select(WebSession).where(
                    WebSession.token_hash == token_hash,
                    WebSession.revoked_at.is_(None),
                    WebSession.expires_at > _aware(now, label="세션 조회 시각"),
                )
            )

    def revoke(self, owner_id: str, token_hash: str, *, revoked_at: datetime) -> bool:
        with self._sessions.begin() as session:
            item = session.scalar(
                select(WebSession).where(
                    WebSession.owner_id == _owner(owner_id), WebSession.token_hash == token_hash
                )
            )
            if item is None or item.revoked_at is not None:
                return False
            item.revoked_at = _aware(revoked_at, label="세션 해제 시각")
            return True

    def revoke_all(self, owner_id: str, *, revoked_at: datetime) -> int:
        when = _aware(revoked_at, label="세션 해제 시각")
        with self._sessions.begin() as session:
            rows = tuple(
                session.scalars(
                    select(WebSession).where(
                        WebSession.owner_id == _owner(owner_id), WebSession.revoked_at.is_(None)
                    )
                )
            )
            for item in rows:
                item.revoked_at = when
            return len(rows)


class ScenarioRepository(_Repository):
    def save(
        self,
        owner_id: str,
        *,
        realm: str,
        payload: Mapping[str, Any],
        effective_at: datetime,
        created_at: datetime | None = None,
    ) -> ScenarioSnapshot:
        normalized, digest = _canonical_payload(payload)
        item = ScenarioSnapshot(
            owner_id=_owner(owner_id),
            realm=_text(realm, label="자료 영역", limit=40),
            payload=normalized,
            content_sha256=digest,
            effective_at=_aware(effective_at, label="자료 기준 시각"),
            created_at=_aware(created_at or utc_now(), label="자료 저장 시각"),
        )
        with self._sessions.begin() as session:
            session.add(item)
            session.flush()
        return item

    def list(self, owner_id: str, *, limit: int = 50) -> tuple[ScenarioSnapshot, ...]:
        checked = _limit(limit)
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(ScenarioSnapshot)
                    .where(ScenarioSnapshot.owner_id == _owner(owner_id))
                    .order_by(ScenarioSnapshot.created_at.desc(), ScenarioSnapshot.id.desc())
                    .limit(checked)
                )
            )

    def latest(self, owner_id: str) -> ScenarioSnapshot | None:
        rows = self.list(owner_id, limit=1)
        return rows[0] if rows else None


class ReportRepository(_Repository):
    def save(
        self,
        owner_id: str,
        *,
        report_markdown: str,
        status: str,
        data_cutoff_at: datetime,
        generated_at: datetime | None = None,
        scenario_snapshot_id: int | None = None,
        data_scope: str | None = None,
        generation_kind: str | None = None,
    ) -> ReportSnapshot:
        # The legacy/cloud model has no display-metadata columns.  The current
        # localhost adapter persists these values beside its immutable market
        # report row; keeping the arguments here preserves the shared service
        # contract without inventing historical metadata.
        del data_scope, generation_kind
        markdown = _document(report_markdown, label="보고서", limit=2_000_000)
        owner = _owner(owner_id)
        with self._sessions.begin() as session:
            if scenario_snapshot_id is not None:
                scenario = session.scalar(
                    select(ScenarioSnapshot).where(
                        ScenarioSnapshot.id == scenario_snapshot_id,
                        ScenarioSnapshot.owner_id == owner,
                    )
                )
                if scenario is None:
                    raise DomainValidationError("보고서 원본 자료를 확인할 수 없습니다.")
            item = ReportSnapshot(
                owner_id=owner,
                scenario_snapshot_id=scenario_snapshot_id,
                report_markdown=markdown,
                content_sha256=_sha256_text(markdown),
                status=_text(status, label="보고서 상태", limit=32),
                data_cutoff_at=_aware(data_cutoff_at, label="보고서 자료 기준 시각"),
                generated_at=_aware(generated_at or utc_now(), label="보고서 생성 시각"),
            )
            session.add(item)
            session.flush()
        return item

    def list(self, owner_id: str, *, limit: int = 50) -> tuple[ReportSnapshot, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(ReportSnapshot)
                    .where(ReportSnapshot.owner_id == _owner(owner_id))
                    .order_by(ReportSnapshot.generated_at.desc(), ReportSnapshot.id.desc())
                    .limit(_limit(limit))
                )
            )

    def get(self, owner_id: str, report_id: int) -> ReportSnapshot | None:
        with self._sessions() as session:
            return session.scalar(
                select(ReportSnapshot).where(
                    ReportSnapshot.owner_id == _owner(owner_id), ReportSnapshot.id == report_id
                )
            )


class StrategyTargetRepository(_Repository):
    def save(
        self,
        owner_id: str,
        *,
        version_name: str,
        mix: TargetStrategyMix,
        created_at: datetime | None = None,
    ) -> StrategyTargetVersion:
        if not isinstance(mix, TargetStrategyMix):
            raise DomainValidationError("전략 목표 설정을 확인할 수 없습니다.")
        weights = mix.as_strings()
        canonical = json.dumps(weights, sort_keys=True, separators=(",", ":"))
        item = StrategyTargetVersion(
            owner_id=_owner(owner_id),
            version_name=_text(version_name, label="전략 목표 버전", limit=100),
            weights=weights,
            content_sha256=_sha256_text(canonical),
            created_at=_aware(created_at or utc_now(), label="전략 목표 저장 시각"),
        )
        try:
            with self._sessions.begin() as session:
                session.add(item)
                session.flush()
        except IntegrityError:
            raise DomainValidationError("같은 이름의 전략 목표 버전이 이미 있습니다.") from None
        return item

    def current(self, owner_id: str) -> StrategyTargetVersion | None:
        with self._sessions() as session:
            return session.scalar(
                select(StrategyTargetVersion)
                .where(StrategyTargetVersion.owner_id == _owner(owner_id))
                .order_by(StrategyTargetVersion.created_at.desc(), StrategyTargetVersion.id.desc())
                .limit(1)
            )

    def list(
        self, owner_id: str, *, limit: int = 100
    ) -> tuple[StrategyTargetVersion, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(StrategyTargetVersion)
                    .where(StrategyTargetVersion.owner_id == _owner(owner_id))
                    .order_by(
                        StrategyTargetVersion.created_at.desc(),
                        StrategyTargetVersion.id.desc(),
                    )
                    .limit(_limit(limit))
                )
            )

    def get_by_name(self, owner_id: str, version_name: str) -> StrategyTargetVersion | None:
        with self._sessions() as session:
            return session.scalar(
                select(StrategyTargetVersion).where(
                    StrategyTargetVersion.owner_id == _owner(owner_id),
                    StrategyTargetVersion.version_name
                    == _text(version_name, label="전략 목표 버전", limit=100),
                )
            )


@dataclass(frozen=True)
class CredentialStatus:
    provider: str
    environment: str
    secret_name: str
    configured: bool
    updated_at: datetime | None
    key_version: str | None


class CredentialRepository(_Repository):
    def __init__(self, session_factory: sessionmaker[Session], vault: SecretVault) -> None:
        super().__init__(session_factory)
        self._vault = vault

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
        owner, provider_name, environment_name, field_name = self._identity(
            owner_id, provider, environment, secret_name
        )
        encrypted = self._vault.encrypt(
            plaintext,
            owner_id=owner,
            provider=provider_name,
            environment=environment_name,
            secret_name=field_name,
        )
        timestamp = _aware(now or utc_now(), label="인증정보 저장 시각")
        with self._sessions.begin() as session:
            item = session.scalar(
                self._query(owner, provider_name, environment_name, field_name)
            )
            if item is None:
                item = ProviderSecret(
                    owner_id=owner,
                    provider=provider_name,
                    environment=environment_name,
                    secret_name=field_name,
                    ciphertext=encrypted.ciphertext,
                    nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(item)
            else:
                item.ciphertext = encrypted.ciphertext
                item.nonce = encrypted.nonce
                item.key_version = encrypted.key_version
                item.updated_at = timestamp
            session.flush()
        return CredentialStatus(provider_name, environment_name, field_name, True, timestamp, encrypted.key_version)

    def status(
        self, owner_id: str, *, provider: str, environment: str, secret_name: str
    ) -> CredentialStatus:
        identity = self._identity(owner_id, provider, environment, secret_name)
        with self._sessions() as session:
            item = session.scalar(self._query(*identity))
            if item is None:
                return CredentialStatus(identity[1], identity[2], identity[3], False, None, None)
            return CredentialStatus(
                item.provider, item.environment, item.secret_name, True, item.updated_at, item.key_version
            )

    def delete(
        self, owner_id: str, *, provider: str, environment: str, secret_name: str
    ) -> bool:
        identity = self._identity(owner_id, provider, environment, secret_name)
        with self._sessions.begin() as session:
            item = session.scalar(self._query(*identity))
            if item is None:
                return False
            session.delete(item)
            return True

    def rotate(
        self,
        owner_id: str,
        *,
        provider: str,
        environment: str,
        secret_name: str,
        now: datetime | None = None,
    ) -> CredentialStatus:
        """Re-encrypt one stored secret with the vault's active key version."""

        identity = self._identity(owner_id, provider, environment, secret_name)
        timestamp = _aware(now or utc_now(), label="인증정보 키 교체 시각")
        with self._sessions.begin() as session:
            item = session.scalar(self._query(*identity).with_for_update())
            if item is None:
                raise DomainValidationError("공급자 인증정보가 설정되지 않았습니다.")
            rotated = self._vault.rotate(
                EncryptedSecret(item.ciphertext, item.nonce, item.key_version),
                owner_id=identity[0],
                provider=identity[1],
                environment=identity[2],
                secret_name=identity[3],
            )
            item.ciphertext = rotated.ciphertext
            item.nonce = rotated.nonce
            item.key_version = rotated.key_version
            item.updated_at = timestamp
            return CredentialStatus(
                item.provider,
                item.environment,
                item.secret_name,
                True,
                timestamp,
                rotated.key_version,
            )

    def decrypt_for_provider_adapter(
        self, owner_id: str, *, provider: str, environment: str, secret_name: str
    ) -> str:
        identity = self._identity(owner_id, provider, environment, secret_name)
        with self._sessions() as session:
            item = session.scalar(self._query(*identity))
            if item is None:
                raise DomainValidationError("공급자 인증정보가 설정되지 않았습니다.")
            encrypted = EncryptedSecret(item.ciphertext, item.nonce, item.key_version)
        return self._vault.decrypt_for_provider_adapter(
            encrypted,
            owner_id=identity[0],
            provider=identity[1],
            environment=identity[2],
            secret_name=identity[3],
        )

    @staticmethod
    def _identity(
        owner_id: str, provider: str, environment: str, secret_name: str
    ) -> tuple[str, str, str, str]:
        return (
            _owner(owner_id),
            _text(provider, label="공급자", limit=40).lower(),
            _text(environment, label="공급자 환경", limit=20).lower(),
            _text(secret_name, label="인증정보 이름", limit=60),
        )

    @staticmethod
    def _query(owner: str, provider: str, environment: str, secret_name: str) -> Select[tuple[ProviderSecret]]:
        return select(ProviderSecret).where(
            ProviderSecret.owner_id == owner,
            ProviderSecret.provider == provider,
            ProviderSecret.environment == environment,
            ProviderSecret.secret_name == secret_name,
        )


class PushSubscriptionRepository(_Repository):
    """Encrypt browser push capability URLs and keys; never return plaintext."""

    def __init__(self, session_factory: sessionmaker[Session], vault: SecretVault) -> None:
        super().__init__(session_factory)
        self._vault = vault

    def upsert(
        self,
        owner_id: str,
        *,
        endpoint: str,
        keys: Mapping[str, str],
        device_label: str,
        created_at: datetime | None = None,
    ) -> str:
        owner = _owner(owner_id)
        endpoint_hash = _sha256_text(_text(endpoint, label="알림 구독 주소", limit=4096))
        if set(keys) != {"p256dh", "auth"} or any(
            not isinstance(value, str) or not value or len(value) > 4096
            for value in keys.values()
        ):
            raise DomainValidationError("알림 구독 키 형식을 확인할 수 없습니다.")
        label = _text(device_label, label="알림 기기 이름", limit=100)
        plaintext = json.dumps(
            {"endpoint": endpoint, "keys": dict(keys), "device_label": label},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encrypted = self._vault.encrypt(
            plaintext,
            owner_id=owner,
            provider="web_push",
            environment="browser",
            secret_name="subscription",
        )
        timestamp = _aware(created_at or utc_now(), label="알림 구독 시각")
        with self._sessions.begin() as session:
            item = session.scalar(
                select(PushSubscription).where(
                    PushSubscription.owner_id == owner,
                    PushSubscription.endpoint_hash == endpoint_hash,
                )
            )
            if item is None:
                item = PushSubscription(
                    owner_id=owner,
                    endpoint_hash=endpoint_hash,
                    encrypted_payload=encrypted.ciphertext,
                    nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                    created_at=timestamp,
                )
                session.add(item)
            else:
                item.encrypted_payload = encrypted.ciphertext
                item.nonce = encrypted.nonce
                item.key_version = encrypted.key_version
                item.created_at = timestamp
                item.disabled_at = None
            session.flush()
        return endpoint_hash

    def delete_by_endpoint(self, owner_id: str, *, endpoint: str) -> bool:
        owner = _owner(owner_id)
        endpoint_hash = _sha256_text(_text(endpoint, label="알림 구독 주소", limit=4096))
        with self._sessions.begin() as session:
            item = session.scalar(
                select(PushSubscription).where(
                    PushSubscription.owner_id == owner,
                    PushSubscription.endpoint_hash == endpoint_hash,
                )
            )
            if item is None:
                return False
            session.delete(item)
            return True

    def endpoint_hashes(self, owner_id: str) -> tuple[str, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(PushSubscription.endpoint_hash).where(
                        PushSubscription.owner_id == _owner(owner_id),
                        PushSubscription.disabled_at.is_(None),
                    )
                )
            )


class JobRepository(_Repository):
    def claim(
        self,
        owner_id: str,
        *,
        job_kind: str,
        scheduled_for: datetime,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
    ) -> JobRun | None:
        owner = _owner(owner_id)
        kind = _text(job_kind, label="예약 작업 종류", limit=40)
        worker = _text(worker_id, label="작업자 식별자", limit=100)
        scheduled = _aware(scheduled_for, label="예약 슬롯 시각")
        claimed = _aware(now, label="작업 시작 시각")
        if lease_for <= timedelta(0) or lease_for > timedelta(hours=6):
            raise DomainValidationError("작업 임대 시간을 확인할 수 없습니다.")
        lease_expires = claimed + lease_for
        try:
            with self._sessions.begin() as session:
                item = session.scalar(
                    select(JobRun)
                    .where(
                        JobRun.owner_id == owner,
                        JobRun.job_kind == kind,
                        JobRun.scheduled_for == scheduled,
                    )
                    .with_for_update()
                )
                if item is None:
                    item = JobRun(
                        owner_id=owner,
                        job_kind=kind,
                        scheduled_for=scheduled,
                        state="RUNNING",
                        claimed_by=worker,
                        claimed_at=claimed,
                        lease_expires_at=lease_expires,
                        attempt_count=1,
                    )
                    session.add(item)
                    session.flush()
                    return item
                if item.state == "SUCCEEDED" or item.attempt_count >= 3:
                    return None
                if item.state == "RUNNING" and item.lease_expires_at > claimed:
                    return None
                if item.state == "RUNNING":
                    session.add(
                        JobAttempt(
                            job_run_id=item.id,
                            attempt_number=item.attempt_count,
                            worker_id=item.claimed_by,
                            started_at=item.claimed_at,
                            finished_at=claimed,
                            state="ABANDONED_STALE",
                            outcome_code="LEASE_EXPIRED",
                        )
                    )
                item.attempt_count += 1
                item.state = "RUNNING"
                item.claimed_by = worker
                item.claimed_at = claimed
                item.lease_expires_at = lease_expires
                item.finished_at = None
                item.outcome_code = None
                return item
        except IntegrityError:
            return None

    def finish(
        self,
        owner_id: str,
        job_id: int,
        *,
        worker_id: str,
        state: str,
        outcome_code: str,
        finished_at: datetime,
    ) -> bool:
        if state not in {"SUCCEEDED", "PARTIAL", "FAILED"}:
            raise DomainValidationError("작업 완료 상태를 확인할 수 없습니다.")
        with self._sessions.begin() as session:
            item = session.scalar(
                select(JobRun).where(JobRun.id == job_id, JobRun.owner_id == _owner(owner_id))
            )
            if item is None or item.state != "RUNNING" or item.claimed_by != worker_id:
                return False
            item.state = state
            item.outcome_code = _text(outcome_code, label="작업 결과 코드", limit=60)
            item.finished_at = _aware(finished_at, label="작업 완료 시각")
            session.add(
                JobAttempt(
                    job_run_id=item.id,
                    attempt_number=item.attempt_count,
                    worker_id=item.claimed_by,
                    started_at=item.claimed_at,
                    finished_at=item.finished_at,
                    state=state,
                    outcome_code=item.outcome_code,
                )
            )
            return True

    def list(self, owner_id: str, *, limit: int = 50) -> tuple[JobRun, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(JobRun)
                    .where(JobRun.owner_id == _owner(owner_id))
                    .order_by(JobRun.scheduled_for.desc(), JobRun.id.desc())
                    .limit(_limit(limit))
                )
            )


@dataclass(frozen=True)
class MarketRecordInput:
    kind: str
    provider_record_id: str
    effective_at: datetime
    collected_at: datetime
    source_url: str
    value: Any = None
    instrument_id: str | None = None
    market: str | None = None
    currency: str | None = None
    published_at: datetime | None = None
    unit: str | None = None
    revised: bool = False
    source_metadata: Mapping[str, Any] | None = None


class MarketRepository(_Repository):
    """Global official-market records, kept separate from user-owned data."""

    def start_run(
        self,
        *,
        provider: str,
        started_at: datetime,
        initiated_by: str | None = None,
        connection_mode: str = "FIXTURE",
    ) -> CollectionRun:
        if connection_mode not in {"FIXTURE", "LIVE"}:
            raise DomainValidationError("수집 연결 구분을 확인할 수 없습니다.")
        item = CollectionRun(
            provider=_text(provider, label="공급자", limit=40).lower(),
            connection_mode=connection_mode,
            initiated_by=_owner(initiated_by) if initiated_by is not None else None,
            status="RUNNING",
            started_at=_aware(started_at, label="수집 시작 시각"),
            record_count=0,
        )
        with self._sessions.begin() as session:
            session.add(item)
            session.flush()
        return item

    def latest_run(self, provider: str) -> CollectionRun | None:
        with self._sessions() as session:
            return session.scalar(
                select(CollectionRun)
                .where(CollectionRun.provider == _text(provider, label="공급자", limit=40).lower())
                .order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc())
                .limit(1)
            )

    def append_records(
        self, collection_run_id: int, records: Sequence[MarketRecordInput]
    ) -> tuple[MarketRecord, ...]:
        if not records:
            return ()
        stored: list[MarketRecord] = []
        with self._sessions.begin() as session:
            run = session.get(CollectionRun, collection_run_id)
            if run is None or run.status != "RUNNING":
                raise DomainValidationError("수집 실행 상태를 확인할 수 없습니다.")
            for source in records:
                metadata, _ = _canonical_payload(source.source_metadata or {})
                normalized_value, value_json = self._json_value(source.value)
                content = {
                    "provider": run.provider,
                    "kind": source.kind,
                    "provider_record_id": source.provider_record_id,
                    "instrument_id": source.instrument_id,
                    "market": source.market,
                    "currency": source.currency,
                    "effective_at": _aware(source.effective_at, label="자료 기준 시각").isoformat(),
                    "published_at": (
                        _aware(source.published_at, label="자료 발표 시각").isoformat()
                        if source.published_at is not None
                        else None
                    ),
                    "collected_at": _aware(source.collected_at, label="자료 수집 시각").isoformat(),
                    "unit": source.unit,
                    "value": json.loads(value_json),
                    "revised": bool(source.revised),
                    "source_url": source.source_url,
                    "metadata": metadata,
                }
                digest = _sha256_text(
                    json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                )
                existing = session.scalar(
                    select(MarketRecord).where(
                        MarketRecord.provider == run.provider,
                        MarketRecord.kind == source.kind,
                        MarketRecord.provider_record_id == source.provider_record_id,
                        MarketRecord.content_sha256 == digest,
                    )
                )
                if existing is not None:
                    # Provider retries are idempotent: preserve the first
                    # provenance row rather than creating a duplicate.
                    continue
                item = MarketRecord(
                    collection_run_id=run.id,
                    provider=run.provider,
                    kind=_text(source.kind, label="자료 종류", limit=40),
                    provider_record_id=_text(
                        source.provider_record_id, label="공급자 자료 식별자", limit=200
                    ),
                    instrument_id=(
                        _text(source.instrument_id, label="종목 식별자", limit=100)
                        if source.instrument_id is not None
                        else None
                    ),
                    market=(
                        _text(source.market, label="시장", limit=40)
                        if source.market is not None
                        else None
                    ),
                    currency=(
                        _text(source.currency, label="통화", limit=12)
                        if source.currency is not None
                        else None
                    ),
                    effective_at=_aware(source.effective_at, label="자료 기준 시각"),
                    published_at=(
                        _aware(source.published_at, label="자료 발표 시각")
                        if source.published_at is not None
                        else None
                    ),
                    collected_at=_aware(source.collected_at, label="자료 수집 시각"),
                    unit=(
                        _text(source.unit, label="자료 단위", limit=80)
                        if source.unit is not None
                        else None
                    ),
                    value=normalized_value,
                    revised=bool(source.revised),
                    source_url=_text(source.source_url, label="공식 출처 주소", limit=4096),
                    source_metadata=metadata,
                    content_sha256=digest,
                )
                session.add(item)
                stored.append(item)
            session.flush()
        return tuple(stored)

    def finish_run(
        self,
        collection_run_id: int,
        *,
        status: str,
        finished_at: datetime,
        failure_code: str | None = None,
    ) -> CollectionRun:
        if status not in {"SUCCEEDED", "PARTIAL", "FAILED"}:
            raise DomainValidationError("수집 완료 상태를 확인할 수 없습니다.")
        with self._sessions.begin() as session:
            run = session.get(CollectionRun, collection_run_id)
            if run is None or run.status != "RUNNING":
                raise DomainValidationError("수집 실행 상태를 확인할 수 없습니다.")
            run.status = status
            run.finished_at = _aware(finished_at, label="수집 완료 시각")
            run.record_count = int(
                session.scalar(
                    select(func.count(MarketRecord.id)).where(
                        MarketRecord.collection_run_id == collection_run_id
                    )
                )
                or 0
            )
            run.failure_code = (
                _text(failure_code, label="수집 실패 코드", limit=60)
                if failure_code is not None
                else None
            )
            return run

    def latest(
        self,
        *,
        provider: str,
        kind: str,
        instrument_id: str | None,
        cutoff_at: datetime,
    ) -> MarketRecord | None:
        cutoff = _aware(cutoff_at, label="자료 조회 기준 시각")
        conditions = [
            MarketRecord.provider == _text(provider, label="공급자", limit=40).lower(),
            MarketRecord.kind == _text(kind, label="자료 종류", limit=40),
            MarketRecord.effective_at <= cutoff,
            MarketRecord.collected_at <= cutoff,
            (MarketRecord.published_at.is_(None) | (MarketRecord.published_at <= cutoff)),
        ]
        if instrument_id is None:
            conditions.append(MarketRecord.instrument_id.is_(None))
        else:
            conditions.append(
                MarketRecord.instrument_id
                == _text(instrument_id, label="종목 식별자", limit=100)
            )
        with self._sessions() as session:
            # UTCDateTime is stored with microseconds.  A Python guard keeps the
            # cutoff exact even on an unusual backend datetime implementation.
            candidates = tuple(
                session.scalars(
                    select(MarketRecord)
                    .where(*conditions)
                    .order_by(
                        MarketRecord.effective_at.desc(),
                        MarketRecord.collected_at.desc(),
                        MarketRecord.id.desc(),
                    )
                    .limit(20)
                )
            )
        return next(
            (
                item
                for item in candidates
                if item.effective_at <= cutoff
                and item.collected_at <= cutoff
                and (item.published_at is None or item.published_at <= cutoff)
            ),
            None,
        )

    @staticmethod
    def _json_value(value: Any) -> tuple[Any, str]:
        def reject_binary_float(item: Any) -> None:
            if isinstance(item, float):
                raise DomainValidationError(
                    "공급자 금융 수치는 이진 부동소수가 아닌 문자열이어야 합니다."
                )
            if isinstance(item, Mapping):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise DomainValidationError("공급자 자료 값을 확인할 수 없습니다.")
                    reject_binary_float(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    reject_binary_float(child)

        reject_binary_float(value)
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return json.loads(rendered), rendered
        except (TypeError, ValueError, RecursionError):
            raise DomainValidationError("공급자 자료 값을 확인할 수 없습니다.") from None


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 500:
        raise DomainValidationError("조회 개수를 확인할 수 없습니다.")
    return value
