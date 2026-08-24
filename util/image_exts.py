"""The one answer to "is this file one of the images a run processes?".

Two modules used to answer it separately, and they disagreed:

* ``process_microscopy._GROUP_IMAGE_EXTS`` — what the pipeline will actually
  group and segment out of an archive.
* ``services.remote_runner._IMAGE_MEMBER_EXTS`` — what the upload planner counts
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
pipeline must stay free of ``well_viewer`` / ``widgets`` / Qt
(``test_architecture_contracts.py::test_pipeline_is_self_sufficient``), and
``services/`` must stay free of Qt — ``util/`` satisfies both, which is why
``util/well_token.py`` already lives here for exactly this reason.

**Not every image-extension list in the app belongs here**, and merging them
would be wrong. They answer different questions and legitimately differ:
``pipeline.io._OUTPUT_IMAGE_EXTS`` is what the pipeline *writes*,
``util.schema_infer._TIF_SUFFIXES`` is what filename inference samples, and the
folder zipper takes ``.tif``/``.tiff`` only. This module is one question:
*would the pipeline group this file as input?*
"""

from __future__ import annotations

#: Suffixes the pipeline will group and process as run input. Lower-case, with
#: the leading dot, so a caller can test ``Path(name).suffix.lower() in`` it or
#: ``name.lower().endswith(tuple(...))``.
GROUP_IMAGE_EXTS: frozenset[str] = frozenset(
    {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
)


def is_group_image(name: str) -> bool:
    """Whether *name* is an image the pipeline would group as input.

    Takes a bare name or a path; only the suffix is read, so this answers one
    question and not two. ``.DS_Store`` has no image suffix and is rejected.
    ``._x.tif`` — an AppleDouble sidecar, which is not an image at all — has
    one, and is **accepted** here: a caller reading a real archive has to reject
    those itself, by name, before asking this. The docstring used to claim the
    opposite, which is the more dangerous way round to be wrong.
    """
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in GROUP_IMAGE_EXTS
