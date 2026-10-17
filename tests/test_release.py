from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from asset_assistant.domain import DomainValidationError
from asset_assistant.release import (
    ALLOWED_JSON_MEMBERS,
    ALLOWED_MEMBERS,
    build_release_zip,
    validate_release_zip,
)


ROOT = Path(__file__).resolve().parents[1]


def patch_central_member(
    archive: Path,
    member_name: str,
    *,
    external_attr: int | None = None,
    encrypted: bool = False,
) -> None:
    payload = bytearray(archive.read_bytes())
    cursor = 0
    found = False
    while True:
        offset = payload.find(b"PK\x01\x02", cursor)
        if offset < 0:
            break
        name_length = int.from_bytes(payload[offset + 28 : offset + 30], "little")
        extra_length = int.from_bytes(payload[offset + 30 : offset + 32], "little")
        comment_length = int.from_bytes(payload[offset + 32 : offset + 34], "little")
        name_start = offset + 46
        name_end = name_start + name_length
        name = bytes(payload[name_start:name_end]).decode("utf-8")
        if name == member_name:
            if external_attr is not None:
                payload[offset + 38 : offset + 42] = external_attr.to_bytes(4, "little")
            if encrypted:
                flags = int.from_bytes(payload[offset + 8 : offset + 10], "little")
                payload[offset + 8 : offset + 10] = (flags | 0x1).to_bytes(2, "little")
            found = True
            break
        cursor = name_end + extra_length + comment_length
    if not found:
        raise AssertionError(f"ZIP 테스트 멤버를 찾지 못함: {member_name}")
    archive.write_bytes(payload)


def corrupt_first_compressed_byte(archive: Path, member_name: str) -> None:
    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo(member_name)
        payload = bytearray(archive.read_bytes())
    header = info.header_offset
    name_length = int.from_bytes(payload[header + 26 : header + 28], "little")
    extra_length = int.from_bytes(payload[header + 28 : header + 30], "little")
    compressed_start = header + 30 + name_length + extra_length
    payload[compressed_start] ^= 0x02
    archive.write_bytes(payload)


