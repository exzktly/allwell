"""The filename-schema builder: a separator, five position dropdowns, and the
schema string the two of them write.

A raw image filename is described by a colon-delimited schema
(``experiment:channel:well:fov:timepoint``) plus the character that separates
its tokens. Two places in the app ask the user for that pair — the Analyze
form's **Filename Schema** section, and the Datasets pane's **Zip Loose
Folder** page, which has to parse the same filenames before it can group them
into per-well archives. Both are answering the same question about the same
files, so they ask it with the same control.

The dropdowns and the string edit are two views of one value and stay in step
in both directions: picking a field rewrites the string, and typing a string
resets the dropdowns. Signals are blocked across each write so the two do not
chase each other.

The vocabulary and the validity rules come from :mod:`util.schema`, which the
pipeline itself parses schemas with — so what the picker offers is exactly what
the pipeline understands, and what it calls invalid is what the pipeline would
reject.

Layout is the host's business. ``row_factory`` takes a ``(parent, label)`` pair
and returns the cell the control goes in, which is the signature of the Analyze
form's own ``_row`` — passing it in keeps the picker inside that form's label
column and general look, while a page with no such chrome gets a plain labelled
row instead.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from widgets._support import set_variant
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP, SCHEMA_FIELDS, parse_schema, validate_schema

#: Human-readable label per schema field, positionally paired with
#: ``util.schema.SCHEMA_FIELDS``. ``strict=`` on the zips below turns a dropped
#: label into an error rather than a field silently unreachable in the picker.
FIELD_LABELS: tuple[str, ...] = (
    "Experiment", "Channel", "Well", "FOV", "Timepoint", "— ignore —",
)
LABEL_TO_FIELD: dict[str, str] = dict(zip(FIELD_LABELS, SCHEMA_FIELDS, strict=True))
FIELD_TO_LABEL: dict[str, str] = dict(zip(SCHEMA_FIELDS, FIELD_LABELS, strict=True))

#: How many filename positions the picker offers. The default schema's length —
#: a schema string may be shorter (the remaining dropdowns read "— ignore —")
#: and a longer one typed into the string edit keeps its extra fields, which the
#: dropdowns cannot show but ``schema()`` still returns.
POSITIONS = len(DEFAULT_SCHEMA.split(":"))

_SEP_EDIT_W = 30
_COMBO_W = 160
_SCHEMA_EDIT_MIN_W = 165
#: Cap on the schema string edit for a host that does not pin a width. The value
#: is ~37 characters of the default schema plus room to type; without it the
#: edit's stretch takes the whole row, which reads as a text *area* on a
#: full-width page while a sidebar host is unaffected either way.
_SCHEMA_EDIT_MAX_W = 420
#: Label-column width and label→control gap for the fallback row. A host that
#: brings its own ``row_factory`` (the Analyze form) sets its own.
_PLAIN_LABEL_W = 110
_PLAIN_LABEL_GAP = 4


class SchemaPicker(QWidget):
    """Separator + position dropdowns + schema string, kept in step.

    ``changed`` fires whenever the schema string or the separator moves,
    whichever control the user touched.
    """

    changed = Signal()
    #: Pressed the detect button. Carries nothing: the widget has no idea where
    #: filenames come from, and finding that out is the host's job.
    detectRequested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        detect_label: str = "",
        row_factory: Callable[[QWidget, str], QWidget] | None = None,
        combo_width: int = _COMBO_W,
        schema_edit_min_width: int = _SCHEMA_EDIT_MIN_W,
        schema_edit_max_width: int | None = _SCHEMA_EDIT_MAX_W,
        separator_edit_width: int = _SEP_EDIT_W,
        label_width: int = _PLAIN_LABEL_W,
    ) -> None:
        super().__init__(parent)
        self._combo_w = combo_width
        self._schema_edit_min_w = schema_edit_min_width
        self._schema_edit_max_w = schema_edit_max_width
        self._sep_edit_w = separator_edit_width
        self._label_w = label_width

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._row = row_factory or self._plain_row

        sep_cell = self._row(self, "Separator char")
        self._sep_edit = QLineEdit(DEFAULT_SEP, sep_cell)
        self._sep_edit.setFixedWidth(self._sep_edit_w)
        # An empty box still separates on the default, so say which one rather
        # than substituting it invisibly — a blank field beside a readout that
        # keeps splitting on "_" looks like the readout is ignoring the field.
        self._sep_edit.setPlaceholderText(DEFAULT_SEP)
        sep_cell.layout().addWidget(self._sep_edit)
        sep_cell.layout().addStretch(1)
        self._sep_edit.textChanged.connect(lambda _t: self.changed.emit())

        default_fields = DEFAULT_SCHEMA.split(":")
        self._combos: list[QComboBox] = []
        for index in range(POSITIONS):
            cell = self._row(self, f"Position {index + 1}")
            combo = QComboBox(cell)
            combo.addItems(list(FIELD_LABELS))
            combo.setCurrentText(FIELD_TO_LABEL.get(default_fields[index], "— ignore —"))
            combo.setFixedWidth(self._combo_w)
            cell.layout().addWidget(combo)
            cell.layout().addStretch(1)
            self._combos.append(combo)
            combo.currentIndexChanged.connect(self._on_combo_changed)

        schema_cell = self._row(self, "Schema string")
        self._schema_edit = QLineEdit(DEFAULT_SCHEMA, schema_cell)
        # Expand to fill the row, up to a cap. The string never fits unscrolled
        # at the width a sidebar offers — a scrollable single-line edit, so the
        # minimum just keeps a legible chunk on screen, and the cursor starts at
        # the front so the field opens showing the beginning rather than the
        # tail. The cap is what keeps a full-width page from stretching a
        # 37-character value across the window; a sidebar never reaches it.
        self._schema_edit.setMinimumWidth(self._schema_edit_min_w)
        if self._schema_edit_max_w is not None:
            self._schema_edit.setMaximumWidth(self._schema_edit_max_w)
        self._schema_edit.setCursorPosition(0)
        schema_cell.layout().addWidget(self._schema_edit, 1)
        # Both halves of this control answer the same question, so both answer
        # it at the same moment: the separator is live per keystroke, and so is
        # this. `editingFinished` additionally re-syncs the dropdowns, which
        # would otherwise thrash through five fields mid-word.
        self._schema_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._schema_edit.editingFinished.connect(self._on_schema_text_committed)

        # The detect control is opt-in, and deliberately so. A button that is
        # always built is a button a host can forget to connect, and a schema
        # detector that does nothing when pressed looks exactly like one that
        # found nothing — the failure this whole control exists to stop being
        # invisible. Passing a label is the host saying it has filenames to
        # offer and will answer `detectRequested`.
        self._detect_btn: QPushButton | None = None
        self._detect_lbl: QLabel | None = None
        if detect_label:
            cell = self._row(self, "")
            self._detect_btn = QPushButton(detect_label, cell)
            set_variant(self._detect_btn, "secondary")
            self._detect_btn.clicked.connect(lambda: self.detectRequested.emit())
            cell.layout().addWidget(self._detect_btn)
            cell.layout().addStretch(1)

            self._detect_lbl = QLabel("", self)
            self._detect_lbl.setObjectName("Muted")
            self._detect_lbl.setWordWrap(True)
            outer.addWidget(self._detect_lbl)

    # ── layout ───────────────────────────────────────────────────────────────
    def _plain_row(self, parent: QWidget, label: str) -> QWidget:
        """The fallback ``row_factory``: a label beside a cell, both appended to
        *parent*'s layout. Returns the cell, matching the Analyze form's
        ``_row`` so either can be passed in."""
        row = QWidget(parent)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(_PLAIN_LABEL_GAP)
        lbl = QLabel(label, row)
        lbl.setObjectName("Muted")
        # Fixed, so all seven rows put their control at one left edge. Sized to
        # the natural width of each label, the rows step in and out by tens of
        # pixels and the block reads as three separate groups.
        lbl.setFixedWidth(self._label_w)
        rl.addWidget(lbl, 0, Qt.AlignVCenter)
        cell = QWidget(row)
        cl = QHBoxLayout(cell)
        cl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(cell, 1)
        parent.layout().addWidget(row)
        return cell

    # ── the two views, kept in step ──────────────────────────────────────────
    def _on_combo_changed(self, _index: int) -> None:
        text = ":".join(self.fields())
        if text != self._schema_edit.text():
            self._schema_edit.blockSignals(True)
            self._schema_edit.setText(text)
            self._schema_edit.blockSignals(False)
        self.changed.emit()

    def _on_schema_text_committed(self) -> None:
        if self._apply_string_to_combos():
            self.changed.emit()

    def _apply_string_to_combos(self) -> bool:
        """Reset the dropdowns from the string edit. False if nothing moved."""
        raw = self._schema_edit.text().strip()
        if not raw:
            return False
        fields = (parse_schema(raw) + ["ignore"] * POSITIONS)[:POSITIONS]
        if fields == self.fields():
            return False
        for combo, field in zip(self._combos, fields):
            label = FIELD_TO_LABEL.get(field, "— ignore —")
            if combo.currentText() != label:
                combo.blockSignals(True)
                combo.setCurrentText(label)
                combo.blockSignals(False)
        return True

    # ── what the host reads ──────────────────────────────────────────────────
    def fields(self) -> list[str]:
        """The dropdowns' field names, in position order."""
        return [LABEL_TO_FIELD.get(c.currentText(), "ignore") for c in self._combos]

    def schema(self) -> str:
        """The schema string. The typed string wins over the dropdowns when the
        two disagree — it is the one that can express a schema longer than the
        picker has positions for."""
        raw = self._schema_edit.text().strip()
        return raw or ":".join(self.fields())

    def separator(self) -> str:
        """The token separator; never empty — blank falls back to the default."""
        return self._sep_edit.text() or DEFAULT_SEP

    def errors(self) -> list[str]:
        """Human-readable reasons the current schema is unusable; empty if it is
        fine. Delegates to ``util.schema.validate_schema``, so the message is
        word-for-word what the pipeline's own CLI would print."""
        return validate_schema(parse_schema(self.schema()))

    # ── what the host writes ─────────────────────────────────────────────────
    def setSchema(self, schema: str) -> None:  # Qt naming
        """Set the string and reset the dropdowns from it. Emits ``changed``."""
        self._schema_edit.blockSignals(True)
        self._schema_edit.setText(schema or DEFAULT_SCHEMA)
        self._schema_edit.setCursorPosition(0)
        self._schema_edit.blockSignals(False)
        self._apply_string_to_combos()
        self.changed.emit()

    def setSeparator(self, sep: str) -> None:  # Qt naming
        """Set the separator. Emits ``changed``."""
        self._sep_edit.setText(sep or DEFAULT_SEP)

    # ── the widgets themselves, for a host that must reach one ───────────────
    @property
    def schema_edit(self) -> QLineEdit:
        return self._schema_edit

    @property
    def separator_edit(self) -> QLineEdit:
        return self._sep_edit

    @property
    def combos(self) -> list[QComboBox]:
        return list(self._combos)

    def setDetection(self, text: str, *, problem: bool = False) -> None:  # Qt naming
        """Show *text* beneath the control as the result of a detection.

        The widget does not compose this sentence and does not know what it
        means — it is handed one and shows it. *problem* picks the styled
        object name, so a refusal reads as a refusal rather than as a quieter
        success; the two must not look alike, which is the one thing this
        readout exists for.

        A no-op when the host did not ask for the detect control, so a caller
        cannot push a result into a picker that never offered to find one.
        """
        if self._detect_lbl is None:
            return
        self._detect_lbl.setObjectName("Error" if problem else "Muted")
        # An object name changed after the sheet was applied does not restyle on
        # its own; Qt re-polishes only when told to.
        self._detect_lbl.style().unpolish(self._detect_lbl)
        self._detect_lbl.style().polish(self._detect_lbl)
        self._detect_lbl.setText(text or "")

    def detectionText(self) -> str:  # Qt naming
        """What :meth:`setDetection` last displayed, or ``""``."""
        return self._detect_lbl.text() if self._detect_lbl is not None else ""

    def detectButton(self) -> QPushButton | None:
        """The detect button, or ``None`` when the host did not ask for one."""
        return self._detect_btn
