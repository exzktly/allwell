#!/bin/bash
# SessionStart hook — make a fresh Claude Code on the web container able to run
# the linter and the fast lane.
#
# A remote session starts from a clean clone in a container that has none of the
# test stack: no pytest, no PySide6, and none of the system GL/EGL libraries Qt
# needs even under the offscreen QPA platform. Without them `ruff check .` works
# but every test collection dies at `import PySide6` with
# "libEGL.so.1: cannot open shared object file".
#
# The steps mirror .github/workflows/ci.yml's setup, so a web session and CI
# install the same pinned versions from the same file.
#
# NOT installed here: the ML stack (TensorFlow + StarDist, Torch + Cellpose).
# It is several GB, only the `slow` segmentation tests need it, and
# tests/requirements-dev.txt deliberately excludes it — see
# _Docs/Markdowns/ARCHITECTURE.md §10.5.
#
# Local (non-remote) runs exit immediately: a developer machine has its own
# environment, and this must never touch it.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

log() { echo "[session-start] $*"; }

# ── Qt system libraries ──────────────────────────────────────────────────────
# Checked via ldconfig so a cached container skips the apt round-trip entirely.
if ! ldconfig -p 2>/dev/null | grep -q "libEGL.so.1"; then
  log "installing Qt system libraries"
  SUDO=""
  [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get update -qq || true
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq \
    libegl1 libgl1 libxkbcommon0 libdbus-1-3
else
  log "Qt system libraries present"
fi

# ── Python test stack ────────────────────────────────────────────────────────
# Pinned to the versions the app ships against (tests/requirements-dev.txt).
if python3 -c "import pytest, PySide6" >/dev/null 2>&1; then
  log "test stack present"
else
  log "installing tests/requirements-dev.txt"
  python3 -m pip install --quiet --upgrade pip
  python3 -m pip install --quiet -r tests/requirements-dev.txt
fi

# ── Linter ───────────────────────────────────────────────────────────────────
# Same pin CI enforces; a version drift here would report different findings.
if ! command -v ruff >/dev/null 2>&1; then
  log "installing ruff"
  python3 -m pip install --quiet "ruff==0.15.8"
fi

log "ready"
