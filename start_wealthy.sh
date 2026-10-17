#!/usr/bin/env bash
# Wealthy Stage 1: persistent local-only launcher.
set -Eeuo pipefail
cd "$(dirname "$0")"
DATA_DIR="/home/ww/wealthy-stage1-data/state"
MARKET_DB="/home/ww/wealthy-stage1-data/market.sqlite3"
VENV=".venv"
if [ ! -d wealthy_server ] || [ ! -f pyproject.toml ]; then echo "Wealthy source files are missing" >&2; exit 2; fi
if [ ! -f "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -e '.[web]'
fi
PY="$PWD/$VENV/bin/python"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
if [ ! -d web/frontend/node_modules ]; then npm --prefix web/frontend ci; fi
# Rebuild after every update; cached node_modules avoids repeated downloads.
npm --prefix web/frontend run build
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"
# Read-only inspection; never resets an existing database, account or password.
USER_COUNT="$("$PY" - "$DATA_DIR/web-state.sqlite3" <<'PY'
import sqlite3, sys, pathlib
p=pathlib.Path(sys.argv[1])
if not p.exists():
    print('0')
    raise SystemExit(0)
if p.is_symlink() or not p.is_file():
    raise SystemExit('Unsafe state database')
with sqlite3.connect(p.as_uri()+'?mode=ro', uri=True) as db:
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'users' not in tables:
        raise SystemExit('Existing database has no users table; preserving DB; manual diagnosis required')
    result=db.execute("PRAGMA quick_check").fetchone()
    if result != ('ok',):
        raise SystemExit('Database integrity check failed; preserving DB')
    print(db.execute('SELECT COUNT(*) FROM users').fetchone()[0])
PY
)"
if [ "$USER_COUNT" = "0" ]; then
  if [ ! -t 0 ]; then echo "First administrator creation requires an interactive terminal" >&2; exit 2; fi
  echo "최초 관리자 계정이 없습니다. 지금 한 번만 등록합니다."
  "$PY" -m wealthy_server.local_runtime create-admin --data-dir "$DATA_DIR" --market-database "$MARKET_DB"
elif [ "$USER_COUNT" != "1" ]; then
  echo "관리자 계정 상태가 예상과 다릅니다. DB를 변경하지 않고 종료합니다." >&2
  exit 2
fi
exec "$PY" -m wealthy_server.local_runtime serve --data-dir "$DATA_DIR" --market-database "$MARKET_DB" --host 127.0.0.1 --port 8000
