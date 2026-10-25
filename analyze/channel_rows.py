"""The Analyze form's channel rows: one per channel in the filenames.

Each row is the channel's name, a **Role** dropdown — Quantify, smFISH or Phase
— and a ✕ to remove it. A channel's role is one choice of three: every channel
is quantified, an smFISH channel also has its spots counted, and at most one
channel is Phase (brightfield), which skips correction and is what the
Label-free method segments. The row used to carry two checkboxes for it, which
offered four states for three meanings and put the remove button between the
name and its roles.

The two checkboxes are still the row's state — ``_fluor_rows`` holds
``(edit, smfish_cb, phase_cb, remove_btn)`` exactly as before, hidden — because
the sidecar restore, run-time detection, the input scan and their tests read
and set them by that shape. The dropdown is a view of them, kept in step both
ways, and the exclusivity rules (one Phase across the rows; Phase and smFISH
never together) stay on the checkboxes where they always were.

Moved out of ``analyze/tab.py``, which was at its line budget.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QPushButton, QWidget

from widgets.value_combo import ValueCombo

#: A channel's roles, ``(value, label)``.
CHANNEL_ROLES = (
    ("quantify", "Quantify"),
    ("smfish", "smFISH"),
    ("phase", "Phase"),
)

ROLE_TIP = (
    "Quantify: measure the channel's intensities (every channel is measured).\n"
    "smFISH: also count spots in it.\n"
    "Phase: the brightfield / phase-contrast image. It skips all background "
    "correction, and the Label-free method segments it. At most one channel.")


class AnalyzeChannelRowsMixin:
    """The channel rows' builders and readers. Binds to the ``AnalyzeTab``."""

    def _fluor_add_row(self, default_token: str = "") -> None:
        row = QWidget(self._fluor_frame)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        edit = QLineEdit(default_token, row)
        edit.setPlaceholderText("channel name")
        edit.setMinimumWidth(0)
        edit.textChanged.connect(lambda _: self._refresh_channel_dependents())
        rl.addWidget(edit, 1)

        role = ValueCombo(CHANNEL_ROLES, row)
        role.setToolTip(ROLE_TIP)
        rl.addWidget(role)

        remove_btn = QPushButton("✕", row)
        remove_btn.setFixedWidth(self._REMOVE_BTN_W)   # wide enough that the button padding doesn't clip ✕
        remove_btn.setProperty("variant", "secondary")
        remove_btn.setToolTip("Remove this channel")
        rl.addWidget(remove_btn)

        # The role's state: two hidden checkboxes, as the rows always held it.
        smfish_cb = QCheckBox("smFISH", row)
        phase_cb = QCheckBox("Phase", row)
        smfish_cb.hide()
        phase_cb.hide()

        self._fluor_rows.append((edit, smfish_cb, phase_cb, remove_btn))

        # Phase is single across the experiment (mutually exclusive across rows)
        # and cannot coexist with smFISH on the same channel.
        def _on_phase_toggled(checked: bool, this_cb=phase_cb, this_sm=smfish_cb) -> None:
            if checked:
                this_sm.setChecked(False)
                for _e, _s, _p, _b in self._fluor_rows:
                    if _p is not this_cb:
                        _p.setChecked(False)
            self._refresh_segmentation_hints()

        def _on_smfish_toggled(checked: bool, this_ph=phase_cb) -> None:
            if checked:
                this_ph.setChecked(False)

        def _show_role(_checked: bool = False) -> None:
            value = ("phase" if phase_cb.isChecked()
                     else "smfish" if smfish_cb.isChecked() else "quantify")
            if role.value() != value:
                role.blockSignals(True)
                role.setValue(value)
                role.blockSignals(False)
            self._refresh_storage_rows()

        def _take_role(_index: int) -> None:
            value = role.value()
            phase_cb.setChecked(value == "phase")
            smfish_cb.setChecked(value == "smfish")
            _show_role()

        phase_cb.toggled.connect(_on_phase_toggled)
        smfish_cb.toggled.connect(_on_smfish_toggled)
        phase_cb.toggled.connect(_show_role)
        smfish_cb.toggled.connect(_show_role)
        role.currentIndexChanged.connect(_take_role)
        remove_btn.clicked.connect(
            lambda _=False, r=row, b=remove_btn: self._fluor_remove_row(r, b)
        )
        self._fluor_frame_layout.addWidget(row)
        self._fluor_refresh_remove_buttons()
        # The same call the other two sites make. Adding a row is as much a
        # change to the channel set as removing one or retyping a token, and
        # leaving this one on the older, narrower refresh gave the file two
        # definitions of "the rows changed" — which is how the segmentation
        # hint went stale in the first place.
        self._refresh_channel_dependents()

    def _fluor_remove_row(self, row: QWidget, remove_btn: QPushButton) -> None:
        if len(self._fluor_rows) <= 1:
            return
        self._fluor_rows = [(e, s, p, b) for e, s, p, b in self._fluor_rows if b is not remove_btn]
        row.deleteLater()
        self._fluor_refresh_remove_buttons()
        self._refresh_channel_dependents()
        self._refresh_storage_rows()

    def _refresh_channel_dependents(self) -> None:
        """Everything downstream of the channel rows.

        Both readouts are derived from those rows and both have to move with
        them. Removing the row marked "Phase", or clearing its token, leaves the
        Label-free routine with no channel to segment — and the hint went on
        reading "Use when there's no stain at all…" because only the schema
        preview was refreshed. The run was correctly refused at Run; the live
        feedback said it would succeed.
        """
        self._refresh_schema_preview()
        self._refresh_segmentation_hints()

    def _refresh_storage_rows(self) -> None:
        """"Reference the phase input image" is about the Phase channel, so it is
        shown only while a channel is marked Phase."""
        box = getattr(self, "_phase_reference_cb", None)
        if box is not None:
            box.setVisible(any(p.isChecked() for _e, _s, p, _b in self._fluor_rows))

    def _fluor_refresh_remove_buttons(self) -> None:
        only_one = len(self._fluor_rows) == 1
        for _, _, _, btn in self._fluor_rows:
            btn.setEnabled(not only_one)

    def _fluor_tokens_list(self) -> list[str]:
        return [e.text().strip() for e, _, _, _ in self._fluor_rows if e.text().strip()]

    def _smfish_tokens_list(self) -> list[str]:
        return [e.text().strip() for e, s, _, _ in self._fluor_rows if s.isChecked() and e.text().strip()]

    def _phase_token(self) -> str:
        """The single channel token flagged as the phase/brightfield image (or '')."""
        for e, _s, p, _b in self._fluor_rows:
            if p.isChecked() and e.text().strip():
                return e.text().strip()
        return ""
