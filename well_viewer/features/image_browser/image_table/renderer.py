"""Image Table export chrome: the facet plan drawn in matplotlib.

The live grid lays its cells out in a ``QGridLayout`` and labels the row / column
axes with ``QLabel`` widgets (``facet_view.render_facet_chrome``). The exported
figure is a separate matplotlib ``Figure``, so it needs the same placement and the
same labels drawn as artists. Both surfaces read ONE plan — the ``FacetPlan`` that
``facet_view.plan_facets`` computes — so the exported figure carries the axis
labels, group strips, corner label and inter-group gaps the user sees on screen.

Pure matplotlib and Qt-free: the plan is duck-typed through the argument (this
module never imports ``facet_view``, which is Qt-bound), so the geometry and the
label drawing are unit-testable without building a widget.

Colours are stated here rather than taken from ``theme`` on purpose. The theme
tokens describe the dark app chrome; an exported figure is a document, saved on a
transparent background and usually landing on a white page, where the live
``#98A2B3``-on-``#1A2230`` palette is unreadable. The export restates the same
hierarchy — muted per-value labels, emphasised group bands — in dark-on-light.
"""

from __future__ import annotations

#: ``gid`` stamped on every label axes, so callers that count image cells can tell
#: chrome from content. matplotlib also writes it as the ``id`` attribute in SVG.
FACET_GID = "image-table-facet-label"

_PX_PER_IN = 96.0     # Qt / QSS logical pixels
_PT_PER_IN = 72.0     # matplotlib points

_INNER_COLOR = "#333333"
_GROUP_COLOR = "#111111"
_CORNER_COLOR = "#666666"
_GROUP_BAND_FACE = "#EEF1F5"

_LABEL_ROW_EM = 1.9   # per-value label row height, in multiples of the font size
_BAND_EM = 2.4        # group band height / rotated strip width, same units
_CHAR_EM = 0.62       # mean glyph advance of DejaVu Sans, in em
_PAD_EM = 0.5
_EMPTY_IN = 0.02      # an unused intermediate label slot (three-deep nesting)
_MIN_LABEL_IN = 0.12
_MIN_PT = 4.0


def px_to_pt(px: float) -> float:
    """A QSS pixel font size as matplotlib points.

    CSS/Qt logical pixels are 1/96 in and a point is 1/72 in, so the conversion is
    a flat ×0.75. That lands on the on-screen proportions: a live cell thumbnail is
    240 px ≈ 2.5 in at 96 dpi against an exported cell of 2.6 in, so the
    label-to-cell size ratio comes out the same on both surfaces.
    """
    return max(_MIN_PT, float(px) * _PT_PER_IN / _PX_PER_IN)


def cell_pad_inches(cell_pad: float) -> float:
    """The export dialog's ``cell_pad`` expressed in inches.

    The setting historically fed ``tight_layout(w_pad=…, h_pad=…)``, whose unit is a
    fraction of the font size, so converting through ``rcParams["font.size"]`` keeps
    a given number meaning the same gap it always did.
    """
    import matplotlib

    return max(0.0, float(cell_pad)) * float(matplotlib.rcParams["font.size"]) / _PT_PER_IN


def _text_width_inches(text: str, pt: float) -> float:
    """Approximate rendered width of *text* at *pt*, without a renderer.

    Measuring for real needs an Agg canvas, which the SVG / PDF export paths would
    otherwise never build. ``_CHAR_EM`` is a DejaVu Sans average and runs a few
    percent wide, which errs the safe way: the label column ends up slightly
    generous rather than clipping. ``bbox_inches="tight"`` grows the saved bbox to
    include the text regardless, so an underestimate only shifts the centring.
    """
    return _CHAR_EM * (pt / _PT_PER_IN) * len(str(text or ""))


def facet_slot_inches(plan, n_rows: int, n_cols: int, *,
                      cell_w_in: float, cell_h_in: float):
    """Per-slot height and width, in inches, for the plan's grid.

    Returns ``(heights, widths)`` — one entry per grid row / column, indexed by the
    plan's own grid indices. Cell slots get the image cell size, spacer slots the
    plan's group gap (Qt px at 96/in), label slots a size derived from the font.

    Fed straight to ``GridSpec`` as ``height_ratios`` / ``width_ratios``: ratios are
    scale-free, so expressing them in inches makes the figure's inch size and the
    slot proportions agree by construction.
    """
    label_pt, group_pt = px_to_pt(plan.label_pt), px_to_pt(plan.group_pt)
    first_row, first_col = int(plan.first_row), int(plan.first_col)
    n_grid_rows = int(plan.bottom_row) or (first_row + n_rows + len(plan.spacer_rows))
    n_grid_cols = int(plan.bottom_col) or (first_col + n_cols + len(plan.spacer_cols))

    heights = [_EMPTY_IN] * n_grid_rows
    widths = [_EMPTY_IN] * n_grid_cols

    # Label rows: the innermost one (``first_row - 1``) carries the per-column
    # values, any outer row a spanning group band. With three or more column dims
    # the plan only fills rows 0 and ``first_row - 1``, so the rest stay empty.
    for i in range(first_row):
        pt = label_pt if i == first_row - 1 else group_pt
        em = _LABEL_ROW_EM if i == first_row - 1 else _BAND_EM
        heights[i] = em * pt / _PT_PER_IN

    # Left label columns: the innermost holds horizontal text, so it is sized to the
    # widest string; the outer ones are rotated strips, sized to the band thickness.
    inner_w = max(
        (_text_width_inches(t, label_pt)
         for (t, _gr, _gc, _rs, _cs, grp) in plan.left if not grp),
        default=0.0,
    )
    for j in range(first_col):
        if j == first_col - 1:
            widths[j] = max(_MIN_LABEL_IN, inner_w + 2 * _PAD_EM * label_pt / _PT_PER_IN)
        else:
            widths[j] = _BAND_EM * group_pt / _PT_PER_IN

    for grid_row in plan.grid_row_of[:n_rows]:
        heights[grid_row] = float(cell_h_in)
    for grid_col in plan.grid_col_of[:n_cols]:
        widths[grid_col] = float(cell_w_in)
    for grid_row in plan.spacer_rows:
        heights[grid_row] = max(_EMPTY_IN, float(plan.row_gap) / _PX_PER_IN)
    for grid_col in plan.spacer_cols:
        widths[grid_col] = max(_EMPTY_IN, float(plan.col_gap) / _PX_PER_IN)
    return heights, widths


