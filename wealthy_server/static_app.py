"""Safe same-origin serving for a pre-built React single-page application."""

from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from asset_assistant.domain import DomainValidationError


_LEGACY_PWA_PATHS = frozenset({"sw.js", "manifest.webmanifest"})


def validate_frontend_directory(value: Path) -> Path:
    try:
        candidate = value.expanduser()
        if not candidate.is_absolute():
            candidate = candidate.resolve(strict=True)
        else:
            candidate = candidate.resolve(strict=True)
        info = candidate.stat(follow_symlinks=False)
        index = candidate / "index.html"
        index_info = index.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or not stat.S_ISREG(index_info.st_mode):
            raise ValueError
        if index.is_symlink() or index.resolve(strict=True).parent != candidate:
            raise ValueError
    except (OSError, RuntimeError, ValueError):
        raise DomainValidationError(
            "React 빌드 결과를 안전하게 찾을 수 없습니다. 전체 경로는 표시하지 않습니다."
        ) from None
    return candidate


def _spa_candidate(path: str) -> bool:
    if not path or "\x00" in path or "\\" in path:
        return path == ""
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return False
    if candidate.parts and candidate.parts[0] == "api":
        # Unknown API paths must remain JSON/404 failures, never HTML with a
        # misleading success status from the SPA fallback.
        return False
    # Asset-like paths must stay 404 instead of receiving executable HTML.
    return all("." not in part for part in candidate.parts)


class SafeSpaStaticFiles(StaticFiles):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory=str(directory), html=True, check_dir=True)

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        normalized = path.lstrip("/")
        if normalized in _LEGACY_PWA_PATHS:
            return Response(
                status_code=410,
                headers={
                    "Cache-Control": "no-store",
                    "Clear-Site-Data": '"cache"',
                },
            )
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not _spa_candidate(normalized):
                raise
            response = await super().get_response("index.html", scope)
        if response.status_code == 404 and _spa_candidate(normalized):
            response = await super().get_response("index.html", scope)
        if normalized in {"", "index.html"} or _spa_candidate(normalized):
            response.headers["Cache-Control"] = "no-store"
        return response


def mount_frontend(app: FastAPI, frontend_dir: Path) -> Path:
    """Mount last so explicit API routes always win over the SPA fallback."""

    checked = validate_frontend_directory(frontend_dir)
    app.mount("/", SafeSpaStaticFiles(checked), name="frontend")
    app.state.frontend_dir = checked
    return checked
