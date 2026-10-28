"""One definition of "this well is finished", and the listing every remote reader takes it from (tracker P4).

"Is this well finished?" was answered six ways, and three of them — the remote pre-flight cache hit, the pre-staging
download (``services.remote_recover``) and the re-attach probe — counted any ``<well>_out.zip`` and ignored the
completion seal, while the host's own skip asked only whether the output existed. A run that ended with PARTIAL
wells then became "Remote pipeline completed successfully" on a plain re-run, and a well left part way was skipped
by the host on Resume and fetched as the result (B1, B2).

The answer now lives in one place, ``pipeline.run_report.finished_wells`` — output **and** seal, and an unreadable
listing finishes nothing — and the remote side reads a directory once, through
``services.remote_finished_wells.list_remote_outputs``, which says "unknown" (``None``) when its ssh fails rather
than "empty". This file holds both halves:

* the listing, driven against a real directory under ``bash`` standing in for the host, with the mess real output
  folders carry (AppleDouble sidecars, a half-written archive, a seal with no archive), a failed ssh and a
  truncated reply;
* a guard that no module outside the definition lists ``*_out.zip`` (a glob, an ``endswith``/``removesuffix`` on the
  archive suffix, or a regex for it) — every other such site is allowlisted **by function**, with the reason it is
  not deciding whether a well is finished. A new site fails until someone says which it is.
"""

from __future__ import annotations

import ast
import re
import subprocess
import types
from pathlib import Path

import pytest

from tests._repo_scan import parse_source, python_sources, rel

# ── the listing ─────────────────────────────────────────────────────────────


def _bash_cache(*, returncode: int | None = None, cut: bool = False):
    """A ``DatasetCache`` stand-in whose ``_ssh_script`` runs the script under local ``bash`` — or, with
    *returncode*, fails as ssh does (255: the connection; nothing printed), or with *cut* loses the reply's end."""
    def _ssh_script(script, *, check):
        if returncode is not None:
            return subprocess.CompletedProcess(["ssh"], returncode, "", "ssh: connect to host h: Connection refused")
        done = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=20)
        out = done.stdout
        if cut:
            out = "\n".join(out.splitlines()[:1]) + "\n"
        return subprocess.CompletedProcess(["bash"], done.returncode, out, done.stderr)

    return types.SimpleNamespace(_ssh_script=_ssh_script)


def _output_dir(tmp_path: Path) -> Path:
    from pipeline.run_report import complete_seal_path, withdrawn_note_path

    out = tmp_path / "runs" / "run-x" / "out"
    out.mkdir(parents=True)
    for well in ("A01", "B02", "C03"):
        (out / f"{well}_out.zip").write_bytes(b"zip")
    (out / "._A01_out.zip").write_bytes(b"appledouble")                # a macOS sidecar, not a well
    (out / ".D04_out.zip.tmp").write_bytes(b"half")                    # an archive still being written
    (out / ".DS_Store").write_bytes(b"")
    for well in ("A01", "E05"):                                          # E05: a seal with no output beside it
        complete_seal_path(out, well).parent.mkdir(exist_ok=True)
        complete_seal_path(out, well).write_text("{}")
    withdrawn_note_path(out, "B02").touch()                             # B02: rewritten, never sealed
    return out


def test_the_listing_reads_archives_and_seals(tmp_path):
    from services.remote_finished_wells import finished_on_remote, list_remote_outputs

    listing = list_remote_outputs(_bash_cache(), str(_output_dir(tmp_path)))
    assert listing is not None
    assert listing.with_output == {"A01", "B02", "C03"}, sorted(listing.with_output)
    assert listing.sealed == {"A01", "E05"}, sorted(listing.sealed)
    assert finished_on_remote(listing) == {"A01"}, (
        f"finished must be archive AND seal: got {sorted(finished_on_remote(listing))}")
    assert listing.part_way == {"B02", "C03"}


def test_a_directory_that_is_not_there_holds_nothing_and_says_so(tmp_path):
    from services.remote_finished_wells import RemoteOutputs, list_remote_outputs

    assert list_remote_outputs(_bash_cache(), str(tmp_path / "never-made")) == RemoteOutputs()


@pytest.mark.parametrize("cache", [_bash_cache(returncode=255), _bash_cache(cut=True)], ids=["ssh-failed", "cut"])
def test_a_listing_that_failed_is_unknown_not_empty(tmp_path, cache):
    """A1's shape, on this listing: a failed ssh read as "the remote holds nothing" is the trigger for most of the
    review's CRITICAL rows. Unknown is ``None``, and :func:`finished_on_remote` makes it "nothing finished"."""
    from services.remote_finished_wells import finished_on_remote, list_remote_outputs

    listing = list_remote_outputs(cache, str(_output_dir(tmp_path)))
    assert listing is None, f"a failed listing came back as a finding: {listing}"
    assert finished_on_remote(listing) == frozenset()


def test_the_definition_finishes_nothing_on_an_unknown_listing():
    from pipeline.run_report import finished_wells

    assert finished_wells({"A01"}, {"A01"}) == {"A01"}
    assert finished_wells({"A01", "B02"}, {"B02", "C03"}) == {"B02"}
    assert finished_wells(None, {"A01"}) == frozenset() and finished_wells({"A01"}, None) == frozenset()


# ── the guard ───────────────────────────────────────────────────────────────

#: The definition itself, and the one listing of a remote directory every remote reader takes it from.
DEFINITION = {
    "pipeline/run_report.py::<module>": "the output-file patterns `wells_left_part_way` reads, beside the seal",
    "pipeline/run_report.py::well_of_out_zip": "the one parse of an archive's name into its well",
    "services/remote_finished_wells.py::list_remote_outputs": "the one listing of a remote output directory",
}

