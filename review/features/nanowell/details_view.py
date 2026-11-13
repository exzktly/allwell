"""The Distribution view's **Details**: its footer, under the figure rather than in it.

Written in the figure, the footer — the counts, what the figure leaves out or marks, how to read it — had grown to
eight lines and left the panels a sliver. ``renderer.render_distribution(footer_out=True)`` returns it as a
``renderer.Footer`` on the hit map instead, and this field shows it.

Collapsed (the default), its header is the counts line and how many alerts and notes it holds, so what the figure
leaves out is never out of sight altogether; an alert makes the header say so in the theme's warning ink. Opened, it lists
the alerts and then the notes, one each. It is shown on the Distribution view, and on the Statistics view while it
draws the Rare responders test, whose full report it holds (``_rare_controller.footer_of``) so the figure keeps its
room for the panels; it remembers being opened for the session.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

_TIP = "The figure's footer: its counts, what it leaves out or marks, and how to read it."


def summary(footer) -> str:
    """The header of a collapsed field: the counts, and how many alerts and notes are inside."""
    parts = [footer.counts]
    alerts = len(footer.alerts) + len(footer.warnings)
    if alerts:
        parts.append(f"⚠ {alerts} alert{'s' if alerts != 1 else ''}")
    if footer.notes:
        parts.append(f"{len(footer.notes)} note{'s' if len(footer.notes) != 1 else ''}")
    return " · ".join(p for p in parts if p)


def body(footer) -> str:
    """The open field: what the drawing left out, the alerts, then the notes, a line each."""
    return "\n".join([f"⚠ {a}" for a in [*footer.warnings, *footer.alerts]] + [f"• {n}" for n in footer.notes])


def _footer(st) -> object | None:
    """The footer the field shows: the Distribution view's, or the Statistics view's Rare responders report
    (``_rare_controller.footer_of``); ``None`` on any other view."""
    if st is None:
        return None
    if st.view == "distribution":
        return getattr(getattr(st, "hits", None), "footer", None)
    if st.view == "statistics":
        return getattr(st, "rare_footer", None)
    return None


def build_details_panel(ctx, parent) -> QWidget:
    """The field; registers ``_nw_details_btn``, ``_nw_details_summary``, ``_nw_details_body`` and
    ``_nw_details_refresh``."""
    panel = QWidget(parent)
    panel.setObjectName("NanowellDetails")
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(2)
    head = QHBoxLayout()
    button = QToolButton(panel)
    button.setCheckable(True)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setProperty("variant", "secondary")
    button.setToolTip(_TIP)
    head_label = QLabel("", panel)
    head_label.setWordWrap(True)
    head_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    head.addWidget(button, 0, Qt.AlignTop)
    head.addWidget(head_label, 1)
    text = QLabel("", panel)
    text.setObjectName("InfoHint")
    text.setWordWrap(True)
    text.setTextInteractionFlags(Qt.TextSelectableByMouse)
    text.hide()
    outer.addLayout(head)
    outer.addWidget(text)

    def refresh() -> None:
        st = ctx.state.feature_slice("nanowell")
        footer = _footer(st)
        shown = footer is not None
        panel.setVisible(shown)
        if not shown:
            return
        button.setText(f"Details {'▾' if button.isChecked() else '▸'}")
        head_label.setText(summary(footer))
        name = "Warn" if footer.alerts or footer.warnings else ""                  # the theme's warning ink (``QLabel#Warn``)
        if head_label.objectName() != name:
            head_label.setObjectName(name)
            head_label.style().unpolish(head_label)
            head_label.style().polish(head_label)
        text.setText(body(footer))
        text.setVisible(button.isChecked() and bool(footer.alerts or footer.notes or footer.warnings))

    button.toggled.connect(lambda _on: refresh())
    reg = ctx.widgets
    reg.register("_nw_details_btn", button)
    reg.register("_nw_details_summary", head_label)
    reg.register("_nw_details_body", text)
    reg.register("_nw_details_refresh", refresh)
    panel.hide()
    return panel
