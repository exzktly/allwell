"""The resume hook tells a resumed or compacted session to re-read its open PRs.

PR events that fire while a session's worker is being replaced can be lost: on
PR #1302 three failed CI jobs were never delivered after a worker restart, and
nothing was queued afterwards. CLAUDE.md section 6 says what a resumed session
must do about that, and ``.claude/hooks/resume-check.sh`` prints those steps
into the session's context on every resume and compaction, so they do not
depend on being remembered.

These tests hold the two halves that can silently stop working: the hook's
registration (a matcher that no longer names ``resume`` runs it never), and
what it prints (a step dropped from the text is a step nobody is told to take).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "resume-check.sh"
HOOK_COMMAND = "$CLAUDE_PROJECT_DIR/.claude/hooks/resume-check.sh"


def _registrations() -> list[dict]:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    return [entry for entry in settings["hooks"]["SessionStart"]
            if any(h.get("command") == HOOK_COMMAND for h in entry.get("hooks", []))]


def test_the_hook_is_registered_for_resume_and_compact_only():
    entries = _registrations()
    assert len(entries) == 1, f"expected one SessionStart entry running the resume hook, found {entries}"
    sources = set(entries[0].get("matcher", "").split("|"))
    assert sources == {"resume", "compact"}, sources


def _run(project_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    return subprocess.run(["bash", str(HOOK)], capture_output=True, text=True, env=env, timeout=30)


def test_the_hook_prints_every_step_and_names_the_branch():
    result = _run(ROOT)
    assert result.returncode == 0, result.stderr
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    out = result.stdout
    assert f"this branch is '{branch}'" in out
    for step in ("ReadNotifications", "subscribe_pr_activity", "get_check_runs", "mergeable_state",
                 "git merge-tree --write-tree HEAD origin/main", "send_later"):
        assert step in out, f"the resume hook no longer mentions {step!r}:\n{out}"


def test_the_hook_never_fails_the_session_outside_a_repository(tmp_path):
    """A hook that exits non-zero is reported as failed; this one must print and succeed even where git cannot
    answer."""
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "this branch is 'unknown'" in result.stdout
