#!/usr/bin/env bash
# Alpha Forge setup: alpha-harness (base) + alpha-copilot (reference) + this blueprint.
# Usage (from the folder holding the three downloads, usually ~/Downloads):
#   bash -c "$(unzip -p alpha-forge-blueprint.zip scripts/setup_forge.sh)"
# Options: SRC=<folder with downloads>  DEST=<target>  SKIP_INSTALL=1 (no uv/pnpm installs)
set -euo pipefail

SRC="${SRC:-$PWD}"
DEST="${DEST:-$HOME/quant/alpha-forge}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing '$1'. Install it with: brew install $2"; exit 1; }; }

need git git
need unzip unzip
if [ "$SKIP_INSTALL" != "1" ]; then need uv uv; need pnpm pnpm; need node node; fi

BLUEPRINT="$SRC/alpha-forge-blueprint.zip"
[ -f "$BLUEPRINT" ] || { echo "Can't find $BLUEPRINT (run from your Downloads folder or set SRC=)."; exit 1; }
if [ -e "$DEST" ]; then echo "$DEST already exists. Move it away or set DEST=<new path>."; exit 1; fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Find a project either as a folder or as a zip whose top folder contains <marker>.
locate() { # name marker
  local name="$1" marker="$2" zip inner
  if [ -d "$SRC/$name" ] && [ -e "$SRC/$name/$marker" ]; then echo "$SRC/$name"; return; fi
  zip="$(ls "$SRC"/"$name"*.zip 2>/dev/null | head -1 || true)"
  if [ -n "$zip" ]; then
    mkdir -p "$TMP/$name" && unzip -q "$zip" -d "$TMP/$name"
    inner="$(find "$TMP/$name" -maxdepth 3 -name "$marker" | head -1 || true)"
    if [ -n "$inner" ]; then dirname "$inner"; return; fi
  fi
  echo ""
}

say "1/6 Locating alpha-harness and alpha-copilot in $SRC"
AH="$(locate alpha-harness backend)"
AC="$(locate alpha-copilot CLAUDE.md)"
[ -n "$AH" ] || { echo "alpha-harness not found (folder or alpha-harness*.zip) in $SRC"; exit 1; }
[ -n "$AC" ] || { echo "alpha-copilot not found (folder or alpha-copilot*.zip) in $SRC"; exit 1; }
echo "  alpha-harness: $AH"
echo "  alpha-copilot: $AC"

say "2/6 Creating $DEST from alpha-harness"
mkdir -p "$(dirname "$DEST")"
cp -R "$AH" "$DEST"
rm -rf "$DEST/.git"

say "3/6 Adding alpha-copilot as read-only reference and unpacking the blueprint"
mkdir -p "$DEST/reference"
cp -R "$AC" "$DEST/reference/alpha-copilot"
rm -rf "$DEST/reference/alpha-copilot/.venv" "$DEST/reference/alpha-copilot/.git"
unzip -q -o "$BLUEPRINT" -d "$DEST"

cat >> "$DEST/.gitignore" <<'EOF'

# Alpha Forge
reference/
.env
*.db
*.db-wal
*.db-shm
cookies.json
catalog_*.json
EOF

say "4/6 Git baseline"
cd "$DEST"
git init -q
git add -A
if git -c core.hooksPath=/dev/null commit -q -m "alpha-forge: alpha-harness base + blueprint"; then
  echo "  committed"
else
  echo "  Commit skipped: set your git identity, then commit:"
  echo "    git config --global user.name 'Your Name'; git config --global user.email you@example.com"
  echo "    git add -A && git commit -m 'alpha-forge baseline'"
fi

if [ "$SKIP_INSTALL" = "1" ]; then
  say "5/6 and 6/6 skipped (SKIP_INSTALL=1)"
else
  say "5/6 Backend: Python 3.14 + dependencies (uv)"
  uv python install 3.14
  (cd backend && uv sync)
  say "6/6 Frontend dependencies (pnpm)"
  (cd frontend && pnpm install)
fi

say "Done. Next:"
echo "  cd $DEST && code ."
echo "  Then open Claude Code and follow docs/forge/BUILD_PLAYBOOK.md from Phase 0."