def facet_figsize(plan, n_rows: int, n_cols: int, *,
                  cell_w_in: float, cell_h_in: float, pad_in: float):
    """Figure size in inches for the plan's grid, including the inter-slot pad."""
    heights, widths = facet_slot_inches(
        plan, n_rows, n_cols, cell_w_in=cell_w_in, cell_h_in=cell_h_in)
    return (
        max(2.4, sum(widths) + max(0, len(widths) - 1) * pad_in),
        max(2.4, sum(heights) + max(0, len(heights) - 1) * pad_in),
    )


def make_facet_gridspec(fig, plan, n_rows: int, n_cols: int, *,
                        cell_w_in: float, cell_h_in: float, pad_in: float):
    """A ``GridSpec`` on *fig* matching the plan's slot layout.

    *fig* must already be the size :func:`facet_figsize` reports for the same
    arguments — the slot ratios are inches, so the two then agree.

    The margins are pinned and the separation solved for an absolute pad rather
    than left to ``tight_layout`` / ``constrained_layout``. Both of those
    redistribute space to fit each axes' content, which flattens exactly the
    inter-group gaps this layout exists to express. matplotlib spreads ONE uniform
    absolute separator between adjacent slots, sized as ``wspace × (usable / n)``
    and taken out before the ratios apply, so an absolute inch pad inverts to
    ``wspace = pad_in × n / sum(sizes)``.
    """
    heights, widths = facet_slot_inches(
        plan, n_rows, n_cols, cell_w_in=cell_w_in, cell_h_in=cell_h_in)
    n_grid_rows, n_grid_cols = len(heights), len(widths)
    total_h, total_w = sum(heights), sum(widths)
    wspace = (pad_in * n_grid_cols / total_w) if total_w > 0 else 0.0
    hspace = (pad_in * n_grid_rows / total_h) if total_h > 0 else 0.0
    return fig.add_gridspec(
        n_grid_rows, n_grid_cols, height_ratios=heights, width_ratios=widths,
        left=0.0, right=1.0, bottom=0.0, top=1.0, wspace=wspace, hspace=hspace,
    )


def _label_axes(fig, gs, gr: int, gc: int, rs: int, cs: int, text: str, *,
                pt: float, color: str, weight: str = "normal",
                rotation: int = 0, y: float = 0.5, va: str = "center",
                band: bool = False):
    """One axes-off slot carrying a single centred label."""
    ax = fig.add_subplot(gs[gr:gr + rs, gc:gc + cs])
    ax.set_axis_off()
    ax.patch.set_visible(False)
    ax.set_gid(FACET_GID)
    kwargs = {}
    if band:
        kwargs["bbox"] = {"boxstyle": "round,pad=0.25",
                          "facecolor": _GROUP_BAND_FACE, "edgecolor": "none"}
    ax.text(0.5, y, str(text), transform=ax.transAxes, ha="center", va=va,
            fontsize=pt, color=color, fontweight=weight, rotation=rotation,
            **kwargs)
    return ax


def draw_facet_labels(fig, gs, plan) -> None:
    """Add the plan's top / left / corner labels to *fig* as their own axes.

    Each label gets a slot of the same gridspec the cells sit in, so the text stays
    aligned with the row or column it names however the grid is sized.
    """
    label_pt, group_pt = px_to_pt(plan.label_pt), px_to_pt(plan.group_pt)

    for text, gr, gc, rs, cs, is_group in plan.top:
        if not text:
            continue
        # The live grid bottom-aligns the innermost column labels so they sit
        # against the cells they title; the group bands centre in their strip.
        _label_axes(fig, gs, gr, gc, rs, cs, text,
                    pt=group_pt if is_group else label_pt,
                    color=_GROUP_COLOR if is_group else _INNER_COLOR,
                    weight="semibold" if is_group else "normal",
                    y=0.5 if is_group else 0.12,
                    va="center" if is_group else "bottom",
                    band=is_group)

    for text, gr, gc, rs, cs, is_group in plan.left:
        if not text:
            continue
        # ``rotation=90`` reproduces ``facet_view._VLabel``'s -90° painter rotation:
        # both read bottom-to-top, the conventional vertical facet-axis orientation.
        _label_axes(fig, gs, gr, gc, rs, cs, text,
                    pt=group_pt if is_group else label_pt,
                    color=_GROUP_COLOR if is_group else _INNER_COLOR,
                    weight="semibold" if is_group else "normal",
                    rotation=90 if is_group else 0,
                    band=is_group)

    if plan.corner and plan.first_row and plan.first_col:
        _label_axes(fig, gs, 0, 0, int(plan.first_row), int(plan.first_col),
                    plan.corner, pt=max(_MIN_PT, label_pt - 0.75),
                    color=_CORNER_COLOR)
