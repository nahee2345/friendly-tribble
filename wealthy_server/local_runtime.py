"""Ubuntu localhost runtime: FastAPI + built React + two separate SQLite DBs."""

from __future__ import annotations

import getpass
import hmac
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

import uvicorn
from sqlalchemy import func, select

from asset_assistant.automation import AutomationService
from asset_assistant.cli import _SafeArgumentParser
from asset_assistant.credential_store import CredentialStoreError
from asset_assistant.domain import DomainValidationError
from asset_assistant.file_io import paths_refer_to_same_file
from asset_assistant.market_data import MarketDataStore
from asset_assistant.market_information import (
    MarketInformationRepository,
    MarketInformationService,
)

from .app import create_app
from .config import ServerSettings
from .database import Database
from .local_adapters import (
    LocalJobRepository,
    LocalMarketRepository,
    LocalReportRepository,
)
from .local_credentials import LocalCredentialRepository, LocalSecretStore
from .models import User
from .repositories import AuthRepository
from .security import PasswordManager


WEB_STATE_FILENAME = "web-state.sqlite3"
DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "web" / "frontend" / "dist"


def _absolute_path(value: Path, *, label: str, must_exist: bool = False) -> Path:
    try:
        candidate = value.expanduser()
        if not candidate.is_absolute():
            raise ValueError
        resolved = candidate.resolve(strict=must_exist)
        if "\x00" in str(resolved):
            raise ValueError
        return resolved
    except (OSError, RuntimeError, ValueError):
        raise DomainValidationError(
            f"{label} 경로를 안전하게 확인할 수 없습니다. 전체 경로는 표시하지 않습니다."
        ) from None


def prepare_data_directory(value: Path) -> Path:
    candidate = _absolute_path(value, label="로컬 데이터 폴더")
    try:
        candidate.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = candidate.stat(follow_symlinks=False)
        if candidate.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise OSError
        if os.name != "nt":
            if info.st_uid != os.getuid():
                raise OSError
            os.chmod(candidate, 0o700)
    except OSError:
        raise DomainValidationError(
            "로컬 데이터 폴더를 안전하게 준비할 수 없습니다. 전체 경로는 표시하지 않습니다."
        ) from None
    return candidate


def prepare_market_database(value: Path) -> Path:
    path = _absolute_path(value, label="시장자료 DB")
    try:
        parent_existed = path.parent.exists()
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent = path.parent.resolve(strict=True)
        if path.parent != parent or not parent.is_dir():
            raise OSError
        parent_info = parent.stat(follow_symlinks=False)
        if os.name != "nt":
            if parent_info.st_uid != os.getuid():
                raise OSError
            if not parent_existed:
                os.chmod(parent, 0o700)
        if path.exists() or path.is_symlink():
            info = path.stat(follow_symlinks=False)
            if path.is_symlink() or not stat.S_ISREG(info.st_mode):
                raise OSError
    except (OSError, RuntimeError):
        raise DomainValidationError(
            "시장자료 DB 경로를 안전하게 준비할 수 없습니다. 전체 경로는 표시하지 않습니다."
        ) from None
    return path


def secure_database_file(path: Path, *, label: str) -> None:
    """Require an owned regular DB file and remove group/other access."""

    try:
        info = path.stat(follow_symlinks=False)
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise OSError
        if os.name != "nt":
            if info.st_uid != os.getuid():
                raise OSError
            os.chmod(path, 0o600)
    except OSError:
        raise DomainValidationError(
            f"{label} 파일 권한을 안전하게 설정할 수 없습니다. 전체 경로는 표시하지 않습니다."
        ) from None


def _assert_distinct_databases(web_state: Path, market: Path) -> None:
    try:
        if web_state.resolve(strict=False) == market.resolve(strict=False):
            raise DomainValidationError("웹 상태 DB와 시장자료 DB는 서로 다른 파일이어야 합니다.")
        if web_state.exists() and market.exists() and paths_refer_to_same_file(web_state, market):
            raise DomainValidationError("웹 상태 DB와 시장자료 DB는 서로 다른 파일이어야 합니다.")
    except DomainValidationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise DomainValidationError("두 로컬 DB 경로를 안전하게 비교할 수 없습니다.") from None


