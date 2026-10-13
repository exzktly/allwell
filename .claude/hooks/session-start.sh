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
# The stack lives in its own virtualenv, built from an interpreter the pins
# support, and that venv is put first on PATH for the session.
#
# Bare `python3` is whatever the image's default happens to be, and that moved
# to 3.13 underneath this hook: tests/requirements-dev.txt pins
# PySide6==6.7.3, which publishes no wheel for 3.13 (Requires-Python <3.13),
# so pip found no match, the hook exited 1, and the session started with no
# pytest, no PySide6 and no ruff — silently, since a failing SessionStart hook
# does not stop the session. CI runs 3.11 (.github/workflows/ci.yml), so that
# is the first choice; 3.12 is the newest the pins still support.
VENV="${ALLWELL_TEST_VENV:-$HOME/.allwell-test-venv}"
PY=""
for candidate in python3.11 python3.12; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  log "ERROR: neither python3.11 nor python3.12 is installed (python3 is" \
      "$(python3 --version 2>&1)). PySide6==6.7.3 needs Python <3.13, so the" \
      "test stack cannot be installed; add one of them to the environment."
  exit 1
fi

if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import pytest, PySide6" >/dev/null 2>&1; then
  log "test stack present ($("$VENV/bin/python" --version 2>&1), $VENV)"
else
  log "building $VENV with $PY and installing tests/requirements-dev.txt"
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
  # A venv's own pip upgrades cleanly, but the upgrade is still a convenience,
  # never a precondition: failing the line below is what matters.
  "$VENV/bin/python" -m pip install --quiet --upgrade pip || log "pip upgrade skipped"
  "$VENV/bin/python" -m pip install --quiet -r tests/requirements-dev.txt
fi

# ── Linter ───────────────────────────────────────────────────────────────────
# Same pin CI enforces; a version drift here would report different findings.
if [ ! -x "$VENV/bin/ruff" ]; then
  log "installing ruff"
  "$VENV/bin/python" -m pip install --quiet "ruff==0.15.8"
fi

# ── Session PATH ─────────────────────────────────────────────────────────────
# `scripts/test.sh` runs plain `python`, and `ruff` is called by name, so the
# venv has to come first on PATH for every later command in the session. A
# SessionStart hook persists environment for the session by appending exports
# to $CLAUDE_ENV_FILE.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VIRTUAL_ENV=\"$VENV\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$VENV/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
else
  log "CLAUDE_ENV_FILE is not set; run: export PATH=\"$VENV/bin:\$PATH\""
fi

log "ready"
