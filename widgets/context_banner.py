"""ContextBanner — a strip that says a page is being used for something else.

A page normally does one thing, and its own controls say what. Occasionally a
page is borrowed: the Job Queue sends the user to a tool's page to fill in or
correct a queued job, and while that is happening the page's own Run button is
not what the user came for. Without a strip saying so, the page looks exactly
as it always does and the only way to finish is to guess.

The banner is an *information* notice (``widgets/notice.py``): the sentence,
one button that confirms, and the close symbol that cancels, its tooltip naming
what closing means ("Cancel", "Not now"). It is not a dialog and it does not
block the page beneath it. The page keeps working normally throughout, which is
the point: the user is editing it.

API
---
* ``ContextBanner(text, confirm_text, parent=None, *, cancel_text="Cancel")``
* ``setText(text)`` — restate the context without rebuilding.
* ``confirmed`` / ``cancelled`` — the two ways out.

Host-agnostic by design: it knows nothing about queues, jobs or datasets, and
whoever shows it decides what confirming means.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from widgets.notice import INFORMATION, Notice


class ContextBanner(Notice):
    """An information notice whose button confirms and whose close symbol cancels."""

    confirmed = Signal()
    cancelled = Signal()

    def __init__(self, text: str = "", confirm_text: str = "Save", parent: QWidget | None = None, *,
                 cancel_text: str = "Cancel") -> None:
        super().__init__("", text, parent, severity=INFORMATION, action_text=confirm_text,
                         close_tip=cancel_text)
        self._confirm_btn = self.action_button()
        self._cancel_btn = self.close_button()
        self.actionTriggered.connect(self.confirmed)
        self.closed.connect(self.cancelled)

    def setText(self, text: str) -> None:  # noqa: N802 — Qt naming
        self.setMessage("", text)

    def setConfirmText(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._confirm_btn.setText(text)
        self._fit()
