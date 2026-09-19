"""No path removes a directory it only partly archived.

Every place that turns a ``<well>/`` folder into ``<well>.zip`` packs an
**extension-filtered** subset of the folder. Four of them then removed the
whole folder, which destroyed everything the filter skipped — OME companion
XML, acquisition logs, mask subdirectories, and on macOS the ``.DS_Store`` in
every folder a user has opened in Finder. The archive beside it holds only the
images, so there was no copy anywhere.

Two of the four had already written the reasoning down and applied it to the
wrong branch: both spared a folder holding *no* images because "it may hold
non-image content", and both then removed the folder in the branch where it
does yield images, which is the branch where the content was actually lost.

So the folder is not the unit of deletion. A function that writes an archive
and then removes a directory must go through
:func:`util.zip_write.replace_folder_with_archive`, which deletes the archived
files and removes the directory only when nothing is left in it.

**Archive-writing is followed through calls**, because two of the four sites
did not name ``atomic_zip`` themselves — ``stage_local_zips`` zips through
``_zip_well_folder`` and ``compress_folder_images_to_zip_and_remove`` through
``compress_images_to_zip``. A guard that only looked for a literal
``atomic_zip`` call in the same function found two of the four and would have
called the sweep done.

**And it is followed across a first-party import**, which the first version of
this guard did not do. ``compress_images_to_zip`` moved from
``process_microscopy.py`` to ``pipeline/io.py``; the chain from
``process_well_zip_task`` to ``atomic_zip`` then crossed a module boundary, the
module-local closure stopped following it, and both real pairings in that file
became invisible. Nothing went red — a blinded guard reports as a *greener*
suite, which is exactly what :func:`test_allowlist_is_live` exists to notice,
and it is the only thing that did.

So the closure is computed once over the whole tree and then handed back to
each module as the writers it **binds**: a name is followed only where the
module defines it or imports it by name from a module that really defines a
writer. That keeps the property the module-local rule was protecting — a
same-named helper in some unrelated module still does not make a function an
archive writer — while following the one case that matters, a writer that
moved to the package it belongs in.
"""
from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

#: Writing an archive. ``atomic_zip`` is the one publisher every well-archive
#: path in the app goes through (ARCHITECTURE §6.5).
_ARCHIVE_WRITERS = frozenset({"atomic_zip"})

#: Removing a directory and everything under it.
_DIRECTORY_REMOVERS = frozenset({"rmtree", "remove_directory"})

#: The helper that makes the pairing safe.
_SAFE = frozenset({"replace_folder_with_archive"})

#: Sites where the removed directory is a per-well **scratch** directory the
#: pipeline created and wholly owns, not a folder of the user's own files.
#: Keyed on ``(file, function)`` rather than on the file, so a new pairing
#: elsewhere in the same module is still caught. ``test_allowlist_is_live``
#: fails when an entry stops naming a real pairing.
_SCRATCH_DIR_ALLOWLIST = frozenset({
    # ``tmp_extract`` / ``tmp_images`` are made by this task for this well and
    # hold only what the run just wrote into them; the user's inputs are the
    # archive it read from, which it never removes.
    ("process_microscopy.py", "process_well_zip_task"),
    ("process_microscopy.py", "process_well_folder_task"),
    # Both of these became visible only when the closure started following a
    # writer across a first-party import, and both remove a directory
    # ``_new_transit_dir`` made moments earlier for this one well: the CSV
    # transit dir in ``_fetch_one`` (which reaches a writer through
    # ``_materialize``) and the extraction dir in ``_materialize_incremental``
    # (through ``pipeline.io.merge_images_into_zip``). Neither ever holds a
    # file of the user's — the inputs are the remote copy and the archive
    # beside it, and neither is removed here.
    ("services/remote_runner.py", "_fetch_one"),
    ("services/remote_runner.py", "_materialize_incremental"),
})


