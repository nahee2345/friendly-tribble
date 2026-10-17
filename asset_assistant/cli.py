"""플랫폼 중립 OFFLINE 명령행 진입점."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .domain import DomainValidationError
from .file_io import paths_refer_to_same_file, write_report_text
from .reporting import build_markdown_report
from .scenario import load_scenario


class _SafeArgumentParser(argparse.ArgumentParser):
    """명령행 구문 오류에 사용자 입력 원문을 되풀이하지 않습니다."""

    def error(self, message: str) -> None:
        del message
        self.print_usage(sys.stderr)
        self.exit(
            2,
            "입력 오류: 명령행 인수를 처리할 수 없습니다. "
            "원본 인수는 표시하지 않습니다.\n",
        )


def _same_file(input_path: Path, output_path: Path) -> bool:
    """이전 내부 호출 호환용 공용 파일 안전 함수 래퍼."""

    return paths_refer_to_same_file(input_path, output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="asset-assistant",
        description="샘플/수동 JSON으로 OFFLINE 자산관리 보고서를 생성합니다.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("examples") / "offline_sample.json",
        help="UTF-8 입력 JSON 경로 (기본: examples/offline_sample.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Markdown 출력 경로. 생략하면 표준 출력에 표시합니다.",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.output is not None and _same_file(args.input, args.output):
            raise DomainValidationError("입력 파일과 출력 파일은 같은 파일일 수 없습니다.")
        scenario = load_scenario(args.input)
        report = build_markdown_report(scenario)
        if args.output is None:
            sys.stdout.write(report)
        else:
            write_report_text(report, args.output, source_path=args.input)
    except DomainValidationError as exc:
        parser.exit(2, f"입력 오류: {exc}\n")
    except OSError:
        parser.exit(
            2,
            "입력 오류: 파일을 읽거나 쓸 수 없습니다. 경로와 운영체제 원문은 표시하지 않습니다.\n",
        )
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
