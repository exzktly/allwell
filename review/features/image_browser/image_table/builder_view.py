"""Image Table — the root + nested-channels drag-and-drop builder shelf.

This is the primary authoring surface for the Image Table (replacing the old
"Build Layout" dropdown strip). Each axis (Rows / Columns) is a small tree: a
**root** facet dimension (Well / Timepoint / FOV) with **channels** nested,
indented, under it. Each nested channel carries its own render options — raw /
corrected · mask · ≥threshold, a boundary-outline toggle, colormap + LUT — set
from a ⚙ menu on the chip. A derived **Fixed** panel lists every root dimension
not on an axis, pinned to one value (drag it onto an axis to make it vary; an ×
on a root drops it back). A root's channels get a **Separate ↔ Overlay** switch
(composite). Wells come from the sidebar plate (``active_wells``); no well combo.

The shelf edits a Qt-free :class:`~...state.TableSpec` held on
``ctx.runtime.image_table.builder_spec`` and, on every edit (debounced), calls
``build_layout.image_table_apply_spec`` which flattens it onto the existing
layout state — but does *not* render: the grid only draws when the user presses
**Generate**. Between an edit and that press the header cues "edited — press
Generate". The tree is the single source of truth; the per-cell selector grid +
the render seam are downstream of it.

ctx-native — reads ``ctx.state`` / ``ctx.runtime`` / ``ctx.widgets`` only, never the
unwrapped app god-object (the tab-unwrap guardrail is 0). Styling: a scoped QSS
block (``_qss_for_shelf``) on the shelf root reproduces the design mockup with the app's
theme tokens (mirrors ``ui/theme.py``).
"""

from __future__ import annotations

from string import Template

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ui import theme

from core.safe import swallow
from widgets import menu_exec
from review.core.ui_helpers import parse_float_edit
from widgets.stepper import Stepper
from widgets.lut_cmaps import resolve_channel_tint
from review.features.image_browser.image_table.state import (
    Axis, ChannelSpec, spec_from_state,
)
from review.features.image_browser.image_table.build_layout import (
    image_table_apply_spec,
)
from review.features.image_browser.image_table.grid_layout import (
    _channel_options, _fov_options, _timepoint_options, _well_options,
)
from review.features.image_browser.image_table.image_compose import (
    LUT_COLOR_NAMES, LUT_COLORS, _is_binary_variant, _is_nuc_seg,
)
from widgets.owned_action import owned_action

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_SW_W = 11
_SW_H = 11
_WRAP_W = 84
_Z_MIN_H = 96

_MIME = "application/x-aw-imgtable-token"
_ROOT_DIMS = ("well", "timepoint", "fov")
_ROOT_LABEL = {"well": "Well", "timepoint": "Timepoint", "fov": "FOV"}
_RENDER_MODES = [("corrected", "corrected"), ("raw", "raw"),
                 ("mask", "cell mask"), ("threshold", "≥ threshold")]
_RENDER_SHORT = {"corrected": "corr", "raw": "raw", "mask": "mask", "threshold": "≥thr"}

# Colour swatch per root dimension + a rough colour per colormap (mirrors the
# LUT the render actually applies; the chip swatch is a glanceable hint).
_DIM_SWATCH = {
    "well": theme.Colors.accent,
    "timepoint": theme.Colors.warn,
    "fov": theme.Colors.text_secondary,
}
_CMAP_SWATCH = {
    "Greys": "#C8CDD8", "Reds": "#F87171", "Greens": "#4ADE80", "Blues": "#6EA8FF",
    "Purples": "#A78BFA", "Oranges": "#FB923C", "Cyan": "#22D3EE", "Magenta": "#E879F9",
    "viridis": "#4ADE80", "magma": "#F472B6", "inferno": "#F59E0B", "cividis": "#6EA8FF",
}

# Recognised fluorophores → their conventional display colour. Matched
# case-insensitively as a substring of the channel token, so "eGFP", "GFP-A",
# "mCherry" … all resolve; unrecognised channels fall back to the nearest-tint
# default. Longer/more-specific tokens are listed first so they win.
_STANDARD_CMAP = (
    ("mcherry", "Reds"), ("hoechst", "Blues"),
    ("gfp", "Greens"), ("rfp", "Reds"), ("yfp", "Oranges"),
    ("bfp", "Cyan"), ("cfp", "Cyan"), ("dapi", "Blues"), ("nir", "Magenta"),
)

# Scoped stylesheet — applied to the shelf root; object-name selectors keep it
# from leaking onto the rest of the app. Colours mirror ``ui/theme.py`` tokens.
_QSS_TEMPLATE = Template("""
QWidget#ITBShelf { background: transparent; }
QLabel#ITBTitle { color: $text_primary; font-weight: 640; font-size: ${fs_title}px; }
QLabel#ITBShape { color: $text_secondary; font-size: ${fs_caption}px; }
QLabel#ITBShape b { color: $text_primary; }

QFrame#ITBZone {
    border: 1px dashed $border; border-radius: 9px; background: $panel;
}
QFrame#ITBZone[dropActive="true"] { border: 1px dashed $accent; background: $accent_dim; }
QFrame#ITBFixed { border: 1px solid $border; border-radius: 9px; background: $panel; }
QFrame#ITBTray { border: 1px solid $border_subtle; border-radius: 9px; background: $rail; }

QLabel#ITBHead { color: $text_primary; font-weight: 620; font-size: ${fs_small}px; }
QLabel#ITBSub  { color: $text_muted; font-size: ${fs_xs}px; }
QLabel#ITBHint { color: $text_muted; font-style: italic; font-size: ${fs_caption}px; }

QFrame#ITBRoot {
    background: $accent_dim; border: 1px solid $accent; border-radius: 7px;
}
QLabel#ITBRootLbl { color: $text_primary; font-weight: 640; font-size: ${fs_small}px; }

QWidget#ITBNest { border-left: 2px solid $accent_dim; background: transparent; }

QFrame#ITBChan, QFrame#ITBChip {
    background: $panel_elevated; border: 1px solid $border; border-radius: 7px;
}
QFrame#ITBChan:hover, QFrame#ITBChip:hover { border-color: $accent; }
/* Reorder cue: the edge a dragged channel would be inserted at. The rule is
   drawn 2px against the pill's 1px resting border, which nudges the pill's
   contents by a pixel while the drag hovers — cheap, and unmistakable about
   which side the drop lands on. */
QFrame#ITBChan[dropEdge="above"] { border-top: 2px solid $accent; }
QFrame#ITBChan[dropEdge="below"] { border-bottom: 2px solid $accent; }
QLabel#ITBChanLbl { color: $text_primary; font-size: ${fs_small}px; font-weight: 560; }
QLabel#ITBVar { color: $text_secondary; font-size: ${fs_var}px; }
QLabel#ITBGrip { color: $text_muted; font-size: ${fs_caption}px; }
QLabel#ITBNote { color: $text_muted; font-size: ${fs_xs}px; }

QToolButton#ITBOpt {
    color: $text_secondary; background: $panel; border: 1px solid $border;
    border-radius: 5px; padding: 3px 8px; font-size: ${fs_small}px;
}
QToolButton#ITBOpt:hover { color: $text_primary; border-color: $accent; }
QToolButton#ITBOpt::menu-indicator { image: none; width: 0px; }

QToolButton#ITBX {
    color: $text_muted; background: transparent; border: none;
    font-size: ${fs_h3}px; padding: 0px 4px;
}
QToolButton#ITBX:hover { color: $danger; }

QToolButton#ITBSeg {
    background: $panel; color: $text_secondary; border: 1px solid $border;
    padding: 3px 11px; font-size: ${fs_caption}px;
}
QToolButton#ITBSeg:hover { color: $text_primary; }
QToolButton#ITBSeg[on="true"] { background: $accent_dim; color: $accent_fg; }
QToolButton#ITBSeg[on="true"][ov="true"] { background: $threshold_dim; color: $threshold; }

QComboBox#ITBCombo {
    background: $panel_elevated; color: $text_primary; border: 1px solid $border;
    border-radius: 6px; padding: 2px 6px; min-height: 20px;
}
QComboBox#ITBCombo:hover { border-color: $border_strong; }
QLabel#ITBFixLbl { color: $text_primary; font-size: ${fs_small}px; }
QLabel#ITBEq { color: $text_muted; font-size: ${fs_small}px; }

QFrame#ITBLut { border: 1px solid $border; border-radius: 9px; background: $panel; }
QFrame#ITBLutRow { background: transparent; border: none; }
QLabel#ITBLutChan { color: $text_primary; font-size: ${fs_small}px; font-weight: 560; }
QLabel#ITBLutTag { color: $text_muted; font-size: ${fs_caption}px; }
QLineEdit#ITBLutEdit {
    background: $panel_elevated; color: $text_primary; border: 1px solid $border;
    border-radius: 6px; padding: 2px 6px; min-height: 18px; max-width: 66px;
}
QLineEdit#ITBLutEdit:focus { border-color: $accent; }
QToolButton#ITBLutFit {
    color: $text_secondary; background: $panel; border: 1px solid $border;
    border-radius: 5px; padding: 2px 9px; font-size: ${fs_caption}px;
}
QToolButton#ITBLutFit:hover { color: $text_primary; border-color: $accent; }

QToolButton#ITBClear {
    color: $text_secondary; background: $panel; border: 1px solid $border;
    border-radius: 6px; padding: 3px 10px; font-size: ${fs_caption}px;
}
QToolButton#ITBClear:hover { color: $danger; border-color: $danger; }
""")


