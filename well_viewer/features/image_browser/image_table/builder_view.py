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
block (``_QSS``) on the shelf root reproduces the design mockup with the app's
theme tokens (mirrors ``theme.py``).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from well_viewer.core.safe import swallow
from well_viewer.features.image_browser.cell_browser.worker import (
    resolve_channel_tint,
)
from well_viewer.features.image_browser.image_table.state import (
    Axis, ChannelSpec, spec_from_state,
)
from well_viewer.features.image_browser.image_table.build_layout import (
    image_table_apply_spec,
)
from well_viewer.features.image_browser.image_table.grid_layout import (
    _channel_options, _fov_options, _timepoint_options, _well_options,
)
from well_viewer.features.image_browser.image_table.image_compose import (
    LUT_COLOR_NAMES, _is_binary_variant,
)

_MIME = "application/x-aw-imgtable-token"
_ROOT_DIMS = ("well", "timepoint", "fov")
_ROOT_LABEL = {"well": "Well", "timepoint": "Timepoint", "fov": "FOV"}
_RENDER_MODES = [("corrected", "corrected"), ("raw", "raw"),
                 ("mask", "cell mask"), ("threshold", "≥ threshold")]
_RENDER_SHORT = {"corrected": "corr", "raw": "raw", "mask": "mask", "threshold": "≥thr"}

# Colour swatch per root dimension + a rough colour per colormap (mirrors the
# LUT the render actually applies; the chip swatch is a glanceable hint).
_DIM_SWATCH = {"well": "#6B8AFD", "timepoint": "#F59E0B", "fov": "#98A2B3"}
_CMAP_SWATCH = {
    "Greys": "#C8CDD8", "Reds": "#F87171", "Greens": "#4ADE80", "Blues": "#6EA8FF",
    "Purples": "#A78BFA", "Oranges": "#FB923C", "viridis": "#4ADE80",
    "magma": "#F472B6", "inferno": "#F59E0B", "cividis": "#6EA8FF",
}

# Scoped stylesheet — applied to the shelf root; object-name selectors keep it
# from leaking onto the rest of the app. Colours mirror ``theme.py`` tokens.
_QSS = """
QWidget#ITBShelf { background: transparent; }
QLabel#ITBTitle { color: #E6E9EF; font-weight: 640; font-size: 12.5px; }
QLabel#ITBShape { color: #98A2B3; font-size: 11px; }
QLabel#ITBShape b { color: #B9C6FF; }

QFrame#ITBZone {
    border: 1px dashed #2A3343; border-radius: 9px; background: #131A24;
}
QFrame#ITBZone[dropActive="true"] { border: 1px dashed #6B8AFD; background: #17203A; }
QFrame#ITBFixed { border: 1px solid #2A3343; border-radius: 9px; background: #131A24; }
QFrame#ITBTray { border: 1px solid #1B2331; border-radius: 9px; background: #0E131C; }

QLabel#ITBHead { color: #E6E9EF; font-weight: 620; font-size: 12px; }
QLabel#ITBSub  { color: #5F6B7C; font-size: 10px; }
QLabel#ITBHint { color: #5F6B7C; font-style: italic; font-size: 11px; }

QFrame#ITBRoot {
    background: #202B4D; border: 1px solid #4560A8; border-radius: 7px;
}
QLabel#ITBRootLbl { color: #E9ECF5; font-weight: 640; font-size: 12px; }

QWidget#ITBNest { border-left: 2px solid #3A4E86; background: transparent; }

QFrame#ITBChan, QFrame#ITBChip {
    background: #1A2230; border: 1px solid #2A3343; border-radius: 7px;
}
QFrame#ITBChan:hover, QFrame#ITBChip:hover { border-color: #4560A8; }
QLabel#ITBChanLbl { color: #E6E9EF; font-size: 12px; font-weight: 560; }
QLabel#ITBVar { color: #98A2B3; font-size: 10.5px; }
QLabel#ITBGrip { color: #5F6B7C; font-size: 11px; }
QLabel#ITBNote { color: #5F6B7C; font-size: 10px; }

QToolButton#ITBOpt {
    color: #98A2B3; background: #131A24; border: 1px solid #2A3343;
    border-radius: 5px; padding: 1px 7px; font-size: 11px;
}
QToolButton#ITBOpt:hover { color: #E6E9EF; border-color: #6B8AFD; }
QToolButton#ITBOpt::menu-indicator { image: none; width: 0px; }

QToolButton#ITBX {
    color: #5F6B7C; background: transparent; border: none;
    font-size: 15px; padding: 0px 4px;
}
QToolButton#ITBX:hover { color: #F87171; }

QToolButton#ITBSeg {
    background: #131A24; color: #98A2B3; border: 1px solid #2A3343;
    padding: 3px 11px; font-size: 11px;
}
QToolButton#ITBSeg:hover { color: #E6E9EF; }
QToolButton#ITBSeg[on="true"] { background: #2C3A66; color: #F0F4FF; }
QToolButton#ITBSeg[on="true"][ov="true"] { background: #3A2E12; color: #F5A524; }

QComboBox#ITBCombo {
    background: #1A2230; color: #E6E9EF; border: 1px solid #2A3343;
    border-radius: 6px; padding: 2px 6px; min-height: 20px;
}
QComboBox#ITBCombo:hover { border-color: #3B475C; }
QLabel#ITBFixLbl { color: #E6E9EF; font-size: 12px; }
QLabel#ITBEq { color: #5F6B7C; font-size: 12px; }
"""


