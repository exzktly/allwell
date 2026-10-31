#!/usr/bin/env bash
# The version check CI runs: HEAD's version.py against its first parent's must be exactly one increment.
#
# On a pull request HEAD is GitHub's merge ref, whose first parent is the base branch as it stands when the job
# runs, not when the author last fetched it; on a push to main HEAD is the merge itself and its first parent the
# main it landed on. scripts/preflight.sh checks the same thing against the author's origin/main, which is stale
# exactly when it matters: two pull requests open at once each bump to the same next number, each passes, and the
# second merges with no increment of its own. Seven of the 34 pull requests merged between 2026-10-28 and
# 2026-10-31 did.
#
# The pull-request half catches a collision whenever the job runs after the sibling merged; the push half makes a
# collision that slipped past it a red main rather than a silent one. Requiring branches to be up to date before
# merging (a repository setting) closes the gap between them, because it re-runs this job after the merge-in.
set -euo pipefail

version_of() {
    git show "$1:version.py" 2>/dev/null | sed -n 's/^__version__ = "\(.*\)"/\1/p'
}

base=$(version_of HEAD^1)
head=$(version_of HEAD)
verdict=$(python "$(dirname "$0")/version_delta.py" "$base" "$head")
case "$verdict" in
    ONE)  echo "version.py: $base -> $head, one increment" ;;
    SAME) echo "::error file=version.py::version.py is $head on both sides of this merge: no increment of its own." \
               "Every pull request bumps the version once (CLAUDE.md §6); if a sibling pull request took this number" \
               "first, merge main in and bump again."
          exit 1 ;;
    *)    echo "::error file=version.py::version.py moved $base -> $head ($verdict); a pull request moves it by" \
               "exactly one increment (CLAUDE.md §6)."
          exit 1 ;;
esac
