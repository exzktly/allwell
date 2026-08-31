"""The Analyze form and ``build_pipeline_args`` agree which routine a field is for.

``stardist_tile_px`` is read by exactly one segmentation routine, and its widget
appears on exactly one page of the segmentation stack. Two places have to know
that, and they are in layers that cannot import one another —
``analyze.fields`` is the form's boundary validator and ``services`` must
stay free of the form — so the rule is restated rather than shared, and a
restated rule is two rules the day one of them changes.

The gap this closes had both halves wrong in opposite directions at once. The
validator refused a stale value under *every* routine, so a leftover ``-4``
blocked a Cellpose run over a control that routine does not show. The emitter
sent a *valid* one to argv under every routine, and argv is what
``util.settings_digest`` hashes — so a tile size left in the box changed a
Cellpose plate's reuse key and forced it to recompute for a setting that cannot
reach its pixels.
"""

from __future__ import annotations

from analyze.fields import _METHOD_GATED_INT_FIELDS
from services.pipeline_service import build_pipeline_args

_METHODS = ("stardist_nuclei", "stardist_seeded_watershed_cell", "cellpose",
            "cellpose_labelfree", "classical_threshold")


def _argv(method: str, tile: str) -> list[str]:
    """A complete-enough form, differing only in the two fields under test."""
    opts = dict(
        nuclear_token="NIR", fluor_tokens=["GFP"], csv_prefix="m",
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_", min_nucleus_area_px=50, smfish_tokens=[],
        shape_features=[], tf_threads="0", workers="0", flatfield="none",
        background_well="", qc_remove_debris=False, qc_min_area=0.0,
        qc_max_area=0.0, qc_min_solidity=0.0,
        segmentation_method=method, stardist_tile_px=tile,
    )
    return build_pipeline_args("pm.py", "in", "out", opts)


def test_the_form_gates_the_tile_size_on_stardist():
    assert _METHOD_GATED_INT_FIELDS.get("stardist_tile_px") == "stardist", (
        "the form's gate moved; the emitter below is keyed on the same prefix")


def test_a_stardist_run_still_carries_the_tile_size():
    for method in _METHODS:
        argv = _argv(method, "512")
        carried = "--stardist_tile_px" in argv
        assert carried == method.startswith("stardist"), (
            f"{method}: --stardist_tile_px {'reached' if carried else 'did not reach'} "
            "argv, which is the opposite of what the form's gate says")


def test_the_two_gates_agree_for_every_routine():
    """The property, stated once: what the form validates is what argv carries."""
    for method in _METHODS:
        form_checks = not (
            (needs := _METHOD_GATED_INT_FIELDS.get("stardist_tile_px"))
            and not method.startswith(needs))
        argv_carries = "--stardist_tile_px" in _argv(method, "512")
        assert form_checks == argv_carries, (
            f"{method}: the form {'checks' if form_checks else 'ignores'} the "
            f"tile size while argv {'carries' if argv_carries else 'drops'} it")


def test_a_blank_tile_size_reaches_argv_from_nowhere():
    for method in _METHODS:
        assert "--stardist_tile_px" not in _argv(method, "")
        assert "--stardist_tile_px" not in _argv(method, "0")
