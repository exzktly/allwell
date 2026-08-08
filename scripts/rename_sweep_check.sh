#!/usr/bin/env bash
# After moving or renaming a module, find what a path substitution left behind.
#
# Replacing "domain/fold_change_controls" with "core/fold_change_controls_view"
# looked complete and was not. It left the bare basename intact in a directory
# tree and in a layer diagram, and it left a paragraph reading "It lives in
# `domain/`" directly above the freshly-updated path — a sentence contradicting
# itself inside the same commit, which is the failure CLAUDE.md §7.3 #6 already
# describes.
#
# Both misses come from grepping the qualified path only. This runs the two
# queries that find them:
#
#   1. the bare basename, anywhere (trees, diagrams, prose, docstrings)
#   2. the OLD directory name within a few lines of the NEW symbol name — the
#      query that surfaces a paragraph whose path was updated and whose claim
#      about where the file lives was not
#
# Usage:
#   scripts/rename_sweep_check.sh fold_change_controls fold_change_controls_view
#   scripts/rename_sweep_check.sh old_name new_name [<old-dir>]
#
# Reports; changes nothing. Exit status is 0 only when both queries are clean.
set -uo pipefail

cd "$(dirname "$0")/.."

OLD="${1:-}"
NEW="${2:-}"
OLD_DIR="${3:-}"
if [[ -z "$OLD" || -z "$NEW" ]]; then
    echo "usage: scripts/rename_sweep_check.sh <old_basename> <new_basename> [<old-dir>]" >&2
    exit 2
fi

# Where the old module used to live, so query 2 knows what stale claim to hunt.
# Inferred from git history when the caller does not say.
if [[ -z "$OLD_DIR" ]]; then
    OLD_PATH=$(git log --all --diff-filter=D --name-only --format= -- "*/$OLD.py" 2>/dev/null | head -1)
    [[ -n "$OLD_PATH" ]] && OLD_DIR=$(basename "$(dirname "$OLD_PATH")")
fi

EXCLUDES=(":(exclude).git" ":(exclude)*.lock" ":(exclude)**/__pycache__/**")
found=0

printf '\033[1m── 1. the bare basename %s (not just a qualified path)\033[0m\n' "$OLD"
# `\b` on both sides so `fold_change_controls_view` does not match `fold_change_controls`.
hits=$(git grep -n -E "\\b${OLD}\\b(?!_)" --perl-regexp -- . "${EXCLUDES[@]}" 2>/dev/null \
       || git grep -n -E "\\b${OLD}\\b" -- . "${EXCLUDES[@]}" 2>/dev/null | grep -v "${NEW}")
if [[ -n "$hits" ]]; then
    echo "$hits" | sed 's/^/   /'
    echo
    echo "   Each of these still names the old module. A tree entry or a diagram"
    echo "   writes the basename alone, so a path substitution never touched it."
    found=1
else
    echo "   clean"
fi

echo
if [[ -n "$OLD_DIR" ]]; then
    printf '\033[1m── 2. "%s" claimed within 5 lines of %s\033[0m\n' "$OLD_DIR" "$NEW"
    stale=$(git grep -n -C5 -E "\\b${NEW}\\b" -- . "${EXCLUDES[@]}" 2>/dev/null \
            | grep -E "[-:][0-9]+[-:].*\\b${OLD_DIR}/" || true)
    if [[ -n "$stale" ]]; then
        echo "$stale" | sed 's/^/   /'
        echo
        echo "   A paragraph near the new name still says the file lives in"
        echo "   ${OLD_DIR}/. Read the whole paragraph, not the line you changed."
        found=1
    else
        echo "   clean"
    fi
else
    printf '\033[1m── 2. skipped — could not infer the old directory\033[0m\n'
    echo "   Pass it as the third argument to run this query."
fi

echo
if (( found )); then
    printf '\033[31m   rename sweep incomplete\033[0m\n'
    exit 1
fi
printf '\033[32m   rename sweep clean\033[0m\n'
