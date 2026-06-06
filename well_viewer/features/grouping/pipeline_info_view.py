"""Pipeline Info sub-tab (Sample Definitions).

Loads the dataset's ``pipeline_info.json`` and presents the run parameters in a
human-readable, organised tree — schema, channels, segmentation, the Quality-
Control block (flat-field / **background method** / debris), shape features, and
execution options. This is the run's *receipt*: in particular it shows which
``background`` method the run actually used (``qc_options.background``), so there
is no ambiguity about whether a run was top_hat or sliding_disk.

Pure view: reads the already-parsed ``app._app_state.dataset.pipeline_info`` and,
for completeness, the raw ``pipeline_info.json`` from the loaded data folder.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)


def _raw_pipeline_info(app) -> dict:
    """Best-effort read of the full pipeline_info.json from the loaded folder,
    falling back to the curated dict the loader already parsed."""
    ds = getattr(app, "_app_state", None)
    ds = getattr(ds, "dataset", None) if ds is not None else None
    data_dir = getattr(ds, "data_dir", None) if ds is not None else None
    if data_dir is not None:
        for cand in (Path(data_dir) / "pipeline_info.json",
                     Path(data_dir).parent / "pipeline_info.json"):
            try:
                if cand.exists():
                    data = json.loads(cand.read_text())
                    if isinstance(data, dict):
                        return data
            except (OSError, json.JSONDecodeError):
                pass
    return dict(getattr(ds, "pipeline_info", {}) or {}) if ds is not None else {}


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "—"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items()) if value else "—"
    s = str(value)
    return s if s not in ("", "None") else "—"


def build_pipeline_info_subtab(app, parent: QWidget) -> None:
    layout = parent.layout() or QVBoxLayout(parent)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)

    hdr = QHBoxLayout(); hdr.setSpacing(8)
    title = QLabel("Pipeline Info", parent)
    title.setStyleSheet("font-weight:600;")
    hdr.addWidget(title)
    hdr.addStretch(1)
    from widgets.icon_button import IconButton as _IconButton
    reload_ib = _IconButton("download", parent)
    reload_ib.setToolTip("Reload pipeline_info.json from the data folder.")
    hdr.addWidget(reload_ib)
    layout.addLayout(hdr)

    tree = QTreeWidget(parent)
    tree.setObjectName("PipelineInfoTree")
    tree.setColumnCount(2)
    tree.setHeaderLabels(["Parameter", "Value"])
    tree.setRootIsDecorated(True)
    # The alternating row colour banded every other row white, leaving the value
    # text invisible (the rows used the palette's default light alternate while
    # the text stayed light). Drop it and pin themed colours explicitly so every
    # row is the dark panel background with high-contrast text, regardless of any
    # ambient palette.
    import theme as _theme
    _c = _theme.Colors
    tree.setAlternatingRowColors(True)
    tree.setStyleSheet(
        f"QTreeWidget#PipelineInfoTree {{ background:{_c.panel}; "
        f"alternate-background-color:{_c.surface}; color:{_c.text_primary}; }}"
        f"QTreeWidget#PipelineInfoTree::item {{ color:{_c.text_primary}; "
        f"padding:3px 6px; }}"
        f"QTreeWidget#PipelineInfoTree::item:selected {{ "
        f"background:{_c.accent}; color:{_c.ink_dark}; }}"
    )
    tree.header().setStretchLastSection(True)
    layout.addWidget(tree, 1)

    app._pipeline_info_tree = tree

    def _refresh() -> None:
        tree.clear()
        info = _raw_pipeline_info(app)
        if not info:
            QTreeWidgetItem(tree, ["(no dataset loaded)",
                                   "open a results folder to see its run parameters"])
            return
        qc = dict(info.get("qc_options", {}) or {})

        def section(name: str, rows: list[tuple[str, object]], *, expand=True):
            node = QTreeWidgetItem(tree, [name, ""])
            f = node.font(0); f.setBold(True); node.setFont(0, f)
            for k, v in rows:
                QTreeWidgetItem(node, [k, _fmt(v)])
            node.setExpanded(expand)
            return node

        section("Acquisition", [
            ("Schema version", info.get("schema_version")),
            ("Filename schema", info.get("schema") or info.get("schema_fields")),
            ("Separator", info.get("separator")),
        ])
        section("Channels", [
            ("Nuclear", info.get("nuclear_token")),
            ("Fluorescent", info.get("fluor_tokens")),
            ("smFISH", info.get("smfish_tokens")),
            ("Phase", info.get("phase_token")),
            ("Cytoplasm", info.get("cytoplasm_token")),
        ])
        section("Segmentation", [
            ("Method", info.get("segmentation_method")),
            ("Min nucleus area (px)", info.get("min_nucleus_area_px")),
        ])
        # Quality Control — the run's receipt. The background method goes first
        # and is emphasised because it's the usual point of confusion.
        bg_node = section("Background & Quality Control", [
            ("Background method", qc.get("background", "top_hat")),
            ("Flat-field", qc.get("flatfield", "none")),
            ("Background well", qc.get("background_well")),
            ("Remove debris", qc.get("qc_remove_debris")),
            ("Min/Max area (px)", [qc.get("qc_min_area"), qc.get("qc_max_area")]),
            ("Min solidity", qc.get("qc_min_solidity")),
        ])
        # bold the background-method row so it reads as the headline fact
        bg_row = bg_node.child(0)
        if bg_row is not None:
            for col in (0, 1):
                f = bg_row.font(col); f.setBold(True); bg_row.setFont(col, f)
        section("Shape features", [
            ("Measured", info.get("shape_features")),
        ])
        if info.get("execution_options"):
            section("Execution", list(dict(info["execution_options"]).items()),
                    expand=False)
        section("Dataset", [
            ("Timepoints", len(info.get("available_timepoints", []) or [])),
            ("FOVs", len(info.get("available_fovs", []) or [])),
        ], expand=False)
        tree.resizeColumnToContents(0)

    reload_ib.clicked.connect(lambda _=False: _refresh())
    app._pipeline_info_refresh = _refresh
    _refresh()
