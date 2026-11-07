"""Every command line sent after ``ssh_argv(...)`` is packed, and every ``bash -s`` script is grouped.

What runs on a host is what arrives, and a long command line from Windows does not always arrive whole: a user's remote
home folder was emptied by an ``rm -rf`` that, by every sign, reached the host cut after a ``"$HOME"``. A packed
command (``remote_transport.pack_for_login_shell``) runs only when it decodes whole, and a grouped script
(``guard_script``) only when it was read whole (``tests/test_remote_commands_cannot_run_cut.py`` runs both, cut). This
holds the next call site to the same rule: what follows ``ssh_argv(...)`` is a ``pack_for_login_shell(...)`` call, or
``["bash", "-s"]`` in a function that groups its script with ``guard_script``. An exemption is a site with its reason.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

EXEMPT = {
    "scripts/ssh_command_line_probe.py::_send":
        "The read-only probe (printf / wc) that measures how much of an unpacked command reaches a host -- the very "
        "thing packing protects against -- so it must send unpacked.",
}


def _name(call) -> str:
    return getattr(call.func, "id", getattr(call.func, "attr", ""))


def _sites():
    """``(path::function, the list added to ssh_argv(...), the function)`` for every ``….ssh_argv(...) + [ … ]``."""
    out = []
    for source in python_sources():
        for fn in ast.walk(parse_source(source)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and isinstance(node.left, ast.Call)
                        and isinstance(node.left.func, ast.Attribute) and node.left.func.attr == "ssh_argv"
                        and isinstance(node.right, ast.List)):
                    out.append((f"{rel(source)}::{fn.name}", node.right, fn))
    return out


def test_every_command_sent_after_ssh_argv_is_packed_or_a_grouped_script():
    bad = []
    for site, lst, fn in _sites():
        if site in EXEMPT:
            continue
        elts = lst.elts
        if [getattr(e, "value", None) for e in elts] == ["bash", "-s"]:
            if "guard_script" not in {_name(c) for c in ast.walk(fn) if isinstance(c, ast.Call)}:
                bad.append(f"{site}: sends bash -s without guard_script")
        elif not (len(elts) == 1 and isinstance(elts[0], ast.Call) and _name(elts[0]) == "pack_for_login_shell"):
            bad.append(f"{site}: sends an unpacked command line")
    assert not bad, "\n".join(bad)


def test_the_scan_sees_the_call_sites_and_every_exemption_is_one():
    sites = {site for site, _l, _f in _sites()}
    assert len(sites) >= 10
    assert set(EXEMPT) <= sites, set(EXEMPT) - sites                 # an exemption that names no site is stale
