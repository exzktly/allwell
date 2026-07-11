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

# Build-time FEATURE selection: choose which features go in the binary with a
# profile manifest (profiles/<name>.toml). The spec omits excluded features'
# source + their unique heavy deps (e.g. a StarDist-only build drops torch /
# torchvision / cellpose) and bakes the profile so the frozen app matches.
# Unset = the full app. Example (thin build — Plotting / Sample Definitions /
# Segmentation only):
#   ALLWELL_PROFILE=thin scripts/build_executable.sh
if [[ -n "${ALLWELL_PROFILE:-}" && "${ALLWELL_PROFILE}" != "full" ]]; then
    echo "  (ALLWELL_PROFILE=${ALLWELL_PROFILE} — building a feature subset)"
fi
echo "Running PyInstaller against $SPEC_FILE ..."
pyinstaller "$SPEC_FILE" --noconfirm --log-level "${ALLWELL_LOG_LEVEL:-INFO}"

if [[ -d "dist/AllWell.app" ]]; then
    echo "✓ Build complete: dist/AllWell.app"
elif [[ -d "dist/AllWell" ]]; then
    echo "✓ Build complete: dist/AllWell/"
else
    echo "ERROR: build finished but neither dist/AllWell.app nor dist/AllWell/ was produced." >&2
    exit 1
fi