def _single_local_owner_guard(database: Database) -> Callable[[str | None], None]:
    """Return a fail-closed guard for machine-wide single-user adapters."""

    def require(owner_id: str | None = None) -> None:
        try:
            with database.session_factory() as session:
                owner_ids = tuple(session.scalars(select(User.id).limit(2)))
            if len(owner_ids) != 1:
                raise DomainValidationError("로컬 사용자 상태를 확인할 수 없습니다.")
            if owner_id is not None and (
                not isinstance(owner_id, str)
                or not hmac.compare_digest(str(owner_ids[0]), owner_id)
            ):
                raise DomainValidationError("로컬 사용자 상태를 확인할 수 없습니다.")
        except DomainValidationError:
            raise
        except Exception:
            raise DomainValidationError(
                "로컬 사용자 상태를 확인할 수 없습니다."
            ) from None

    return require


@contextmanager
def one_process_lock(data_dir: Path) -> Iterator[None]:
    """Prevent two local web servers from sharing one web-state DB."""

    if os.name == "nt":  # current local runtime is Ubuntu-only
        raise DomainValidationError("현재 로컬 웹 실행은 Ubuntu에서만 지원합니다.")
    import fcntl

    lock_path = data_dir / ".wealthy-local.lock"
    descriptor: int | None = None
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        raise DomainValidationError("같은 로컬 데이터 폴더에서 앱이 이미 실행 중입니다.") from None
    except OSError:
        raise DomainValidationError("로컬 앱 중복 실행 잠금을 사용할 수 없습니다.") from None
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def build_local_application(
    *,
    data_dir: Path,
    market_database: Path,
    frontend_dir: Path = DEFAULT_FRONTEND_DIR,
    secret_store: LocalSecretStore | None = None,
    clock=None,
    market_information_auto_start: bool = False,
):
    """Build without starting a socket; tests can inject a fake OS keyring."""

    checked_data = prepare_data_directory(data_dir)
    checked_market = prepare_market_database(market_database)
    web_state = checked_data / WEB_STATE_FILENAME
    _assert_distinct_databases(web_state, checked_market)
    database = Database.for_local_file(web_state)
    try:
        database.create_local_schema()
        owner_guard = _single_local_owner_guard(database)
        # ``serve`` is only valid after the one TTY-created local account
        # exists.  Check this before touching the OS keyring or market store.
        owner_guard()
        market_store = MarketDataStore(checked_market)
        market_store.initialize()
        secure_database_file(checked_market, label="시장자료 DB")
        local_secrets = secret_store or LocalSecretStore()
        master_key = local_secrets.load_or_create_master_key()
        provider_secret_store = local_secrets.provider_store()
        credentials = LocalCredentialRepository(
            provider_secret_store,
            owner_guard=owner_guard,
            **({"clock": clock} if clock is not None else {}),
        )
        # Prime once before requests/background polling begin.  Passive status
        # endpoints and the SEC scheduler subsequently use only the in-process
        # snapshot and therefore cannot repeatedly trigger an OS unlock UI.
        credentials.prime()
        market_information = MarketInformationService(
            MarketInformationRepository(market_store),
            sec_user_agent=credentials.cached_sec_user_agent,
            owner_guard=owner_guard,
            **({"clock": clock} if clock is not None else {}),
        )
        settings = ServerSettings(
            database_url=str(database.engine.url),
            master_key=master_key,
            secure_cookies=False,
            allowed_hosts=("localhost", "127.0.0.1"),
            public_origin=None,
            vapid_public_key=None,
            vapid_private_key=None,
        )
        kwargs = {}
        if clock is not None:
            kwargs["clock"] = clock
        app = create_app(
            settings,
            database=database,
            local_mode=True,
            credential_repository=credentials,
            report_repository=LocalReportRepository(
                market_store, owner_guard=owner_guard
            ),
            job_repository=LocalJobRepository(market_store, owner_guard=owner_guard),
            market_repository=LocalMarketRepository(market_store),
            local_owner_guard=owner_guard,
            market_information_service=market_information,
            market_information_auto_start=market_information_auto_start,
            frontend_dir=frontend_dir,
            **kwargs,
        )
    except Exception:
        database.dispose()
        raise
    app.state.market_store = market_store
    app.state.web_state_path = web_state
    return app


