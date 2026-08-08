"""Qt builder for the Concatenate Datasets page (the main viewer area).

One equation-style input row — dataset 1 (the base / earlier timepoints) +
dataset 2 (the one appended after) = a fresh destination — over an
Analyze-style status view: a live per-well :class:`~datasets.concatenate.dashboard.ConcatDashboard`
with the raw text log demoted to a collapsible "Show raw log" checkbox
(mirrors ``analyze_tab_form.py``'s ``_build_log``/``_on_toggle_raw_log``).

Pure view: it owns widgets and emits intent (``runRequested``,
``openInReviewRequested``); the worker wiring lives in
:mod:`~datasets.concatenate.controller`. Colours/spacing come from the app QSS
and theme tokens — nothing hardcoded (CLAUDE.md §5).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from widgets._support import set_variant
from widgets.path_field import PathField, folder_picker
from datasets._common.dataset_paths import preview_wells
from datasets._common.log_panel import LogPanelMixin, hrule as _hrule
from datasets.concatenate.concatenate_compute import (
    destination_has_unrecognized_content,
    destination_is_complete_dataset,
)
from datasets.concatenate.dashboard import ConcatDashboard
from datasets._common.dataset_fields import is_dataset, is_free_destination
from datasets._common.dataset_fields import promote_open_button

# The `+` / `=` glyph column in the equation row.
_EQUATION_OP_W = 20




_is_dataset = is_dataset


def _is_destination(root: Path) -> str:
    """This page's destination rule: the shared one, bound to this tool's
    own in-progress marker through its compute module's predicates."""
    return is_free_destination(
        root,
        is_complete=destination_is_complete_dataset,
        has_unrecognized=destination_has_unrecognized_content,
    )


