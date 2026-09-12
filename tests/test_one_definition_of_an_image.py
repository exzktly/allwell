"""The set of image extensions is spelled in exactly one place.

``util.image_exts.GROUP_IMAGE_EXTS`` is the answer to "would the pipeline group
this file as input?", and it was re-spelled as a literal in four more places —
most damningly in `process_microscopy.py`, which imports the real one at the
top of the file and then wrote a narrower list three times further down. The
narrow copies took ``.tif``/``.tiff`` only, so a PNG plate published an empty
``available_timepoints``, inferred no schema at all, and sized its disk
pre-flight from nothing.

The failure mode is not that a copy is wrong when written; it is that the two
drift. `services.remote_runner`'s copy already did exactly this once — its own
docstring claimed it "mirrors what ``find_image_groups_in_zip`` skips" while
listing three fewer suffixes, so a ``.png`` member was invisible to the upload
planner and was dropped from any archive rewritten for another reason. A silent
loss of input, from two spellings of one predicate.

So this is a syntactic rule, because the alternative — noticing in review that a
new tuple duplicates an old one — is what already failed twice.

**Not every image-extension list in the app belongs to this rule.** They answer
different questions and legitimately differ: ``pipeline.io._OUTPUT_IMAGE_EXTS``
is what the pipeline *writes*, and the folder zipper deliberately takes
``.tif``/``.tiff`` only because packing a well folder is a different question
from grouping run input. Those carry an explicit allow marker below.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: The one definition, and the two helpers every caller should reach for.
_FUNNEL = ("GROUP_IMAGE_EXTS", "is_group_image", "is_run_input_member")

#: Suffixes that make a collection an image-extension set rather than an
#: incidental tuple of strings. Two or more of these together is the signal.
_IMAGE_SUFFIXES = frozenset({
    ".tif", ".tiff", ".png", ".jpg", ".jpeg",
    "tif", "tiff", "png", "jpg", "jpeg",
    "*.tif", "*.tiff", "*.png", "*.jpg", "*.jpeg",
})

#: The surface this rule governs: the code that decides what a *run* takes as
#: input. Deliberately not the whole repo. `review/data/` answers "what can
#: Review display", which legitimately includes formats the pipeline never
#: groups, and `services/remote_runner.py`'s planner questions were funnelled
#: through `util.image_exts` in an earlier pass and are checked by
#: `tests/test_remote_manifest.py` instead. Widening this tuple is how the rule
#: grows; doing so without reading each new site is how an allowlist becomes a
#: rubber stamp.
_GOVERNED = (
    "pipeline/", "util/", "process_microscopy.py",
    "services/input_resolution_service.py",
    # well_plate_zipper is the *other half* of the input-resolution operation:
    # `tif_files_in` collects what a run will consume and deletes it on
    # success, and this script decides what actually gets grouped. Leaving it
    # outside meant the guard could not see a disagreement between the two —
    # and one shipped: the delete side was widened to PNG and JPEG, this side
    # was not, and a two-channel plate lost its fluorescent channel with no
    # copy anywhere. A guard that cannot see both sides of the predicate it
    # governs is a guard for one of them.
    "pipeline/well_plate_zipper.py",
)

#: A site is exempt only when its own line carries this marker with a reason.
#: **The site, not the file** (CLAUDE.md §7.5 #10). The first version of this
#: guard allowlisted whole files, and the proof run showed what that costs: it
#: reported one offending site against `origin/main` when there were five,
#: because four of them sat in a file the roster had exempted for the sake of a
#: single legitimate literal.
_MARKER = "aw-image-exts-ok:"

#: The one file-level exemption left, and it is the definition itself. The
#: entry for `pipeline/auto_threshold.py` was a cross-branch placeholder —
#: another group owned that file's sampler filter — and it is gone now that
#: that group has landed and converted it. Its removal was not remembered:
#: `test_the_file_level_exemptions_still_name_a_real_literal` went red on the
#: merge and said so, which is the whole reason an allowlist here carries a
#: liveness test beside it (CLAUDE.md §7.5 #10).
_ALLOWED_FILES: dict[str, str] = {
    "util/image_exts.py":
        "the one definition itself",
}


def _string_items(node: ast.AST) -> list[str]:
    """Every string constant a collection literal holds.

    Set / list / tuple displays, a dict's **keys and values** (a suffix set is
    just as much a set when it is spelled `{".tif": 1, ".tiff": 2}`), and both
    operands of a `+` or `|` — `(".tif",) + (".tiff",)` and
    `frozenset({".tif"}) | frozenset({".tiff"})` each hold one suffix per side
    and so slipped under a "two or more together" rule applied per node.

    All four shapes were found by a reviewer driving synthetic sources through
    this function; none was in the paired-case roster, which is the point of
    checking a guard against evasions rather than against the violation that
    prompted it.
    """
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(node, ast.Dict):
        return [e.value for e in (*node.keys, *node.values)
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.BitOr)):
        return _string_items(node.left) + _string_items(node.right)
    if isinstance(node, ast.Call):
        # `frozenset({...})`, `set([...])`, `tuple((...))` — the collection is
        # the argument, and the call is only how it is typed.
        out: list[str] = []
        for arg in node.args:
            out.extend(_string_items(arg))
        return out
    return []


def _image_ext_literals(tree: ast.AST, lines: list[str] | None = None
                        ) -> list[tuple[int, str]]:
    """Collection literals holding two or more image suffixes.

    Two rather than one, so an unrelated tuple that happens to mention ``.tif``
    — a docstring example, a single-suffix check — is not swept in. Two is what
    makes it a *set of image extensions* rather than a mention of one.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        items = _string_items(node)
        hits = [s for s in items if s.lower() in _IMAGE_SUFFIXES]
        if len(hits) < 2:
            continue
        lineno = getattr(node, "lineno", 0)
        if lines is not None and _marked(lines, lineno, node):
            continue
        out.append((lineno, ", ".join(sorted(hits))))
    return out