# ── spec accessors ──────────────────────────────────────────────────────────

def _rt(ctx):
    return ctx.runtime.image_table


def _base_channels(ctx) -> List[str]:
    """Real channel tokens for the Channels tray — the ``(binary)`` variants are
    a render *option* here, not separate tray chips."""
    return [c for c in _channel_options(ctx) if not _is_binary_variant(c)]


def _root_values(ctx, dim: str) -> List[str]:
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
    """Reconstruct from state if it already describes a table, else the classic
    Channels-down-rows × Timepoints-across default."""
    st = ctx.state.image_table
    if st.row_dims or st.col_dims:
        return spec_from_state(st)
    chans = _base_channels(ctx)
    spec = spec_from_state(st)  # empty axes + current filters
    if chans:
        spec.rows = Axis(root=None, channels=[ChannelSpec(c, "corrected") for c in chans])
        if _timepoint_options(ctx):
            spec.cols = Axis(root="timepoint")
    for dim in _ROOT_DIMS:
        spec.fixed.setdefault(dim, _default_value(ctx, dim))
    return spec


def _channel_axis(spec) -> Optional[str]:
    return spec.channel_axis()


def _fixed_dims(spec) -> List[str]:
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


def _add_channel(ctx, spec, name: str, target: str) -> None:
    ax = getattr(spec, target)
    other = spec.cols if target == "rows" else spec.rows
    if other.channels:                      # channels cohere on one axis
        ax.channels.extend(other.channels)
        other.channels = []
        other.overlay = False
    ax.channels.append(ChannelSpec(channel=name, render="corrected", colormap="Greys"))


def _move_channel(spec, src_axis: str, idx: int, target: str) -> None:
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
    tgt.channels.append(cs)


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


def _edit(ctx) -> None:
    """After any shelf edit: mark the table stale (so the header cues a Generate),
    redraw the shelf, and schedule a render-less layout rebuild. Rendering is
    gated on the Generate button — no live redraw on edit."""
    _rt(ctx).builder_dirty = True
    builder_refresh(ctx)
    _schedule_apply(ctx)


