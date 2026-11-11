#!/bin/bash
# SessionStart hook for `resume` and `compact` only (the matcher in
# .claude/settings.json): tell the session to re-read its open PRs.
#
# A PR subscription delivers CI and review events into the session, but events
# that fire while the session's worker is being replaced can be lost outright.
# On PR #1302 three failed CI jobs were never delivered after a worker restart,
# and nothing was queued afterwards, so the failures sat unreported until the
# user found them. A compaction has the same effect from the other side: the
# session's memory of which PRs it is watching is reduced to a summary.
#
# CLAUDE.md section 6 says what to do on resume. This hook exists because a rule
# that has to be remembered is weakest at exactly this moment, when the
# session's context is thinnest. A SessionStart hook's standard output is added
# to the session's context, so these lines are read every time, whether or not
# anyone remembers the rule.
#
# It only prints. It never fails the session: no `set -e`, and every command
# that could fail falls back to a placeholder.

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" 2>/dev/null || true

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"

cat <<EOF
[resume-check] This session resumed or was compacted. PR events that fired meanwhile may have been lost.
[resume-check] Before anything else, for every open PR this session owns (this branch is '${branch}'):
[resume-check]   1. ReadNotifications, until it reports 0 remaining.
[resume-check]   2. subscribe_pr_activity again (idempotent; it says if another session holds the PR).
[resume-check]   3. pull_request_read get_check_runs: act on any failed job, whether or not an event arrived.
[resume-check]   4. pull_request_read get and read mergeable_state; confirm with: git fetch origin main && git merge-tree --write-tree HEAD origin/main
[resume-check]   5. Make sure a send_later check-in is armed for the PR (CLAUDE.md section 6).
EOF
exit 0
