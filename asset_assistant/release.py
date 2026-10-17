"""개인자료·가상환경을 제외한 소스 인계 ZIP 빌더."""

from __future__ import annotations

import stat
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from .cli import _SafeArgumentParser
from .domain import DomainValidationError


ROOT_FILES = (
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "CLAUDE_VALIDATION.md",
    "CODEX_WORKFLOW.md",
    "DOCS_INDEX.md",
    "PROJECT_RULES.md",
    "README.md",
    "UI_DESIGN_SPEC.md",
    "VALIDATION_LOG.md",
    "WEB_DESIGN_SPEC.md",
    "pyproject.toml",
)
PACKAGE_FILES = (
    "__init__.py",
    "__main__.py",
    "allocation.py",
    "automation.py",
    "calculations.py",
    "cli.py",
    "credential_store.py",
    "domain.py",
    "file_io.py",
    "gui.py",
    "gui_service.py",
    "kr_providers.py",
    "ledger.py",
    "market_data.py",
    "market_information.py",
    "paper.py",
    "paper_persistence.py",
    "providers.py",
    "release.py",
    "reporting.py",
    "satellite.py",
    "scenario.py",
    "scheduler.py",
    "strategy.py",
    "strategy_ui.py",
    "storage_maintenance.py",
)
DOCUMENT_FILES = (
    "ARCHITECTURE.md",
    "BACKUP_RESTORE.md",
    "COORDINATION_REPORT.md",
    "COST_BUDGET.md",
    "DATA_SOURCES.md",
    "DESIGN_COMPARISON.md",
    "DEPLOYMENT.md",
    "FREE_INFRASTRUCTURE.md",
    "FUTURE_VALIDATION_DESIGN.md",
    "MIGRATION_SQLITE_POSTGRES.md",
    "NEXT_STAGE_PLAN.md",
    "PWA_PUSH.md",
    "REQUIREMENTS_TRACEABILITY.md",
    "WEB_API.md",
    "WEB_ARCHITECTURE.md",
    "WINDOWS_VALIDATION.md",
)
SERVER_FILES = (
    "__init__.py",
    "admin.py",
    "api_schemas.py",
    "application.py",
    "app.py",
    "collection.py",
    "config.py",
    "container_entrypoint.py",
    "database.py",
    "main.py",
    "local_adapters.py",
    "local_credentials.py",
    "local_runtime.py",
    "models.py",
    "offline_sample.json",
    "repositories.py",
    "security.py",
    "static_app.py",
    "strategy_targets.py",
    "worker.py",
)
DEPLOYMENT_FILES = (
    "Caddyfile.local",
    "Caddyfile.production.example",
    "compose.production.yaml",
    "compose.yaml",
    "Dockerfile.backend",
    "Dockerfile.backend.dockerignore",
    "Dockerfile.frontend",
    "Dockerfile.frontend.dockerignore",
)
DESIGN_FILES = (
    "README.md",
    "references/00_전체_화면_구성도_시안.png",
    "references/01_전체_대시보드.png",
    "references/02_투자전략_비중_설정.png",
    "references/03_비중_오류와_저장_차단.png",
    "references/04_전략명_클릭_가치투자_상세.png",
    "references/05_계좌_ISA_관리.png",
    "references/06_종목_ETF_분석.png",
    "references/07_매도_세금_시뮬레이터.png",
    "references/08_배당_현금흐름.png",
    "references/09_자동_보고서.png",
    "references/10_KIS_API_연동_설정.png",
    "references/11_리밸런싱_일정_관리.png",
    "references/12_반응형_화면_가이드.png",
    "references/README_디자인_구현_가이드.md",
    "references/SHA256SUMS",
)
FRONTEND_FILES = (
    ".gitignore",
    "README.md",
    "index.html",
    "package-lock.json",
    "package.json",
    "public/manifest.webmanifest",
    "public/sw.js",
    "public/wealthy-mark.svg",
    "src/App.test.tsx",
    "src/App.tsx",
    "src/MarkdownReport.test.tsx",
    "src/MarkdownReport.tsx",
    "src/MarketPage.test.tsx",
    "src/api.test.ts",
    "src/api.ts",
    "src/auth.tsx",
    "src/catalog.ts",
    "src/decimal.test.ts",
    "src/decimal.ts",
    "src/design.test.ts",
    "src/format.ts",
    "src/hooks.ts",
    "src/main.tsx",
    "src/marketLinks.ts",
    "src/notifications.test.ts",
    "src/notifications.tsx",
    "src/pages.tsx",
    "src/pwa.ts",
    "src/runtimeStatus.tsx",
    "src/strategyDraft.tsx",
    "src/styles.css",
    "src/sw.test.ts",
    "src/test-setup.ts",
    "src/types.ts",
    "tsconfig.app.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
)
TEST_FILES = (
    "test_allocation.py",
    "test_calculations.py",
    "test_credentials.py",
    "test_gui_automation.py",
    "test_gui_service.py",
    "test_job_status_display.py",
    "test_kr_providers.py",
    "test_ledger.py",
    "test_market_automation.py",
    "test_market_data_precision.py",
    "test_market_information.py",
    "test_paper.py",
    "test_paper_persistence.py",
    "test_providers.py",
    "test_release.py",
    "test_scenario_reporting.py",
    "test_scheduler.py",
    "test_strategy.py",
    "test_strategy_gui.py",
    "test_strategy_ui.py",
    "test_strategy_workspace.py",
    "test_storage_maintenance.py",
    "test_web_api.py",
    "test_web_foundation.py",
    "test_web_local_backup.py",
    "test_web_local_runtime.py",
    "test_web_local_service.py",
    "test_web_market_information.py",
    "test_web_operations.py",
    "test_web_postgres.py",
    "test_web_worker.py",
)
TOOL_FILES = (
    "__init__.py",
    "gui_smoke.py",
    "web_backup.py",
    "web_backup_crypto.py",
    "web_browser_smoke.mjs",
    "web_local_backup.py",
    "web_local_service.py",
    "web_migration_plan.py",
    "web_restore.py",
    "web_secret_setup.py",
    "web_validate_deploy.py",
)
ALLOWED_MEMBERS = frozenset(
    ROOT_FILES
    + tuple(f"asset_assistant/{name}" for name in PACKAGE_FILES)
    + tuple(f"docs/{name}" for name in DOCUMENT_FILES)
    + tuple(f"wealthy_server/{name}" for name in SERVER_FILES)
    + tuple(f"deploy/{name}" for name in DEPLOYMENT_FILES)
    + tuple(f"design/{name}" for name in DESIGN_FILES)
    + tuple(f"web/frontend/{name}" for name in FRONTEND_FILES)
    + tuple(f"tests/{name}" for name in TEST_FILES)
    + tuple(f"tools/{name}" for name in TOOL_FILES)
    + ("examples/offline_sample.json",)
)
ALLOWED_JSON_MEMBERS = frozenset(
    {
        "examples/offline_sample.json",
        "web/frontend/package-lock.json",
        "web/frontend/package.json",
        "web/frontend/tsconfig.app.json",
        "web/frontend/tsconfig.json",
        "web/frontend/tsconfig.node.json",
        "wealthy_server/offline_sample.json",
    }
)
FORBIDDEN_COMPONENTS = {
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    ".venv-clean",
    "__pycache__",
    "backups",
    "build",
    "dist",
    "node_modules",
    "reports",
    "screenshots",
    "secrets",
    "user_data",
    "validation-artifacts",
    "venv",
}
FORBIDDEN_SUFFIXES = {
    ".backup",
    ".db",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".wbak",
    ".wbackup",
}


