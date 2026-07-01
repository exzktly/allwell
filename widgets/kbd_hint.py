"""KbdHint — inline mono keyboard-shortcut glyph (e.g. ``⌘O``, ``⌘K``).

A tiny ``QLabel`` styled with the design-token mono font, faint border, and a
subtle background so it reads as a keycap rather than text. Sized off the
current font's caption size so it scales with DPI / theme.

Two shapes are supported:

* **Standalone** — drop ``KbdHint("⌘K")`` into any layout.
* **Composed with an ``IconButton``** — call ``KbdHint.attach(btn, "⌘O")``
  to right-pad the button's text with the glyph. Internally the helper
  simply appends a ``KbdHint`` to the button as a child layout item via
  a wrapping ``QWidget`` returned to the caller.

API
---
* ``KbdHint(text, parent=None)``
* ``setText(text)``
* class method ``KbdHint.attach(btn, text) -> QWidget`` — wraps the button +
  hint into a single ``QFrame`` you can drop into a layout in place of the
  button.

The widget exposes no signals — it's purely presentational. ``QShortcut`` is
the live binding for the actual key combo.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget,
)

import theme  # noqa: E402


class KbdHint(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("KbdHint")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(self._build_qss())

    def setText(self, text: str) -> None:  # noqa: A003 - QLabel override
        super().setText(text or "")

    def _build_qss(self) -> str:
        c, t, r, s = theme.Colors, theme.Typography, theme.Radii, theme.Spacing
        return f"""
        QLabel#KbdHint {{
            color: {c.text_faint};
            background-color: {c.panel_elevated};
            border: 1px solid {c.border_subtle};
            border-radius: {r.xs}px;
            padding: 1px {s.xs}px;
            font-family: {t.family_mono};
            font-size: {t.caption_size}px;
            font-weight: 500;
        }}
        """

    # ── composition helper ───────────────────────────────────────────────
    @classmethod
    def attach(cls, btn: QWidget, text: str) -> QFrame:
        """Wrap *btn* and a trailing ``KbdHint(text)`` into a single ``QFrame``.

        The button keeps its own click handlers / styling; the helper just
        right-pads it with the hint. Drop the returned frame into a layout
        in place of the bare button.
        """
        wrap = QFrame(btn.parent())
        wrap.setObjectName("KbdHintWrap")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.Spacing.xs)
        # Re-parent the button into the wrap; preserves signals / state.
        btn.setParent(wrap)
        lay.addWidget(btn)
        lay.addWidget(cls(text, wrap))
        return wrap


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout
    from widgets.icon_button import IconButton

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    host = QWidget()
    host.setWindowTitle("KbdHint — demo")
    host.resize(360, 220)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(24, 24, 24, 24)
    lay.setSpacing(12)

    lay.addWidget(QLabel("Standalone:"))
    row = QFrame()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(6)
    rl.addWidget(KbdHint("⌘O"))
    rl.addWidget(KbdHint("⌘K"))
    rl.addWidget(KbdHint("⌘E"))
    rl.addWidget(KbdHint("⌥⇧A"))
    rl.addStretch(1)
    lay.addWidget(row)

    lay.addWidget(QLabel("Composed with a button:"))
    open_btn = QPushButton("Open")
    open_btn.setProperty("variant", "primary")
    lay.addWidget(KbdHint.attach(open_btn, "⌘O"))

    lay.addWidget(QLabel("Composed with an IconButton:"))
    ib = IconButton("folder-open")
    ib.setText("  Open")
    lay.addWidget(KbdHint.attach(ib, "⌘O"))

    lay.addStretch(1)
    host.show()
    _sys.exit(app.exec())
