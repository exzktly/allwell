"""Every place that installs Qt's system libraries goes through the mirror-aware script.

On 2026-11-11 Ubuntu's own servers, the only apt sources the GitHub runner image
is configured with, were unreachable for well over half an hour, and every pytest
job on every open PR failed in its "Install Qt system libraries" step before a
line of Python ran. ``scripts/install_qt_system_libs.sh`` fetches through
independent Ubuntu mirrors first and Ubuntu's own archive last, verifying every
index against Ubuntu's signing key.

The fix only holds while every install site uses it: one workflow left calling
``apt-get install libegl1`` directly fails the next outage exactly as before, and
nothing else would notice until then. These tests hold the sites, and the parts
of the script that make it a fallback rather than a renamed ``apt-get``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_qt_system_libs.sh"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
SESSION_HOOK = ROOT / ".claude" / "hooks" / "session-start.sh"


def _run_blocks(workflow: Path) -> list[tuple[str, str]]:
    data = yaml.safe_load(workflow.read_text())
    blocks = []
    for job_name, job in (data.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if "run" in step:
                blocks.append((f"{workflow.name}::{job_name}::{step.get('name', '?')}", step["run"]))
    return blocks


def _code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_no_workflow_or_hook_installs_packages_with_apt_directly():
    offenders = []
    sources = [(name, text) for wf in WORKFLOWS for name, text in _run_blocks(wf)]
    sources.append((str(SESSION_HOOK.relative_to(ROOT)), SESSION_HOOK.read_text()))
    for name, text in sources:
        for line in _code_lines(text):
            if re.search(r"\bapt(-get)?\s+(update|install)\b", line):
                offenders.append(f"{name}: {line.strip()}")
    assert not offenders, ("these call apt against the machine's own sources, which an Ubuntu outage takes down; "
                           "use scripts/install_qt_system_libs.sh:\n" + "\n".join(offenders))


def test_every_pytest_job_installs_the_qt_libraries_through_the_script():
    """A job that runs pytest needs libEGL, so it must install it — through the script."""
    missing = []
    for wf in WORKFLOWS:
        data = yaml.safe_load(wf.read_text())
        for job_name, job in (data.get("jobs") or {}).items():
            runs = [s.get("run", "") for s in job.get("steps") or []]
            if any("scripts/test.sh" in r or "pytest" in r for r in runs) \
                    and not any("scripts/install_qt_system_libs.sh" in r for r in runs):
                missing.append(f"{wf.name}::{job_name}")
    assert not missing, f"jobs that run tests without installing Qt's libraries through the script: {missing}"
    assert "scripts/install_qt_system_libs.sh" in SESSION_HOOK.read_text()


def test_the_script_uses_its_own_mirror_list_with_ubuntu_last_and_verifies_signatures():
    text = SCRIPT.read_text()
    default = re.search(r'MIRRORS="\$\{ALLWELL_APT_MIRRORS:-([^}]*)\}"', text)
    assert default, "the default mirror list is gone"
    mirrors = default.group(1).split()
    assert len(mirrors) >= 3, mirrors
    assert "ubuntu.com" in mirrors[-1], f"Ubuntu's own archive should be the last resort: {mirrors}"
    assert not any("ubuntu.com" in m for m in mirrors[:-1]), f"only the last entry may be Ubuntu's own: {mirrors}"
    # The machine's own sources (Ubuntu's servers and third-party repos) are not consulted.
    assert "Dir::Etc::SourceList=$WORK/sources.list" in text
    assert "Dir::Etc::SourceParts=$WORK/parts" in text
    # A mirror is transport only: indexes are checked against Ubuntu's archive key.
    assert "signed-by=$KEYRING" in text and "ubuntu-archive-keyring.gpg" in text
    assert "mirror+file:" in text


def test_the_cleanup_removes_what_sudo_apt_wrote_and_never_fails_the_install():
    """On a CI runner the script runs as an unprivileged user and calls apt through sudo, so the lists apt writes
    into the script's temp dir are root's. A plain ``rm -rf`` there failed the first CI run that used the script,
    with every library already installed. The cleanup must use the same sudo and keep the install's exit status."""
    trap = next(line for line in SCRIPT.read_text().splitlines() if line.startswith("trap "))
    assert '$SUDO rm -rf "$WORK"' in trap, trap
    assert "exit $status" in trap, trap
