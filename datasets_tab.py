"""datasets_tab.py — DatasetsTab widget, the third top-level pane.

The Datasets pane sits beside Analyze and Review. Like Review it has a left
sidebar that lists page "features"; unlike Review it has **no well picker** — the
sidebar is a plain :class:`~widgets.rail_nav.RailNav` page navigator over a
:class:`~well_viewer.core.centre_view.NamedPageStack`. It is a peer of
``AnalyzeTab``: a self-contained ``QWidget`` that owns its own ``_left_sidebar``
(which the shell's ``apply_sidebar_visibility`` shows/hides per mode).

Features are declared in one small local list (:data:`_FEATURES`) — adding a
future Datasets feature is a one-line entry, mirroring Review's feature registry
without pulling in its well/selection-scoped machinery.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import theme
from widgets.rail_nav import RailNav
from well_viewer.core.centre_view import NamedPageStack
from datasets.concatenate.controller import ConcatenateController
from datasets.ome_import.controller import OmeImportController
from datasets.validate.controller import ValidateController


@dataclass(frozen=True)
class _Feature:
    """A Datasets-pane page: its rail label, icon, and a factory.

    ``build(tab)`` returns ``(page_widget, teardown_or_None)`` — the teardown is
    called on app close so a running worker can stop cleanly.
    """

    name: str
    icon: str
    build: Callable[["DatasetsTab"], tuple[QWidget, Callable[[], None] | None]]


def _build_concatenate(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = ConcatenateController(on_open_in_review=tab._open_in_review)
    return controller.page, controller.shutdown


def _build_ome_import(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = OmeImportController(on_open_in_analyze=tab._open_in_analyze)
    return controller.page, controller.shutdown


def _build_validate(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = ValidateController(on_open_in_analyze=tab._open_in_analyze)
    return controller.page, controller.shutdown


_FEATURES: tuple[_Feature, ...] = (
    _Feature(name="Concatenate datasets", icon="copy", build=_build_concatenate),
    _Feature(name="Convert OME-TIFF", icon="folder-open", build=_build_ome_import),
    _Feature(name="Validate & Repair", icon="scan-line", build=_build_validate),
)


class DatasetsTab(QWidget):
    """Left: page rail. Right: the active feature's page."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_open_in_review: Callable[[Path], None] | None = None,
        on_open_in_analyze: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_review = on_open_in_review
        self._on_open_in_analyze = on_open_in_analyze
        self._teardowns: list[Callable[[], None]] = []
        self._build_ui()

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal, self)
        outer.addWidget(splitter, 1)

        splitter.addWidget(self._build_sidebar())
        splitter.setStretchFactor(0, 0)

        self._pages = NamedPageStack(self)
        splitter.addWidget(self._pages)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 900])

        for feat in _FEATURES:
            self._nav.addItem(feat.name, icon=feat.icon, key=feat.name)
            page, teardown = feat.build(self)
            self._pages.addPage(feat.name, page)
            if teardown is not None:
                self._teardowns.append(teardown)

        self._nav.currentChanged.connect(self._pages.setCurrentByName)
        if _FEATURES:
            self._nav.setCurrentKey(_FEATURES[0].name)
            self._pages.setCurrentByName(_FEATURES[0].name)

    def _build_sidebar(self) -> QWidget:
        # Matches Review's own rail (well_viewer/core/app_shell_view.py): the
        # RailNav sits in a zero-margin layout so its row highlight and left
        # accent bar reach the panel's true edges (RailNav's own QSS already
        # insets the row body by 6px/4px — an outer container margin here
        # would stack on top of that and pull the highlight away from the
        # edge, which is what a themed.Spacing margin around the whole panel
        # previously did). Only the header label gets its own inset.
        panel = QWidget(self)
        panel.setObjectName("Sidebar")
        panel.setFixedWidth(260)
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QLabel("DATASETS", panel)
        header.setObjectName("RailHead")
        header.setContentsMargins(13, 10, 13, 4)
        header.setStyleSheet(
            f"color: {theme.Colors.text_muted}; "
            f"font-family: {theme.Typography.family}; "
            f"font-size: 11px; "
            f"font-weight: 600; "
            f"letter-spacing: 0.08em;"
        )
        col.addWidget(header)

        self._nav = RailNav(panel)
        col.addWidget(self._nav)
        col.addStretch(1)

        # A scroll area keeps the rail usable if the feature list ever outgrows
        # the panel height, matching the Review/Analyze sidebars.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setFixedWidth(260)
        self._left_sidebar = scroll  # shell left-rail toggle hides/shows this
        return scroll

    # ── callbacks ────────────────────────────────────────────────────────────
    def _open_in_review(self, dest_root: str) -> None:
        if self._on_open_in_review is not None and dest_root:
            self._on_open_in_review(Path(dest_root))

    def _open_in_analyze(self, dataset_root: str) -> None:
        if self._on_open_in_analyze is not None and dataset_root:
            self._on_open_in_analyze(Path(dataset_root))

    def shutdown(self) -> None:
        """Stop any running feature workers (called on app close)."""
        for teardown in self._teardowns:
            teardown()
