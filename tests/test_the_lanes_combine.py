"""Workstream B's own guard: the two lanes' coverage data really does combine.

The whole of B exists to answer one question — which application modules does no
test reach — and the way that answer goes wrong is not a crash. It is a union
that quietly contains one lane, which names live code dead: measured on this
repo, a fast-lane-only inventory listed eleven modules and six of them were
reached, five by a single slow-lane file.

Nothing pinned any of it until this file. Two defects had already shipped
underneath that gap, both reproduced before being fixed:

* the fast lane wrote plain ``.coverage``; ``coverage combine`` globs
  ``<data_file>.*``, which never matches ``.coverage`` itself, so the documented
  fast → slow → combine sequence combined the slow lane alone and overwrote the
  fast lane's file. It printed ``Combined 1 file``.
* in the other order it was worse: pytest-cov calls ``Coverage.erase()`` at
  session start, and in parallel mode that unlinks every ``<data_file>.*`` — so
  a fast lane run after the slow one **deleted** every ``.coverage.slow-*``.

Both die to the same property, which is what these tests pin: **each lane writes
under its own prefix, and neither prefix is the bare default.** The first two
tests hold the scripts to it; the third holds ``coverage`` itself to the
behaviour the fix depends on, so a library change that broke the assumption
fails here rather than in a silently short inventory months later.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAST = ROOT / "scripts" / "test.sh"
SLOW = ROOT / "scripts" / "test_slow.sh"


def _coverage_file_assignments(script: Path) -> list[str]:
    """Every value the script assigns to ``COVERAGE_FILE``."""
    text = script.read_text()
    return re.findall(r'COVERAGE_FILE=(?:"\$\{COVERAGE_FILE:-)?"?([^"\'}\s]+)', text)


def test_the_fast_lane_writes_under_a_name_of_its_own():
    """Not ``.coverage``. That name is the one ``combine`` writes *to* and never
    reads, so data under it is dropped by the very step meant to merge it."""
    names = _coverage_file_assignments(FAST)

    assert names, "the fast lane sets no COVERAGE_FILE; it will write .coverage"
    for name in names:
        assert name != ".coverage", (
            "the fast lane is back on the default name — combine will discard it"
        )
        assert name.startswith(".coverage."), name


def test_neither_lane_can_erase_the_other_s_data():
    """``Coverage.erase()`` in parallel mode unlinks ``<data_file>.*``. So one
    lane's prefix must not be a prefix of the other's, in either direction —
    which is the property, rather than "the two strings differ"."""
    fast = _coverage_file_assignments(FAST)
    slow = _coverage_file_assignments(SLOW)

    assert fast and slow
    for f in fast:
        for s in slow:
            assert not s.startswith(f), f"slow lane {s!r} is swept by fast lane {f!r}"
            assert not f.startswith(s), f"fast lane {f!r} is swept by slow lane {s!r}"


def test_the_lane_scripts_do_not_share_one_prefix():
    fast = set(_coverage_file_assignments(FAST))
    slow = set(_coverage_file_assignments(SLOW))
    assert not (fast & slow), (fast, slow)


@pytest.mark.parametrize("order", [("fast", "slow"), ("slow", "fast")])
def test_two_lanes_written_under_their_own_names_really_union(tmp_path, order):
    """The behaviour the fix rests on, driven through ``coverage`` itself.

    Each "lane" covers a module the other does not, so a union that lost one is
    visible as a module at zero — which is exactly the failure shape in
    production, rather than a proxy for it. Run in both orders because the two
    original defects had opposite orders.
    """
    (tmp_path / "mod_fast.py").write_text("def f():\n    return 1\n")
    (tmp_path / "mod_slow.py").write_text("def s():\n    return 2\n")
    (tmp_path / ".coveragerc").write_text(
        "[run]\nsource = .\nrelative_files = true\nparallel = true\n"
    )
    scripts = {
        "fast": ("import mod_fast; mod_fast.f()", ".coverage.fast"),
        "slow": ("import mod_slow; mod_slow.s()", ".coverage.slow-1-1"),
    }
    for lane in order:
        body, data_file = scripts[lane]
        (tmp_path / f"run_{lane}.py").write_text(body + "\n")
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"run_{lane}.py"],
            cwd=tmp_path, check=True, capture_output=True,
            env={"PATH": "/usr/bin:/bin", "COVERAGE_FILE": data_file,
                 "HOME": str(tmp_path)},
        )

    # stdout and stderr merged: `coverage combine` writes its "Combined N
    # files" line to one or the other depending on the path it takes, and a
    # test that watched only stdout would pass on an empty string.
    combined = subprocess.run(
        [sys.executable, "-m", "coverage", "combine"],
        cwd=tmp_path, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ).stdout
    assert "Combined 2 files" in combined, combined

    report = subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--include=mod_*.py"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    ).stdout
    for module in ("mod_fast.py", "mod_slow.py"):
        line = next((l for l in report.splitlines() if l.startswith(module)), None)
        assert line is not None, f"{module} missing from the union:\n{report}"
        # Parse the number rather than matching the text. The first version of
        # this asserted the row did not *end with* "0%" — which is true of
        # "100%" as well, so it failed on a perfectly good union. An assertion
        # that cannot tell the right answer from the wrong one in either
        # direction is worth less than none.
        percent = float(line.rstrip().rsplit(maxsplit=1)[-1].rstrip("%"))
        assert percent > 0.0, (
            f"{module} is in the union with nothing executed — "
            f"a lane was dropped:\n{report}"
        )


def test_combine_still_ignores_the_bare_default_name(tmp_path):
    """Why the fast lane may not use ``.coverage``, pinned as a fact about the
    library rather than as a claim in a comment. If a future ``coverage`` starts
    folding the bare file in, this fails and the workaround can be reconsidered
    — rather than the comment quietly becoming false."""
    (tmp_path / "mod_x.py").write_text("def x():\n    return 1\n")
    (tmp_path / ".coveragerc").write_text(
        "[run]\nsource = .\nrelative_files = true\nparallel = true\n"
    )
    (tmp_path / "run_x.py").write_text("import mod_x; mod_x.x()\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}

    # One run under the bare default name…
    subprocess.run([sys.executable, "-m", "coverage", "run", "run_x.py"],
                   cwd=tmp_path, check=True, capture_output=True,
                   env={**env, "COVERAGE_FILE": ".coverage"})
    subprocess.run([sys.executable, "-m", "coverage", "combine"],
                   cwd=tmp_path, check=True, capture_output=True)
    assert (tmp_path / ".coverage").exists()

    # …and one under a prefixed name. Combining now must report ONE file: the
    # bare one is not read, which is the whole hazard.
    (tmp_path / "mod_y.py").write_text("def y():\n    return 1\n")
    (tmp_path / "run_y.py").write_text("import mod_y; mod_y.y()\n")
    subprocess.run([sys.executable, "-m", "coverage", "run", "run_y.py"],
                   cwd=tmp_path, check=True, capture_output=True,
                   env={**env, "COVERAGE_FILE": ".coverage.other"})
    out = subprocess.run([sys.executable, "-m", "coverage", "combine"],
                         cwd=tmp_path, check=True, text=True,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT).stdout
    assert "Combined 1 file" in out, out
