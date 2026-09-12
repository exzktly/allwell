"""The Segmentation tab's Delete-scope control.

A drag in Delete mode normally acts on the one frame on screen. Plate-scale
artifacts — a dust speck at a fixed stage position, a bright corner, a scratch on
the plate bottom — sit in the same image region across many wells, fields and
timepoints, and clearing those one frame at a time means repeating an identical
gesture hundreds of times.

This popover arms the next drag with a wider set of wells / FOVs / timepoints, so
one rectangle covers all of them. It writes nothing but
``ReviewImageState.delete_scope_*``; the delete itself is
``review_image/callbacks.py``.

The widget is the shared :func:`~review.features.preview.frame_scope_view.build_frame_scope_control`
— the crop on the same tab is scoped by the same control, with the opposite
un-armed default. Everything specific to the delete is the :data:`DELETE_SCOPE`
spec below: its fields live on ``ReviewImageState``, an un-armed field means
"the displayed one", and nothing has to be re-derived when it changes because
the scope is only read when a drag finishes.

The armed scope is legible without opening anything: the button's own text is the
readout ("Delete scope: this frame", "Delete scope: 3 wells · all FOVs · 2 TPs").
That is deliberate — a scope that stays armed across drags has to be visible on
the control row, or a later delete reaches further than the user remembers
asking for.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from review.core.frame_scope import (
    ScopeSpec, describe_scope, refresh_scope_button,
)
from review.features.preview.frame_scope_view import (
    build_frame_scope_control,
)

_SCOPE_BTN = "_review_delete_scope_btn"

DELETE_SCOPE = ScopeSpec(
    state_attr="review_image",
    fields=("delete_scope_wells", "delete_scope_fovs", "delete_scope_tps"),
    title="Delete scope",
    unarmed_text="this frame",
    unarmed_field_words=("this well", "this FOV", "this TP"),
    button_key=_SCOPE_BTN,
    seed_current_frame=True,
    hint="Apply a drag-delete to:",
    tooltip=(
        "Which frames the next drag-delete applies to.\n"
        "• On the displayed frame a cell goes if any of its pixels are in the "
        "box.\n"
        "• On every other frame in scope a cell goes if its centroid is in the "
        "box (its mask is not loaded, so the CSV's centroid decides).\n"
        "The scope stays armed until you reset it."
    ),
    reset_label="Reset to this frame",
    reset_tooltip=(
        "Disarm the scope: a drag-delete goes back to acting on the displayed "
        "frame only."
    ),
    reset_status="Delete scope: this frame only.",
    plate_status_ok="Delete scope: armed {n} well(s) from the plate selection.",
    plate_status_empty="Delete scope: no wells are selected on the plate.",
)


def describe_delete_scope(ctx) -> str:
    """One-line readout of the armed delete scope, used as the button's label."""
    return describe_scope(ctx, DELETE_SCOPE)


def refresh_delete_scope_button(ctx) -> None:
    """Re-label the scope button. Safe to call when the tab was never built."""
    refresh_scope_button(ctx, DELETE_SCOPE)


def build_delete_scope_control(ctx, ctrl: QWidget):
    """Build the Delete-scope popover button for the Segmentation control band."""
    return build_frame_scope_control(ctx, ctrl, DELETE_SCOPE)
