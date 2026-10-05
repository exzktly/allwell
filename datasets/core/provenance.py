"""The ``derived_from`` entry a Datasets tool leaves in the dataset it wrote.

Subset, Merge Plates and Concatenate each build a new dataset out of one or two
others, and each finishes the same way: verify the copied output, then write
the destination's ``pipeline_info.json``. That write is the moment the new
dataset exists, so it is where the history (:mod:`util.provenance_log`) records
which datasets it came from. Recorded after the verification and the sidecar,
never before: a derivation that raised part-way has no ``pipeline_info.json``
and records nothing, rather than a history entry for a dataset that does not
exist.

The importers (CZI, ND2, OME-TIFF, loose TIFFs) are deliberately not covered.
They write raw input with no sidecar, so there is no directory that will hold
the dataset's history until its first Analyze run decides one — and that run is
recorded by the Analyze runner.
"""

from __future__ import annotations

from util.provenance_log import append_event

from datasets.core.dataset_paths import DatasetPaths


def record_derivation(dest: DatasetPaths, *, tool: str,
                      sources: tuple[DatasetPaths, ...], **detail) -> bool:
    """Append ``derived_from`` to *dest*'s log. ``True`` when it reached disk.

    *detail* carries whatever the tool's own summary says about the result
    (wells kept, the hour offset, the well remap), so the history reads the
    same as the dashboard did.
    """
    return append_event(dest.out_dir, "derived_from", source="datasets", detail={
        "tool": tool,
        "sources": [str(s.root) for s in sources],
        **detail,
    })
