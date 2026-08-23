"""Pipeline Info tab (Home).

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

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)


def _raw_pipeline_info(ctx) -> dict:
    """Best-effort read of the full pipeline_info.json from the loaded folder,
    falling back to the curated dict the loader already parsed."""
    ds = getattr(ctx.state, "dataset", None)
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


def build_pipeline_info_subtab(ctx, parent: QWidget) -> None:
    layout = parent.layout() or QVBoxLayout(parent)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(6)

    hdr = QHBoxLayout(); hdr.setSpacing(8)
    title = QLabel("Pipeline Info", parent)
    title.setStyleSheet("font-weight:600;")
    hdr.addWidget(title)
    hdr.addStretch(1)
    from widgets.icon_button import IconButton as _IconButton
    # "refresh-cw", not "download": this re-reads a file already on disk in the
    # dataset folder. A download glyph promised a fetch that never happens.
    reload_ib = _IconButton("refresh-cw", parent)
    reload_ib.setToolTip("Re-read pipeline_info.json from the data folder.")
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

    ctx.widgets.register("_pipeline_info_tree", tree)

    def _refresh() -> None:
        tree.clear()
        info = _raw_pipeline_info(ctx)
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
            # ``qc_options`` is written by ``process_microscopy`` with
            # *un-prefixed* names (``remove_debris``, ``min_area``, …); the
            # ``qc_`` prefix belongs to the CLI flags they come from, not to
            # the sidecar. Reading the prefixed names meant these three rows of
            # the run receipt showed "—" and the literal "None, None" for every
            # run ever made.
            ("Remove debris", qc.get("remove_debris")),
            ("Min/Max area (px)", [qc.get("min_area"), qc.get("max_area")]),
            ("Min solidity", qc.get("min_solidity")),
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

        # Catch-all: the curated sections above are a readable digest of the
        # common fields, but they hard-code which keys to show — so anything
        # else the pipeline wrote (cellpose/track options, smfish_thresholds,
        # cell_gating, sample_definitions, timestamps, nested qc_options keys…)
        # was simply never displayed. Render the COMPLETE pipeline_info.json
        # here so the tab is a full receipt with nothing hidden.
        def _add_node(parent: QTreeWidgetItem, key: object, value: object) -> None:
            if isinstance(value, dict):
                node = QTreeWidgetItem(parent, [str(key), f"{{{len(value)}}}"])
                for k, v in value.items():
                    _add_node(node, k, v)
            elif isinstance(value, (list, tuple)) and value and any(
                isinstance(x, (dict, list, tuple)) for x in value
            ):
                node = QTreeWidgetItem(parent, [str(key), f"[{len(value)}]"])
                for i, item in enumerate(value):
                    _add_node(node, f"[{i}]", item)
            else:
                QTreeWidgetItem(parent, [str(key), _fmt(value)])

        raw_node = QTreeWidgetItem(
            tree, ["All parameters (raw pipeline_info.json)", f"{{{len(info)}}}"])
        rf = raw_node.font(0); rf.setBold(True); raw_node.setFont(0, rf)
        for k in sorted(info, key=str):
            _add_node(raw_node, k, info[k])
        raw_node.setExpanded(False)

        tree.resizeColumnToContents(0)

    reload_ib.clicked.connect(lambda _=False: _refresh())
    # Live-callback stamp (AppRuntime's sanctioned case): Home's sub-tab switch
    # invokes this to re-read pipeline_info.json when the Pipeline Info sub-tab is
    # entered. Reached via ctx.runtime.pipeline_info.refresh.
    ctx.runtime.pipeline_info.refresh = _refresh
    _refresh()
