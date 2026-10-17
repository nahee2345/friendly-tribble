#!/usr/bin/env bash
set -euo pipefail
SOURCE="${1:-/home/ww/Wealthy_Stage1_Web_04}"
TARGET="/home/ww/wealthy-github"
REPO="https://github.com/nahee2345/friendly-tribble.git"
[ -d "$SOURCE/wealthy_server" ] || { echo "소스 폴더 오류: $SOURCE"; exit 1; }
command -v rsync >/dev/null || { echo "rsync 필요: sudo apt install rsync"; exit 1; }
command -v git >/dev/null || { echo "git 필요"; exit 1; }
if [ ! -d "$TARGET/.git" ]; then
  [ ! -e "$TARGET" ] || { echo "기존 경로가 있어 중단: $TARGET"; exit 1; }
  git clone "$REPO" "$TARGET"
fi
# Copy only intentional source, documentation, and examples.
for name in asset_assistant wealthy_server tests tools web docs design examples; do
  if [ -d "$SOURCE/$name" ]; then
    mkdir -p "$TARGET/$name"
    rsync -a --exclude='.git/' --exclude='.venv/' --exclude='node_modules/' --exclude='dist/' --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='secrets/' --exclude='releases/' --exclude='*.sqlite3' --exclude='*.db' --exclude='.env' --exclude='*.key' --exclude='*.zip' "$SOURCE/$name/" "$TARGET/$name/"
  fi
done
for name in pyproject.toml PROJECT_RULES.md README.md WEB_DESIGN_SPEC.md UI_DESIGN_SPEC.md VALIDATION_LOG.md; do
  if [ -f "$SOURCE/$name" ] && [ "$name" != README.md ]; then cp -p "$SOURCE/$name" "$TARGET/$name"; fi
done
cd "$TARGET"
# Reject sensitive files even if the working tree has unexpected files.
git add -- asset_assistant wealthy_server tests tools web docs design examples pyproject.toml PROJECT_RULES.md WEB_DESIGN_SPEC.md UI_DESIGN_SPEC.md VALIDATION_LOG.md 2>/dev/null || true
if git diff --cached --name-only | grep -E '(^|/)(secrets|\.venv|node_modules|dist|releases)/|\.env|\.(db|sqlite3?|key|zip)$'; then
  echo "보안 검사 실패: 비공개 파일이 스테이징됨. 업로드 중단."
  exit 1
fi
git diff --cached --stat
if git diff --cached --quiet; then echo "업로드할 새 소스가 없습니다."; exit 0; fi
git -c user.name="Wealthy Maintainer" -c user.email="wealthy-local@users.noreply.github.com" commit -m "feat: import sanitized Wealthy Stage1 Web 04 source"
git push origin main
echo "GitHub 업로드 완료: $REPO"
