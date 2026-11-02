"""The GUI caps its BLAS thread pools before numpy loads, and the pipeline gets the cap removed.

See ``util/blas_threads.py`` for why: on Windows every OpenBLAS thread commits a
page-file-backed buffer it never uses, so a window with one thread per core held
gigabytes of page file until it quit.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from services import pipeline_service
from tests._repo_scan import parse_source
from util.blas_threads import GUI_BLAS_THREADS, MARKER_ENV, THREAD_VARS, cap_for_gui, uncapped_env

ROOT = Path(__file__).resolve().parents[1]


def test_the_cap_fills_every_unset_variable_with_the_gui_bound():
    env: dict[str, str] = {}
    added = cap_for_gui(env, cpu_count=32)
    assert added == list(THREAD_VARS)
    assert {name: env[name] for name in THREAD_VARS} == {name: str(GUI_BLAS_THREADS) for name in THREAD_VARS}
    assert env[MARKER_ENV] == ",".join(THREAD_VARS)


def test_the_cap_never_exceeds_the_machine():
    env: dict[str, str] = {}
    cap_for_gui(env, cpu_count=2)
    assert env["OPENBLAS_NUM_THREADS"] == "2"


def test_a_value_the_user_set_is_kept_and_not_marked():
    env = {"OPENBLAS_NUM_THREADS": "16"}
    cap_for_gui(env, cpu_count=32)
    assert env["OPENBLAS_NUM_THREADS"] == "16"
    assert "OPENBLAS_NUM_THREADS" not in env[MARKER_ENV].split(",")


def test_a_second_call_changes_nothing():
    env: dict[str, str] = {}
    cap_for_gui(env, cpu_count=32)
    before = dict(env)
    assert cap_for_gui(env, cpu_count=32) == []
    assert env == before


def test_uncapping_removes_what_the_cap_added_and_nothing_the_user_set():
    env = {"OPENBLAS_NUM_THREADS": "16", "PATH": "/bin"}
    cap_for_gui(env, cpu_count=32)
    out = uncapped_env(env)
    assert out == {"OPENBLAS_NUM_THREADS": "16", "PATH": "/bin"}


def test_the_pipeline_is_launched_without_the_window_s_cap(monkeypatch):
    """The real launch call: ``spawn_pipeline`` hands Popen an env with no GUI cap in it."""
    for name in (*THREAD_VARS, MARKER_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MKL_NUM_THREADS", "3")  # the user's own, which must survive
    import os
    cap_for_gui(os.environ, cpu_count=32)
    assert os.environ["OPENBLAS_NUM_THREADS"] == str(GUI_BLAS_THREADS)

    seen = {}

    def fake_popen(args, **kwargs):
        seen.update(kwargs["env"])
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    pipeline_service.spawn_pipeline(["python", "process_microscopy.py"])
    assert seen["MKL_NUM_THREADS"] == "3"
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "NUMEXPR_NUM_THREADS", MARKER_ENV):
        assert name not in seen, name


def _first_line(tree: ast.Module, predicate) -> int:
    lines = [node.lineno for node in tree.body if predicate(node)]
    assert lines, "statement not found"
    return lines[0]


def _calls_cap(node: ast.stmt) -> bool:
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_cap_blas_for_gui" for n in ast.walk(node))


def _imports(node: ast.stmt, module: str) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == module for alias in node.names)
    return isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == module


def test_the_window_entry_caps_before_the_first_import_that_loads_numpy():
    """OpenBLAS reads the variable once, when numpy loads it; a cap set afterwards does nothing."""
    tree = parse_source(ROOT / "allwell.py")
    assert _first_line(tree, _calls_cap) < _first_line(tree, lambda n: _imports(n, "PySide6"))
    # Everything above the cap is the standard library or the boot log.
    for node in tree.body:
        if _calls_cap(node):
            break
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert all(m.split(".")[0] in {"__future__", "argparse", "logging", "sys", "pathlib", "startup", "util"}
                       for m in mods), mods


def test_the_frozen_launcher_caps_the_window_before_matplotlib():
    tree = parse_source(ROOT / "all_well_launcher.py")
    assert _first_line(tree, _calls_cap) < _first_line(tree, lambda n: _imports(n, "matplotlib"))