def _qss_for_shelf() -> str:
    """The shelf's stylesheet at the tokens in force *now*.

    Built when the shelf is, not at import: a sheet frozen at import carries
    whatever type sizes were current then, so a shelf first opened in phone
    layout would get desktop-sized text. The sizes are the theme's type
    scale — ``fs_xs`` is one step under the caption size, which is where the
    shelf's two smallest labels sit, and the half steps keep the two
    sizes the shelf was drawn with between the scale's own steps.
    """
    t = theme.Typography
    return _QSS_TEMPLATE.substitute(
        vars(theme.Colors), fs_xs=t.caption_size - 1, fs_var=t.caption_size - 0.5,
        fs_caption=t.caption_size, fs_small=t.small_size,
        fs_title=t.small_size + 0.5, fs_h3=t.h3_size)


# ── spec accessors ──────────────────────────────────────────────────────────

def _rt(ctx):
    return ctx.runtime.image_table


def _base_channels(ctx) -> list[str]:
    """Real channel tokens for the Channels tray — the ``(binary)`` variants are
    a render *option* here, not separate tray chips."""
    return [c for c in _channel_options(ctx) if not _is_binary_variant(c)]


def _root_values(ctx, dim: str) -> list[str]:
    if dim == "well":
        sel = sorted(ctx.state.image_table.active_wells or set(),
                     key=lambda w: (str(w),))
        return sel or _well_options(ctx)
    if dim == "timepoint":
        return _timepoint_options(ctx)
    if dim == "fov":
        return _fov_options(ctx)
    return []


def _default_value(ctx, dim: str) -> str:
    """The value a Fixed root is pinned to when nothing has been chosen — the
    first of its options. For FOV that is the first FOV in the run, which is what
    the fresh-tab default pins so the opening grid shows one field of view rather
    than multiplying by all of them."""
    vals = _root_values(ctx, dim)
    return vals[0] if vals else ""


def builder_spec(ctx):
    """The live builder tree, seeded from the current state on first access."""
    spec = getattr(_rt(ctx), "builder_spec", None)
    if spec is None:
        spec = _default_spec(ctx)
        _rt(ctx).builder_spec = spec
    return spec


def _default_spec(ctx):
    """Reconstruct from state if it already describes a table, else the
    fresh-tab default: **wells down the rows with each well's channels nested
    under it, timepoints across the columns, FOV pinned**.

    Rows are ``well`` with the channels nested inside (``Axis.root`` outer,
    ``Axis.channels`` inner) rather than channels alone, so opening the tab
    shows the plate you loaded rather than a single well's channels.

    Only the **last** timepoint is selected. The column axis is timepoint, so
    selecting all of them would multiply the grid by the length of the run — a
    96-well plate with 3 channels and 12 timepoints is 288 rows × 12 columns of
    decoded images before the user has touched anything. The last timepoint is
    the one worth seeing first, and the picker adds the rest.
    """
    st = ctx.state.image_table
    if st.row_dims or st.col_dims:
        return spec_from_state(st)
    chans = _base_channels(ctx)
    spec = spec_from_state(st)  # empty axes + current filters
    if chans:
        spec.rows = Axis(root="well", channels=[
            ChannelSpec(c, "corrected", colormap=_default_colormap_for(ctx, c))
            for c in chans])
        if _timepoint_options(ctx):
            spec.cols = Axis(root="timepoint")
    for dim in _ROOT_DIMS:
        spec.fixed.setdefault(dim, _default_value(ctx, dim))
    return spec


def _fixed_dims(spec) -> list[str]:
    return [d for d in _ROOT_DIMS if spec.rows.root != d and spec.cols.root != d]


# ── spec mutations ──────────────────────────────────────────────────────────

def _move_facet(ctx, spec, dim: str, target: str) -> None:
    if spec.rows.root == dim:
        spec.rows.root = None
    if spec.cols.root == dim:
        spec.cols.root = None
    if target == "rows":
        spec.rows.root = dim
    elif target == "cols":
        spec.cols.root = dim
    # target "fixed" (or anything else): off both axes → auto-listed under Fixed
    spec.fixed.setdefault(dim, _default_value(ctx, dim))


def _add_channel(ctx, spec, name: str, target: str, at: int | None = None) -> None:
    """Nest a fresh channel *name* under the *target* axis. *at* is the position
    it lands in, counted against the axis's channel list as it stands on entry;
    None appends."""
    ax = getattr(spec, target)
    other = spec.cols if target == "rows" else spec.rows
    if other.channels:                      # channels cohere on one axis
        ax.channels.extend(other.channels)
        other.channels = []
        other.overlay = False
    cs = ChannelSpec(channel=name, render="corrected",
                     colormap=_default_colormap_for(ctx, name))
    if at is None:
        ax.channels.append(cs)
    else:
        ax.channels.insert(max(0, min(int(at), len(ax.channels))), cs)


def _move_channel(spec, src_axis: str, idx: int, target: str,
                  at: int | None = None) -> None:
    """Move the channel at *idx* on *src_axis* onto the *target* axis, landing at
    position *at* — counted against the target axis's channel list as it stands
    on entry, i.e. the index of the pill it was dropped in front of. None appends,
    which is what a drop on the axis zone's own background means.

    Passing *at* is what makes an in-place reorder possible: with only the append
    behaviour, dragging a channel anywhere within its own axis sent it to the
    bottom of the list, and dragging the bottom one did nothing at all."""
    src = getattr(spec, src_axis)
    if not (0 <= idx < len(src.channels)):
        return
    cs = src.channels.pop(idx)
    tgt = getattr(spec, target)
    other = spec.cols if target == "rows" else spec.rows
    if other is not tgt and other.channels:
        tgt.channels.extend(other.channels)
        other.channels = []
        other.overlay = False
    if at is None:
        tgt.channels.append(cs)
        return
    pos = int(at)
    if src is tgt and idx < pos:
        pos -= 1        # the pop above shifted every later item down by one
    tgt.channels.insert(max(0, min(pos, len(tgt.channels))), cs)


def _remove_channel(spec, axis: str, idx: int) -> None:
    ax = getattr(spec, axis)
    if 0 <= idx < len(ax.channels):
        ax.channels.pop(idx)


# ── apply (debounced) ───────────────────────────────────────────────────────

def _schedule_apply(ctx) -> None:
    rt = _rt(ctx)
    t = getattr(rt, "builder_timer", None)
    if t is None:
        t = QTimer()
        t.setSingleShot(True)
        t.setInterval(120)
        t.timeout.connect(lambda: _apply_now(ctx))
        rt.builder_timer = t
    t.start()