def builder_notify_rendered(ctx) -> None:
    """Clear the "edited — press Generate" cue once the table has rendered. Called
    from the Generate action so the header reflects that the grid is now current."""
    rt = _rt(ctx)
    rt.builder_dirty = False
    if getattr(rt, "rows_zone_body", None) is not None:
        _refresh_shape(ctx, builder_spec(ctx))


# ── drag-and-drop primitives ────────────────────────────────────────────────

class _Chip(QFrame):
    """A draggable chip carrying a token string. Child labels are made
    mouse-transparent by the builders so a drag starts from anywhere on the
    chip while its ⚙ / × buttons stay clickable."""

    def __init__(self, token: str, parent=None) -> None:
        super().__init__(parent)
        self._token = token
        self._press = None

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is None:
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_MIME, self._token.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)
        self._press = None


class _DropZone(QFrame):
    """A frame that accepts token drops and forwards them to *on_drop*, with a
    drag-over highlight."""

    def __init__(self, on_drop, parent=None) -> None:
        super().__init__(parent)
        self._on_drop = on_drop
        self.setAcceptDrops(True)

    def _set_active(self, on: bool) -> None:
        self.setProperty("dropActive", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(_MIME):
            self._set_active(True)
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(_MIME):
            e.acceptProposedAction()

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._set_active(False)

    def dropEvent(self, e) -> None:  # noqa: N802
        self._set_active(False)
        if e.mimeData().hasFormat(_MIME):
            tok = bytes(e.mimeData().data(_MIME).data()).decode("utf-8")
            with swallow("image table builder: drop"):
                self._on_drop(tok)
            e.acceptProposedAction()


# ── small styled widget helpers ─────────────────────────────────────────────

def _swatch(color: str) -> QLabel:
    sw = QLabel()
    sw.setFixedSize(11, 11)
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


def _chan_color(ctx, name: str) -> str:
    """The app's own per-channel palette colour (by index), as a hex string —
    so a tray chip's dot matches the tint the Cell Browser / composites use."""
    with swallow("image table builder: channel tint"):
        names = _base_channels(ctx)
        idx = names.index(name) if name in names else 0
        r, g, b = resolve_channel_tint(name, idx, None)
        return "#{:02X}{:02X}{:02X}".format(
            int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
    return "#C8CDD8"


# ── shelf construction ──────────────────────────────────────────────────────

def build_shelf(ctx, parent: QWidget) -> None:
    """Build the builder shelf into *parent* (called from build_image_table_tab)."""
    reg = ctx.widgets
    parent.setObjectName("ITBShelf")
    parent.setStyleSheet(_QSS)
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
    layout.addLayout(header)

    # Channels tray (drag source) at the top.
    tray = _DropZone(lambda tok: _on_tray_drop(ctx, tok), parent)
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
        z = _DropZone(lambda tok, n=name: _on_zone_drop(ctx, n, tok), axes_row)
        z.setObjectName("ITBZone")
        z.setProperty("dropActive", "false")
        z.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        z.setMinimumHeight(96)
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
    fixed = _DropZone(lambda tok: _on_zone_drop(ctx, "fixed", tok), parent)
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
    _build_tray(ctx, spec)
    _refresh_shape(ctx, spec)


def _refresh_shape(ctx, spec) -> None:
    lbl = getattr(_rt(ctx), "shape_label", None)
    if lbl is None:
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
        bits.append("<span style='color:#F5A524;font-weight:640'>"
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
    h.addWidget(_swatch(_DIM_SWATCH.get(dim, "#98A2B3")))
    lbl = _text(_ROOT_LABEL[dim], "ITBRootLbl")
    lbl.setAttribute(Qt.WA_TransparentForMouseEvents, False)  # clickable for filter
    lbl.setCursor(Qt.PointingHandCursor)
    lbl.setToolTip("Filter which values appear on this axis"
                   if dim != "well" else "Wells come from the plate — click to focus it")
    lbl.mousePressEvent = lambda _e, d=dim: _open_root_filter(ctx, d)
    h.addWidget(lbl)
    h.addStretch(1)
    h.addWidget(_x_btn("Move to Fixed",
                       lambda d=dim: (_move_facet(ctx, spec, d, "fixed"), _edit(ctx))))
    return pill


def _chan_pill(ctx, spec, axis: str, idx: int, cs: ChannelSpec) -> QFrame:
    pill = _Chip(f"chan:{axis}:{idx}")
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
    gear.setMenu(_channel_menu(ctx, cs))
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
    seg.setStyleSheet("QFrame{border:1px solid #2A3343;border-radius:7px;}")
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
        cl.addWidget(_swatch(_DIM_SWATCH.get(dim, "#98A2B3")))
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
            _schedule_apply(ctx)
            _refresh_shape(ctx, spec)
        combo.currentIndexChanged.connect(_changed)
        cl.addWidget(combo)
        if dim == "well":
            cl.addWidget(_text("(from plate)", "ITBNote"))
        lay.addWidget(chip)
    lay.addStretch(1)


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
        cl.addWidget(_swatch(_chan_color(ctx, name)))
        cl.addWidget(_text(str(name).upper(), "ITBChanLbl"))
        chip.setToolTip("Drag onto a root in Rows or Columns")
        lay.addWidget(chip)
    lay.addStretch(1)


# ── per-channel ⚙ menu + labels ─────────────────────────────────────────────

def _render_label(cs: ChannelSpec) -> str:
    s = _RENDER_SHORT.get(cs.render, cs.render)
    return s + ("+outline" if cs.boundary else "")


def _channel_menu(ctx, cs: ChannelSpec) -> QMenu:
    menu = QMenu()

    render_menu = menu.addMenu("Render")
    for key, label in _RENDER_MODES:
        act = render_menu.addAction(label)
        act.setCheckable(True)
        act.setChecked(cs.render == key)
        act.triggered.connect(lambda _c=False, k=key: (_set_render(cs, k), _edit(ctx)))

    cmap_menu = menu.addMenu("Colour / LUT")
    for name in ["Greys"] + [n for n in LUT_COLOR_NAMES if n != "Greys"]:
        act = cmap_menu.addAction(name)
        act.setCheckable(True)
        act.setChecked(cs.colormap == name)
        act.triggered.connect(lambda _c=False, n=name: (_set_attr(cs, "colormap", n), _edit(ctx)))

    b = menu.addAction("Boundary outline")
    b.setCheckable(True)
    b.setChecked(bool(cs.boundary))
    b.triggered.connect(lambda c=False: (_set_attr(cs, "boundary", bool(c)), _edit(ctx)))

    lg = menu.addAction("Log scale")
    lg.setCheckable(True)
    lg.setChecked(bool(cs.log))
    lg.triggered.connect(lambda c=False: (_set_attr(cs, "log", bool(c)), _edit(ctx)))
    return menu


def _set_render(cs: ChannelSpec, key: str) -> None:
    cs.render = key


def _set_attr(cs: ChannelSpec, attr: str, val) -> None:
    setattr(cs, attr, val)


def _open_root_filter(ctx, dim: str) -> None:
    """Filter which timepoint / FOV values appear when *dim* is a root (Well comes
    from the plate). Reuses the existing per-dim active_* filter sets."""
    if dim == "well":
        with swallow("image table builder: focus plate"):
            plate = getattr(_rt(ctx), "sidebar_plate", None)
            if plate is not None:
                plate.setFocus()
        return
    # Toggle-all shortcut: clear the filter (⇒ all values) — a full checkable
    # popover lives on the Advanced panel; the shelf keeps this lightweight.
    st = ctx.state.image_table
    attr = "active_timepoints" if dim == "timepoint" else "active_fovs"
    setattr(st, attr, set())
    spec = builder_spec(ctx)
    spec.filters[dim] = set()
    _schedule_apply(ctx)
