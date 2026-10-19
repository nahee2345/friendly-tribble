#!/usr/bin/env bash
# Stage10 non-destructive validation. User DB and backups are not modified.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
python3 -m pytest -q tests/test_stage9_catalog_import.py tests/test_stage10_offline_research.py tests/test_stage10_screener.py tests/test_stage10_news_evidence.py tests/test_stage10_backtest.py tests/test_stage10_corporate_actions.py tests/test_stage10_dashboard_fallback.py tests/test_stage10_navigation_contract.py tests/test_stage10_news_manual_assets.py tests/test_stage10_settings_placement.py
bash scripts/stage7_acceptance.sh
if ! command -v npm >/dev/null 2>&1; then
  echo "NOT VERIFIED: npm unavailable" >&2; exit 2
fi
if [[ ! -d web/frontend/node_modules ]]; then
  echo "NOT VERIFIED: frontend dependencies unavailable" >&2; exit 2
fi
npm --prefix web/frontend run build
echo "Automated tests completed. Manual Ubuntu browser inspection remains necessary."