def _apply_now(ctx) -> None:
    # Flatten the spec onto state + rebuild the selector layout, but do NOT
    # render — the table only draws when the user presses Generate.
    with swallow("image table builder: apply spec"):
        image_table_apply_spec(ctx, builder_spec(ctx), render=False)


def builder_flush_pending(ctx) -> None:
    """Force any debounced shelf edit to land NOW (called from the Generate action).

    Shelf edits schedule ``_apply_now`` on a 120 ms single-shot timer
    (``_schedule_apply``). A Generate click inside that window would otherwise
    render the *stale* cell model — the apply fires just after — so the grid takes
    two clicks to catch up. Flushing the pending timer synchronously here makes the
    first Generate reflect the latest edit."""
    rt = _rt(ctx)
    t = getattr(rt, "builder_timer", None)
    if t is not None and t.isActive():
        t.stop()
        _apply_now(ctx)


def schedule_selection_rebuild(ctx) -> None:
    """Rebuild the selector grid (debounced) when the sidebar plate selection changes.

    Selecting / drag-selecting wells (or Select All / Clear) only updated
    ``active_wells`` + the plate display before — nothing re-ran the layout, so a
    Wells row/column axis stayed frozen at whatever the selection was when the shelf
    was last edited. A user who built "channels × wells" with one well selected and
    then picked three more still saw a single column. This re-flattens the live spec
    against the new selection, reusing the shelf's 120 ms debounce so a drag-select's
    per-cell ``selectionChanged`` burst coalesces into one rebuild (and a Generate
    inside that window flushes it via ``builder_flush_pending``). No render — the
    grid draws on Generate, matching a shelf edit. No-op before the shelf has
    authored a spec."""
    if getattr(_rt(ctx), "builder_spec", None) is None:
        return
    _schedule_apply(ctx)


def _edit(ctx) -> None:
    """After any shelf edit: mark the table stale (so the header cues a Generate),
    redraw the shelf, and schedule a render-less layout rebuild. Rendering is
    gated on the Generate button — no live redraw on edit."""
    _rt(ctx).builder_dirty = True
    builder_refresh(ctx)
    _schedule_apply(ctx)


def builder_mark_inclusion_stale(ctx) -> None:
    """Cue a Generate after the inclusion layers changed under a drawn grid.

    The grid draws only the cells the analysis counts, but it draws them once:
    a cell deleted on the Segmentation tab, or excluded by a crop, keeps its
    outline in an already-rendered table until the next Generate. Re-rendering
    here instead would be worse — a plate-wide grid is seconds of decoding, and
    an inclusion edit is something a user does in runs of ten — so the table
    says it is out of date rather than quietly disagreeing with every plot.

    Only ever a cue: nothing about the layout changed, so unlike a shelf edit
    this schedules no re-apply. No-op when the tab was never built.
    """
    rt = _rt(ctx)
    if getattr(rt, "cells", None) is None:
        return
    rt.builder_dirty = True
    if getattr(rt, "rows_zone_body", None) is not None:
        _refresh_shape(ctx, builder_spec(ctx))


def builder_notify_rendered(ctx) -> None:
    """Clear the "edited — press Generate" cue once the table has rendered. Called
    from the Generate action so the header reflects that the grid is now current."""
    rt = _rt(ctx)
    rt.builder_dirty = False
    if getattr(rt, "rows_zone_body", None) is not None:
        _refresh_shape(ctx, builder_spec(ctx))


def _set_wrap(ctx, n: int) -> None:
    builder_spec(ctx).wrap = max(0, int(n))
    _edit(ctx)


def _set_plate(ctx, on: bool) -> None:
    spec = builder_spec(ctx)
    spec.plate = bool(on)
    if spec.plate:
        spec.wrap = 0            # plate and wrap are mutually exclusive arrangements
    _edit(ctx)


def _wrap_applicable(spec) -> bool:
    """Wrap applies to any axis pairing except plate mode, which fixes both axes
    to the well row/col dims and so has nowhere to band. Nested axes (a root with
    channels under it) band on the column *product*, so they wrap like the flat
    single-dimension case rather than being excluded from it."""
    return not getattr(spec, "plate", False)


def _refresh_wrap(ctx, spec) -> None:
    w = getattr(_rt(ctx), "wrap_stepper", None)
    if w is None:
        return
    w.setEnabled(_wrap_applicable(spec))
    with swallow("image table builder: wrap sync"):
        w.blockSignals(True)
        w.setValue(float(spec.wrap or 0))
        w.blockSignals(False)


def _refresh_plate(ctx, spec) -> None:
    btn = getattr(_rt(ctx), "plate_btn", None)
    if btn is None:
        return
    with swallow("image table builder: plate sync"):
        btn.blockSignals(True)
        btn.setChecked(bool(getattr(spec, "plate", False)))
        btn.blockSignals(False)


def builder_clear(ctx) -> None:
    """Empty the builder (top half): drop every root + nested channel and clear
    any value filters, so the user can assemble a fresh table. Leaves the
    rendered grid untouched — 'Clear images' clears the bottom half."""
    spec = builder_spec(ctx)
    spec.rows = Axis(root=None, channels=[])
    spec.cols = Axis(root=None, channels=[])
    spec.filters = {}
    for dim in _ROOT_DIMS:
        spec.fixed.setdefault(dim, _default_value(ctx, dim))
    _edit(ctx)


# ── drag-and-drop primitives ────────────────────────────────────────────────

class _Chip(QFrame):
    """A draggable chip carrying a token string. Child labels are made
    mouse-transparent by the builders so a drag starts from anywhere on the
    chip while its ⚙ / × buttons stay clickable."""

    def __init__(self, token: str, parent=None) -> None:
        super().__init__(parent)
        self._token = token
        self._press = None

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            # Accept the press rather than chaining up to QFrame, whose default
            # implementation *ignores* it and so re-delivers the press to the
            # parent zone. Measured on Linux/Qt 6.7.3, the implicit mouse grab
            # stays on this chip either way and the drag still starts; accepting
            # is defensive — it matches Qt's own drag-and-drop example and leaves
            # no room for a platform to route the gesture's move events to the
            # zone instead, which would leave the chip unable to move at all.
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._press = None
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        # Clear the press *before* exec(): the drop rebuilds the shelf, which
        # reparents and deletes this chip, so nothing may touch self afterwards.
        hotspot = self._press
        self._press = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_MIME, self._token.encode("utf-8"))
        drag.setMimeData(mime)
        # Carry the chip itself as the drag image, so the gesture reads as moving
        # this chip rather than as an anonymous cursor change.
        with swallow("image table builder: drag pixmap"):
            drag.setPixmap(self.grab())
            drag.setHotSpot(hotspot)
        drag.exec(Qt.MoveAction)


def _token_of(e) -> str:
    return bytes(e.mimeData().data(_MIME).data()).decode("utf-8")


def _drop_ok(e, accepts) -> bool:
    """True when *e* carries one of this shelf's tokens and *accepts* (which may
    be None, meaning anything) admits it."""
    if not e.mimeData().hasFormat(_MIME):
        return False
    return True if accepts is None else bool(accepts(_token_of(e)))


class _ChanPill(_Chip):
    """A nested-channel chip that is itself a drop target, so a channel dropped
    onto it lands *at that position* instead of being appended to the end of the
    axis. The half of the pill under the cursor picks the side — top half inserts
    above, bottom half below — and that side is drawn as an accent rule while the
    drag hovers. Without this the only drop target was the axis zone as a whole,
    whose handler can only append."""

    def __init__(self, token: str, accepts, on_drop, parent=None) -> None:
        super().__init__(token, parent)
        self._on_drop = on_drop           # (token, after: bool) -> None
        self._accepts = accepts
        self.setProperty("dropEdge", "none")
        self.setAcceptDrops(True)

    def _ok(self, e) -> bool:
        return _drop_ok(e, self._accepts)

    def _after(self, e) -> bool:
        return e.position().toPoint().y() >= self.height() / 2

    def _set_edge(self, edge: str) -> None:
        if self.property("dropEdge") == edge:
            return
        self.setProperty("dropEdge", edge)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e) -> None:
        if self._ok(e):
            self._set_edge("below" if self._after(e) else "above")
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        if self._ok(e):
            self._set_edge("below" if self._after(e) else "above")
            e.acceptProposedAction()

    def dragLeaveEvent(self, e) -> None:
        self._set_edge("none")

    def dropEvent(self, e) -> None:
        after = self._after(e)
        self._set_edge("none")
        if self._ok(e):
            tok = _token_of(e)
            with swallow("image table builder: reorder drop"):
                self._on_drop(tok, after)
            e.acceptProposedAction()


