"""The converters' timepoint picker: which of the source's timepoints to convert.

A :class:`~widgets.checkable_dropdown.CheckableDropdown` listing every
timepoint the source holds by its own token (``t0``, ``24h``), with the one
piece of meaning the three converter pages share on top of it: **everything
ticked is the default and is reported as no selection at all**, so a page left
alone converts the whole timelapse and writes the same resume marker it always
did (``convert_marker.OMITTED_WHEN_EMPTY``).

The selection is held here as a value rather than read back from the ticks,
because the list is rebuilt whenever the source changes and the restored
settings of a queued job can arrive before the scan that lists the tokens.
A restored token the source does not hold is **kept and listed**, marked as
missing, rather than silently dropped: ``plan_conversion`` then refuses the
run naming it, and the user can untick it — where dropping it would convert a
different set of timepoints than the job asked for, with nothing saying so.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from widgets.checkable_dropdown import CheckableDropdown

_MISSING_SUFFIX = " (not in this source)"


class TimepointPicker(CheckableDropdown):
    """Tick the timepoints to convert; ``subset()`` is what the params carry."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, noun="timepoints",
                         empty_text="No timepoints ticked")
        self.setObjectName("TimepointPicker")
        self._tokens: list[str] = []
        #: ``()`` converts every timepoint, ``None`` means nothing is ticked
        #: (refused by the page), anything else is the explicit selection.
        self._subset: tuple[str, ...] | None = ()
        self._settled = False
        self.changed.connect(self._on_ticks_changed)
        self._rebuild()

    # ── API ──────────────────────────────────────────────────────────────
    def source_tokens(self) -> list[str]:
        return list(self._tokens)

    def subset(self) -> tuple[str, ...] | None:
        """``()`` for every timepoint, ``None`` for none, else the tokens ticked."""
        return self._subset

    def set_subset(self, tokens) -> None:
        """Restore a saved selection; empty ticks every timepoint."""
        self._subset = tuple(str(t) for t in (tokens or ()))
        self._rebuild()

    def set_source_tokens(self, tokens) -> None:
        """List *tokens*, keeping the current selection. A no-op if unchanged."""
        tokens = [str(t) for t in tokens]
        if tokens == self._tokens:
            return
        self._tokens = tokens
        self._rebuild()

    # ── internals ────────────────────────────────────────────────────────
    def _set_all(self, value: bool) -> None:
        """The menu's **All** / **None**. *All* means the whole source.

        Overridden because ticking every *listed* item also ticks a restored
        token the source does not hold — and when the plan refused on that
        token, the list has not been given the source's tokens yet, so the
        missing ones are all it holds. All would then re-assert the very
        selection being refused, with no way out of it but None, which is
        refused too. Resetting to "every timepoint" lets the plan resolve,
        which is what lists the real tokens.
        """
        self._subset = () if value else None
        self._rebuild()
        # Emitted with the value already set: read back from the ticks, "every
        # timepoint" over a list that is still empty would come out as none.
        self._settled = True
        try:
            self.changed.emit()
        finally:
            self._settled = False

    def _rebuild(self) -> None:
        subset = self._subset
        held = set(self._tokens)
        missing = [t for t in (subset or ()) if t not in held]
        self.blockSignals(True)
        try:
            self.clear()
            for token in self._tokens:
                self.addItem(token, token)
            for token in missing:
                # Labelled only once the source is known: before the scan
                # lands every token is "missing", and saying so would be false.
                label = token + (_MISSING_SUFFIX if self._tokens else "")
                self.addItem(label, token)
            if subset is None:
                self.setCheckedData([])
            elif subset:
                self.setCheckedData(list(subset))
            else:
                self.setCheckedData(list(self._tokens))
        finally:
            self.blockSignals(False)
        self.setToolTip(
            "Which timepoints to convert. Everything ticked converts the whole "
            "timelapse; the stride beside this then keeps every Nth of the "
            "ticked ones. Each kept timepoint keeps its own name.")

    def _on_ticks_changed(self) -> None:
        if self._settled:
            return
        checked = [str(d) for d in self.checkedData()]
        if not checked:
            self._subset = None
        elif (self._tokens and len(checked) == len(self._tokens)
              and set(checked) == set(self._tokens)):
            self._subset = ()
        else:
            self._subset = tuple(checked)
