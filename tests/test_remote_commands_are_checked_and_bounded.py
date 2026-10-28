"""A remote command whose result matters is read, and every subprocess the services start is bounded.

The remote path lost plates the same way again and again: an ssh control
command ran with ``check=False`` and nobody read what it returned, so a failure
looked exactly like success. The batch hand-off ``touch`` that left a run silent
for an hour, the flag-clearing ``rm`` that let a driver run a half-uploaded
batch, the cleanup that logged "removed" over an ``rm`` that never ran — each
was a call statement whose result was thrown away (review rows A7, G1, P2 in
``_Docs/Markdowns/REMOTE_REVIEW_TRACKER.md``). The second shape was a
``subprocess.run`` with no timeout, which over a dead link waits for as long as
TCP keepalive takes to give up (rows C7, C8, P3).

Both are decidable from the syntax tree, so they are guarded here rather than
remembered:

* **No discarded ``check=False`` remote command.** A call to one of the ssh
  helpers with ``check=False`` may not be a bare expression statement — its
  result has to be bound, tested or returned. A site where throwing the result
  away is genuinely right is exempted *by function name* in
  ``DISCARD_ALLOWED`` with the reason written beside it.
* **No unbounded ``subprocess.run`` in ``services/``.** Every call passes
  ``timeout=``, or is exempted by function name in ``UNBOUNDED_ALLOWED`` with
  the reason.

Each exemption is checked for liveness: an entry that no longer names a site
the rule would flag fails, so the roster cannot quietly outlive the code.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

#: The helpers that run a command on the remote host. A local alias of one of
#: them (``ssh = proc._runner._cache._ssh_run``) is called as ``ssh``.
SSH_HELPERS = frozenset({"_ssh_run", "_ssh_script", "ssh_control", "ssh_run", "ssh"})

#: Roots the discard rule reads: everything that talks to a remote host.
DISCARD_ROOTS = ("services", "pipeline", "util", "analyze", "datasets")

#: (module, enclosing function) → why discarding the result is right there.
DISCARD_ALLOWED: dict[tuple[str, str], str] = {
    ("services/remote_bootstrap.py", "measure_bandwidth"):
        "best-effort removal of a throwaway probe file in /tmp; nothing reads it, and a leftover costs 50 MB "
        "of the host's tmp until its next reboot, never a wrong result",
}

#: (module, enclosing function) → why that subprocess may run unbounded.
UNBOUNDED_ALLOWED: dict[tuple[str, str], str] = {
    ("services/remote_bootstrap.py", "_ssh_run"):
        "the bootstrap's own ssh, which also streams conda and pip installs that legitimately run for tens of "
        "minutes; a dead link is still noticed within about two minutes by the keepalive every bootstrap ssh "
        "carries (remote_config.keepalive_options)",
}


def _callee(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _functions(tree: ast.AST):
    """Yield (function name, node) for every node, naming its nearest enclosing function."""
    def walk(node, owner):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else owner
            yield name, child
            yield from walk(child, name)
    yield from walk(tree, "<module>")


def _discarded_unchecked(paths) -> dict[tuple[str, str], list[int]]:
    found: dict[tuple[str, str], list[int]] = {}
    for path in paths:
        for owner, node in _functions(parse_source(path)):
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
                continue
            call = node.value
            check = _keyword(call, "check")
            if (_callee(call) in SSH_HELPERS and isinstance(check, ast.Constant)
                    and check.value is False):
                found.setdefault((rel(path), owner), []).append(node.lineno)
    return found


def _is_subprocess_call(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
            and f.value.id == "subprocess"
            and f.attr in ("run", "check_output", "check_call", "call"))


def _unbounded(paths) -> dict[tuple[str, str], list[int]]:
    found: dict[tuple[str, str], list[int]] = {}
    for path in paths:
        for owner, node in _functions(parse_source(path)):
            if isinstance(node, ast.Call) and _is_subprocess_call(node) and _keyword(node, "timeout") is None:
                found.setdefault((rel(path), owner), []).append(node.lineno)
    return found


def test_no_remote_command_result_is_thrown_away():
    found = _discarded_unchecked(python_sources(roots=DISCARD_ROOTS))
    bad = {site: lines for site, lines in found.items() if site not in DISCARD_ALLOWED}
    assert not bad, (
        "A remote command run with check=False has its result thrown away — a failure there reads as success "
        "(review A7/G1/P2). Bind and test the result, or exempt the function in DISCARD_ALLOWED with the reason:\n"
        + "\n".join(f"  {m}:{lines} in {fn}()" for (m, fn), lines in sorted(bad.items())))


def test_every_services_subprocess_is_bounded():
    found = _unbounded(python_sources(roots=("services",)))
    bad = {site: lines for site, lines in found.items() if site not in UNBOUNDED_ALLOWED}
    assert not bad, (
        "A subprocess in services/ runs with no timeout — over a dead link it waits as long as TCP keepalive "
        "takes to give up (review C7/C8/P3). Pass timeout=, or exempt the function in UNBOUNDED_ALLOWED with "
        "the reason:\n" + "\n".join(f"  {m}:{lines} in {fn}()" for (m, fn), lines in sorted(bad.items())))


def test_every_exemption_still_names_a_site_the_rule_would_flag():
    discarded = _discarded_unchecked(python_sources(roots=DISCARD_ROOTS))
    unbounded = _unbounded(python_sources(roots=("services",)))
    stale = [f"DISCARD_ALLOWED {site}" for site in DISCARD_ALLOWED if site not in discarded]
    stale += [f"UNBOUNDED_ALLOWED {site}" for site in UNBOUNDED_ALLOWED if site not in unbounded]
    assert not stale, "Exemptions that no longer describe a flagged site — remove them:\n  " + "\n  ".join(stale)
