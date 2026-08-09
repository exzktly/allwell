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
from widgets.context_banner import ContextBanner
from widgets.page_body import build_page_body
from widgets.rail_nav import RailNav
from well_viewer.core.app_shell_view import RAIL_W
from well_viewer.core.centre_view import NamedPageStack
from datasets.concatenate.controller import ConcatenateController
from datasets.import_loose.controller import ImportLooseController
from datasets.job_queue.controller import JobQueueController
from datasets.merge_plates.controller import MergePlatesController
from datasets.ome_import.controller import OmeImportController
from datasets.subset.controller import SubsetController
from datasets.validate.controller import ValidateController

# Kinds a Datasets controller can queue itself under — registered against the
# Job Queue page once every controller (including Analyze, injected from
# outside) has been built.
_QUEUEABLE_KINDS = ("concatenate", "ome_import", "validate", "subset", "import_loose", "merge_plates")


@dataclass(frozen=True)
class _Feature:
    """A Datasets-pane page: its rail label, icon, and a factory.

    ``build(tab)`` returns ``(page_widget, teardown_or_None)`` — the teardown is
    called on app close so a running worker can stop cleanly.
    """

    name: str
    icon: str
    build: Callable[["DatasetsTab"], tuple[QWidget, Callable[[], None] | None]]
    # The job-queue kind this page runs, so an edit session can find the page
    # for a job without a second name -> kind map to keep in step. None for a
    # page that is not a queueable tool (the Job Queue's own page).
    kind: str | None = None