class ConcatenatePage(LogPanelMixin, QWidget):
    """The Concatenate Datasets feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInReviewRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._running = False
        self._build_ui()

    def _build_ui(self) -> None:
        # Wrap in a scroll area so the page's content (fields, plate
        # selector, dashboard, log) can exceed the window's default height
        # (1640x980 — allwell.py) without crushing whatever's at the bottom —
        # that used to make ConcatDashboard._plate render illegibly small,
        # since its cell size derives from its actual allotted width/height
        # (well_plate_selector's _metrics()). Mirrors the pattern
        # analyze_tab.py already uses for its own form panel.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page_layout.addWidget(scroll)

        content = QWidget(scroll)
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        outer.setSpacing(theme.Spacing.md)

        heading = QLabel("Concatenate Datasets", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Append dataset 2 onto dataset 1. The two datasets should be the "
            "same plate imaged in separate sessions; dataset 2's timepoints are "
            "relabelled to continue after dataset 1's last timepoint. The merged "
            "result is written to the destination folder — both originals are "
            "left untouched.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._build_equation_row(outer)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Concatenate", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        run_row_l.addWidget(self._run_btn)
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip("Add this run to the Job Queue instead of running it now.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        run_row_l.addWidget(self._queue_btn)
        run_row_l.addStretch(1)
        outer.addWidget(run_row, 0, Qt.AlignLeft)

        self._build_status_section(outer)

        self._open_btn = QPushButton("Open merged dataset in Review", self)
        self._open_btn.setProperty("variant", "secondary")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(lambda: self.openInReviewRequested.emit(self._dest_root))
        outer.addWidget(self._open_btn, 0, Qt.AlignLeft)

        # A trailing stretch outranks every 0-stretch item above it for any
        # leftover space the QScrollArea's content widget picks up filling
        # the viewport, so slack collects as blank page background below
        # everything instead of inflating the dashboard's own plate map (the
        # dashboard has no maximumHeight of its own, and with the raw log
        # hidden by default there was nothing else to absorb the growth).
        outer.addStretch(1)

    def _build_equation_row(self, outer: QVBoxLayout) -> None:
        row = QWidget(self)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.Spacing.sm)

        self._ds1_field = PathField(
            "Dataset 1 (base)", pick=folder_picker("Select dataset 1 folder"),
            validator=_is_dataset)
        self._ds2_field = PathField(
            "Dataset 2 (appended)", pick=folder_picker("Select dataset 2 folder"),
            validator=_is_dataset)
        self._dest_field = PathField(
            "Destination", pick=folder_picker("Select destination folder"),
            validator=_is_destination)

        def _operator(text: str) -> QLabel:
            lbl = QLabel(text, row)
            lbl.setObjectName("Heading")
            lbl.setContentsMargins(0, theme.Spacing.lg, 0, 0)
            lbl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            lbl.setFixedWidth(_EQUATION_OP_W)
            return lbl

        hl.addWidget(self._ds1_field, 1)
        hl.addWidget(_operator("+"))
        hl.addWidget(self._ds2_field, 1)
        hl.addWidget(_operator("="))
        hl.addWidget(self._dest_field, 1)
        outer.addWidget(row)

        self._cross_hint = QLabel("", self)
        self._cross_hint.setObjectName("Danger")
        self._cross_hint.setWordWrap(True)
        self._cross_hint.setVisible(False)
        outer.addWidget(self._cross_hint)

        for field in (self._ds1_field, self._ds2_field, self._dest_field):
            field.changed.connect(self._on_field_changed)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        self.build_log_header(
            outer, "Concatenate Output",
            toggle_tooltip="Show the full per-file merge log beneath the progress dashboard")

        self.dashboard = ConcatDashboard(self)
        # Hidden until the two source datasets are both valid — the plate
        # used to be visible from the moment the page loaded, painted with
        # no wells at all (set_wells() is only ever called once a run's
        # worker reports them), reading as "something is wrong" rather than
        # "nothing has happened yet". _update_status_preview() reveals it
        # once there's a real answer to show, seeded with the wells the run
        # would actually touch.
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard)

        self.build_log_tail(outer, placeholder="Status will appear here.", object_name="ConcatStatus")

    # ── public API used by the controller ──────────────────────────────
    def dataset1_path(self) -> str:
        return self._ds1_field.path()

    def dataset2_path(self) -> str:
        return self._ds2_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def set_dataset1_path(self, path: str) -> None:
        self._ds1_field.set_path(path)

    def set_dataset2_path(self, path: str) -> None:
        self._ds2_field.set_path(path)

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path)

    def revalidate_fields(self, *, ok: bool = False) -> None:
        """Re-run every field's validator against current disk state.

        A run changes what's on disk under the destination path (and the
        button's enabled state already reflects that on the next check), so
        call this once a run finishes to refresh the inline hints too —
        otherwise a destination that just became "already holds a finished
        dataset" silently disables Concatenate without saying why.

        *ok*: after a successful run, the destination's validator message is
        this exact "already holds a finished dataset" wording — technically
        true, but it's the *expected* result of the run that just succeeded,
        not a problem with the pick. Swap it for a neutral note instead of
        re-showing the pre-run warning verbatim.
        """
        for field in (self._ds1_field, self._ds2_field, self._dest_field):
            field.revalidate()
        if ok and self._dest_field.message():
            self._dest_field.show_note(
                "This destination now holds the merged dataset from that "
                "run — pick a new folder to merge again."
            )
        self._revalidate_cross()

    def set_running(self, running: bool) -> None:
        self._running = running
        self._run_btn.setEnabled(not running and self._inputs_ready())
        self._queue_btn.setEnabled(not running and self._inputs_ready())
        self._run_btn.setText("Concatenating…" if running else "▶  Concatenate")
        if running:
            self._open_btn.setVisible(False)
            set_variant(self._run_btn, "primary")
        # Locked for the run's duration — Concatenate used to lock none of
        # its fields at all, the one Datasets tool where a mid-run pick
        # could silently change what a subsequent Run press would act on.
        # setLocked(), not setEnabled(False): the latter dims the whole
        # field (including its already-read-only path text) rather than
        # just disabling the one truly interactive control (Browse).
        self._ds1_field.setLocked(running)
        self._ds2_field.setLocked(running)
        self._dest_field.setLocked(running)

    def show_open_in_review(self, dest_root: str) -> None:
        self._dest_root = dest_root
        promote_open_button(self._open_btn, self._run_btn)

    # ── internals ────────────────────────────────────────────────────────────
    def _inputs_ready(self) -> bool:
        return (
            self._ds1_field.is_valid()
            and self._ds2_field.is_valid()
            and self._dest_field.is_valid()
            and not self._cross_message()
        )

    def _cross_message(self) -> str:
        d1, d2, dest = self._ds1_field.path(), self._ds2_field.path(), self._dest_field.path()
        if dest and (dest == d1 or dest == d2):
            return "Destination must be a different folder from both datasets."
        return ""

    def _on_field_changed(self) -> None:
        # A dataset picked again is the point a fresh run becomes the
        # relevant next action once more — hand the primary emphasis back
        # from a still-visible post-success Open button (show_open_in_review),
        # and let the dashboard go back to previewing the (now different)
        # inputs instead of continuing to show the previous run's result.
        # Deliberately not inside _revalidate_cross() itself: revalidate_fields()
        # (called from _on_finished, right after show_open_in_review/set_done
        # already set the post-run state) calls _revalidate_cross() too, and
        # doing either reset there would immediately undo that run's own
        # result — self._running is already False by then, so a guard on it
        # doesn't help; only a genuine field edit may clear a shown result.
        set_variant(self._run_btn, "primary")
        self._update_status_preview()
        self._revalidate_cross()

    def _revalidate_cross(self) -> None:
        message = self._cross_message()
        self._cross_hint.setText(message)
        self._cross_hint.setVisible(bool(message))
        self._refresh_run_enabled()

    def _update_status_preview(self) -> None:
        # Never touches the dashboard once a run has actually started — a
        # run's own worker (ConcatenateController) drives set_wells()/
        # set_phase()/well colouring from that point on, and both source
        # fields should already be locked for the run's duration; this only
        # ever runs from a field's own changed() signal, so in practice a
        # running worker's own dashboard state is never at risk here, but the
        # guard costs nothing and keeps the two responsibilities visibly
        # separate.
        if self._running:
            return
        if not (self._ds1_field.is_valid() and self._ds2_field.is_valid()):
            self.dashboard.setVisible(False)
            self.dashboard.reset()
            return
        wells = sorted(set(preview_wells(self.dataset1_path())) | set(preview_wells(self.dataset2_path())))
        self.dashboard.reset()
        self.dashboard.set_wells(wells)
        self.dashboard.set_headline(f"Ready — {len(wells)} well(s) to merge.")
        self.dashboard.setVisible(True)

    def _refresh_run_enabled(self) -> None:
        self._run_btn.setEnabled(self._inputs_ready())
        self._queue_btn.setEnabled(self._inputs_ready())
