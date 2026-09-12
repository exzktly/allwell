#!/usr/bin/env bash
# bootstrap_remote.sh — one-time setup for an AllWell remote-execution host.
#
# Thin wrapper around services/remote_bootstrap.py — the real logic lives
# there so the Linux/macOS/Git Bash path and the native Windows
# PowerShell path (bootstrap_remote.ps1) share a single source of truth.
#
# Usage:
#   scripts/bootstrap_remote.sh <ssh-alias> [--force]

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/../services/remote_bootstrap.py" "$@"