class _DropZone(QFrame):
    """A frame that accepts token drops and forwards them to *on_drop*, with a
    drag-over highlight. *accepts* (token -> bool) gates which payloads light the
    zone up and are actually dropped, so a zone never shows a false "will accept"
    highlight for a token its handler would ignore."""

    def __init__(self, on_drop, accepts=None, parent=None) -> None:
        super().__init__(parent)
        self._on_drop = on_drop
        self._accepts = accepts
        self.setAcceptDrops(True)

    def _set_active(self, on: bool) -> None:
        self.setProperty("dropActive", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _ok(self, e) -> bool:
        return _drop_ok(e, self._accepts)

    def dragEnterEvent(self, e) -> None:
        if self._ok(e):
            self._set_active(True)
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        if self._ok(e):
            e.acceptProposedAction()

    def dragLeaveEvent(self, e) -> None:
        self._set_active(False)

    def dropEvent(self, e) -> None:
        self._set_active(False)
        if self._ok(e):
            tok = _token_of(e)
            with swallow("image table builder: drop"):
                self._on_drop(tok)
            e.acceptProposedAction()


# ── small styled widget helpers ─────────────────────────────────────────────

def _swatch(color: str) -> QLabel:
    sw = QLabel()
    sw.setFixedSize(_SW_W, _SW_H)
    sw.setStyleSheet(
        f"background:{color}; border-radius:3px; border:1px solid rgba(255,255,255,0.16);")
    sw.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return sw


def _grip() -> QLabel:
    g = QLabel("⣿")
    g.setObjectName("ITBGrip")
    g.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return g


def _text(text: str, obj: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(obj)
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return lbl


def _x_btn(tip: str, on_click) -> QToolButton:
    b = QToolButton()
    b.setObjectName("ITBX")
    b.setText("✕")
    b.setCursor(Qt.PointingHandCursor)
    b.setToolTip(tip)
    b.clicked.connect(lambda _=False: on_click())
    return b


def _cmap_swatch(cmap: str) -> str:
    return _CMAP_SWATCH.get(cmap, "#C8CDD8")


def _standard_cmap(name: str) -> str | None:
    """The conventional display colour for a recognised fluorophore in *name*
    (GFP→Greens, RFP→Reds, BFP→Cyan, NIR→Magenta, DAPI→Blues, …), or None."""
    s = str(name or "").strip().lower()
    for token, cmap in _STANDARD_CMAP:
        if token in s:
            return cmap
    return None


def _default_colormap_for(ctx, name: str) -> str:
    """The channel's default display colormap. A recognised fluorophore gets its
    conventional colour (GFP→Greens, BFP→Cyan, NIR→Magenta, …); otherwise the
    named LUT colour nearest the channel's palette tint. This is what a tray chip's
    swatch shows *and* what the channel adopts when dragged in — so the colour
    carries through instead of resetting to grey — and it stays fully editable in
    the LUT box below."""
    std = _standard_cmap(name)
    if std is not None:
        return std
    with swallow("image table builder: default colormap"):
        names = _base_channels(ctx)
        idx = names.index(name) if name in names else 0
        tr, tg, tb = resolve_channel_tint(name, idx, None)
        best, best_d = "Greys", None
        for cname, rgb in LUT_COLORS.items():
            if rgb is None:                      # Greys carries no tint
                continue
            d = (rgb[0] - tr) ** 2 + (rgb[1] - tg) ** 2 + (rgb[2] - tb) ** 2
            if best_d is None or d < best_d:
                best, best_d = cname, d
        return best
    return "Greys"


# ── drop acceptance (so a zone never highlights for a token it ignores) ──────

def _accept_axis(tok: str) -> bool:
    return tok in _ROOT_DIMS or tok.startswith(("newchan:", "chan:"))


def _accept_fixed(tok: str) -> bool:
    return tok in _ROOT_DIMS          # only roots pin under Fixed


def _accept_tray(tok: str) -> bool:
    return tok.startswith("chan:")    # only an on-axis channel drops back to remove


def _accept_chan(tok: str) -> bool:
    # A channel pill takes channels only. A root dropped on one is left
    # unaccepted so the event propagates out to the axis zone behind it.
    return tok.startswith(("chan:", "newchan:"))


# ── shelf construction ──────────────────────────────────────────────────────

def build_shelf(ctx, parent: QWidget) -> None:
    """Build the builder shelf into *parent* (called from build_image_table_tab)."""
    reg = ctx.widgets
    # Expose the selection-rebuild trigger on the runtime so the sidebar plate /
    # Select All / Clear can re-run the layout on a well-set change without
    # importing this view module (matching the row/col_dims_refresh callback hooks).
    _rt(ctx).selection_rebuild_cb = lambda: schedule_selection_rebuild(ctx)
    parent.setObjectName("ITBShelf")
    parent.setStyleSheet(_qss_for_shelf())
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(9)

    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(10)
    title = QLabel("Build", parent)
    title.setObjectName("ITBTitle")
    header.addWidget(title)
    shape = QLabel("", parent)
    shape.setObjectName("ITBShape")
    header.addWidget(shape, 1)
    _rt(ctx).shape_label = shape

    # Plate: arrange each well's image in true 96-well-plate positions — rows label
    # A–H, columns 1–12 — using the builder's first channel as the (fixed) image.
    # Correctly labels every plate row, unlike the Wrap-N band trick (which only
    # labels the first band). Wells come from the sidebar plate selection.
    plate_btn = QToolButton(parent)
    plate_btn.setObjectName("ITBClear")
    plate_btn.setText("Plate A–H × 1–12")
    plate_btn.setCheckable(True)
    plate_btn.setCursor(Qt.PointingHandCursor)
    plate_btn.setToolTip("Lay each selected well's image out in its 96-well-plate "
                         "position (rows A–H, columns 1–12), using the first channel "
                         "in the builder. Wells come from the plate selection.")
    plate_btn.toggled.connect(lambda on: _set_plate(ctx, bool(on)))
    reg.register("_image_table_plate_btn", plate_btn)
    _rt(ctx).plate_btn = plate_btn
    header.addWidget(plate_btn)

    # Wrap cols: fold the columns into vertically-stacked bands of N — e.g. the
    # first N wells across all channels, then the next N below. Writes the spec's
    # wrap; disabled only in plate mode, which pins both axes to the plate's own
    # row/column dims and so has nothing left to band.
    wrap_lbl = QLabel("Wrap cols:", parent)
    wrap_lbl.setObjectName("ITBSub")
    header.addWidget(wrap_lbl)
    wrap = Stepper(parent, value=float(getattr(builder_spec(ctx), "wrap", 0) or 0),
                   minimum=0.0, maximum=50.0, single_step=1.0, decimals=0)
    wrap.setFixedWidth(_WRAP_W)
    wrap.setToolTip("Wrap the columns into vertically-stacked bands of this many "
                    "columns (e.g. the first N wells across all channels, then the "
                    "next N below). 0 = off. Unavailable in plate mode.")
    wrap.valueChanged.connect(lambda v: _set_wrap(ctx, int(v)))
    reg.register("_image_table_wrap_stepper", wrap)
    _rt(ctx).wrap_stepper = wrap
    header.addWidget(wrap)

    clear_btn = QToolButton(parent)
    clear_btn.setObjectName("ITBClear")
    clear_btn.setText("Clear builder")
    clear_btn.setCursor(Qt.PointingHandCursor)
    clear_btn.setToolTip("Remove every root + channel and start the table fresh "
                         "(leaves the rendered images — use 'Clear images' for those).")
    clear_btn.clicked.connect(lambda: builder_clear(ctx))
    reg.register("_image_table_clear_builder_btn", clear_btn)
    header.addWidget(clear_btn)
    layout.addLayout(header)

    # Channels tray (drag source) at the top.
    tray = _DropZone(lambda tok: _on_tray_drop(ctx, tok), _accept_tray, parent)
    tray.setObjectName("ITBTray")
    tl = QVBoxLayout(tray)
    tl.setContentsMargins(11, 8, 11, 9)
    tl.setSpacing(6)
    tl.addWidget(_zone_head("Channels", "drag onto a root to nest it"))
    tbody = QWidget(tray)
    tbl = QHBoxLayout(tbody)
    tbl.setContentsMargins(0, 0, 0, 0)
    tbl.setSpacing(7)
    tl.addWidget(tbody)
    reg.register("_image_table_channels_tray", tray)
    _rt(ctx).channels_tray_body = tbody
    layout.addWidget(tray)

    # Rows / Columns axis zones, side by side.
    axes_row = QWidget(parent)
    ar = QHBoxLayout(axes_row)
    ar.setContentsMargins(0, 0, 0, 0)
    ar.setSpacing(10)
    for name, heading, sub in (("rows", "Rows", "root ↓ · channels nested"),
                               ("cols", "Columns", "root → · channels nested")):
        z = _DropZone(lambda tok, n=name: _on_zone_drop(ctx, n, tok),
                      _accept_axis, axes_row)
        z.setObjectName("ITBZone")
        z.setProperty("dropActive", "false")
        z.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        z.setMinimumHeight(_Z_MIN_H)
        zl = QVBoxLayout(z)
        zl.setContentsMargins(11, 9, 11, 11)
        zl.setSpacing(7)
        zl.addWidget(_zone_head(heading, sub))
        body = QWidget(z)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        zl.addWidget(body)
        zl.addStretch(1)
        reg.register(f"_image_table_{name}_zone", z)
        setattr(_rt(ctx), f"{name}_zone_body", body)
        ar.addWidget(z, 1)
    layout.addWidget(axes_row)

    # Fixed panel (derived) — a drop target for roots + its value pickers.
    fixed = _DropZone(lambda tok: _on_zone_drop(ctx, "fixed", tok), _accept_fixed, parent)
    fixed.setObjectName("ITBFixed")
    fl = QVBoxLayout(fixed)
    fl.setContentsMargins(11, 8, 11, 9)
    fl.setSpacing(7)
    fl.addWidget(_zone_head("Fixed", "same value for every cell · drag a root here to pin it"))
    fbody = QWidget(fixed)
    fbl = QHBoxLayout(fbody)
    fbl.setContentsMargins(0, 0, 0, 0)
    fbl.setSpacing(8)
    fl.addWidget(fbody)
    reg.register("_image_table_fixed_panel", fixed)
    _rt(ctx).fixed_panel_body = fbody
    layout.addWidget(fixed)

    # LUT panel (derived) — one row per distinct channel in the table, for its
    # display colormap + min/max range. Not a drop target.
    lut = QFrame(parent)
    lut.setObjectName("ITBLut")
    lutl = QVBoxLayout(lut)
    lutl.setContentsMargins(11, 8, 11, 9)
    lutl.setSpacing(7)
    lutl.addWidget(_zone_head("LUT", "display range per channel · shown once even if it repeats"))
    lbody = QWidget(lut)
    lbl_lay = QVBoxLayout(lbody)
    lbl_lay.setContentsMargins(0, 0, 0, 0)
    lbl_lay.setSpacing(5)
    lutl.addWidget(lbody)
    reg.register("_image_table_lut_panel", lut)
    _rt(ctx).lut_box_body = lbody
    layout.addWidget(lut)

    builder_refresh(ctx)
    # No auto-apply on build: the shelf stays inert until the user edits it, so it
    # never clobbers the initial grid (built by image_table_rebuild_grid) or
    # whatever a restore / the Advanced controls / a test sets up. The spec is
    # seeded from current state, so it already reflects reality; the first edit
    # renders.


def _zone_head(name: str, sub: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    lbl = QLabel(name)
    lbl.setObjectName("ITBHead")
    h.addWidget(lbl)
    subl = QLabel(sub)
    subl.setObjectName("ITBSub")
    h.addWidget(subl)
    h.addStretch(1)
    return w


def _on_zone_drop(ctx, zone: str, tok: str) -> None:
    spec = builder_spec(ctx)
    if tok in _ROOT_DIMS:
        _move_facet(ctx, spec, tok, zone)
    elif zone in ("rows", "cols") and tok.startswith("newchan:"):
        _add_channel(ctx, spec, tok[len("newchan:"):], zone)
    elif zone in ("rows", "cols") and tok.startswith("chan:"):
        _, axis, idx = tok.split(":", 2)
        _move_channel(spec, axis, int(idx), zone)
    else:
        return
    # Assigning a root/channel to an axis is a manual row/col layout — it's
    # mutually exclusive with Plate mode, which otherwise overrides BOTH axes with
    # well_row/well_col (spec_to_state_fields) and silently discards this drop. Exit
    # plate mode so the drop takes effect (the button re-syncs via _refresh_plate).
    if zone in ("rows", "cols") and getattr(spec, "plate", False):
        spec.plate = False
    _edit(ctx)


def _on_pill_drop(ctx, axis: str, idx: int, tok: str, after: bool) -> None:
    """A channel was dropped onto the nested pill at *idx* on *axis*: put it there,
    before or after that pill, rather than at the end of the list. This is the
    reorder path — dropping a channel back onto its own axis anywhere else can
    only append."""
    spec = builder_spec(ctx)
    at = idx + 1 if after else idx
    if tok.startswith("newchan:"):
        _add_channel(ctx, spec, tok[len("newchan:"):], axis, at=at)
    elif tok.startswith("chan:"):
        _, src_axis, src_idx = tok.split(":", 2)
        _move_channel(spec, src_axis, int(src_idx), axis, at=at)
    else:
        return
    if getattr(spec, "plate", False):
        spec.plate = False      # same manual-layout exit as _on_zone_drop
    _edit(ctx)


def _on_tray_drop(ctx, tok: str) -> None:
    if tok.startswith("chan:"):
        _, axis, idx = tok.split(":", 2)
        _remove_channel(builder_spec(ctx), axis, int(idx))
        _edit(ctx)


# ── shelf refresh (rebuild dynamic contents from the spec) ───────────────────

def _clear(w: QWidget) -> None:
    lay = w.layout()
    if lay is None:
        return
    while lay.count():
        it = lay.takeAt(0)
        cw = it.widget()
        if cw is not None:
            cw.setParent(None)
            cw.deleteLater()


def builder_refresh(ctx) -> None:
    """Redraw the shelf (trees + Fixed + tray) from the current spec. No-op before
    the shelf is built (the body handles are absent)."""
    rt = _rt(ctx)
    if getattr(rt, "rows_zone_body", None) is None:
        return
    spec = builder_spec(ctx)
    _build_tree(ctx, spec, "rows")
    _build_tree(ctx, spec, "cols")
    _build_fixed(ctx, spec)
    _build_lut_box(ctx, spec)
    _build_tray(ctx, spec)
    _refresh_shape(ctx, spec)
    _refresh_wrap(ctx, spec)
    _refresh_plate(ctx, spec)


def _refresh_shape(ctx, spec) -> None:
    lbl = getattr(_rt(ctx), "shape_label", None)
    if lbl is None:
        return

    if getattr(spec, "plate", False):
        chans = spec.rows.channels or spec.cols.channels
        chan = str(chans[0].channel).upper() if chans else "—"
        bits = [f"<b>Plate</b> · rows <b>A–H</b> ↓ · cols <b>1–12</b> → · {chan}"]
        if getattr(_rt(ctx), "builder_dirty", False):
            bits.append(
                f"<span style='color:{theme.Colors.threshold};font-weight:640'>"
                "edited — press Generate</span>")
        lbl.setText("  ·  ".join(bits))
        return

    def axis_txt(ax):
        parts = []
        if ax.root:
            parts.append(_ROOT_LABEL[ax.root])
        if ax.channels:
            names = ",".join(c.channel for c in ax.channels)
            parts.append(("⧉[" if ax.overlay else "[") + names + "]")
        return " › ".join(parts)

    r, c = axis_txt(spec.rows), axis_txt(spec.cols)
    fx = ", ".join(f"{_ROOT_LABEL[d]}={spec.fixed.get(d, '')}" for d in _fixed_dims(spec))
    bits = []
    if r:
        bits.append(f"<b>{r}</b> ↓")
    if c:
        bits.append(f"<b>{c}</b> →")
    if fx:
        bits.append(f"fixed {fx}")
    if getattr(_rt(ctx), "builder_dirty", False):
        bits.append(
            f"<span style='color:{theme.Colors.threshold};font-weight:640'>"
            "edited — press Generate</span>")
    lbl.setText("  ·  ".join(bits))


def _build_tree(ctx, spec, axis: str) -> None:
    body = getattr(_rt(ctx), f"{axis}_zone_body")
    _clear(body)
    lay = body.layout()
    ax = getattr(spec, axis)

    # root row
    if ax.root:
        lay.addWidget(_root_pill(ctx, spec, axis, ax.root))
    else:
        lay.addWidget(_text("drop a Well / Timepoint / FOV root", "ITBHint"))

    # nested channels (indented under a left accent rail)
    if ax.channels:
        nest = QWidget(body)
        nest.setObjectName("ITBNest")
        nl = QVBoxLayout(nest)
        nl.setContentsMargins(11, 2, 0, 2)
        nl.setSpacing(6)
        for idx, cs in enumerate(ax.channels):
            nl.addWidget(_chan_pill(ctx, spec, axis, idx, cs))
        lay.addWidget(nest)
        lay.addWidget(_overlay_toggle(ctx, ax))
    elif ax.root:
        # root present but no channels yet — invite a drop (mirrors the mockup)
        lay.addWidget(_text("drop channels here", "ITBHint"))


def _root_pill(ctx, spec, axis: str, dim: str) -> QFrame:
    pill = _Chip(dim)
    pill.setObjectName("ITBRoot")
    h = QHBoxLayout(pill)
    h.setContentsMargins(9, 4, 5, 4)
    h.setSpacing(7)
    h.addWidget(_swatch(_DIM_SWATCH.get(dim, theme.Colors.text_secondary)))
    lbl = _text(_ROOT_LABEL[dim], "ITBRootLbl")
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents, False)  # clickable for filter
    lbl.setCursor(Qt.PointingHandCursor)
    lbl.setToolTip("Wells come from the plate — click to focus it" if dim == "well"
                   else "Click to choose which values appear on this axis")
    lbl.mousePressEvent = lambda _e, d=dim, a=lbl: _open_root_filter(ctx, d, a)
    h.addWidget(lbl)
    h.addStretch(1)
    h.addWidget(_x_btn("Move to Fixed",
                       lambda d=dim: (_move_facet(ctx, spec, d, "fixed"), _edit(ctx))))
    return pill


def _chan_pill(ctx, spec, axis: str, idx: int, cs: ChannelSpec) -> QFrame:
    pill = _ChanPill(f"chan:{axis}:{idx}", _accept_chan,
                     lambda tok, after, a=axis, i=idx: _on_pill_drop(ctx, a, i, tok, after))
    pill.setObjectName("ITBChan")
    h = QHBoxLayout(pill)
    h.setContentsMargins(8, 3, 5, 3)
    h.setSpacing(7)
    h.addWidget(_grip())
    h.addWidget(_swatch(_cmap_swatch(cs.colormap)))
    h.addWidget(_text(str(cs.channel).upper(), "ITBChanLbl"))
    h.addStretch(1)
    gear = QToolButton(pill)
    gear.setObjectName("ITBOpt")
    gear.setText(f"⚙ {_render_label(cs)}")
    gear.setCursor(Qt.PointingHandCursor)
    gear.setPopupMode(QToolButton.InstantPopup)
    # Build the menu LAZILY on open, not eagerly for every chip at load. Parent it
    # to the button (setMenu doesn't take ownership, so a parentless QMenu is
    # GC'd) and (re)fill it in aboutToShow — so nothing is constructed until the
    # user actually clicks a ⚙, and no long-lived menu objects accumulate across
    # refreshes (which, with native macOS menus + GC timing, could fault).
    menu = QMenu(gear)
    menu.aboutToShow.connect(lambda m=menu, c=cs: _fill_channel_menu(ctx, m, c))
    gear.setMenu(menu)
    h.addWidget(gear)
    h.addWidget(_x_btn("Remove channel",
                       lambda a=axis, i=idx: (_remove_channel(spec, a, i), _edit(ctx))))
    return pill


def _overlay_toggle(ctx, ax) -> QWidget:
    wrap = QWidget()
    hl = QHBoxLayout(wrap)
    hl.setContentsMargins(11, 2, 0, 0)
    hl.setSpacing(0)
    seg = QFrame(wrap)
    seg.setStyleSheet(
        f"QFrame{{border:1px solid {theme.Colors.border};border-radius:7px;}}")
    sl = QHBoxLayout(seg)
    sl.setContentsMargins(0, 0, 0, 0)
    sl.setSpacing(0)

    sep = QToolButton(seg)
    sep.setObjectName("ITBSeg")
    sep.setText("Separate")
    sep.setCursor(Qt.PointingHandCursor)
    sep.setProperty("on", "false" if ax.overlay else "true")
    ovl = QToolButton(seg)
    ovl.setObjectName("ITBSeg")
    ovl.setText("⧉ Overlay")
    ovl.setCursor(Qt.PointingHandCursor)
    ovl.setProperty("on", "true" if ax.overlay else "false")
    ovl.setProperty("ov", "true")
    sep.setToolTip("One row/column per channel")
    ovl.setToolTip("Blend this root's channels into one cell")

    def _set(overlay: bool) -> None:
        ax.overlay = overlay
        _edit(ctx)
    sep.clicked.connect(lambda: _set(False))
    ovl.clicked.connect(lambda: _set(True))
    sl.addWidget(sep)
    sl.addWidget(ovl)
    hl.addWidget(seg)
    hl.addStretch(1)
    return wrap


def _build_fixed(ctx, spec) -> None:
    body = getattr(_rt(ctx), "fixed_panel_body")
    _clear(body)
    lay = body.layout()
    dims = _fixed_dims(spec)
    if not dims:
        lay.addWidget(_text("every dimension is on an axis", "ITBHint"))
        lay.addStretch(1)
        return
    for dim in dims:
        chip = _Chip(dim)
        chip.setObjectName("ITBChip")
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(9, 3, 9, 3)
        cl.setSpacing(7)
        cl.addWidget(_grip())
        cl.addWidget(_swatch(_DIM_SWATCH.get(dim, theme.Colors.text_secondary)))
        chip.setToolTip("Drag onto Rows or Columns to make it vary")
        cl.addWidget(_text(_ROOT_LABEL[dim], "ITBFixLbl"))
        cl.addWidget(_text("=", "ITBEq"))
        combo = QComboBox(chip)
        combo.setObjectName("ITBCombo")
        vals = _root_values(ctx, dim)
        combo.addItems(vals)
        cur = spec.fixed.get(dim) or (vals[0] if vals else "")
        if cur and combo.findText(cur) >= 0:
            combo.setCurrentText(cur)
        spec.fixed[dim] = combo.currentText()

        def _changed(_i, d=dim, cb=combo):
            spec.fixed[d] = cb.currentText()
            # Like every other edit, a Fixed-value change leaves the grid stale
            # until Generate — flag it so the header cues that (a full
            # builder_refresh would tear down this combo mid-selection, so just
            # set the flag + repaint the shape line).
            _rt(ctx).builder_dirty = True
            _schedule_apply(ctx)
            _refresh_shape(ctx, spec)
        combo.currentIndexChanged.connect(_changed)
        cl.addWidget(combo)
        if dim == "well":
            cl.addWidget(_text("(from plate)", "ITBNote"))
        lay.addWidget(chip)
    lay.addStretch(1)


def _has_display_range(cs) -> bool:
    """Whether *cs* renders a greyscale intensity image the LUT window applies to.

    A binary render (``mask`` / ``≥threshold``) paints a black-and-white silhouette
    and the ``NUC+SEG`` channel is a pre-rendered RGB segmentation overlay; the
    render seam (``image_compose._compose_image_table_rgb``) passes both through
    without consulting the LUT window, colormap, or log flag. Neither has a display
    range, so neither may be offered one.
    """
    return (cs.render in ("raw", "corrected")
            and not _is_nuc_seg(cs.channel))


def _axis_channels(spec) -> list:
    """The channel specs on whichever axis carries them (``TableSpec.channel_axis``)."""
    ax = spec.channel_axis() if hasattr(spec, "channel_axis") else None
    if ax == "rows":
        return list(spec.rows.channels)
    if ax == "cols":
        return list(spec.cols.channels)
    return []


def _plate_shown_token(spec) -> str | None:
    """In plate mode, the one channel token the grid actually displays.

    Plate mode overrides both axes with the plate's rows and columns
    (``state.spec_to_state_fields``), so there is nowhere left for a channel axis
    to tile: it pins ``fixed_vals["channel"]`` to the FIRST channel in the shelf
    and every cell shows that one. ``None`` outside plate mode, where all of them
    are on the grid.
    """
    if not getattr(spec, "plate", False):
        return None
    channels = _axis_channels(spec)
    return channels[0].axis_token() if channels else None


def _table_channels(spec) -> dict:
    """Distinct LUT-adjustable channels in the table, in row-then-column order,
    mapped to every ChannelSpec that shares that channel — so a channel shown once
    here edits all of its instances.

    Keyed by the BASE channel (not ``axis_token``): a channel with no display range
    (:func:`_has_display_range`) never gets a LUT row, because editing one would
    repaint nothing. A channel shown as both its fluorescence and its ≥Thresh binary
    therefore appears exactly once (its fluorescence instance drives the LUT); a
    channel shown only as binary/mask, or the ``NUC+SEG`` overlay, gets no LUT row.

    In plate mode only one channel reaches the grid (:func:`_plate_shown_token`),
    so only that one gets a row — a row for a channel with no cells would edit a
    value nothing reads.
    """
    shown = _plate_shown_token(spec)
    seen: dict = {}
    for axis in ("rows", "cols"):
        for cs in getattr(spec, axis).channels:
            if not _has_display_range(cs):
                continue
            if shown is not None and cs.axis_token() != shown:
                continue
            key = str(cs.channel or "").strip().upper()
            if key:
                seen.setdefault(key, []).append(cs)
    return seen


def _plate_hidden_channels(spec) -> list[str]:
    """Axis tokens dropped in the shelf that plate mode does not display.

    Only :func:`_plate_shown_token` reaches the grid; the rest have a display
    range and would be perfectly adjustable on any other layout, so their absence
    is about the layout rather than about them. Editing one used to repaint zero
    of the grid's cells with nothing said — the all-or-nothing sibling of the
    partial repaint the inert list below covers.
    """
    shown = _plate_shown_token(spec)
    if shown is None:
        return []
    out: list[str] = []
    for cs in _axis_channels(spec):
        if not _has_display_range(cs):
            continue  # already named as having no display range at all
        tok = cs.axis_token()
        if tok and tok != shown and tok not in out:
            out.append(tok)
    return out


def _lut_inert_channels(spec) -> list[str]:
    """Axis tokens in the table the LUT panel does NOT govern, in axis order.

    These are the cells that keep the same pixels through every LUT edit — binary
    and mask renders, and the pre-rendered ``NUC+SEG`` overlay. They still draw an
    image, so without naming them a LUT edit reads as "only some of the grid
    updated": the panel lists a row per adjustable channel and says nothing about
    the rest. Listing them makes the shortfall visible in the control itself.
    """
    out: list[str] = []
    for axis in ("rows", "cols"):
        for cs in getattr(spec, axis).channels:
            if _has_display_range(cs):
                continue
            # ``axis_token`` already names the render — a mask channel is
            # "GFP (mask)" — so this reads as the grid labels it.
            tok = cs.axis_token()
            if tok and tok not in out:
                out.append(tok)
    return out


def _build_lut_box(ctx, spec) -> None:
    body = getattr(_rt(ctx), "lut_box_body", None)
    if body is None:
        return
    _clear(body)
    lay = body.layout()
    chans = _table_channels(spec)
    inert = _lut_inert_channels(spec)
    hidden = _plate_hidden_channels(spec)
    if not chans and not inert and not hidden:
        lay.addWidget(_text("channels dragged into the table appear here", "ITBHint"))
        return
    for token, specs in chans.items():
        lay.addWidget(_lut_row(ctx, token, specs))
    if inert:
        lay.addWidget(_text(
            "no display range (unchanged by any LUT edit): " + ", ".join(inert),
            "ITBHint"))
    if hidden:
        lay.addWidget(_text(
            "not shown in plate mode (one channel per plate): " + ", ".join(hidden),
            "ITBHint"))


def _lut_row(ctx, token: str, specs: list) -> QFrame:
    """One LUT editor row: colour swatch · channel · colormap · min · max · Auto.
    Edits fan out to every ChannelSpec sharing this channel token."""
    cs0 = specs[0]
    row = QFrame()
    row.setObjectName("ITBLutRow")
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(7)
    sw = _swatch(_cmap_swatch(cs0.colormap))
    h.addWidget(sw)
    h.addWidget(_text(str(cs0.channel).upper(), "ITBLutChan"))
    if len(specs) > 1:
        h.addWidget(_text(f"×{len(specs)}", "ITBLutTag"))
    h.addStretch(1)

    cmap = QComboBox(row)
    cmap.setObjectName("ITBCombo")
    names = ["Greys"] + [n for n in LUT_COLOR_NAMES if n != "Greys"]
    cmap.addItems(names)
    if cs0.colormap in names:
        cmap.setCurrentText(cs0.colormap)

    def _cmap_changed(_i):
        name = cmap.currentText()
        for cs in specs:
            cs.colormap = name
        sw.setStyleSheet(
            f"background:{_cmap_swatch(name)}; border-radius:3px;"
            " border:1px solid rgba(255,255,255,0.16);")
        _lut_touched(ctx)
    cmap.currentIndexChanged.connect(_cmap_changed)
    h.addWidget(cmap)

    def _edit_field(kind: str) -> QLineEdit:
        le = QLineEdit(row)
        le.setObjectName("ITBLutEdit")
        le.setPlaceholderText("auto")
        val = cs0.lut_min if kind == "min" else cs0.lut_max
        le.setText("" if val is None else _fmt_lut(val))
        le.setToolTip("Blank = auto-fit each image")

        def _committed(_le=le, _kind=kind):
            v = parse_float_edit(_le, None, allow_auto=True)
            for cs in specs:
                setattr(cs, "lut_min" if _kind == "min" else "lut_max", v)
            _le.setText("" if v is None else _fmt_lut(v))
            _lut_touched(ctx)
        le.editingFinished.connect(_committed)
        return le

    h.addWidget(_text("min", "ITBLutTag"))
    h.addWidget(_edit_field("min"))
    h.addWidget(_text("max", "ITBLutTag"))
    h.addWidget(_edit_field("max"))

    fit = QToolButton(row)
    fit.setObjectName("ITBLutFit")
    fit.setText("Auto")
    fit.setCursor(Qt.PointingHandCursor)
    fit.setToolTip("Clear the range → auto-fit each image")

    def _auto():
        for cs in specs:
            cs.lut_min = cs.lut_max = None
        builder_refresh(ctx)          # rebuild so the cleared fields show blank
        _schedule_apply(ctx)
    fit.clicked.connect(lambda: _auto())
    h.addWidget(fit)
    return row


def _fmt_lut(v: float) -> str:
    """Compact LUT number: drop a trailing ``.0`` so 1200.0 shows as ``1200``."""
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _lut_touched(ctx) -> None:
    """A LUT edit (colormap / min / max): flag the grid stale + schedule the
    render-less apply, but don't rebuild the box (that would drop focus / kill the
    field mid-edit)."""
    _rt(ctx).builder_dirty = True
    _schedule_apply(ctx)
    _refresh_shape(ctx, builder_spec(ctx))


def _build_tray(ctx, spec) -> None:
    body = getattr(_rt(ctx), "channels_tray_body")
    _clear(body)
    lay = body.layout()
    names = _base_channels(ctx)
    if not names:
        lay.addWidget(_text("load a dataset to see channels", "ITBHint"))
        lay.addStretch(1)
        return
    for name in names:
        chip = _Chip(f"newchan:{name}")
        chip.setObjectName("ITBChip")
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(9, 3, 10, 3)
        cl.setSpacing(7)
        cl.addWidget(_grip())
        cl.addWidget(_swatch(_cmap_swatch(_default_colormap_for(ctx, name))))
        cl.addWidget(_text(str(name).upper(), "ITBChanLbl"))
        chip.setToolTip("Drag onto a root in Rows or Columns — it keeps this colour")
        lay.addWidget(chip)
    lay.addStretch(1)


# ── per-channel ⚙ menu + labels ─────────────────────────────────────────────

def _render_label(cs: ChannelSpec) -> str:
    s = _RENDER_SHORT.get(cs.render, cs.render)
    return s + ("+outline" if cs.boundary else "")


def _fill_channel_menu(ctx, menu: QMenu, cs: ChannelSpec) -> QMenu:
    """(Re)populate a channel's ⚙ menu in place. Flat — no submenus and no
    cross-referenced child menus (an earlier ``addMenu``-submenu + keepalive
    created a GC cycle that could fault under native macOS menus). Colour also
    lives in the LUT box below; it's mirrored here for one-click access."""
    menu.clear()

    # Render mode — corrected / raw / cell mask / ≥ threshold (radio-like).
    hdr = menu.addAction("Render")
    hdr.setEnabled(False)
    for key, label in _RENDER_MODES:
        act = owned_action(menu, label)
        act.setCheckable(True)
        act.setChecked(cs.render == key)
        act.triggered.connect(lambda _c=False, k=key: (_set_render(cs, k), _edit(ctx)))

    menu.addSeparator()
    # Only a binary render has label ids to colour — the other two draw the
    # intensity image, where there is no per-cell distinction to make. Shown
    # greyed rather than hidden so the option does not appear and disappear as
    # the render is switched.
    lc = owned_action(menu, "Colour cells")
    lc.setCheckable(True)
    lc.setChecked(bool(cs.label_colors))
    lc.setEnabled(cs.render in ("mask", "threshold"))
    lc.setToolTip(
        "Give every cell its own colour instead of painting them all white — two "
        "nuclei that touch are otherwise one white shape. The colour is a fixed "
        "function of the label id, so it does not change between redraws."
    )
    lc.triggered.connect(
        lambda c=False: (_set_attr(cs, "label_colors", bool(c)), _edit(ctx)))

    b = owned_action(menu, "Boundary outline")
    b.setCheckable(True)
    b.setChecked(bool(cs.boundary))
    b.triggered.connect(lambda c=False: (_set_attr(cs, "boundary", bool(c)), _edit(ctx)))

    lg = owned_action(menu, "Log scale")
    lg.setCheckable(True)
    lg.setChecked(bool(cs.log))
    lg.triggered.connect(lambda c=False: (_set_attr(cs, "log", bool(c)), _edit(ctx)))

    menu.addSeparator()
    chdr = menu.addAction("Colour / LUT")
    chdr.setEnabled(False)
    for name in ["Greys"] + [n for n in LUT_COLOR_NAMES if n != "Greys"]:
        act = owned_action(menu, name)
        act.setCheckable(True)
        act.setChecked(cs.colormap == name)
        act.triggered.connect(lambda _c=False, n=name: (_set_attr(cs, "colormap", n), _edit(ctx)))
    return menu


def _channel_menu(ctx, cs: ChannelSpec, parent=None) -> QMenu:
    """Build a fully-populated ⚙ menu (used directly by tests; the live chips
    build it lazily via :func:`_fill_channel_menu` on ``aboutToShow``)."""
    return _fill_channel_menu(ctx, QMenu(parent), cs)


def _set_render(cs: ChannelSpec, key: str) -> None:
    cs.render = key


def _set_attr(cs: ChannelSpec, attr: str, val) -> None:
    setattr(cs, attr, val)


def _set_dim_filter(ctx, dim: str, values) -> None:
    """Write the timepoint / FOV value filter (empty set ⇒ all values), flag the
    grid stale, and schedule the render-less apply."""
    st = ctx.state.image_table
    attr = "active_timepoints" if dim == "timepoint" else "active_fovs"
    chosen = set(values)
    setattr(st, attr, chosen)
    builder_spec(ctx).filters[dim] = set(chosen)
    _rt(ctx).builder_dirty = True
    _schedule_apply(ctx)
    _refresh_shape(ctx, builder_spec(ctx))


class _StayOpenMenu(QMenu):
    """A QMenu that keeps itself open when a *checkable* item (or one flagged
    ``stayOpen``, e.g. Select all / Select none) is clicked, so the user can tick
    several values in one pass. Other items and off-menu clicks close as usual."""

    def mouseReleaseEvent(self, e):
        act = self.activeAction()
        if act is not None and act.isEnabled() and (
                act.isCheckable() or bool(act.property("stayOpen"))):
            act.trigger()            # act in place, don't dismiss
            return
        super().mouseReleaseEvent(e)


def _root_filter_menu(ctx, dim: str, anchor: QWidget) -> QMenu | None:
    """Build the checkable timepoint / FOV value-filter menu (all ticked ⇒ show
    every value). Returns None when there are no values. Split from
    :func:`_open_root_filter` so the wiring is drivable without a blocking exec."""
    all_vals = _timepoint_options(ctx) if dim == "timepoint" else _fov_options(ctx)
    if not all_vals:
        return None
    st = ctx.state.image_table
    active = set(getattr(st, "active_timepoints" if dim == "timepoint"
                         else "active_fovs", set()) or set())
    menu = _StayOpenMenu(anchor)
    acts = []

    def _commit():
        chosen = {a.text() for a in acts if a.isChecked()}
        # all (or somehow none) ticked ⇒ canonical "all" = empty filter
        _set_dim_filter(ctx, dim, set() if chosen == set(all_vals) else chosen)

    def _bulk(check: bool):
        for a in acts:
            a.blockSignals(True)
            a.setChecked(check)
            a.blockSignals(False)
        _commit()

    sel_all = owned_action(menu, "Select all")
    sel_all.setProperty("stayOpen", True)
    sel_all.triggered.connect(lambda _c=False: _bulk(True))
    sel_none = owned_action(menu, "Select none")
    sel_none.setProperty("stayOpen", True)
    sel_none.triggered.connect(lambda _c=False: _bulk(False))
    menu.addSeparator()

    for val in all_vals:
        a = owned_action(menu, val)
        a.setCheckable(True)
        a.setChecked((not active) or val in active)  # empty filter = all shown
        acts.append(a)
        a.toggled.connect(lambda _c=False: _commit())
    return menu


def _open_root_filter(ctx, dim: str, anchor: QWidget) -> None:
    """Pop a checkable value filter for a root on an axis. Wells come from the
    plate, so for Well this just focuses the plate."""
    if dim == "well":
        with swallow("image table builder: focus plate"):
            plate = getattr(_rt(ctx), "sidebar_plate", None)
            if plate is not None:
                plate.setFocus()
        return
    menu = _root_filter_menu(ctx, dim, anchor)
    if menu is not None:
        menu_exec.exec_transient_menu(menu, anchor.mapToGlobal(anchor.rect().bottomLeft()))