def _serve(args) -> int:  # type: ignore[no-untyped-def]
    if args.host != "127.0.0.1" or not 1024 <= args.port <= 65535:
        raise DomainValidationError("로컬 서버 바인딩 설정을 확인할 수 없습니다.")
    data_dir = prepare_data_directory(args.data_dir)
    with one_process_lock(data_dir):
        app = build_local_application(
            data_dir=data_dir,
            market_database=args.market_database,
            frontend_dir=args.frontend_dir,
            market_information_auto_start=True,
        )
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            server_header=False,
        )
    return 0


def _run_task(args) -> int:  # type: ignore[no-untyped-def]
    data_dir = prepare_data_directory(args.data_dir)
    market = prepare_market_database(args.market_database)
    _assert_distinct_databases(data_dir / WEB_STATE_FILENAME, market)
    store = MarketDataStore(market)
    store.initialize()
    secure_database_file(market, label="시장자료 DB")
    service = AutomationService(store=store)
    summary = service.run_scheduled(args.task)
    sys.stdout.write(f"예약 작업 상태: {summary.status}\n")
    return 0 if summary.status in {"OK", "PARTIAL", "SKIPPED_DUPLICATE"} else 2


def _create_admin(args, *, secret_store: LocalSecretStore | None = None) -> int:  # type: ignore[no-untyped-def]
    """Create the sole initial local administrator via hidden TTY prompts."""

    if not sys.stdin.isatty():
        sys.stderr.write("로컬 관리 오류: 최초 관리자 생성은 대화형 TTY에서만 가능합니다.\n")
        return 2
    database: Database | None = None
    password = ""
    confirmation = ""
    try:
        data_dir = prepare_data_directory(args.data_dir)
        market = prepare_market_database(args.market_database)
        web_state = data_dir / WEB_STATE_FILENAME
        _assert_distinct_databases(web_state, market)
        with one_process_lock(data_dir):
            # Prove Secret Service is usable before creating a login which the
            # server could not subsequently start.  No plaintext fallback.
            (secret_store or LocalSecretStore()).load_or_create_master_key()
            database = Database.for_local_file(web_state)
            database.create_local_schema()
            with database.session_factory() as session:
                if int(session.scalar(select(func.count(User.id))) or 0) != 0:
                    raise DomainValidationError("최초 관리자 계정이 이미 구성되어 있습니다.")
            username = input("관리자 사용자 이름: ").strip()
            password = getpass.getpass("새 비밀번호(12자 이상): ")
            confirmation = getpass.getpass("새 비밀번호 확인: ")
            if not password or not hmac.compare_digest(
                password.encode("utf-8"), confirmation.encode("utf-8")
            ):
                raise DomainValidationError("비밀번호 확인이 일치하지 않습니다.")
            AuthRepository(database.session_factory).create_user(
                username,
                PasswordManager().hash(password),
                is_admin=True,
            )
    except (CredentialStoreError, DomainValidationError, EOFError, KeyboardInterrupt):
        # Inputs, paths, backend details, and the attempted username are never
        # copied to stderr.
        sys.stderr.write(
            "로컬 관리 오류: 최초 관리자 계정을 안전하게 생성할 수 없습니다. "
            "입력과 보안 저장소 상태를 확인해 주세요.\n"
        )
        return 2
    except Exception:
        sys.stderr.write(
            "로컬 관리 오류: 최초 관리자 계정을 안전하게 생성할 수 없습니다. "
            "입력과 보안 저장소 상태를 확인해 주세요.\n"
        )
        return 2
    finally:
        password = ""
        confirmation = ""
        if database is not None:
            database.dispose()
    sys.stdout.write(
        "최초 관리자 계정을 생성했습니다. 비밀번호는 출력하거나 파일에 저장하지 않았습니다.\n"
    )
    return 0


