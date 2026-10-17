"""Uvicorn entry point for the private Wealthy API."""

from __future__ import annotations

import sys

import uvicorn

from asset_assistant.cli import _SafeArgumentParser
from asset_assistant.domain import DomainValidationError

from .app import create_app
from .config import ServerSettings


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(prog="wealthy-web", description="Wealthy 비공개 웹 API")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"))
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        sys.stderr.write("서버 오류: 포트 설정을 확인할 수 없습니다.\n")
        return 2
    try:
        settings = ServerSettings.from_environment()
        app = create_app(settings)
    except DomainValidationError as exc:
        sys.stderr.write(f"서버 오류: {exc}\n")
        return 2
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=True,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

