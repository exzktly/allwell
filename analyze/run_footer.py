"""The Analyze pane's run footer, and the remote options it governs.

**Run is always on screen.** The form is a long scroll — measured at 1,857 px
in an 860 px window with the default sections folded — and the Run button used
to be its last row, two screens below where a user starts. The footer holds
the two decisions a user makes at the moment of starting, *where* (Run on) and
*go* (Run, + Queue, and Suspend / Stop while a run is going), and is pinned
under the scrolling form by :class:`widgets.footer_scroll_area.FooterScrollArea`,
so it neither scrolls away nor covers the form's last rows.

The remote-only options (batch size, auto-delete, dry-run cleanup) stay in the
scrolling form as its last section, shown only while a remote is chosen: three
rows of settings are form content, and a footer that grew by three rows every
time a remote was picked would take that height from the form above it.

Mixed into ``AnalyzeTabFormMixin``; every widget lands on the ``AnalyzeTab``
under the name the rest of the pane reaches for (``_remote_cb``,
``_remote_params_widget``, ``_run_btn``, …).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from services.remote_config import default_remote_index, load_remotes
from widgets.wrap_checkbox import WrapCheckBox


class AnalyzeRunFooterMixin:
    """Builders for the run footer and the Remote options section."""

    def _build_remote_options_section(self, parent: QWidget) -> None:
        """Every knob that only matters for a remote run, in one section.

        The whole section is shown only while a remote is selected (see
        ``_refresh_remote_controls``), so a Local run isn't cluttered with
        options that do nothing. ``_remote_params_widget`` is the section's
        container, so hiding it takes the title and hairline with the rows.
        """
        sec = self._section(parent, "Remote options")
        self._remote_params_widget = sec.parentWidget()

        # Batched mode. Any positive integer processes that many wells at a
        # time: peak remote disk ≈ batch_size wells of input + ~1 well of output
        # (per-well Fetcher cleanup), at the cost of one model reload per batch.
        # ``0`` (default) is one well per worker the pipeline will start
        # (services.remote_batch_auto), so the first upload is only as big as
        # the workers need; the whole plate when the worker count cannot be
        # known before the run, and the run log says why.
        bs_row = self._row(
            sec, "Batch size",
            tooltip="Process this many wells at a time on the remote, which "
                    "bounds its peak disk to about that many wells of input. "
                    "0 sends one well per worker, worked out from the Workers "
                    "field or the CPUs and GPUs in the scheduler command; the "
                    "run log says what it chose.")
        self._remote_batch_size_edit = self._entry(bs_row, "0", width=40)
        self._field_note(bs_row, "wells · 0 = one per worker")
        bs_row.layout().addStretch(1)

        # ON by default: a remote that accumulates every run fills up, and a full
        # volume is how a run silently loses FOV-groups. Only deletes after a
        # verified-successful run, never failed work.
        self._cleanup_remote_cb = QCheckBox("Auto-delete remote files when done", sec)
        self._cleanup_remote_cb.setChecked(True)
        self._cleanup_remote_cb.setToolTip(
            "Checked (default) = after a verified-successful run, delete both the "
            "per-run output dir AND the cached input on the remote, so a shared "
            "machine does not fill up. Unchecked = leave it untouched.")
        sec.layout().addWidget(self._cleanup_remote_cb)

        # Log what auto-delete WOULD remove without removing it. Only enabled
        # when auto-delete is checked.
        self._dry_run_cleanup_cb = WrapCheckBox(
            "Dry-run cleanup (show what would be deleted, don't delete)", sec)
        self._dry_run_cleanup_cb.setToolTip(
            "With auto-delete enabled, log path + file count + total bytes for "
            "each remote directory that WOULD be removed at end-of-run, without "
            "removing anything. Useful for confirming what auto-delete will do "
            "on a shared remote before committing to it.")
        sec.layout().addWidget(self._dry_run_cleanup_cb)

    def _mount_run_footer(self, scroll) -> None:
        """Build the footer and pin it under *scroll*, the form's
        :class:`~widgets.footer_scroll_area.FooterScrollArea`."""
        footer = QWidget()
        footer.setObjectName("AnalyzeRunFooter")
        self._build_run_footer(footer)
        scroll.setFooter(footer)
        self._run_footer = footer
        # The schema check also decides whether Run is enabled, and the first
        # one ran inside _build_form, before the footer's Run button existed.
        self._refresh_schema_preview()
        self._start_live_run_checks()

    def _build_run_footer(self, footer: QWidget) -> None:
        """Fill *footer*: the Run on row, then the run buttons.

        Built after the form, because the remote selector's first refresh
        shows or hides the Remote options section the form holds.
        """
        # Its top edge and fill are QSS (``QWidget#AnalyzeRunFooter``).
        footer.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Remote-execution selector. Populated from remotes.toml in the app
        # folder so users can flip between Local and any configured SSH host
        # without restarting the GUI.
        remote_widget = QWidget(footer)
        rl = QHBoxLayout(remote_widget)
        rl.setContentsMargins(12, 8, 12, 0)
        rl.setSpacing(8)
        rl.addWidget(QLabel("Run on", remote_widget))
        self._remote_cb = QComboBox(remote_widget)
        self._remote_cb.addItem("Local", userData=None)
        try:
            for name in load_remotes():
                self._remote_cb.addItem(name, userData=name)
        except (ValueError, OSError) as exc:
            # Malformed remotes.toml shouldn't bring the GUI down — just
            # surface the error in the log and stick with Local.
            self._log_q.put((
                "line",
                f"[warn] could not load remotes.toml: {exc}\n",
            ))
        # Default to the first configured remote when any exist (else Local).
        # The footer keeps that choice in sight at the moment of pressing Run,
        # which is what the default was missing when it sat at the form's foot.
        self._remote_cb.setCurrentIndex(
            default_remote_index(self._remote_cb.count()))
        rl.addWidget(self._remote_cb, 1)
        self._manage_remotes_btn = QPushButton("Manage…", remote_widget)
        self._manage_remotes_btn.setProperty("variant", "secondary")
        self._manage_remotes_btn.setToolTip(
            "Add, edit, delete, test, or bootstrap remote-execution hosts.")
        self._manage_remotes_btn.clicked.connect(self._open_manage_remotes)
        rl.addWidget(self._manage_remotes_btn)
        layout.addWidget(remote_widget)

        # Show the remote-only section only when a remote is selected; dry-run
        # only makes sense when auto-delete is on.
        self._remote_cb.currentIndexChanged.connect(self._refresh_remote_controls)
        self._cleanup_remote_cb.toggled.connect(self._refresh_remote_controls)
        self._refresh_remote_controls()

        layout.addWidget(self._build_run_row(footer))

        # What stops the form from running, under Run: every problem, the
        # first in full; a click goes to its section (``analyze.run_problems``).
        self._run_problems_lbl = QLabel("", footer)
        self._run_problems_lbl.setWordWrap(True)
        self._run_problems_lbl.setContentsMargins(12, 0, 12, 8)
        self._run_problems_lbl.setCursor(Qt.PointingHandCursor)
        self._run_problems_lbl.mousePressEvent = lambda _ev: self._go_to_run_problem()
        layout.addWidget(self._run_problems_lbl)
        self._set_status(self._run_problems_lbl, "")
