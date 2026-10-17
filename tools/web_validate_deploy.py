"""Static checks for deployment files; this command does not start containers."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from asset_assistant.cli import _SafeArgumentParser


REQUIRED_FILES = (
    "deploy/compose.yaml",
    "deploy/compose.production.yaml",
    "deploy/Dockerfile.backend",
    "deploy/Dockerfile.frontend",
    "deploy/Caddyfile.local",
    "deploy/Caddyfile.production.example",
)


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        description="컨테이너를 시작하지 않고 Wealthy 배포 구성을 검사합니다."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failures: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            failures.append(f"missing:{relative}")

    compose = root / "deploy/compose.yaml"
    rendered: dict[str, object] = {}
    if compose.is_file():
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose),
                "config",
                "--format",
                "json",
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            failures.append("compose-config-invalid")
        else:
            try:
                rendered = json.loads(result.stdout)
            except (json.JSONDecodeError, UnicodeError):
                failures.append("compose-config-not-json")

    services = rendered.get("services", {}) if isinstance(rendered, dict) else {}
    if isinstance(services, dict):
        postgres = services.get("postgres", {})
        if isinstance(postgres, dict) and postgres.get("ports"):
            failures.append("postgres-host-port-published")
        if isinstance(postgres, dict) and "provider_egress" in postgres.get("networks", {}):
            failures.append("postgres-must-not-have-provider-egress")
        backend = services.get("backend", {})
        if isinstance(backend, dict) and backend.get("ports"):
            failures.append("backend-host-port-published")
        expected = {"postgres", "backend", "worker", "caddy"}
        if not expected.issubset(services):
            failures.append("required-service-missing")
        worker = services.get("worker", {})
        if isinstance(worker, dict) and "provider_egress" not in worker.get("networks", {}):
            failures.append("worker-provider-egress-missing")

    production_compose = root / "deploy/compose.production.yaml"
    if compose.is_file() and production_compose.is_file():
        production_environment = dict(os.environ)
        production_environment["WEALTHY_DOMAIN"] = "wealthy.example.invalid"
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose),
                "-f",
                str(production_compose),
                "config",
                "--format",
                "json",
            ],
            cwd=root,
            env=production_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            failures.append("production-compose-config-invalid")
        else:
            try:
                production_rendered = json.loads(result.stdout)
            except (json.JSONDecodeError, UnicodeError):
                failures.append("production-compose-config-not-json")
            else:
                production_services = production_rendered.get("services", {})
                production_backend = (
                    production_services.get("backend", {})
                    if isinstance(production_services, dict)
                    else {}
                )
                environment = (
                    production_backend.get("environment", {})
                    if isinstance(production_backend, dict)
                    else {}
                )
                allowed_hosts = {
                    value.strip()
                    for value in str(environment.get("WEALTHY_ALLOWED_HOSTS", "")).split(",")
                    if value.strip()
                }
                if not {
                    "wealthy.example.invalid",
                    "localhost",
                    "127.0.0.1",
                }.issubset(allowed_hosts):
                    failures.append("production-health-host-not-allowed")
                if environment.get("WEALTHY_SECURE_COOKIES") != "1":
                    failures.append("production-secure-cookies-disabled")

    production = root / "deploy/Caddyfile.production.example"
    if production.is_file():
        content = production.read_text(encoding="utf-8")
        for required in ("{$WEALTHY_DOMAIN}", "Strict-Transport-Security", "reverse_proxy"):
            if required not in content:
                failures.append("production-caddy-missing-security")
                break

    secret_dir = root / "deploy/secrets"
    forbidden = {
        "postgres_password",
        "database_url",
        "master_key",
        "backup_key",
    }
    present = sorted(name for name in forbidden if (secret_dir / name).exists())
    for name in present:
        target = secret_dir / name
        try:
            info = target.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                failures.append("runtime-secret-unsafe-file-type")
                break
            if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
                failures.append("runtime-secret-permission-too-broad")
                break
        except OSError:
            failures.append("runtime-secret-unreadable-metadata")
            break
    for name in forbidden:
        example = secret_dir / f"{name}.example"
        if not example.is_file() or example.stat().st_size != 0:
            failures.append("secret-example-must-be-empty")
            break

    payload = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "runtime_secret_file_count": len(present),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
