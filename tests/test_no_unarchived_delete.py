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

**Archive-writing is followed one call deep**, because two of the four sites
did not name ``atomic_zip`` themselves — ``stage_local_zips`` zips through
``_zip_well_folder`` and ``compress_folder_images_to_zip_and_remove`` through
``compress_images_to_zip``. A guard that only looked for a literal
``atomic_zip`` call in the same function found two of the four and would have
called the sweep done.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

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


def offenders_in(source: str, path: str) -> list[str]:
    """Functions in *source* that write an archive and then remove a directory.

    Propagation is **within the module only**. That is what the four real sites
    need — ``stage_local_zips`` and ``_zip_well_folder`` are both in
    ``remote_runner.py``, ``compress_folder_images_to_zip_and_remove`` and
    ``compress_images_to_zip`` both in ``process_microscopy.py`` — and it is
    what keeps the closure meaningful. Matching callee names across the whole
    repository instead pulls in every same-named helper in the tree and grows
    the writer set until it includes ``remove_directory`` itself.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - the tree parses
        return []

    calls = {(fn.lineno, fn.name): _called_names(fn) for fn in _functions(tree)}
    local: dict[str, set[str]] = {}
    for (_lineno, name), callees in calls.items():
        local.setdefault(name, set()).update(callees)

    writers = set(_ARCHIVE_WRITERS)
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
    for path in python_sources():
        for entry in offenders_in(path.read_text(encoding="utf-8"), rel(path)):
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