def _allowlisted_sources(project_root: Path) -> tuple[Path, ...]:
    sources = [project_root / PurePosixPath(name) for name in sorted(ALLOWED_MEMBERS)]

    unique: dict[str, Path] = {}
    for source in sources:
        if not source.exists() or not source.is_file() or source.is_symlink():
            raise DomainValidationError(
                "배포 대상 소스 파일을 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
            )
        relative = source.relative_to(project_root).as_posix()
        unique[relative] = source
    return tuple(unique[name] for name in sorted(unique))


def _validate_member_name(name: str) -> None:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise DomainValidationError("배포 ZIP에 안전하지 않은 경로가 있습니다.")
    for component in pure.parts:
        lowered = component.lower()
        if lowered in FORBIDDEN_COMPONENTS or lowered.startswith(".venv-"):
            raise DomainValidationError("배포 ZIP에 제외 대상 경로가 있습니다.")
        if lowered.endswith(".egg-info"):
            raise DomainValidationError("배포 ZIP에 제외 대상 경로가 있습니다.")
    basename = pure.name.lower()
    if basename == ".env" or basename.startswith(".env."):
        raise DomainValidationError("배포 ZIP에 비밀 설정 파일이 있습니다.")
    if PurePosixPath(basename).suffix in FORBIDDEN_SUFFIXES:
        raise DomainValidationError("배포 ZIP에 제외 대상 파일이 있습니다.")


