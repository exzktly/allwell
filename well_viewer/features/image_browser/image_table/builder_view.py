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
layout state + renders. The tree is the single source of truth; the per-cell
selector grid + the render seam are downstream of it.

ctx-native — never ``app = ctx.app`` (the tab-unwrap guardrail is 0).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)

from well_viewer.core.safe import swallow
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
    with swallow("image table builder: apply spec"):
        image_table_apply_spec(ctx, builder_spec(ctx))


def _edit(ctx) -> None:
    """After any shelf edit: redraw the shelf and schedule a rebuild+render."""
    builder_refresh(ctx)
    _schedule_apply(ctx)


# ── drag-and-drop primitives ────────────────────────────────────────────────

class _Chip(QFrame):
    """A draggable chip carrying a token string."""

    def __init__(self, token: str, parent=None) -> None:
        super().__init__(parent)
        self._token = token
        self._press = None
        self.setObjectName("ImageTableChip")

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
    """A frame that accepts token drops and forwards them to *on_drop*."""

    def __init__(self, on_drop, parent=None) -> None:
        super().__init__(parent)
        self._on_drop = on_drop
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(_MIME):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(_MIME):
            tok = bytes(e.mimeData().data(_MIME).data()).decode("utf-8")
            with swallow("image table builder: drop"):
                self._on_drop(tok)
            e.acceptProposedAction()


def _x_button(parent, tip, on_click) -> QPushButton:
    b = QPushButton("×", parent)
    b.setProperty("variant", "secondary")
    b.setFixedSize(20, 20)
    b.setToolTip(tip)
    b.clicked.connect(lambda _=False: on_click())
    return b


# ── shelf construction ──────────────────────────────────────────────────────

def build_shelf(ctx, parent: QWidget) -> None:
    """Build the builder shelf into *parent* (called from build_image_table_tab)."""
    reg = ctx.widgets
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    title = QLabel("Build — drag a root, nest channels under it", parent)
    title.setProperty("role", "section")
    layout.addWidget(title)

    axes_row = QWidget(parent)
    ar = QHBoxLayout(axes_row)
    ar.setContentsMargins(0, 0, 0, 0)
    ar.setSpacing(10)

    def _zone(name: str, heading: str, sub: str) -> _DropZone:
        z = _DropZone(lambda tok, n=name: _on_zone_drop(ctx, n, tok), axes_row)
        z.setObjectName("ImageTableSelector")
        zl = QVBoxLayout(z)
        zl.setContentsMargins(8, 6, 8, 8)
        zl.setSpacing(4)
        head = QLabel(f"{heading}", z)
        head.setProperty("role", "section")
        zl.addWidget(head)
        subl = QLabel(sub, z)
        subl.setObjectName("Muted")
        zl.addWidget(subl)
        body = QWidget(z)
        QVBoxLayout(body).setContentsMargins(0, 0, 0, 0)
        zl.addWidget(body)
        reg.register(f"_image_table_{name}_zone", z)
        setattr(_rt(ctx), f"{name}_zone_body", body)
        return z

    ar.addWidget(_zone("rows", "Rows", "root ↓ · channels nested"), 1)
    ar.addWidget(_zone("cols", "Columns", "root → · channels nested"), 1)
    layout.addWidget(axes_row)

    # Fixed panel (derived) — a drop target for roots + its value pickers.
    fixed = _DropZone(lambda tok: _on_zone_drop(ctx, "fixed", tok), parent)
    fixed.setObjectName("Panel")
    fl = QVBoxLayout(fixed)
    fl.setContentsMargins(8, 6, 8, 8)
    fl.setSpacing(4)
    fh = QLabel("Fixed — same value for every cell", fixed)
    fh.setProperty("role", "section")
    fl.addWidget(fh)
    fbody = QWidget(fixed)
    QHBoxLayout(fbody).setContentsMargins(0, 0, 0, 0)
    fl.addWidget(fbody)
    reg.register("_image_table_fixed_panel", fixed)
    _rt(ctx).fixed_panel_body = fbody
    layout.addWidget(fixed)

    # Channels tray — a drop target to remove a nested channel.
    tray = _DropZone(lambda tok: _on_tray_drop(ctx, tok), parent)
    tray.setObjectName("ImageTableSelector")
    tl = QVBoxLayout(tray)
    tl.setContentsMargins(8, 6, 8, 8)
    tl.setSpacing(4)
    th = QLabel("Channels — drag onto a root to nest", tray)
    th.setProperty("role", "section")
    tl.addWidget(th)
    tbody = QWidget(tray)
    tbl = QHBoxLayout(tbody)
    tbl.setContentsMargins(0, 0, 0, 0)
    tbl.setSpacing(6)
    tl.addWidget(tbody)
    reg.register("_image_table_channels_tray", tray)
    _rt(ctx).channels_tray_body = tbody
    layout.addWidget(tray)

    builder_refresh(ctx)
    # No auto-apply on build: the shelf stays inert until the user edits it, so it
    # never clobbers the initial grid (built by image_table_rebuild_grid) or
    # whatever a restore / the Advanced controls / a test sets up. The spec is
    # seeded from current state, so it already reflects reality; the first edit
    # renders.


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


def _swatch_style(cmap: str) -> str:
    return "min-width:11px;max-width:11px;min-height:11px;max-height:11px;border-radius:3px;"


