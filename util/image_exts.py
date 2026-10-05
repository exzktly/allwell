"""The one answer to "is this file one of the images a run processes?".

Two modules used to answer it separately, and they disagreed:

* ``process_microscopy._GROUP_IMAGE_EXTS`` — what the pipeline will actually
  group and segment out of an archive.
* ``services.remote_staging._IMAGE_MEMBER_EXTS`` — what the upload planner counts
  as an image when deciding whether a well archive has to be rewritten, and
  therefore which members survive that rewrite.

The second one's own docstring claimed it "mirrors what
``find_image_groups_in_zip`` already skips on the remote side". It did not: it
listed ``.tif`` and ``.tiff`` where the pipeline also takes ``.png``, ``.jpg``
and ``.jpeg``. A ``.png`` member was therefore invisible to the planner — it
could not cause a rewrite, but any archive rewritten for some other reason was
written without it, and the remote pipeline would have processed it. A silent
loss of input, from two spellings of one predicate.

So the set lives here. ``util/`` is the one package both sides may import: the
pipeline must stay free of ``review`` / ``widgets`` / Qt
(``test_architecture_contracts.py::test_pipeline_is_self_sufficient``), and
``services/`` must stay free of Qt — ``util/`` satisfies both, which is why
``util/well_token.py`` already lives here for exactly this reason.

**Not every image-extension list in the app belongs here**, and merging them
would be wrong. They answer different questions and legitimately differ:
``pipeline.io._OUTPUT_IMAGE_EXTS`` is what the pipeline *writes*. Filename
inference used to keep its own ``.tif``/``.tiff`` list and no longer does —
sampling a narrower set than the run groups meant a PNG plate had nothing to
infer from — so ``util.schema_infer`` now asks :func:`is_run_input_member` like
every other intent-side caller. This module is one question: *would the
pipeline group this file as input, and is it the user's data at all?*
"""

from __future__ import annotations

#: Suffixes the pipeline will group and process as run input. Lower-case, with
#: the leading dot, so a caller can test ``Path(name).suffix.lower() in`` it or
#: ``name.lower().endswith(tuple(...))``.
GROUP_IMAGE_EXTS: frozenset[str] = frozenset(
    {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
)


def is_group_image(name: str) -> bool:
    """Whether *name*'s **suffix** is one the pipeline groups as input.

    A pure suffix test, and only that. Takes a bare name or a path; only the
    part after the last dot is read.

    **This says nothing about whether the file is real.** ``._Plate_A01.tif``
    is an AppleDouble sidecar — four hundred bytes of macOS resource fork, not
    an image — and its suffix is ``.tif``, so this returns ``True`` for it, as
    it does for ``__MACOSX/._x.tif`` and for a dotfile like ``.hidden.tif``.
    An earlier version of this docstring claimed otherwise; the code always
    behaved this way, and the gap between the two is what let sidecars be
    counted as plate content in four separate places.

    Use :func:`is_run_input_member` for the question "is this one of the images
    this run is supposed to process?". Use this one only where the suffix
    itself is the question — notably the incremental done-set, whose behaviour
    is deliberately pinned by
    ``test_incremental_keys_shared.py::test_zip_reader_keeps_its_own_hidden_file_behaviour``
    so that a rule change here cannot silently alter what a resumed run
    considers already finished.
    """
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in GROUP_IMAGE_EXTS


def is_run_input_member(name: str) -> bool:
    """Whether *name* is one of the images a run is actually meant to process.

    The predicate for every *intent*-side question: how many images does this
    plate hold, how many FOV-groups should the pre-flight expect, how much disk
    will they need, which filenames should schema inference learn from. Those
    all ask about the user's data, and a plate carries a great deal that is not
    the user's data:

    * ``._Plate_A01_F1.tif`` — an AppleDouble sidecar, written by macOS beside
      every file on a non-HFS volume. One per image, so a plate copied through
      a USB stick or a network share reads as **twice** its real size.
    * ``__MACOSX/…`` — the same thing inside a zip, in its own directory.
    * ``.DS_Store``, ``.hidden.tif`` and any other leading-dot basename.
    * ``A01/`` and other zip **directory entries**, which have no bytes at all.

    Everything in that list answers "yes" to a bare suffix test, and everything
    unclassifiable answers "no" to a domain test applied outside its domain —
    which is the bug class CLAUDE.md §7.1 names: *a domain predicate applied
    outside its domain*. Deciding which items a predicate is entitled to judge
    is the first half of the rule; this function is that decision, written once.
    """
    if name.endswith("/"):
        return False                    # a zip directory entry holds no bytes
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith("."):
        return False                    # dotfiles, .DS_Store, AppleDouble twins
    if "__MACOSX/" in name or name.startswith("__MACOSX"):
        return False
    return is_group_image(base)
