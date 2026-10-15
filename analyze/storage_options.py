"""The Analyze form's two image-storage checkboxes.

*Reference phase input* is ``process_microscopy --phase_output``: ticked (the
default) the phase / brightfield channel's ``_tophat`` is not written, because
that channel is never corrected and the input archive already holds its frame.
*Store corrected images at source bit depth* is ``--tophat_dtype``: ticked (the
default) each ``_tophat`` is rounded back to its input's integer type when every
value fits. ``pipeline/output_images.py`` says what each does to the archive,
and why neither changes a measured number.

The run options are spelled exactly as the pipeline spells them —
``phase_output`` / ``tophat_dtype`` with the CLI's string values — so a queued
job, a restored sidecar (``execution_options``) and the argv builder all read
one key, and no bridge in ``analyze/domain/job_edit.py`` is needed.

Held apart from ``analyze/form.py`` for the reason ``analyze/qc_section.py`` is:
that file sits at its line budget.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from pipeline.output_policy import (
    PHASE_OUTPUT_COPY,
    PHASE_OUTPUT_REFERENCE,
    TOPHAT_DTYPE_FLOAT32,
    TOPHAT_DTYPE_SOURCE,
)
from widgets.wrap_checkbox import WrapCheckBox

PHASE_REFERENCE_TIP = (
    "The phase / brightfield channel is never background-corrected, so its "
    "corrected image is the input image unchanged. Ticked, the run does not "
    "save it a second time into <WELL>_out.zip — Review shows the phase frame "
    "from the input archive in/<WELL>.zip instead, so keep the in/ folder with "
    "the dataset. Unticked, the phase image is resaved into the output archive "
    "as before. Measurements are identical either way.")

SOURCE_DTYPE_TIP = (
    "Ticked, each background-corrected image (_tophat) is saved in the bit "
    "depth its input was recorded at (8- or 16-bit), rounded to the nearest "
    "count, instead of as 32-bit floating point — about half the bytes before "
    "compression and far less after it. A frame whose corrected values do not "
    "fit (a flat-field correction can lift pixels above the camera's maximum), "
    "or whose input was already floating point, is kept as 32-bit. "
    "Measurements are computed before saving and are identical either way; "
    "only images viewed in Review differ, by at most half a count. smFISH and "
    "cytoplasm intermediates always stay 32-bit.")


def build_storage_rows(form, sec: QWidget) -> None:
    """Add the two checkboxes to *sec*, both ticked, as ``_phase_reference_cb``
    and ``_tophat_source_dtype_cb`` on *form*."""
    form._phase_reference_cb = WrapCheckBox(
        "Reference the phase input image instead of resaving it", sec)
    form._phase_reference_cb.setChecked(True)
    form._phase_reference_cb.setToolTip(PHASE_REFERENCE_TIP)
    sec.layout().addWidget(form._phase_reference_cb)
    form._tophat_source_dtype_cb = WrapCheckBox(
        "Store corrected images at the input's bit depth", sec)
    form._tophat_source_dtype_cb.setChecked(True)
    form._tophat_source_dtype_cb.setToolTip(SOURCE_DTYPE_TIP)
    sec.layout().addWidget(form._tophat_source_dtype_cb)


def storage_options(form) -> dict:
    """The run options the two checkboxes contribute, in the pipeline's spelling."""
    return {
        "phase_output": (PHASE_OUTPUT_REFERENCE if form._phase_reference_cb.isChecked()
                         else PHASE_OUTPUT_COPY),
        "tophat_dtype": (TOPHAT_DTYPE_SOURCE if form._tophat_source_dtype_cb.isChecked()
                         else TOPHAT_DTYPE_FLOAT32),
    }


def restore_storage_options(form, d: dict) -> None:
    """Tick the boxes from a sidecar's or a queued job's options.

    A value that is absent, or is not one of the pipeline's choices, leaves the
    box as it is: a run that predates the options made no decision about them,
    and inheriting "copy / float32" from it would quietly turn the defaults off
    for every plate re-run from an old folder.
    """
    phase = d.get("phase_output")
    box = getattr(form, "_phase_reference_cb", None)
    if box is not None and phase in (PHASE_OUTPUT_REFERENCE, PHASE_OUTPUT_COPY):
        box.setChecked(phase == PHASE_OUTPUT_REFERENCE)
    dtype = d.get("tophat_dtype")
    box = getattr(form, "_tophat_source_dtype_cb", None)
    if box is not None and dtype in (TOPHAT_DTYPE_SOURCE, TOPHAT_DTYPE_FLOAT32):
        box.setChecked(dtype == TOPHAT_DTYPE_SOURCE)
