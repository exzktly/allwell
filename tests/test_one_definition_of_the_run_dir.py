"""A run's remote directory is spelled in exactly one place.

``{remote_staging_dir}/runs/{run_id}`` was a format-string literal in **nine**
places across three classes, while the *dataset* address right beside it had
one definition (``DatasetCache.remote_dataset_dir``). The asymmetry is the
whole problem: every change to "where does a run live" was an N-site sweep with
no mechanical way to discover N, so the sweeps were half-done and the halves
disagreed.

That is not a hypothetical failure mode here. A re-attaching run's own
directory went uncreated because one of the ten sites was the *output* path and
the ``mkdir`` used that one — leaving the pidfile with nowhere to land, so Stop
could no longer kill the remote pipeline, and on a scheduler host the heartbeat
failed silently and the sentinel raised after the whole plate had uploaded. The
cleanup that followed derived the directory a third way again.

``remote_run_dir`` and ``remote_run_paths`` are that one place now, and this is
what keeps the eleventh site from appearing. It is a syntactic rule because the
alternative — noticing in review that a new f-string duplicates an old one — is
what already failed.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: Where the one definition lives. Everything else must call it.
_FUNNEL = ("remote_run_dir", "remote_run_paths")


def _run_dir_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Every f-string that builds a ``runs/<something>`` path.

    Matched on the joined static parts rather than on the interpolations, so
    the spelling of the run-id expression cannot dodge it — ``{run_id}``,
    ``{self._run_id}`` and ``{run.run_id}`` were the three used, and a fourth
    would have been just as invisible.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        static = "".join(v.value for v in node.values
                         if isinstance(v, ast.Constant)
                         and isinstance(v.value, str))
        if "/runs/" in static or static.endswith("/runs"):
            found.append((node.lineno, ast.unparse(node)))
    return found


def test_only_the_funnel_spells_the_run_directory():
    """Scanned across the repo, not against one hardcoded file.

    The first version read `services/remote_runner.py` by name. That is the
    file this guard exists to hold *while it is being taken apart*, so the
    first extraction that moved a run-dir literal — or moved the funnel itself
    — put the literal somewhere the guard could not see. Worse, it failed
    *open*: with the funnel out of the scanned file, `allowed` was empty, the
    guard had nothing to compare, and it passed while measuring nothing. That
    is CLAUDE.md §7.5 #10 — an allowlist with no liveness check — in the guard
    written to hold the whole refactor.

    Roots come from `tests/_repo_scan.py`, per §7.5 #3, and the funnel's own
    literal must be found or this fails: a guard that cannot locate the one
    definition it is written around is not passing, it is blind.
    """
    offenders, found_funnel = [], False
    for path in python_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        allowed = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in _FUNNEL:
                for line, _src in _run_dir_literals(fn):
                    allowed.add(line)
                    found_funnel = True
        offenders += [f"{rel(path)}:{line} — {src}"
                      for line, src in _run_dir_literals(tree)
                      if line not in allowed]

    assert found_funnel, (
        f"none of {' / '.join(_FUNNEL)} was found building a run directory "
        "anywhere in the scanned sources. Either the funnel was renamed or "
        "moved out of the scan surface — and until that is fixed this guard "
        "allows everything, because it has nothing to compare against.")
    assert not offenders, (
        "A remote run directory is built from a literal outside "
        f"{' / '.join(_FUNNEL)}. Call the funnel instead: a second spelling is "
        "a second definition of where a run lives, and the last time there "
        "were several, a re-attaching run's own directory was never created "
        "while another site deleted a third one.\n  "
        + "\n  ".join(offenders))


def test_the_funnel_and_the_paths_agree_about_the_parent():
    """The four paths a run owns must sit under the directory it names.

    They were derived independently before, which is exactly how the output
    directory came to be created and the pidfile's parent not.
    """
    from services.remote_runner import RemoteHost, remote_run_dir, remote_run_paths

    host = RemoteHost(
        name="t", ssh_alias="a", remote_project_dir="~/allwell",
        remote_staging_dir="~/.stg", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0)
    run_dir = remote_run_dir(host, "run-abc")
    paths = remote_run_paths(host, "run-abc")

    assert paths.run_dir == run_dir
    for name in ("out", "pidfile", "sentinel", "heartbeat"):
        value = getattr(paths, name)
        assert value.startswith(run_dir + "/"), (name, value)
