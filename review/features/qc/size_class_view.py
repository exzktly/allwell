"""The Cell Gating panel's **Size classes** rows: two labelled area ranges, each kept cell classed by its area.

The rule is the QC layer's (``QCParams.size_classes``, :mod:`review.domain.size_classes`), edited here beside the
debris gate's own area bounds because it is read from the same area column and in the same pixels. It is not an
inclusion layer: it leaves every cell in and only decides which class a kept cell is counted in, so a change applies
through ``controller.set_size_classes`` — saved, and the Nanowells page and the plots redrawn — without the
re-projection of every verdict layer a gate edit needs.

Shown on every dataset. Every dataset uses it to colour the outlines: the Segmentation view outlines each kept
cell in its class's colour (``review_image.renderer``). On a nanowell dataset it also counts each nanowell's kept cells
per class (the Nanowells page's size metrics) and colours the cells on the nanowell crops. The title and the sentence
under the rows say which of these applies, so a plain dataset is never promised per-nanowell counts.

Each row is a label the user types (the populations are never named by the app) and an inclusive minimum and
maximum in pixels, with the range's equivalent-circle diameter in µm beside it at the run's pixel size. When the
run imaged its timepoints at different pixel sizes the readout says so, because a pixel cutoff then means a
different cell size at each. The sentence under the rows says what is wrong with an incomplete rule, or what the
classes feed and whether the debris gate's bounds cut into a range.
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import QGridLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from review.domain.size_classes import N_CLASSES, SizeClassRule, clipped_by_gate, diameter_text
from ui import theme
from widgets.capped_label import CappedLabel

from .nanowell_focus_view import is_nanowell_dataset

TOOLTIP = (
    "Sort cells into two size classes you name — for example T cells and dendritic cells, told apart by size "
    "when no marker separates them. The Segmentation view outlines each kept cell in its class's colour; on a "
    "nanowell dataset each nanowell's cells are counted per class too.\n"
    "Each class is a cell area range in pixels, inclusive at both ends, read from the same area as the debris "
    "gate above. Leave a gap between the ranges for sizes that could be either: a cell in neither range is counted "
    "as unclassified, never forced into a class. The ranges may not overlap.\n"
    "Only the cells this panel keeps are classified. On a nanowell dataset the Nanowells page then offers each "
    "class's count, the cells in neither, the ratio of the first class to the second and the first class's share, "
    "each well's value being the mean over its nanowells. Nothing is re-run.")
#: The rows' title, by whether the dataset counts per nanowell (:func:`refresh`).
TITLE_NANOWELL = "Size classes, counted per nanowell"
TITLE_PLAIN = "Size classes, outlined in Segmentation"
_LABEL_W = 120


def _fmt(value) -> str:
    return "" if value is None else f"{value:g}"


def build_row(parent: QWidget, params, indent: int, text_width: int) -> tuple[QWidget, dict]:
    """The rows' widget and the widgets the panel registers. Wiring is the panel's: every box applies through its
    one ``_apply``, which reads :func:`rule_from`."""
    row = QWidget(parent)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 6, 0, 0)
    layout.setSpacing(2)
    title = QLabel(TITLE_NANOWELL, row)
    title.setToolTip(TOOLTIP)
    layout.addWidget(title)
    grid = QWidget(row)
    gl = QGridLayout(grid)
    gl.setContentsMargins(indent, 0, 0, 0)
    gl.setHorizontalSpacing(10)
    gl.setVerticalSpacing(3)
    gl.setColumnStretch(4, 1)
    for col, cap in ((0, "label"), (1, "min px"), (2, "max px")):
        head = QLabel(cap, grid)
        head.setObjectName("Muted")
        gl.addWidget(head, 0, col)
    widgets: dict = {"size_classes_row": row, "size_classes_title": title}
    rule = params.size_classes if isinstance(params.size_classes, SizeClassRule) else SizeClassRule()
    for k in range(N_CLASSES):
        c = rule.classes[k]
        label = QLineEdit(c.label, grid)
        label.setPlaceholderText(f"class {k + 1}")
        label.setFixedWidth(_LABEL_W)
        label.setToolTip(TOOLTIP)
        low = QLineEdit(_fmt(c.min_area), grid)
        high = QLineEdit(_fmt(c.max_area), grid)
        for edit in (low, high):
            edit.setFixedWidth(theme.Metrics.num_field_w)
            edit.setPlaceholderText("none")
            edit.setToolTip(TOOLTIP)
        diameter = QLabel("", grid)
        diameter.setObjectName("Muted")
        gl.addWidget(label, k + 1, 0)
        gl.addWidget(low, k + 1, 1)
        gl.addWidget(high, k + 1, 2)
        gl.addWidget(diameter, k + 1, 3)
        widgets.update({f"size_label_{k}": label, f"size_min_{k}": low, f"size_max_{k}": high,
                        f"size_diameter_{k}": diameter})
    layout.addWidget(grid)
    status = CappedLabel("", row)
    status.setObjectName("Muted")
    status.setWordWrap(True)
    status.setMaximumWidth(text_width)
    status.setIndent(indent)
    layout.addWidget(status)
    widgets["size_classes_status"] = status
    return row, widgets


def edits(w: dict) -> list:
    """Every box of the rows, for the panel to wire like its own bounds."""
    return [w[f"size_{part}_{k}"] for k in range(N_CLASSES) for part in ("label", "min", "max")]


def rule_from(w: dict) -> SizeClassRule:
    """The rule as the boxes hold it, normalised as the debris bounds are: a blank, unparseable, zero or negative
    bound is open."""
    return SizeClassRule.of(*[(w[f"size_label_{k}"].text(), w[f"size_min_{k}"].text(), w[f"size_max_{k}"].text())
                              for k in range(N_CLASSES)])


def fill(w: dict, rule: SizeClassRule) -> None:
    """Put *rule* in the boxes, signals blocked: a refill mirrors parameters already in force."""
    for k in range(N_CLASSES):
        c = rule.classes[k]
        for key, text in ((f"size_label_{k}", c.label), (f"size_min_{k}", _fmt(c.min_area)),
                          (f"size_max_{k}", _fmt(c.max_area))):
            box = w[key]
            blocked = box.blockSignals(True)
            try:
                if box.text() != text:
                    box.setText(text)
            finally:
                box.blockSignals(blocked)


def pixel_sizes(ctx) -> list[float]:
    """The run's pixel sizes in µm, one per timepoint it recorded (``pipeline_info.json``'s ``acquisition``)."""
    info = getattr(ctx.state.dataset, "pipeline_info", None) or {}
    blocks = info.get("acquisition") if isinstance(info, dict) else None
    out = []
    for block in (blocks or {}).values() if isinstance(blocks, dict) else ():
        pixel = (block or {}).get("pixel_size_um") if isinstance(block, dict) else None
        value = pixel.get("x") if isinstance(pixel, dict) else pixel
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            out.append(value)
    return out


def status_text(rule: SizeClassRule, params, pixels: list[float], *, nanowell: bool = True) -> tuple[str, bool]:
    """``(sentence, is_problem)`` under the rows. *nanowell*: the dataset counts per nanowell, so the sentence
    names those counts; otherwise it names only what a plain dataset does with the classes — the outlines."""
    if rule.is_blank():
        if nanowell:
            return ("Not set: name two classes and their area ranges to count each nanowell's cells by size.", False)
        return ("Not set: name two classes and their area ranges to outline each kept cell in the Segmentation "
                "view by its size.", False)
    problem = rule.problem()
    if problem:
        if nanowell:
            return (problem + " Until then the size metrics on the Nanowells page stay empty.", True)
        return (problem + " Until then the Segmentation view outlines every kept cell alike.", True)
    a, b = rule.labels
    (_ink_a, name_a), (_ink_b, name_b) = theme.size_class_inks()
    outlined = f"the Segmentation view outlines {a} in {name_a} and {b} in {name_b}"
    if nanowell:
        said = (f"Each nanowell's kept cells are counted as {a}, {b} or neither; the Nanowells page and the plots "
                f"offer the counts, the ratio and the share, and {outlined}, as do the nanowell crops.")
    else:
        said = f"Each kept cell is classed as {a}, {b} or neither; {outlined}."
    population = params.population_label() if callable(getattr(params, "population_label", None)) else ""
    if population:
        said += (f" Every plot is drawn from {population} only (Plotting ▸ Population); the other class and the cells "
                 "in neither are left out of every number.")
    else:
        said += f" To draw every plot from {a} or {b} alone, pick it under Plotting ▸ Population."
    clipped = clipped_by_gate(rule, params.min_area if params.enabled else None,
                              params.max_area if params.enabled else None)
    if clipped:
        said += " " + clipped
    if len({round(p, 6) for p in pixels}) > 1:
        said += (f" This run's timepoints were imaged at different pixel sizes ({min(pixels):g}–{max(pixels):g} µm), "
                 "so a pixel range is a different cell size at each.")
    return said, False


def refresh(ctx, w: dict, params) -> None:
    """Title the rows for this dataset (per-nanowell counts or outlines only), and re-read the diameters and the
    sentence from *params*."""
    nanowell = is_nanowell_dataset(ctx)
    title = w.get("size_classes_title")
    if title is not None:
        title.setText(TITLE_NANOWELL if nanowell else TITLE_PLAIN)
    rule = params.size_classes
    pixels = pixel_sizes(ctx)
    pixel = pixels[0] if pixels and len({round(p, 6) for p in pixels}) == 1 else None
    for k in range(N_CLASSES):
        label = w.get(f"size_diameter_{k}")
        if label is not None:
            label.setText(diameter_text(rule.classes[k], pixel))
    status = w.get("size_classes_status")
    if status is not None:
        text, problem = status_text(rule, params, pixels, nanowell=nanowell)
        status.setText(text)
        status.setObjectName("Notice" if problem else "Muted")
        status.style().unpolish(status)
        status.style().polish(status)
