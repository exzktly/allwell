"""Remote scripts must parse on the bash a server actually has, not only on ours.

The Storage browser's first listing on a real host failed with::

    could not list ~/.allwell-staging
    bash: line 55: syntax error near unexpected token `;;'

Line 55 was ``o=$({ case "$jid" in *.*) squeue …;; *) squeue …;; esac; } …)``
(``services.slurm_ids.squeue_listing``). Bash before 5.2 finds the end of a
``$( … )`` by counting parentheses, so a ``case`` pattern's bare ``)`` closes the
substitution early; bash 3.2 (the one macOS ships) then rejects the whole script.
The bash every test here runs is 5.x, which parses a substitution as commands and
accepts it — so every test passed, and the first real server refused the script.

The POSIX form ``(pattern)`` balances the count. This guard reads each script the
remote features send, finds every ``$( … )``, and requires every ``case`` inside
one to be closed inside it with every pattern opening with ``(``.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from services import remote_liveness, remote_reuse, remote_storage as rs, remote_sweep
from services.slurm_ids import squeue_listing

RUN = "run-0000000000000aa1-0000000000000bb2"
KEY = "0000000000000000000000000000abcd"


def _substitutions(script: str) -> list[str]:
    """The text of every ``$( … )`` in *script*, ended where a parenthesis-counting
    bash would end it (``$((`` arithmetic skipped)."""
    out = []
    for m in re.finditer(r"\$\((?!\()", script):
        depth, i = 1, m.end()
        while i < len(script) and depth:
            depth += {"(": 1, ")": -1}.get(script[i], 0)
            i += 1
        out.append(script[m.end():i - 1])
    return out


def case_violations(script: str) -> list[str]:
    """Each ``case`` inside a ``$( … )`` that an old bash would misparse."""
    bad = []
    for sub in _substitutions(script):
        for m in re.finditer(r"\bcase\b", sub):
            rest = sub[m.start():]
            end = re.search(r"\besac\b", rest)
            if end is None:                         # a bare pattern closed the substitution early
                bad.append(rest[:120])
                continue
            body = rest[:end.start()].split(" in ", 1)[1] if " in " in rest[:end.start()] else ""
            for arm in filter(str.strip, body.split(";;")):
                if not arm.strip().startswith("("):
                    bad.append(arm.strip()[:120])
    return bad


def _item(kind: str, name: str) -> rs.StorageItem:
    return rs.StorageItem(kind, name, 1, 1, label={"run_id": name} if kind == "run" else {"cache_key": name},
                          fetched=True)


def _storage_scripts() -> dict[str, str]:
    st = "~/.allwell-staging"
    items = [_item("run", RUN), _item("dataset", KEY)]
    return {
        "listing (detailed)": rs.listing_script(st, detail=True),
        "listing (run start)": rs.listing_script(st, detail=False),
        "delete": rs.delete_script(st, items),
        "delete (automatic)": rs.delete_script(st, items, automatic=True, host_days=30),
        "set expiry": rs.set_expiry_script(st, items, rs.KEEP),
        "clear expiry": rs.set_expiry_script(st, items, ""),
        "label": rs.label_script(st, run_id=RUN, dataset_key=KEY, label={"p": 1},
                                 borrowed_run="run-0000000000000cc3-0000000000000dd4"),
        "mark downloaded": rs.mark_fetched_script(st, [RUN]),
    }


def _other_scripts() -> dict[str, str]:
    from services.remote_commands import HOLD_DIR
    from services.remote_run_id import LEGACY_UPLOAD_HEARTBEAT, UPLOAD_HEARTBEAT
    from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S
    return {
        "earlier-attempt clean-up": remote_reuse._REMOVE_IF_IDLE.format(
            heartbeat=UPLOAD_HEARTBEAT, legacy=LEGACY_UPLOAD_HEARTBEAT, stale=UPLOAD_HEARTBEAT_STALE_S,
            hold=HOLD_DIR),
        "allocation sweep": remote_sweep._target_script(0, "~/.allwell-staging/runs/r/run.pid",
                                                        "~/.allwell-staging/runs/r/.aw_beat/alive"),
        "liveness probe": remote_liveness._script("~/.allwell-staging/runs/r/run.pid", "123"),
    }


def test_the_shared_squeue_helper_parenthesises_its_patterns():
    assert case_violations("o=$(" + squeue_listing("jid") + ")") == []


def test_the_guard_catches_the_form_that_failed_on_the_server():
    """The exact line 55 the server rejected, so the guard is shown to bite."""
    line55 = ('o=$({ case "$jid" in *.*) squeue -h -s"$jid" -o %i;; *) squeue -h -j "$jid" -o %T;; esac; } '
              '2>"${aw_err:-/dev/null}"); r=$?')
    assert case_violations(line55)


@pytest.mark.parametrize("name, script", sorted({**_storage_scripts(), **_other_scripts()}.items()))
def test_no_remote_script_has_a_case_an_old_bash_misparses(name, script):
    assert case_violations(script) == [], name


def test_the_scripts_still_run(tmp_path, monkeypatch):
    """The parenthesised form behaves the same: a listing still lists."""
    import subprocess
    monkeypatch.setenv("HOME", str(tmp_path))
    run = tmp_path / ".allwell-staging" / "runs" / RUN
    (run / "out").mkdir(parents=True)
    (run / "run.pid.slurm").write_text("4242\n")
    cache = SimpleNamespace(
        host=SimpleNamespace(remote_staging_dir="~/.allwell-staging"),
        _ssh_script=lambda script, *, check, timeout_s=None: subprocess.run(
            ["bash", "-s"], input=script, capture_output=True, text=True, timeout=60))
    listing, error = rs.list_storage(cache)
    assert error == "" and [i.name for i in listing.items] == [RUN]
