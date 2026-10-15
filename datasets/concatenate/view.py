"""Qt builder for the Concatenate Datasets page (the main viewer area).

One equation-style input row — dataset 1 (the base / earlier timepoints) +
dataset 2 (the one appended after) = a fresh destination, built by the shared
:mod:`datasets.core.equation_row` that Merge Plates draws from too — over an
Analyze-style status view: a live per-well :class:`~datasets.concatenate.dashboard.ConcatDashboard`
with the raw text log demoted to a collapsible "Show raw log" checkbox (the
same furniture as ``analyze/log_pane.py``'s ``_build_log``/``_on_toggle_raw_log``,
though Analyze starts its log open and this page starts it closed — see
:mod:`datasets.core.log_panel` for why).

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
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from widgets._support import set_variant
from datasets.core.dataset_paths import preview_wells
from datasets.core.equation_row import build_equation_row
from datasets.core.log_panel import LogPanelMixin, hrule as _hrule
from datasets.concatenate.concatenate_compute import (
    GapPreview,
    destination_has_unrecognized_content,
    destination_is_complete_dataset,
    gap_preview,
)
from datasets.concatenate.dashboard import ConcatDashboard
from datasets.concatenate.raw_compute import is_raw_dataset
from datasets.core.dataset_fields import is_dataset, is_free_destination
from datasets.core.dataset_fields import promote_open_button





def _is_dataset(root: Path) -> str:
    """This page's dataset rule: the shared one, or a raw dataset (``in/`` only)."""
    return "" if is_raw_dataset(root) else is_dataset(root)


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
        # What the gap field was last pre-filled with. Text equal to it is the
        # default untouched, and is sent as "no entry" so a run takes whatever
        # the default is when it runs; anything else is the user's own gap.
        self._gap_default_text = ""
        self._gap_preview = GapPreview()
        self._build_ui()

    def _build_ui(self) -> None:
        # Wrap in a scroll area so the page's content (fields, plate
        # selector, dashboard, log) can exceed the window's default height
        # (1640x980 — allwell.py) without crushing whatever's at the bottom —
        # that used to make ConcatDashboard._plate render illegibly small,
        # since its cell size derives from its actual allotted width/height
        # (well_plate_selector's _metrics()). Mirrors the pattern
        # analyze/tab.py already uses for its own form panel.
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
            "relabelled to continue after dataset 1's last timepoint. Both can be "
            "analysed datasets, or both raw (converted images with no analysis "
            "yet — merge those first, then analyse the plate once). The merged "
            "result is written to the destination folder — both originals are "
            "left untouched.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._build_equation_row(outer)
        self._build_gap_row(outer)

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
        eq = build_equation_row(
            self, outer,
            ds1_label="Dataset 1 (base)", ds2_label="Dataset 2 (appended)",
            is_dataset=_is_dataset, is_destination=_is_destination,
            on_changed=self._on_field_changed,
        )
        self._ds1_field, self._ds2_field = eq.ds1, eq.ds2
        self._dest_field, self._cross_hint = eq.dest, eq.cross_hint

    def _build_gap_row(self, outer: QVBoxLayout) -> None:
        """The time between the two datasets: a field, what it will do, and why."""
        self._gap_box = QWidget(self)
        box = QVBoxLayout(self._gap_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.Spacing.xs)
        row = QHBoxLayout()
        row.setSpacing(theme.Spacing.sm)
        caption = QLabel("Dataset 2's first timepoint comes", self._gap_box)
        row.addWidget(caption)
        self._gap_edit = QLineEdit(self._gap_box)
        self._gap_edit.setObjectName("ConcatGapField")
        self._gap_edit.setPlaceholderText("e.g. 24h")
        self._gap_edit.setMaximumWidth(140)
        self._gap_edit.setToolTip(
            "The time from dataset 1's last timepoint to dataset 2's first, as "
            "6, 24h, 1d or 1d2h30m. Pre-filled from the two datasets' recorded "
            "acquisition times, else from dataset 1's usual interval; type your "
            "own to override it.")
        self._gap_edit.textChanged.connect(lambda _text: self._on_gap_changed())
        row.addWidget(self._gap_edit)
        row.addWidget(QLabel("after dataset 1's last", self._gap_box))
        row.addStretch(1)
        box.addLayout(row)
        self._gap_hint = QLabel("", self._gap_box)
        self._gap_hint.setObjectName("Muted")
        self._gap_hint.setWordWrap(True)
        box.addWidget(self._gap_hint)
        self._gap_labels = QLabel("", self._gap_box)
        self._gap_labels.setObjectName("Muted")
        self._gap_labels.setWordWrap(True)
        box.addWidget(self._gap_labels)
        self._gap_box.setVisible(False)
        outer.addWidget(self._gap_box)

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

    def gap_text_if_entered(self) -> str:
        """The gap field's text when the user chose it, else ``""`` (use the default)."""
        text = self._gap_edit.text().strip()
        return "" if text == self._gap_default_text else text

    def set_gap_text(self, text: str) -> None:
        """Show *text* as the user's own gap; ``""`` puts the default back."""
        self._gap_edit.setText(text or self._gap_default_text)

    def gap_preview(self) -> GapPreview:
        return self._gap_preview

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
        self._gap_edit.setEnabled(not running)

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
            and not self._gap_preview.error
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
        self._refresh_gap(datasets_changed=True)
        self._revalidate_cross()

    def _on_gap_changed(self) -> None:
        set_variant(self._run_btn, "primary")
        self._refresh_gap(datasets_changed=False)
        self._refresh_run_enabled()

    def _refresh_gap(self, *, datasets_changed: bool) -> None:
        """Re-read the two datasets' times and redraw the gap row.

        Runs when a dataset changes (re-pre-filling a field the user has not
        touched) and on every edit of the field itself.
        """
        if self._running:
            return
        if not (self._ds1_field.is_valid() and self._ds2_field.is_valid()):
            self._gap_preview = GapPreview()
            self._gap_box.setVisible(False)
            return
        d1, d2 = self.dataset1_path(), self.dataset2_path()
        base = gap_preview(d1, d2, "")
        if datasets_changed:
            typed = self._gap_edit.text().strip()
            if not typed or typed == self._gap_default_text:
                self._gap_default_text = base.default_text
                blocked = self._gap_edit.blockSignals(True)
                self._gap_edit.setText(base.default_text)
                self._gap_edit.blockSignals(blocked)
            else:
                self._gap_default_text = base.default_text
        entered = self.gap_text_if_entered()
        preview = gap_preview(d1, d2, entered) if entered else base
        self._gap_preview = preview
        self._gap_box.setVisible(True)
        if preview.error:
            self._gap_hint.setObjectName("Error")
            self._gap_hint.setText(preview.error)
        elif preview.warning:
            self._gap_hint.setObjectName("Warn")
            self._gap_hint.setText(preview.warning)
        else:
            self._gap_hint.setObjectName("Muted")
            self._gap_hint.setText(preview.note)
        self._gap_hint.style().unpolish(self._gap_hint)
        self._gap_hint.style().polish(self._gap_hint)
        self._gap_hint.setVisible(bool(self._gap_hint.text()))
        self._gap_labels.setText(
            "Dataset 2's timepoints become: " + ", ".join(preview.labels)
            if preview.labels else "")
        self._gap_labels.setVisible(bool(preview.labels))

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
