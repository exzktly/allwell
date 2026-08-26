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
# feature subset), e.g. ``dist/AllWell-<version>.app`` on macOS or the folder
# bundle ``dist/AllWell-<version>/`` on Linux / Windows; a thin build is
# ``dist/AllWell-<version>-thin[.app]``. The name is computed by the .spec from
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
# Unset = the full app. Example (thin build — Plotting / Statistics / Home /
# Segmentation + Image Table only):
#   ALLWELL_PROFILE=thin scripts/build_executable.sh
if [[ -n "${ALLWELL_PROFILE:-}" && "${ALLWELL_PROFILE}" != "full" ]]; then
    echo "  (ALLWELL_PROFILE=${ALLWELL_PROFILE} — building a feature subset)"
fi
# Build-time THEME selection (soft default): choose which UI theme the frozen app
# starts on. The spec bakes ``allwell_theme.txt`` into the bundle and the launcher
# setdefault()s ALLWELL_THEME from it, so the shipped exe defaults to this theme
# while a runtime ``ALLWELL_THEME`` / ``--theme`` still overrides. Unset leaves
# the app on theme.DEFAULT_THEME, which is 'grey'.
# Example (a blue-by-default build):
#   ALLWELL_THEME=blue scripts/build_executable.sh
if [[ -n "${ALLWELL_THEME:-}" ]]; then
    echo "  (ALLWELL_THEME=${ALLWELL_THEME} — baking as the default theme; runtime-overridable)"
fi
echo "Running PyInstaller against $SPEC_FILE ..."
pyinstaller "$SPEC_FILE" --noconfirm --log-level "${ALLWELL_LOG_LEVEL:-INFO}"

# The bundle name is versioned (+ profile), so ask the same helper the spec used
# (scripts/profile_build.bundle_name) what to look for. Globbing dist/AllWell-*
# would also match the bundles of earlier versions left behind by a build without
# --clean, and report one of those as "complete".
_bundle_name="$(python3 -c "import os, sys; sys.path[:0] = ['.', 'scripts']; from profile_build import bundle_name; from version import __version__; print(bundle_name(__version__, os.environ.get('ALLWELL_PROFILE', '')))" || true)"
if [[ -z "$_bundle_name" ]]; then
    echo "ERROR: PyInstaller succeeded but the expected bundle name could not be resolved" >&2
    echo "       from version.py / scripts/profile_build.py. Check dist/ by hand." >&2
    exit 1
fi

# Prefer the macOS .app; fall back to the Linux/Windows onedir folder.
if [[ -d "dist/${_bundle_name}.app" ]]; then
    echo "✓ Build complete: dist/${_bundle_name}.app"
elif [[ -d "dist/${_bundle_name}" ]]; then
    echo "✓ Build complete: dist/${_bundle_name}/"
else
    echo "ERROR: build finished but neither dist/${_bundle_name}.app nor dist/${_bundle_name}/ was produced." >&2
    exit 1
fi
