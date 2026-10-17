"""TTY-only administration commands; passwords are never command arguments."""

from __future__ import annotations

import getpass
import sys

from asset_assistant.cli import _SafeArgumentParser
from asset_assistant.domain import DomainValidationError

from .config import ServerSettings
from .database import Database
from .repositories import AuthRepository
from .security import PasswordManager


def _create_admin() -> int:
    if not sys.stdin.isatty():
        sys.stderr.write("관리 오류: 최초 관리자 생성은 로컬 대화형 TTY에서만 실행하세요.\n")
        return 2
    try:
        settings = ServerSettings.from_environment()
        database = Database(settings.database_url)
        database.create_schema()
        username = input("관리자 사용자 이름: ").strip()
        password = getpass.getpass("새 비밀번호(12자 이상): ")
        confirmation = getpass.getpass("새 비밀번호 확인: ")
        if not password or not hmac_compare(password, confirmation):
            raise DomainValidationError("비밀번호 확인이 일치하지 않습니다.")
        manager = PasswordManager()
        repository = AuthRepository(database.session_factory)
        repository.create_user(username, manager.hash(password), is_admin=True)
    except (DomainValidationError, EOFError, KeyboardInterrupt) as exc:
        message = str(exc) if isinstance(exc, DomainValidationError) else "관리자 생성을 취소했습니다."
        sys.stderr.write(f"관리 오류: {message}\n")
        return 2
    finally:
        if "password" in locals():
            password = ""
        if "confirmation" in locals():
            confirmation = ""
    sys.stdout.write("최초 관리자 계정을 생성했습니다. 비밀번호는 출력하거나 저장하지 않았습니다.\n")
    return 0


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(prog="wealthy-admin", description="Wealthy 서버 로컬 관리")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-admin", help="대화형 최초 관리자 생성")
    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return _create_admin()
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())