class ReleaseTests(unittest.TestCase):
    def test_gitignore_explicitly_excludes_generated_runtime_paths(self) -> None:
        rules = set(ROOT.joinpath(".gitignore").read_text(encoding="utf-8").splitlines())
        for expected in (
            ".venv/",
            ".venv-clean/",
            ".venv-*/",
            "venv/",
            "__pycache__/",
            "**/__pycache__/",
            "*.pyc",
            "/releases/",
        ):
            self.assertIn(expected, rules)

    def test_allowlisted_release_zip_excludes_private_and_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "asset-assistant-source.zip"
            members = build_release_zip(ROOT, archive)
            validated = validate_release_zip(archive)
            self.assertEqual(members, validated)
            self.assertFalse(any(name.startswith("releases/") for name in members))
            self.assertIn("README.md", members)
            self.assertIn("UI_DESIGN_SPEC.md", members)
            self.assertIn("WEB_DESIGN_SPEC.md", members)
            self.assertIn("asset_assistant/gui.py", members)
            self.assertIn("asset_assistant/paper.py", members)
            self.assertIn("asset_assistant/paper_persistence.py", members)
            self.assertIn("asset_assistant/storage_maintenance.py", members)
            self.assertIn("wealthy_server/app.py", members)
            self.assertIn("wealthy_server/container_entrypoint.py", members)
            self.assertIn("wealthy_server/local_adapters.py", members)
            self.assertIn("wealthy_server/local_credentials.py", members)
            self.assertIn("wealthy_server/local_runtime.py", members)
            self.assertIn("wealthy_server/offline_sample.json", members)
            self.assertIn("wealthy_server/static_app.py", members)
            self.assertIn("wealthy_server/worker.py", members)
            self.assertIn("deploy/compose.yaml", members)
            self.assertIn("deploy/Caddyfile.production.example", members)
            self.assertIn("docs/WEB_ARCHITECTURE.md", members)
            self.assertIn("docs/WEB_API.md", members)
            self.assertIn("docs/COORDINATION_REPORT.md", members)
            self.assertIn("docs/REQUIREMENTS_TRACEABILITY.md", members)
            self.assertIn("docs/DESIGN_COMPARISON.md", members)
            self.assertIn("docs/BACKUP_RESTORE.md", members)
            self.assertIn("design/references/12_반응형_화면_가이드.png", members)
            self.assertIn("design/references/SHA256SUMS", members)
            self.assertIn("web/frontend/src/App.tsx", members)
            self.assertIn("web/frontend/src/App.test.tsx", members)
            self.assertIn("web/frontend/src/design.test.ts", members)
            self.assertIn("web/frontend/src/notifications.test.ts", members)
            self.assertIn("web/frontend/src/notifications.tsx", members)
            self.assertIn("web/frontend/src/runtimeStatus.tsx", members)
            self.assertIn("web/frontend/public/sw.js", members)
            self.assertIn("web/frontend/package-lock.json", members)
            self.assertIn("tests/test_web_local_backup.py", members)
            self.assertIn("tests/test_web_local_runtime.py", members)
            self.assertIn("tests/test_web_local_service.py", members)
            self.assertIn("tests/test_web_postgres.py", members)
            self.assertIn("tests/test_paper.py", members)
            self.assertIn("tests/test_paper_persistence.py", members)
            self.assertIn("tests/test_storage_maintenance.py", members)
            self.assertIn("tools/web_browser_smoke.mjs", members)
            self.assertIn("tools/web_local_backup.py", members)
            self.assertIn("tools/web_local_service.py", members)
            self.assertIn("tools/web_validate_deploy.py", members)
            for module in (
                "automation",
                "credential_store",
                "kr_providers",
                "market_data",
                "providers",
                "scheduler",
                "strategy",
                "strategy_ui",
            ):
                self.assertIn(f"asset_assistant/{module}.py", members)
            for test_module in (
                "credentials",
                "gui_automation",
                "kr_providers",
                "market_automation",
                "market_data_precision",
                "providers",
                "scheduler",
                "strategy",
                "strategy_gui",
                "strategy_ui",
                "strategy_workspace",
            ):
                self.assertIn(f"tests/test_{test_module}.py", members)
            self.assertIn("examples/offline_sample.json", members)
            self.assertEqual(
                {name for name in members if name.endswith(".json")},
                ALLOWED_JSON_MEMBERS,
            )
            forbidden_fragments = (
                ".venv",
                "venv/",
                "__pycache__",
                "user_data/",
                "reports/",
                ".egg-info/",
                "web/frontend/node_modules/",
                "web/frontend/dist/",
                "deploy/secrets/",
                "backups/",
                "design/screenshots/",
                "design/comparisons/",
            )
            self.assertFalse(
                any(fragment in name for name in members for fragment in forbidden_fragments)
            )
            self.assertFalse(any(name.endswith((".pyc", ".db", ".sqlite", ".env")) for name in members))
            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                for info in bundle.infolist():
                    file_type = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
                    self.assertIn(file_type, (0, stat.S_IFREG))
                    self.assertFalse(info.flag_bits & 0x1)

    def test_validator_wraps_corrupt_compressed_member_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "corrupt.zip"
            build_release_zip(ROOT, archive)
            corrupt_first_compressed_byte(archive, "PROJECT_RULES.md")
            with self.assertRaisesRegex(
                DomainValidationError,
                r"^배포 ZIP을 안전하게 검사할 수 없습니다\.$",
            ):
                validate_release_zip(archive)

    def test_validator_rejects_link_directory_and_encrypted_members(self) -> None:
        mutations = (
            ("symbolic link", {"external_attr": (stat.S_IFLNK | 0o777) << 16}),
            ("directory mode", {"external_attr": (stat.S_IFDIR | 0o755) << 16}),
            ("encrypted", {"encrypted": True}),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, (label, arguments) in enumerate(mutations):
                with self.subTest(member_type=label):
                    archive = Path(temporary) / f"unsafe-{index}.zip"
                    build_release_zip(ROOT, archive)
                    patch_central_member(archive, "README.md", **arguments)
                    with self.assertRaisesRegex(
                        DomainValidationError, "안전하지 않은 파일 유형"
                    ):
                        validate_release_zip(archive)

            directory_archive = Path(temporary) / "unsafe-directory-entry.zip"
            build_release_zip(ROOT, directory_archive)
            with zipfile.ZipFile(directory_archive, mode="a") as bundle:
                bundle.writestr("unexpected-directory/", b"")
            with self.assertRaisesRegex(
                DomainValidationError, "안전하지 않은 파일 유형"
            ):
                validate_release_zip(directory_archive)

    def test_validator_accepts_safe_cross_platform_regular_file_modes(self) -> None:
        safe_attributes = (
            0,
            0x20,
            0o644 << 16,
            (stat.S_IFREG | 0o644) << 16,
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, external_attr in enumerate(safe_attributes):
                with self.subTest(external_attr=external_attr):
                    archive = Path(temporary) / f"safe-mode-{index}.zip"
                    build_release_zip(ROOT, archive)
                    patch_central_member(
                        archive,
                        "README.md",
                        external_attr=external_attr,
                    )
                    self.assertEqual(set(validate_release_zip(archive)), ALLOWED_MEMBERS)

    def test_release_argument_error_does_not_echo_untrusted_value(self) -> None:
        sensitive = "110-123-456789"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "asset_assistant.release",
                "--output",
                "unused.zip",
                "--acct",
                sensitive,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        error = completed.stderr
        self.assertIn("입력 오류", error)
        self.assertNotIn(sensitive, error)
        self.assertNotIn("456789", error)
        self.assertNotIn("Traceback", error)

    def test_release_integrity_scope_is_documented(self) -> None:
        for document in (ROOT / "README.md", ROOT / "docs" / "WINDOWS_VALIDATION.md"):
            text = document.read_text(encoding="utf-8")
            self.assertIn("구조 검사", text)
            self.assertIn("외부 SHA-256", text)
            self.assertIn("서명", text)

    def test_unlisted_report_and_secret_like_files_cannot_enter_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            for member in ALLOWED_MEMBERS:
                source = ROOT / member
                destination = project / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            generated_report = project / "docs" / "사용자 생성 보고서.md"
            local_secret = project / "asset_assistant" / "local_secret.py"
            generated_db = project / "user_data" / "market.sqlite3"
            frontend_dependency = project / "web" / "frontend" / "node_modules" / "private.js"
            frontend_build = project / "web" / "frontend" / "dist" / "index.html"
            deploy_secret = project / "deploy" / "secrets" / "credential.key"
            encrypted_backup = project / "backups" / "user-data.backup"
            local_sqlite_backup = project / "user-data.wbackup"
            user_export = project / "reports" / "사용자-보고서.md"
            design_screenshot = project / "design" / "screenshots" / "실행-캡처.png"
            validation_artifact = project / "validation-artifacts" / "browser-state.json"
            local_cache = project / "asset_assistant" / "__pycache__" / "automation.pyc"
            egg_info = project / "asset_assistant.egg-info" / "PKG-INFO"
            plaintext_secret = project / ".env"
            private_key = project / "local-test-only.pem"
            virtualenv_files = (
                project / ".venv" / "bin" / "python",
                project / ".venv-clean" / "pyvenv.cfg",
                project / ".venv-scratch" / "Scripts" / "python.exe",
                project / "venv" / "bin" / "python",
            )
            sensitive_marker = f"PRIVATE_FIXTURE_{Path(temporary).name}"
            generated_report.write_text("가상 사용자 금융자료", encoding="utf-8")
            local_secret.write_text(sensitive_marker, encoding="utf-8")
            for private_file in (
                generated_db,
                frontend_dependency,
                frontend_build,
                deploy_secret,
                encrypted_backup,
                local_sqlite_backup,
                user_export,
                design_screenshot,
                validation_artifact,
                local_cache,
                egg_info,
                plaintext_secret,
                private_key,
                *virtualenv_files,
            ):
                private_file.parent.mkdir(parents=True, exist_ok=True)
                private_file.write_text(sensitive_marker, encoding="utf-8")
            archive = Path(temporary) / "asset-assistant-source.zip"
            members = build_release_zip(project, archive)
            self.assertNotIn("docs/사용자 생성 보고서.md", members)
            self.assertNotIn("asset_assistant/local_secret.py", members)
            self.assertNotIn("user_data/market.sqlite3", members)
            self.assertNotIn("asset_assistant/__pycache__/automation.pyc", members)
            self.assertNotIn("asset_assistant.egg-info/PKG-INFO", members)
            for generated in (
                "web/frontend/node_modules/private.js",
                "web/frontend/dist/index.html",
                "deploy/secrets/credential.key",
                "backups/user-data.backup",
                "user-data.wbackup",
                "reports/사용자-보고서.md",
                "design/screenshots/실행-캡처.png",
                "validation-artifacts/browser-state.json",
            ):
                self.assertNotIn(generated, members)
            for forbidden in (
                ".env",
                "local-test-only.pem",
                ".venv/bin/python",
                ".venv-clean/pyvenv.cfg",
                ".venv-scratch/Scripts/python.exe",
                "venv/bin/python",
            ):
                self.assertNotIn(forbidden, members)
            with zipfile.ZipFile(archive) as bundle:
                archived_payload = b"".join(bundle.read(name) for name in members)
            self.assertNotIn(sensitive_marker.encode("utf-8"), archived_payload)

    def test_validator_rejects_an_unlisted_archive_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "tampered.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("docs/사용자 생성 보고서.md", "가상 사용자 금융자료")
            with self.assertRaises(DomainValidationError):
                validate_release_zip(archive)

    def test_validator_rejects_generated_or_secret_web_members(self) -> None:
        unexpected_members = (
            "web/frontend/node_modules/library/index.js",
            "web/frontend/dist/index.html",
            "deploy/secrets/session.key",
            "backups/wealthy.backup",
            "wealthy-local.wbackup",
            "reports/user-report.md",
            "design/screenshots/dashboard.png",
            "validation-artifacts/browser-state.json",
            "backups/",
            )
        with tempfile.TemporaryDirectory() as temporary:
            for index, unexpected in enumerate(unexpected_members):
                with self.subTest(member=unexpected):
                    archive = Path(temporary) / f"unexpected-{index}.zip"
                    build_release_zip(ROOT, archive)
                    with zipfile.ZipFile(archive, "a") as bundle:
                        bundle.writestr(unexpected, b"synthetic-test-value")
                    with self.assertRaises(DomainValidationError):
                        validate_release_zip(archive)

    def test_validator_rejects_unlisted_json_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "unexpected-json.zip"
            build_release_zip(ROOT, archive)
            with zipfile.ZipFile(archive, "a") as bundle:
                bundle.writestr("web/frontend/private-config.json", b"{}")
            with self.assertRaises(DomainValidationError):
                validate_release_zip(archive)

    def test_release_rejects_symlink_loop_without_path_echo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sensitive_name = "민감-110-123-456789.zip"
            output = Path(temporary) / sensitive_name
            try:
                output.symlink_to(output.name)
            except OSError as exc:
                self.skipTest(f"테스트 파일시스템이 심볼릭 링크를 지원하지 않음: {exc}")
            with self.assertRaises(DomainValidationError) as raised:
                build_release_zip(ROOT, output)
            message = str(raised.exception)
            self.assertNotIn(str(output), message)
            self.assertNotIn(sensitive_name, message)
            self.assertNotIn("456789", message)


if __name__ == "__main__":
    unittest.main()