def _doctor(args) -> int:  # type: ignore[no-untyped-def]
    """Read-only local startup diagnosis; never prints usernames or secrets."""
    import sqlite3

    errors = False
    data = args.data_dir.expanduser()
    market = args.market_database.expanduser()
    if not data.is_absolute() or not market.is_absolute():
        sys.stdout.write("데이터 경로: FAIL (절대경로 필요)\n")
        return 2
    if data.is_symlink() or not data.is_dir():
        sys.stdout.write("데이터 폴더: FAIL (없거나 안전하지 않음)\n")
        errors = True
    else:
        info = data.stat(follow_symlinks=False)
        owned = info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
        sys.stdout.write(f"데이터 폴더 권한: {'PASS' if owned else 'FAIL'}\n")
        errors |= not owned
    web = data / WEB_STATE_FILENAME
    if not web.is_file() or web.is_symlink():
        sys.stdout.write("관리자 DB: MISSING (기존 DB 삭제 금지, 최초 생성 여부 확인)\n")
        errors = True
    else:
        try:
            # file: mode=ro is used only for diagnostics. Existing DB stays untouched.
            with sqlite3.connect(web.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
                result = db.execute('PRAGMA quick_check').fetchone()
                count = db.execute('SELECT count(*) FROM users').fetchone()[0]
            ok = result == ('ok',) and count == 1
            sys.stdout.write(f"관리자 DB/단일 계정: {'PASS' if ok else 'FAIL'}\n")
            errors |= not ok
        except (sqlite3.Error, OSError):
            sys.stdout.write("관리자 DB/단일 계정: FAIL (손상 또는 접근 불가)\n")
            errors = True
    if market.is_symlink() or (market.exists() and not market.is_file()):
        sys.stdout.write("시장자료 DB 경로: FAIL\n")
        errors = True
    else:
        sys.stdout.write(f"시장자료 DB 파일: {'PRESENT' if market.is_file() else 'MISSING'}\n")
    try:
        backend = LocalSecretStore().backend()
        # Keyring read is non-mutating, unlike load_or_create_master_key.
        backend.get_password('org.long-term-asset-assistant.wealthy-local-web', 'login-fingerprint-key-v1')
        sys.stdout.write("Ubuntu 보안 저장소: PASS\n")
    except Exception:
        sys.stdout.write("Ubuntu 보안 저장소: FAIL (Secret Service 잠금/세션 확인)\n")
        errors = True
    sys.stdout.write("진단은 읽기 전용이며 기존 계정과 DB를 변경하지 않았습니다.\n")
    return 2 if errors else 0


def build_parser() -> _SafeArgumentParser:
    parser = _SafeArgumentParser(
        prog="wealthy-local",
        description="Wealthy Ubuntu localhost 웹 실행(계좌 조회·주문 없음)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="127.0.0.1 로컬 웹 서버 실행")
    serve.add_argument("--data-dir", type=Path, required=True)
    serve.add_argument("--market-database", type=Path, required=True)
    serve.add_argument("--frontend-dir", type=Path, default=DEFAULT_FRONTEND_DIR)
    serve.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    task = commands.add_parser("run-task", help="15시/16시 로컬 예약 슬롯 실행")
    task.add_argument("task", choices=("collect", "report"))
    task.add_argument("--data-dir", type=Path, required=True)
    task.add_argument("--market-database", type=Path, required=True)

    admin = commands.add_parser("create-admin", help="대화형 최초 관리자 생성")
    admin.add_argument("--data-dir", type=Path, required=True)
    admin.add_argument("--market-database", type=Path, required=True)
    doctor = commands.add_parser("doctor", help="읽기 전용 웹 실행 전 점검")
    doctor.add_argument("--data-dir", type=Path, required=True)
    doctor.add_argument("--market-database", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return _create_admin(args)
    if args.command == "doctor":
        return _doctor(args)
    try:
        return _serve(args) if args.command == "serve" else _run_task(args)
    except (CredentialStoreError, DomainValidationError):
        sys.stderr.write(
            "로컬 웹 오류: 작업을 안전하게 완료할 수 없습니다. 설정과 보안 저장소를 확인해 주세요.\n"
        )
        return 2
    except Exception:
        sys.stderr.write("로컬 웹 오류: 작업을 안전하게 완료할 수 없습니다.\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
