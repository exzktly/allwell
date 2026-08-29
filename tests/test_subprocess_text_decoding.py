"""Guard: text-mode subprocess output must name its codec.

``text=True`` alone leaves the pipe to be decoded with
``locale.getpreferredencoding(False)``. That is UTF-8 on macOS and Linux and
the ANSI codepage on Windows (cp1252 for a Western install), so every stream
this app reads from ssh, rsync, ssh-keygen, the remote Linux pipeline and the
bootstrap provisioner — all of them UTF-8 — is decoded correctly for the
developer and incorrectly for a Windows user. The visible half is mojibake in
the log drawer; the damaging half is a ``UnicodeDecodeError`` raised out of a
reader thread's iterator on the first byte the codepage has no character for,
which skips the ``proc.wait()`` after it and leaves the GUI reporting that a
run never started while the process keeps working on the user's plate.

Two live cases came from exactly that: the remote pipeline's ``Well X — done.``
markers, which the dashboard's per-well tracking matches on, and the Remotes
dialog's bootstrap reader.

``services.subprocess_text.TEXT_UTF8`` is the one spelling. This guard fails on
any application source that opens a text-mode subprocess without an explicit
``encoding``, whether the flag is a keyword on the call or a key in a kwargs
dict that is splatted into it.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

_TEXT_FLAGS = {"text", "universal_newlines"}

#: The splat that carries text + encoding + errors together.
_SHARED = "TEXT_UTF8"


def _is_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _splats_shared(nodes) -> bool:
    """True when ``**TEXT_UTF8`` (or a dict built from it) is spread in here."""
    for node in nodes:
        if isinstance(node, ast.Name) and node.id == _SHARED:
            return True
        if isinstance(node, ast.Attribute) and node.attr == _SHARED:
            return True
    return False


def _call_violates(node: ast.Call) -> bool:
    """A subprocess call that turns on text mode without naming a codec."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name not in {"run", "Popen", "check_output", "call", "check_call"}:
        return False
    kwargs = {kw.arg for kw in node.keywords if kw.arg}
    if not (kwargs & _TEXT_FLAGS):
        return False
    if "encoding" in kwargs:
        return False
    return not _splats_shared(kw.value for kw in node.keywords if kw.arg is None)


def _dict_violates(node: ast.Dict) -> bool:
    """A kwargs dict that turns on text mode without naming a codec.

    ``subprocess.Popen(cmd, **kwargs)`` where ``kwargs`` was built as a dict
    literal is the same defect one indirection away — and it is the shape the
    Analyze tab's spawn helpers actually use.
    """
    keys = {k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    on = any(
        isinstance(k, ast.Constant) and k.value in _TEXT_FLAGS and _is_true(v)
        for k, v in zip(node.keys, node.values)
    )
    if not on or "encoding" in keys:
        return False
    return not _splats_shared(
        v for k, v in zip(node.keys, node.values) if k is None
    )


def _violations() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        if rel(path) == "services/subprocess_text.py":
            continue  # the definition itself
        for node in ast.walk(parse_source(path)):
            if isinstance(node, ast.Call) and _call_violates(node):
                out.append(f"{rel(path)}:{node.lineno}")
            elif isinstance(node, ast.Dict) and _dict_violates(node):
                out.append(f"{rel(path)}:{node.lineno}")
    return sorted(set(out))


def test_text_mode_subprocesses_declare_utf8():
    bad = _violations()
    assert not bad, (
        "text-mode subprocess output decoded with the build machine's locale "
        "(cp1252 on Windows) instead of UTF-8 at:\n  "
        + "\n  ".join(bad)
        + "\n\nSplat services.subprocess_text.TEXT_UTF8 into the call, or pass "
          "encoding=/errors= explicitly where that module cannot be imported "
          "(scripts/_bootstrap_lib.py is stdlib-only by design)."
    )