def validate_release_zip(archive_path: Path) -> tuple[str, ...]:
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            members = bundle.infolist()
            names = tuple(sorted(member.filename for member in members))
            if not names or len(names) != len(set(names)):
                raise DomainValidationError("배포 ZIP의 파일 목록이 올바르지 않습니다.")
            for member in members:
                _validate_member_name(member.filename)
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                if (
                    member.is_dir()
                    or bool(member.external_attr & 0x10)
                    or file_type not in (0, stat.S_IFREG)
                    or bool(member.flag_bits & 0x1)
                ):
                    raise DomainValidationError(
                        "배포 ZIP에 안전하지 않은 파일 유형이 있습니다."
                    )
            if set(names) != ALLOWED_MEMBERS:
                raise DomainValidationError("배포 ZIP의 허용목록과 파일 목록이 일치하지 않습니다.")
            json_members = {
                name for name in names if name.lower().endswith(".json")
            }
            if json_members != ALLOWED_JSON_MEMBERS:
                raise DomainValidationError(
                    "배포 ZIP의 JSON 파일이 허용된 샘플·소스 메타데이터와 일치하지 않습니다."
                )
            if bundle.testzip() is not None:
                raise DomainValidationError("배포 ZIP 무결성 검사에 실패했습니다.")
    except DomainValidationError:
        raise
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
        RuntimeError,
        NotImplementedError,
    ):
        raise DomainValidationError("배포 ZIP을 안전하게 검사할 수 없습니다.") from None
    return names


def build_release_zip(project_root: Path, output_path: Path) -> tuple[str, ...]:
    try:
        root = project_root.resolve()
        sources = _allowlisted_sources(root)
        if output_path.suffix.lower() != ".zip":
            raise DomainValidationError("배포 파일 확장자는 .zip이어야 합니다.")
        # Refuse even dangling/self-referential links before resolving output.
        if output_path.is_symlink():
            raise DomainValidationError("배포 경로를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다.")
        output = output_path.resolve()
        if any(output == source.resolve() for source in sources):
            raise DomainValidationError("배포 ZIP이 소스 파일을 덮어쓸 수 없습니다.")
    except DomainValidationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise DomainValidationError(
            "배포 경로를 안전하게 확인할 수 없습니다. 경로는 표시하지 않습니다."
        ) from None

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="asset-assistant-release-",
            suffix=".zip",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            with zipfile.ZipFile(
                temporary_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as bundle:
                for source in sources:
                    relative = source.relative_to(root).as_posix()
                    _validate_member_name(relative)
                    bundle.write(source, arcname=relative)
            members = validate_release_zip(temporary_path)
            temporary_path.replace(output)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
    except DomainValidationError:
        raise
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        raise DomainValidationError("배포 ZIP을 생성할 수 없습니다. 경로는 표시하지 않습니다.") from None
    return members


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        prog="asset-assistant-release",
        description="localhost 웹과 읽기 전용 시장자료 자동화 소스 인계 ZIP을 만듭니다.",
    )
    parser.add_argument("--output", type=Path, required=True, help="생성할 .zip 파일")
    args = parser.parse_args(argv)
    try:
        members = build_release_zip(Path.cwd(), args.output)
    except DomainValidationError as exc:
        sys.stderr.write(f"배포 오류: {exc}\n")
        return 2
    sys.stdout.write(f"배포 소스 ZIP 생성 완료: {len(members)}개 파일\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
