"""What a folded Analyze section says about itself.

A folded section used to show its title and nothing else, so a folded *Run
scope* holding "Redo it" — or a folded *Advanced* with a changed tile size —
looked exactly like an untouched one. A folded section's header now carries a
short muted line (``AnalyzeFormChromeMixin._section(summary=…)``): sections
with one or two decisions say what they are ("None · Rolling disk", "Fill in
what's missing · only B02"), and the rest say how many of their settings differ
from what a fresh form holds ("2 changed").

"What a fresh form holds" is a snapshot taken at the end of the build
(:meth:`AnalyzeSectionSummaryMixin._snapshot_form_defaults`): every line edit's
text, combo's index, checkable button's state and checkable dropdown's
selection, keyed by the widget. A sidecar restore that changes a setting
therefore counts as a change, which is the point — the line answers "is this
section as a new form would have it?".
"""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractButton, QComboBox, QLineEdit, QWidget


def _controls(container: QWidget) -> list[QWidget]:
    """The settings inside *container*: its line edits, combos, checkable
    buttons and checkable dropdowns.

    Asked for by type, never as ``findChildren(QWidget)``: that wraps every
    widget Qt builds inside a control for its own use, and a wrapper left
    behind when Qt deletes one of those crashed the interpreter when the pane
    was torn down.
    """
    from widgets.checkable_dropdown import CheckableDropdown

    found: list[QWidget] = []
    for kind in (QLineEdit, QComboBox, QAbstractButton, CheckableDropdown):
        found += container.findChildren(kind)
    return found


def _state(w: QWidget) -> object | None:
    """A comparable value for *w*, or ``None`` for a widget that holds none."""
    if isinstance(w, QLineEdit):
        return w.text()
    if isinstance(w, QComboBox):
        return w.currentIndex()
    if isinstance(w, QAbstractButton) and w.isCheckable():
        return w.isChecked()
    checked = getattr(w, "checkedData", None)
    if callable(checked):
        return tuple(checked())
    return None


class AnalyzeSectionSummaryMixin:
    """Fresh-form snapshot and the "N changed" summary. Binds to the tab."""

    def _snapshot_form_defaults(self) -> None:
        """Record every setting's value as the build left it."""
        self._form_defaults = {}
        for container in getattr(self, "_section_widgets", {}).values():
            for w in _controls(container):
                value = _state(w)
                if value is not None:
                    self._form_defaults[id(w)] = (w, value)
        self.refresh_section_summaries()

    def _changed_settings(self, title: str) -> int:
        """How many settings in section *title* differ from the snapshot.

        Only the widgets that existed at the snapshot are compared; a widget
        built later (none are, today) is not counted rather than guessed at.
        """
        container = getattr(self, "_section_widgets", {}).get(title)
        snapshot = getattr(self, "_form_defaults", None)
        if container is None or not snapshot:
            return 0
        n = 0
        for w in _controls(container):
            entry = snapshot.get(id(w))
            if entry is not None and entry[0] is w and _state(w) != entry[1]:
                n += 1
        return n

    def _changed_summary(self, title: str) -> str:
        """``"2 changed"`` for section *title*, or ``""`` when nothing is."""
        n = self._changed_settings(title)
        return f"{n} changed" if n else ""
