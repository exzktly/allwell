"""The Segmentation tab's Crop-scope control.

The crop rectangle filters every frame in the dataset by default, which is right
for the case it was built for — an objective vignette or a stage artifact that
sits in the same image region everywhere. It is wrong for the other case: a
plate where only two wells were imaged with a bad edge, or a single timepoint
that drifted. Cropping the whole plate to fix those throws away good cells
everywhere else.

So the crop is armed by the same wells / FOVs / timepoints popover the
drag-delete on this tab uses — :func:`~review.features.preview.frame_scope_view.build_frame_scope_control`
— with the opposite un-armed default: an untouched field means *all* values, so
a crop nobody has scoped behaves exactly as it always has. The spec itself
(``CROP_SCOPE``) lives in ``features/crop/controller.py``, next to the Reset
actions and the ``Crop_Pass`` projection that also have to keep it honest.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from review.core.frame_scope import describe_scope
from review.features.crop.controller import CROP_SCOPE
from review.features.preview.frame_scope_view import (
    build_frame_scope_control,
)


def describe_crop_scope(ctx) -> str:
    """One-line readout of the frames the crop applies to."""
    return describe_scope(ctx, CROP_SCOPE)


def build_crop_scope_control(ctx, ctrl: QWidget):
    """Build the Crop-scope popover button for the Segmentation control band."""
    return build_frame_scope_control(ctx, ctrl, CROP_SCOPE)
