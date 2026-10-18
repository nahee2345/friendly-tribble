#!/usr/bin/env bash
# Wealthy Stage7 acceptance: TEST FIXTURES ONLY. NEVER deletes or restores user data.
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"
PYTHON_EXEC="${PYTHON_EXEC:-python3}"
printf '%s\n' 'Wealthy 7/7 acceptance (non-destructive)'
printf 'Repository: %s\n' "$ROOT_DIR"
printf '%s\n' 'Production SQLite files, WAL, SHM, user uploads and account settings are NOT touched.'

if ! command -v "$PYTHON_EXEC" >/dev/null 2>&1; then
  echo "Python not installed: $PYTHON_EXEC" >&2
  exit 2
fi
if ! "$PYTHON_EXEC" -m pytest --version >/dev/null 2>&1; then
  echo "pytest missing. Activate existing project .venv and retry. No automatic install." >&2
  exit 2
fi

export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
"$PYTHON_EXEC" -m pytest -q \
  tests/test_stage7_release.py \
  tests/test_stage4_portfolio.py \
  tests/test_stage5_accounting.py \
  tests/test_stage6_risk.py \
  tests/test_stage1_backup.py \
  tests/test_stage1_schema_migration.py \
  tests/test_stage1_controls_cards.py

if command -v npm >/dev/null 2>&1 && [[ -d web/frontend/node_modules ]]; then
  npm --prefix web/frontend run build
  printf '%s\n' 'PASS: Python offline simulation and React production build.'
else
  printf '%s\n' 'PARTIAL: Python tests passed; React build not run (npm/node_modules unavailable).'
  printf '%s\n' 'Run npm --prefix web/frontend ci then npm --prefix web/frontend run build when ready.'
fi

cat <<'EOF'
Manual Ubuntu Chrome checks still required:
- Sign in to http://localhost:8000/dashboard; verify no desktop home page scrolling.
- Check market/news/reference unavailable status; no fabricated quotes or stock recommendations.
- Open 자산·설정·보고서 > 계좌 관리 > 7단계 원장 통합 감사.
- Create paper-only backup then click '복원 전 안전검사'; no active restore.
- Confirm emergency pause defaults ON. Do not enable unattended paper fills merely to test appearance.
- Restart the server and verify original holdings, policy, order claims and account balance persist.
No live brokerage, AI provider or real-money account is connected by this script.
EOF