#: Sites that list ``<well>_out.zip`` for something other than deciding whether a well is finished. Each names what
#: it does instead; an entry that stops matching anything fails :func:`test_every_allowlisted_site_still_lists`.
NOT_A_COMPLETION_DECISION = {
    "services/remote_fetch.py::FetchedOutputVerifier._list_completed_wells_in": (
        "verify's backstop: every well the remote holds an archive for is one verification must find here — sealed "
        "or not, since an unsealed archive is still output this run may have produced. It decides what to check "
        "locally, not what is finished (fetch slice, A1/A3)"),
    "review/persistence/cell_overrides.py::_fingerprints_for_data_dir": (
        "fingerprints a dataset's archives so saved per-cell overrides are dropped when the images change"),
    "review/persistence/cell_overrides.py::_well_zip_fingerprints": "the same fingerprint, per well",
    "review/features/smfish/tab_view.py::smfish_sync_from_app": "the smFISH tab lists the archives it can count "
                                                                "spots in",
    "review/features/smfish/worker.py::_write_counts_to_csvs": "message text naming the files, not a listing",
    "review/data/image_discovery.py::<module>": "Review's image resolver reads an archive's name into its well",
    "review/data/auto_threshold.py::_iter_well_zips": "the Review-side gating pass samples cells from archives",
    "review/data/auto_threshold.py::_input_zip_for": "the same pass, pairing an archive with its input",
    "pipeline/auto_threshold.py::_open_input_archive": "the end-of-run gating pass pairs an archive with its input",
    "pipeline/auto_threshold.py::estimate_thresholds_from_outputs": "the same pass, listing the archives it "
                                                                    "samples, and its log line",
    "pipeline/auto_threshold.py::_otsu_per_channel": "a warning naming the archives, not a listing",
    "pipeline/completeness.py::wells_with_output": (
        "the end-of-run completeness scan and Validate enumerate the wells an output folder holds evidence for, so "
        "a dataset whose inputs are gone still reports its wells; coverage, not whether a well may be skipped"),
    "datasets/core/dataset_paths.py::well_spelling_collisions": "a dataset tool refuses two spellings of one well",
    "datasets/core/dataset_paths.py::out_zips": "a dataset tool maps each well to its archive to copy or merge it",
}

_ARCHIVE_GLOB = re.compile(r"\*_out\.zip|_out\\\.zip")
_SUFFIX_CALLS = frozenset({"endswith", "removesuffix"})
_SUFFIXES = frozenset({"_out.zip", "_out"})


def _docstrings(tree: ast.Module) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _suffix_args(call: ast.Call) -> list[str]:
    values = []
    for arg in call.args:
        for elt in (arg.elts if isinstance(arg, ast.Tuple) else [arg]):
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
    return values


def archive_listing_sites(tree: ast.Module) -> set[str]:
    """``qualname`` (``<module>`` at the top level) of every place *tree* lists or parses ``<well>_out.zip``."""
    skip = _docstrings(tree)
    found: set[str] = set()

    def visit(node, scope: tuple[str, ...]):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        hit = False
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            hit = bool(_ARCHIVE_GLOB.search(node.value))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in _SUFFIX_CALLS):
            hit = any(v in _SUFFIXES for v in _suffix_args(node))
        if hit:
            found.add(".".join(scope) or "<module>")
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, ())
    return found


def _all_sites() -> dict[str, set[str]]:
    sites: dict[str, set[str]] = {}
    for path in python_sources():
        for qual in archive_listing_sites(parse_source(path)):
            sites.setdefault(rel(path), set()).add(qual)
    return sites


def _keys(sites: dict[str, set[str]]) -> set[str]:
    return {f"{path}::{qual}" for path, quals in sites.items() for qual in quals}


def test_nothing_outside_the_definition_lists_out_zip_to_decide_completion():
    unexplained = sorted(_keys(_all_sites()) - set(DEFINITION) - set(NOT_A_COMPLETION_DECISION))
    assert not unexplained, (
        "These list or parse `<well>_out.zip` outside the one definition of a finished well "
        "(pipeline.run_report.finished_wells, read through services.remote_finished_wells.list_remote_outputs on "
        "the remote). If the site decides whether a well is finished, ask the definition instead; if it does "
        "something else, add it to NOT_A_COMPLETION_DECISION with what it does:\n  " + "\n  ".join(unexplained))


#: Verify's backstop listing counts every archive, sealed or not — right for "what must verification find here",
#: wrong for "what is finished". The pre-flight, the re-attach probe and the pre-staging download all reached it
#: for the second question (tracker B2); only the verifier itself may call it.
_BACKSTOP = frozenset({"_list_completed_wells_in", "_list_remote_completed_wells"})
_BACKSTOP_OWNER = "services/remote_fetch.py"


def backstop_callers(tree: ast.Module) -> list[int]:
    return sorted(node.lineno for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute) and node.attr in _BACKSTOP)


def test_only_verification_reads_its_own_backstop_listing():
    offenders = [f"{rel(path)}:{line}" for path in python_sources() if rel(path) != _BACKSTOP_OWNER
                 for line in backstop_callers(parse_source(path))]
    assert not offenders, (
        "These call verification's archive listing, which counts unsealed archives as wells: ask "
        "services.remote_finished_wells.list_remote_outputs (finished = archive and seal) instead:\n  "
        + "\n  ".join(offenders))


def test_every_allowlisted_site_still_lists():
    """An entry that matches nothing is an exemption nobody can check — a renamed function, or a site that moved."""
    found = _keys(_all_sites())
    stale = sorted(set(DEFINITION) - found) + sorted(set(NOT_A_COMPLETION_DECISION) - found)
    assert not stale, "allowlisted sites that no longer list `<well>_out.zip`:\n  " + "\n  ".join(stale)
