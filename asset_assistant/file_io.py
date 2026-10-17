"""CLI와 GUI가 함께 쓰는 플랫폼 중립 로컬 파일 안전 함수."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .domain import DomainValidationError


def paths_refer_to_same_file(input_path: Path, output_path: Path) -> bool:
    """문자열 경로뿐 아니라 심볼릭 링크·하드링크 동일성도 확인합니다."""

    if "\x00" in str(input_path) or "\x00" in str(output_path):
        raise DomainValidationError(
            "입출력 경로를 안전하게 처리할 수 없습니다. 경로 원문은 표시하지 않습니다."
        )
    try:
        if input_path.resolve() == output_path.resolve():
            return True
        return input_path.samefile(output_path)
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError, ValueError):
        raise DomainValidationError(
            "입출력 파일 동일성을 안전하게 확인할 수 없습니다."
        ) from None


def write_report_text(
    report: str,
    output_path: Path,
    *,
    source_path: Path | None = None,
) -> None:
    """입력 덮어쓰기·symlink 추종을 막고 owner-only 파일로 원자 저장합니다."""

    if source_path is not None and paths_refer_to_same_file(source_path, output_path):
        raise DomainValidationError("입력 파일과 출력 파일은 같은 파일일 수 없습니다.")
    temporary_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            delete=False,
        ) as temporary:
            temporary.write(report)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        if os.name != "nt":
            os.chmod(temporary_path, 0o600)
        temporary_path.replace(output_path)
    except (OSError, UnicodeError, ValueError):
        raise DomainValidationError(
            "보고서를 저장할 수 없습니다. 경로와 운영체제 원문은 표시하지 않습니다."
        ) from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