def _marked(lines: list[str], lineno: int, node: ast.AST) -> bool:
    """Whether the marker appears on any line the literal spans, or just above."""
    last = getattr(node, "end_lineno", lineno) or lineno
    for i in range(max(1, lineno - 1), min(len(lines), last) + 1):
        if _MARKER in lines[i - 1]:
            return True
    return False


def test_the_scan_can_see_the_one_definition():
    """Fail closed. A guard whose scan reaches nothing passes for ever.

    This is the anchor: if `util/image_exts.py` stops being visible to
    `python_sources` — a root renamed, a filter tightened — every assertion
    below becomes vacuously true and the rule silently stops existing.
    """
    seen = {rel(p) for p in python_sources()}
    assert "util/image_exts.py" in seen, (
        "the scan cannot see the module holding the one definition, so this "
        f"guard is measuring nothing. Scanned {len(seen)} file(s).")
    assert "process_microscopy.py" in seen, (
        "the scan cannot see process_microscopy.py, which is where four of "
        "the five copies lived")


def test_no_second_spelling_of_the_image_extension_set():
    offenders: list[str] = []
    for path in python_sources():
        name = rel(path)
        if not name.startswith(_GOVERNED) or name in _ALLOWED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        for lineno, hits in _image_ext_literals(tree, text.splitlines()):
            offenders.append(f"{name}:{lineno} → {hits}")

    assert not offenders, (
        "these build their own image-extension set instead of importing "
        f"{' / '.join(_FUNNEL)} from util.image_exts. Two spellings of one "
        "predicate drift, and the drift is silent — a .png member was once "
        "invisible to the upload planner for exactly this reason. Either call "
        "the funnel, or add the site to _ALLOWED with the reason it is a "
        f"different question by putting `# {_MARKER} <reason>` on its own "
        "line:\n  " + "\n  ".join(offenders))


def test_the_file_level_exemptions_still_name_a_real_literal():
    """An exemption nobody is watching is a licence (CLAUDE.md §7.5 #10).

    Only two files are exempt wholesale and both are meant to be temporary or
    definitional; this fails when one stops describing a real site.
    """
    live = set()
    for path in python_sources():
        name = rel(path)
        if name not in _ALLOWED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        if _image_ext_literals(tree):
            live.add(name)

    dead = sorted(set(_ALLOWED_FILES) - live)
    assert not dead, (
        "these file-level exemptions no longer name a real literal; delete "
        f"them so they cannot cover a future one: {dead}")


def test_a_marked_site_is_exempt_and_an_unmarked_one_is_not():
    """The marker mechanism itself, so a typo in it cannot silently exempt
    everything or nothing."""
    marked = ast.parse(f'X = (".tif", ".png")  # {_MARKER} output side\n')
    plain = ast.parse('X = (".tif", ".png")\n')
    src_marked = f'X = (".tif", ".png")  # {_MARKER} output side\n'.splitlines()

    assert not _image_ext_literals(marked, src_marked)
    assert _image_ext_literals(plain, ['X = (".tif", ".png")'])
