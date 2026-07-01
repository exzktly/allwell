#!/usr/bin/env bash
# scripts/build_executable.sh
# ---------------------------
# Build a PyInstaller bundle for All-Well from the repository root.
#
# Usage:
#   pip install --no-build-isolation -r _Docs/requirements.txt
#   scripts/build_executable.sh
#
# The output lands in ./dist/. On macOS the result is ``dist/AllWell.app``;
# on Linux / Windows it's a folder bundle ``dist/AllWell/``.
#
# Pass ``--clean`` as the first argument to wipe build/ and dist/ before
# starting, e.g.
#   scripts/build_executable.sh --clean

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( dirname "$SCRIPT_DIR" )"
cd "$REPO_ROOT"

SPEC_FILE="_Docs/_Installation/all_well.spec"
if [[ ! -f "$SPEC_FILE" ]]; then
    echo "ERROR: spec file not found at $SPEC_FILE" >&2
    exit 1
fi

if [[ "${1:-}" == "--clean" ]]; then
    echo "Cleaning build/ and dist/ ..."
    rm -rf build dist
fi

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "ERROR: pyinstaller not on PATH." >&2
    echo "Run: pip install --no-build-isolation -r _Docs/requirements.txt" >&2
    exit 1
fi

echo "Running PyInstaller against $SPEC_FILE ..."
pyinstaller "$SPEC_FILE" --noconfirm --log-level WARN

if [[ -d "dist/AllWell.app" ]]; then
    echo "✓ Build complete: dist/AllWell.app"
elif [[ -d "dist/AllWell" ]]; then
    echo "✓ Build complete: dist/AllWell/"
else
    echo "ERROR: build finished but neither dist/AllWell.app nor dist/AllWell/ was produced." >&2
    exit 1
fi