def _called_names(node: ast.AST) -> set[str]:
    """Every callee name reachable in *node*, attribute or bare."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            if isinstance(func, ast.Attribute):
                out.add(func.attr)
            elif isinstance(func, ast.Name):
                out.add(func.id)
    return out


def _functions(tree: ast.AST):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n


def _definitions(tree: ast.AST) -> dict[str, set[str]]:
    """``{function name: the names it calls}`` for every function in *tree*."""
    out: dict[str, set[str]] = {}
    for fn in _functions(tree):
        out.setdefault(fn.name, set()).update(_called_names(fn))
    return out


def _imported_names(tree: ast.AST) -> set[str]:
    """Every name this module binds with ``from X import name``.

    The binding is what licenses following a writer across a module boundary.
    A bare ``import X`` binds only ``X``, and the call then reads as
    ``X.name`` — whose attribute this guard already collects — so both
    spellings are covered without following an unbound name anywhere.
    """
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            out.update(a.asname or a.name for a in n.names)
    return out


def repo_archive_writers() -> frozenset[str]:
    """Every function name in the tree that reaches ``atomic_zip``.

    The closure is computed over ``(module, function)`` pairs and follows each
    call through the importing module's own ``from X import name`` bindings, so
    two unrelated functions that merely share a name are never merged. Unioning
    callees by bare name instead — which is what the first version of this did
    — grew the writer set until it swept in ``_commit_staging_dir``,
    ``prune_superseded_staging`` and five other functions that write no archive
    at all, and the guard then fired on most of the tree.

    The **names** are what comes back, because that is what
    :func:`offenders_in` can use against a single source string. Narrowing
    happens there: a name is followed only where the module binds it.
    """
    defs: dict[tuple[str, str], set[str]] = {}
    imports: dict[str, dict[str, tuple[str, str]]] = {}
    for path in python_sources():
        # ``parse_source``, never ``ast.parse(path.read_text())``: the tree is
        # 479 files and parsing it is 2.8 s, which roughly sixty guards in this
        # lane each want in full. ``test_the_fast_lane_stays_fast`` is what
        # noticed this function doing its own — and it only noticed in the
        # whole lane, not in this file's own run.
        try:
            tree = parse_source(path)
        except (OSError, SyntaxError):  # pragma: no cover - the tree parses
            continue
        mod = rel(path)[:-3].replace("/", ".")
        for name, callees in _definitions(tree).items():
            defs[(mod, name)] = callees
        binding: dict[str, tuple[str, str]] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                for a in n.names:
                    binding[a.asname or a.name] = (n.module, a.name)
        imports[mod] = binding

    def resolve(mod: str, name: str) -> tuple[str, str] | None:
        """Where *name* is defined, as seen from *mod*. ``None`` when unknown."""
        seen: set[tuple[str, str]] = set()
        here = (mod, name)
        while here not in seen:
            seen.add(here)
            if here in defs:
                return here
            src = imports.get(here[0], {}).get(here[1])
            if src is None:
                return None
            here = src
        return None

    writers: set[tuple[str, str]] = set()
    while True:
        grown = set(writers)
        for key, callees in defs.items():
            if key in grown:
                continue
            for callee in callees:
                if callee in _ARCHIVE_WRITERS:
                    grown.add(key)
                    break
                target = resolve(key[0], callee)
                if target is not None and target in grown:
                    grown.add(key)
                    break
        if grown == writers:
            break
        writers = grown
    return frozenset(name for _mod, name in writers)


def offenders_in(source: str, path: str,
                 extra_writers: frozenset[str] = frozenset()) -> list[str]:
    """Functions in *source* that write an archive and then remove a directory.

    The closure over *source*'s own definitions is unconditional. A name from
    *extra_writers* — the repo-wide closure of
    :func:`repo_archive_writers` — is followed only where this module **binds**
    it, by defining it or importing it by name. See the module docstring for
    why both halves of that rule are load-bearing.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - the tree parses
        return []

    calls = {(fn.lineno, fn.name): _called_names(fn) for fn in _functions(tree)}
    local = _definitions(tree)
    bound = set(local) | _imported_names(tree)

    writers = set(_ARCHIVE_WRITERS) | (set(extra_writers) & bound)
    while True:
        grown = writers | {n for n, callees in local.items() if callees & writers}
        if grown == writers:
            break
        writers = grown

    out = []
    for (lineno, name), callees in sorted(calls.items()):
        if not (callees & writers):
            continue
        if not (callees & _DIRECTORY_REMOVERS):
            continue
        if callees & _SAFE:
            continue
        out.append(f"{path}:{lineno} {name}()")
    return out


def _pairings() -> list[tuple[str, int, str]]:
    """Every archive-write-then-remove-directory pairing, allowlist included."""
    found: list[tuple[str, int, str]] = []
    writers = repo_archive_writers()
    for path in python_sources():
        for entry in offenders_in(path.read_text(encoding="utf-8"), rel(path),
                                  writers):
            location, _, name = entry.partition(" ")
            file_part, _, lineno = location.rpartition(":")
            found.append((file_part, int(lineno), name.rstrip("()")))
    return found


def offenders() -> list[str]:
    return [
        f"{file}:{lineno} {name}()"
        for file, lineno, name in _pairings()
        if (file, name) not in _SCRATCH_DIR_ALLOWLIST
    ]


def test_no_function_writes_an_archive_and_removes_a_directory():
    found = offenders()
    assert not found, (
        "These functions write an archive and then remove a directory without "
        "going through util.zip_write.replace_folder_with_archive, so every "
        "file the archive's filter skipped is destroyed with no copy "
        "anywhere:\n  " + "\n  ".join(found)
    )


def test_allowlist_is_live():
    """Every allowlisted site still names a real pairing.

    An exemption that has stopped describing anything is worse than no
    exemption: it reads as a reviewed decision while covering nothing, and it
    goes on covering the next pairing that lands under the same name.
    """
    real = {(file, name) for file, _lineno, name in _pairings()}
    stale = sorted(_SCRATCH_DIR_ALLOWLIST - real)
    assert not stale, (
        "These allowlist entries no longer name a function that writes an "
        "archive and removes a directory — delete them:\n  "
        + "\n  ".join(f"{file}::{name}" for file, name in stale)
    )