def _build_tree(ctx, spec, axis: str) -> None:
    body = getattr(_rt(ctx), f"{axis}_zone_body")
    _clear(body)
    lay = body.layout()
    ax = getattr(spec, axis)

    # root row
    root_row = QWidget(body)
    rl = QHBoxLayout(root_row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(6)
    if ax.root:
        pill = _Chip(ax.root, root_row)
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(8, 3, 4, 3)
        pl.setSpacing(6)
        lbl = QLabel(_ROOT_LABEL[ax.root], pill)
        lbl.setToolTip("Filter which values appear on this axis")
        lbl.mousePressEvent = lambda _e, d=ax.root: _open_root_filter(ctx, d)
        pl.addWidget(lbl)
        pl.addWidget(_x_button(pill, "Move to Fixed",
                               lambda d=ax.root: (_move_facet(ctx, spec, d, "fixed"), _edit(ctx))))
        rl.addWidget(pill)
    else:
        hint = QLabel("drop a Well / Timepoint / FOV root", root_row)
        hint.setObjectName("Muted")
        rl.addWidget(hint)
    rl.addStretch(1)
    lay.addWidget(root_row)

    # nested channels (indented)
    for idx, cs in enumerate(ax.channels):
        row = QWidget(body)
        cl = QHBoxLayout(row)
        cl.setContentsMargins(18, 0, 0, 0)
        cl.setSpacing(6)
        pill = _Chip(f"chan:{axis}:{idx}", row)
        pill.setObjectName("ImageTableChip")
        ppl = QHBoxLayout(pill)
        ppl.setContentsMargins(8, 3, 4, 3)
        ppl.setSpacing(6)
        sw = QLabel("", pill)
        sw.setStyleSheet(_swatch_style(cs.colormap) + f"background:{_cmap_swatch(cs.colormap)};")
        ppl.addWidget(sw)
        ppl.addWidget(QLabel(str(cs.channel).upper(), pill))
        gear = QToolButton(pill)
        gear.setText(f"⚙ {_render_label(cs)}")
        gear.setPopupMode(QToolButton.InstantPopup)
        gear.setProperty("variant", "secondary")
        gear.setMenu(_channel_menu(ctx, cs))
        ppl.addWidget(gear)
        ppl.addWidget(_x_button(pill, "Remove channel",
                                lambda a=axis, i=idx: (_remove_channel(spec, a, i), _edit(ctx))))
        cl.addWidget(pill)
        cl.addStretch(1)
        lay.addWidget(row)

    # overlay toggle when this axis has channels
    if ax.channels:
        tog = QToolButton(body)
        tog.setCheckable(True)
        tog.setChecked(bool(ax.overlay))
        tog.setText("⧉ Overlay" if ax.overlay else "Separate")
        tog.setProperty("variant", "toggle")
        tog.setToolTip("Separate: one row/column per channel. Overlay: blend this "
                       "root's channels into one cell.")

        def _toggle(checked, a=ax):
            a.overlay = bool(checked)
            _edit(ctx)
        tog.toggled.connect(_toggle)
        lay.addWidget(tog)


def _build_fixed(ctx, spec) -> None:
    body = getattr(_rt(ctx), "fixed_panel_body")
    _clear(body)
    lay = body.layout()
    dims = _fixed_dims(spec)
    if not dims:
        lbl = QLabel("every dimension is on an axis", body)
        lbl.setObjectName("Muted")
        lay.addWidget(lbl)
        lay.addStretch(1)
        return
    for dim in dims:
        chip = _Chip(dim, body)
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(8, 3, 8, 3)
        cl.setSpacing(6)
        cl.addWidget(QLabel(_ROOT_LABEL[dim] + " =", chip))
        combo = QComboBox(chip)
        vals = _root_values(ctx, dim)
        combo.addItems(vals)
        cur = spec.fixed.get(dim) or (vals[0] if vals else "")
        if cur and combo.findText(cur) >= 0:
            combo.setCurrentText(cur)
        spec.fixed[dim] = combo.currentText()

        def _changed(_i, d=dim, cb=combo):
            spec.fixed[d] = cb.currentText()
            _schedule_apply(ctx)
        combo.currentIndexChanged.connect(_changed)
        cl.addWidget(combo)
        if dim == "well":
            note = QLabel("(from plate)", chip)
            note.setObjectName("Muted")
            cl.addWidget(note)
        lay.addWidget(chip)
    lay.addStretch(1)


def _build_tray(ctx, spec) -> None:
    body = getattr(_rt(ctx), "channels_tray_body")
    _clear(body)
    lay = body.layout()
    for name in _base_channels(ctx):
        chip = _Chip(f"newchan:{name}", body)
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(8, 3, 8, 3)
        cl.setSpacing(6)
        cl.addWidget(QLabel(str(name).upper(), chip))
        lay.addWidget(chip)
    lay.addStretch(1)


# ── per-channel ⚙ menu + labels ─────────────────────────────────────────────

def _render_label(cs: ChannelSpec) -> str:
    s = _RENDER_SHORT.get(cs.render, cs.render)
    return s + ("+outline" if cs.boundary else "")


def _cmap_swatch(cmap: str) -> str:
    """A rough CSS colour for the chip swatch (best-effort; the real LUT is in the
    render)."""
    table = {"Greys": "#c8cdd8", "Reds": "#f472b6", "Greens": "#4ade80",
             "Blues": "#6ea8ff", "viridis": "#4ade80", "magma": "#f472b6",
             "inferno": "#fbbf24", "cividis": "#22d3ee"}
    return table.get(cmap, "#c8cdd8")


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