def _build_concatenate(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = ConcatenateController(
        on_open_in_review=tab._open_in_review, on_add_to_queue=tab._add_to_queue)
    tab._controllers["concatenate"] = controller
    return controller.page, controller.shutdown


def _build_ome_import(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = OmeImportController(
        on_open_in_analyze=tab._open_in_analyze, on_add_to_queue=tab._add_to_queue)
    tab._controllers["ome_import"] = controller
    return controller.page, controller.shutdown


def _build_validate(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = ValidateController(
        on_open_in_analyze=tab._open_in_analyze, on_add_to_queue=tab._add_to_queue)
    tab._controllers["validate"] = controller
    return controller.page, controller.shutdown


def _build_subset(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = SubsetController(
        on_open_in_review=tab._open_in_review, on_add_to_queue=tab._add_to_queue)
    tab._controllers["subset"] = controller
    return controller.page, controller.shutdown


def _build_import_loose(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = ImportLooseController(
        on_open_in_analyze=tab._open_in_analyze, on_add_to_queue=tab._add_to_queue)
    tab._controllers["import_loose"] = controller
    return controller.page, controller.shutdown


def _build_merge_plates(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    controller = MergePlatesController(
        on_open_in_review=tab._open_in_review, on_add_to_queue=tab._add_to_queue)
    tab._controllers["merge_plates"] = controller
    return controller.page, controller.shutdown


def _build_job_queue(tab: "DatasetsTab") -> tuple[QWidget, Callable[[], None] | None]:
    """Built last — every other feature's controller already exists in
    ``tab._controllers`` by the time this registers them against the bridge."""
    controller = JobQueueController(state_dir=tab._queue_state_dir)
    for kind in _QUEUEABLE_KINDS:
        controller.register(kind, tab._controllers[kind])
    if tab._analyze_controller is not None:
        controller.register("analyze", tab._analyze_controller)
        controller.set_analyze_provider(tab._analyze_controller.collect_spec)
        # The reverse of the injection above: Analyze's own "+ Queue" button
        # needs somewhere to send a run, and neither module imports the other.
        sink = getattr(tab._analyze_controller, "set_queue_sink", None)
        if callable(sink):
            sink(lambda spec: controller.add_job("analyze", spec))
    controller.set_session_hooks(tab._begin_session, tab._end_session)
    controller.set_reveal_hook(tab._reveal_page)
    tab._controllers["job_queue"] = controller
    return controller.page, controller.shutdown


_FEATURES: tuple[_Feature, ...] = (
    _Feature(name="Concatenate Datasets", icon="copy", build=_build_concatenate,
             kind="concatenate"),
    _Feature(name="Convert OME-TIFF", icon="folder-open", build=_build_ome_import,
             kind="ome_import"),
    _Feature(name="Validate & Repair", icon="scan-line", build=_build_validate,
             kind="validate"),
    _Feature(name="Subset / Split", icon="boxes", build=_build_subset,
             kind="subset"),
    _Feature(name="Zip Loose Folder", icon="download", build=_build_import_loose,
             kind="import_loose"),
    _Feature(name="Merge Plates", icon="layout-grid", build=_build_merge_plates,
             kind="merge_plates"),
    _Feature(name="Job Queue", icon="waypoints", build=_build_job_queue),
)


class DatasetsTab(QWidget):
    """Left: page rail. Right: the active feature's page."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_open_in_review: Callable[[Path], None] | None = None,
        on_open_in_analyze: Callable[..., None] | None = None,
        on_reveal_analyze: Callable[[], bool] | None = None,
        analyze_controller: object | None = None,
        queue_state_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_review = on_open_in_review
        self._on_open_in_analyze = on_open_in_analyze
        # "Switch to the Analyze pane, change nothing on it." Analyze is the one
        # queueable kind with no rail entry here, so revealing its page means
        # leaving this pane entirely, which only the shell can do. None when a
        # DatasetsTab is built without a shell (tests, and the Datasets-only
        # profiles): _reveal_page then reports that it could not get there
        # rather than pretending it did.
        self._on_reveal_analyze = on_reveal_analyze
        # The already-built AnalyzeTab, injected by allwell.py the same way
        # on_open_in_review/on_open_in_analyze are — duck-typed to
        # datasets.job_queue.controller.QueueableController via
        # AnalyzeRunMixin, never imported by name here (keeps this module
        # and everything under datasets/job_queue/ free of an analyze_tab
        # import).
        self._analyze_controller = analyze_controller
        # None in production (the Job Queue persists under
        # services.remote_config.default_state_dir()); tests override it so
        # a DatasetsTab built offscreen never touches the real user config dir.
        self._queue_state_dir = queue_state_dir
        self._controllers: dict[str, object] = {}
        self._teardowns: list[Callable[[], None]] = []
        self._session_banner: ContextBanner | None = None
        self._pages_by_kind: dict[str | None, QWidget] = {}
        self._build_ui()

    def _add_to_queue(self, kind: str, spec: dict) -> None:
        job_queue = self._controllers.get("job_queue")
        if job_queue is not None:
            job_queue.add_job(kind, spec)

    # ── queue navigation ─────────────────────────────────────────────────────
    def _reveal_page(self, kind: str) -> bool:
        """Show *kind*'s page and change nothing on it. Returns whether it worked.

        Deliberately not :meth:`_begin_session` with the banner left off. That
        one exists to stand a page in for a job being *edited*, and it is
        reached only after the queue controller has decided a session may
        start; this is the plain "take me to that tool" half, which a click on
        a running row needs precisely because its page must not be written to.
        Keeping them separate is what lets the running case navigate without
        populating.

        The return value is not decoration: Analyze has no rail entry here, so
        reaching it depends on a shell callback that need not exist. The caller
        says why the click did nothing rather than leaving it silent, which is
        the defect this whole path was reported for.
        """
        page_name = self._page_name_for(kind)
        if page_name is None:
            if self._on_reveal_analyze is None:
                return False
            return bool(self._on_reveal_analyze())
        self._nav.setCurrentKey(page_name)
        self._pages.setCurrentByName(page_name)
        return True

    # ── queue edit sessions ──────────────────────────────────────────────────
    def _begin_session(self, kind: str, message: str) -> None:
        """Show *kind*'s page with a banner saying it is standing in for a job.

        The Job Queue controller decides when a session may start and what
        saving one means; this only knows how to get the user to the right
        page and put the strip on it. Without the strip the page looks exactly
        as it always does, and the only way to finish would be to guess.
        """
        page_name = self._page_name_for(kind)
        if page_name is None:
            # Analyze lives in a different pane entirely, so there is no rail
            # entry to switch to — the banner goes onto its form column, where
            # the resume banner already sits, and the user is already looking
            # at it (an Analyze session can only be started from a queue page
            # that Analyze's own "+ Queue" button put the job on).
            self._show_session_banner(self._analyze_form_layout(), message)
            return
        self._nav.setCurrentKey(page_name)
        self._pages.setCurrentByName(page_name)
        page = self._pages_by_kind.get(kind)
        self._show_session_banner(page.layout() if page is not None else None, message)

    def _drop_session_banner(self) -> None:
        if self._session_banner is not None:
            self._session_banner.setParent(None)
            self._session_banner.deleteLater()
            self._session_banner = None

    def _cancel_session(self) -> None:
        """The banner's Cancel. Goes through the controller rather than
        tearing the banner down directly — the controller owns whether a
        session exists, and dropping only the strip would leave it believing
        one was still live and willing to save it later."""
        job_queue = self._controllers.get("job_queue")
        if job_queue is not None:
            job_queue.end_session()
        else:
            self._end_session()

    def _end_session(self, _kind: str = "") -> None:
        """The controller reporting a session is over: take the banner down
        wherever it is and go back to the queue page."""
        self._drop_session_banner()
        queue_page = self._page_name_for(None)
        if queue_page is not None:
            self._nav.setCurrentKey(queue_page)
            self._pages.setCurrentByName(queue_page)

    def _show_session_banner(self, layout, message: str) -> None:
        """Insert the strip at the top of *layout*.

        Index 0 for every Datasets page: each one's own layout holds a single
        QScrollArea, so this lands above the scrolling content rather than
        inside it and stays put while the page scrolls.
        """
        self._drop_session_banner()
        if layout is None:
            return
        job_queue = self._controllers.get("job_queue")
        session = job_queue.session() if job_queue is not None else None
        confirm = "Save changes" if session and session[1] else "Add to queue"
        banner = ContextBanner(message, confirm)
        banner.confirmed.connect(self._save_session)
        banner.cancelled.connect(self._cancel_session)
        layout.insertWidget(0, banner)
        self._session_banner = banner

    def _save_session(self) -> None:
        job_queue = self._controllers.get("job_queue")
        if job_queue is not None:
            job_queue.save_session()

    def current_help_page(self) -> str:
        """Rail label of the tool page showing now, or ``""`` before build.

        The help drawer asks for this so it can open on that tool's section of
        the manual. The label doubles as the ``RailNav`` key and as the
        ``help_topics.DATASETS_PAGE_HELP`` key, so no second name map exists to
        drift out of step.
        """
        nav = getattr(self, "_nav", None)
        if nav is None:
            return ""
        return nav.currentKey() or ""

    def open_tool(self, kind: str, *, dataset_path: str = "") -> bool:
        """Show one tool's page, optionally pointed at a dataset already.

        Another pane can send the user here — Review's Home page offers to
        check a dataset whose run left no completeness record — and landing on
        whichever tool happened to be showing, with an empty folder field, is a
        button that does less than it says. Returns False when this build has
        no such tool, so the caller can tell "done" from "nothing happened".
        """
        page_name = self._page_name_for(kind)
        if page_name is None:
            return False
        self._nav.setCurrentKey(page_name)
        self._pages.setCurrentByName(page_name)
        # Every tool's page is built with the pane, so there is nothing to
        # force here — but not every tool takes a dataset path, and one that
        # does not simply gets shown.
        setter = getattr(self._pages_by_kind.get(kind), "set_dataset_path", None)
        if dataset_path and callable(setter):
            setter(str(dataset_path))
        return True

    def _page_name_for(self, kind: str | None) -> str | None:
        for feat in _FEATURES:
            if feat.kind == kind:
                return feat.name
        return None

    def _analyze_form_layout(self):
        """Where an Analyze session's banner goes.

        The form column rather than the tab's own layout: that is where the
        resume banner already sits, so the two stack in one place instead of
        one of them straddling the splitter.
        """
        return getattr(self._analyze_controller, "_form_layout", None)

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal, self)
        outer.addWidget(splitter, 1)

        splitter.addWidget(self._build_sidebar())
        splitter.setStretchFactor(0, 0)

        # The tool pages carry their own Run controls down inside each page, so
        # this band holds nothing — but every main page in the app starts with
        # one, and adding it here is what keeps this pane's content level with
        # Review's and Analyze's instead of starting a band's height higher
        # (widgets/page_body.py).
        page_host = QWidget(self)
        _band, page_body = build_page_body(page_host)
        self._pages = NamedPageStack(page_host)
        page_body.layout().addWidget(self._pages, 1)
        splitter.addWidget(page_host)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 900])

        for feat in _FEATURES:
            self._nav.addItem(feat.name, icon=feat.icon, key=feat.name)
            page, teardown = feat.build(self)
            self._pages.addPage(feat.name, page)
            self._pages_by_kind[feat.kind] = page
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
        panel.setFixedWidth(RAIL_W)
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
        scroll.setFixedWidth(RAIL_W)
        self._left_sidebar = scroll  # shell left-rail toggle hides/shows this
        return scroll

    # ── callbacks ────────────────────────────────────────────────────────────
    def _open_in_review(self, dest_root: str) -> None:
        if self._on_open_in_review is not None and dest_root:
            self._on_open_in_review(Path(dest_root))

    def _open_in_analyze(
        self, dataset_root: str, pipeline_info: dict | None = None,
        *, incremental: bool = False, force_wells: list[str] | None = None,
    ) -> None:
        """Hand a folder to the Analyze pane.

        *pipeline_info* is a ``pipeline_info``-shaped settings dict to pre-fill
        the form with — the OME-TIFF converter passes the channel roles it
        assigned, Validate & Repair passes the dataset's whole recorded
        sidecar. *incremental* asks Analyze for the run mode that fills in a
        partial run's missing FOV-groups rather than skipping wells that
        already have output.
        """
        if self._on_open_in_analyze is not None and dataset_root:
            self._on_open_in_analyze(
                Path(dataset_root), pipeline_info or {}, incremental=incremental,
                force_wells=list(force_wells or []))

    def shutdown(self) -> None:
        """Stop any running feature workers (called on app close)."""
        for teardown in self._teardowns:
            teardown()
