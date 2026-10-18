"""A popup progress bar for work that runs off the GUI thread.

A thin :class:`QProgressDialog` with the settings every caller wants spelled
once: it opens at once rather than after Qt's default four-second guess, it
never closes or resets itself when the bar reaches the end (the caller decides
when the work is over, and the last step of a job is often a redraw that comes
*after* the last counted item), and a total of zero shows Qt's busy animation
rather than an empty bar.

It is **application-modal**. The work behind it is rewriting data that every
window reads — the main window and any non-modal tool window beside it — so the
popup keeps the user from starting a second change against data the first one
is halfway through. Modal is not the same as frozen: the work is on another
thread, so the event loop keeps running, the window repaints, the bar moves, and
the Cancel button (when the job offers one) answers. Qt hides a progress
dialog the moment its Cancel is pressed, so a caller whose work needs time to
stop replaces it with a fresh no-Cancel popup that says so.

A job that cannot be cancelled says so by offering no button, and the dialog
then refuses every other way out too — Escape, and the title bar's close
button. A popup that can be dismissed while its work carries on would leave a
running job with nothing on screen to say so.

Knows nothing about All-Well: it takes a parent, a title and a sentence.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QProgressDialog, QWidget


class ProgressPopup(QProgressDialog):
    """An application-modal progress dialog the caller opens, feeds and closes."""

    def __init__(self, parent: QWidget | None, *, title: str, text: str,
                 cancellable: bool = False) -> None:
        super().__init__(text, "Cancel" if cancellable else "", 0, 0, parent)
        self._cancellable = bool(cancellable)
        #: Set by :meth:`close_popup`, the only route out of a popup that offers
        #: no Cancel: ``reject`` and ``closeEvent`` refuse until it is set.
        self._finishing = False
        if not cancellable:
            self.setCancelButton(None)
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setValue(0)

    @property
    def cancellable(self) -> bool:
        return self._cancellable

    def set_progress(self, done: int, total: int) -> None:
        """Show *done* of *total*; a *total* of zero or less shows the busy bar."""
        if total <= 0:
            if self.maximum() != 0:
                self.setRange(0, 0)
            return
        if self.maximum() != total:
            self.setRange(0, int(total))
        self.setValue(max(0, min(int(done), int(total))))

    def set_text(self, text: str) -> None:
        self.setLabelText(str(text))

    def close_popup(self) -> None:
        """Close and schedule deletion — the one way out of a no-Cancel popup."""
        self._finishing = True
        # ``QProgressDialog.closeEvent`` emits ``canceled``: a popup closed
        # because its work finished must not read to a listener as the user
        # cancelling it.
        self.blockSignals(True)
        self.close()
        self.deleteLater()

    # A no-Cancel popup must not be dismissed while its work is still running.
    def reject(self) -> None:  # noqa: D401 - Qt override
        if self._cancellable or self._finishing:
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._cancellable or self._finishing:
            super().closeEvent(event)
        else:
            event.ignore()
