#!/usr/bin/env bash
# scripts/build_executable.sh
# ---------------------------
# Build a PyInstaller bundle for All-Well from the repository root.
#
# Usage:
#   pip install --no-build-isolation -r _Docs/requirements.txt
#   scripts/build_executable.sh
#
# The output lands in ./dist/ and carries the app version (and profile name for a
# feature subset), e.g. ``dist/AllWell-8.8.8.app`` on macOS or the folder bundle
# ``dist/AllWell-8.8.8/`` on Linux / Windows; a thin build is
# ``dist/AllWell-8.8.8-thin[.app]``. The name is computed by the .spec from
# version.py + ALLWELL_PROFILE (scripts/profile_build.bundle_name).
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
# Build-time THEME selection (soft default): choose which UI theme the frozen app
# starts on. The spec bakes ``allwell_theme.txt`` into the bundle and the launcher
# setdefault()s ALLWELL_THEME from it, so the shipped exe defaults to this theme
# while a runtime ``ALLWELL_THEME`` / ``--theme`` still overrides. Unset = 'blue'.
# Example (a grey-by-default build):
#   ALLWELL_THEME=grey scripts/build_executable.sh
if [[ -n "${ALLWELL_THEME:-}" ]]; then
    echo "  (ALLWELL_THEME=${ALLWELL_THEME} — baking as the default theme; runtime-overridable)"
fi
echo "Running PyInstaller against $SPEC_FILE ..."
pyinstaller "$SPEC_FILE" --noconfirm --log-level "${ALLWELL_LOG_LEVEL:-INFO}"

# The bundle name is versioned (+ profile), so discover it rather than hardcode.
# Prefer the macOS .app; fall back to the Linux/Windows onedir folder.
_app_bundle="$(ls -d dist/AllWell-*.app 2>/dev/null | head -n1 || true)"
_dir_bundle="$(ls -d dist/AllWell-*/ 2>/dev/null | grep -v '\.app/$' | head -n1 || true)"
if [[ -n "$_app_bundle" && -d "$_app_bundle" ]]; then
    echo "✓ Build complete: $_app_bundle"
elif [[ -n "$_dir_bundle" && -d "$_dir_bundle" ]]; then
    echo "✓ Build complete: $_dir_bundle"
else
    echo "ERROR: build finished but no dist/AllWell-*.app or dist/AllWell-*/ bundle was produced." >&2
    exit 1
fi
