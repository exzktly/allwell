"""The four kinds of button. Every button in the interface is one of them.

The design language (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §2.4):

- **primary** — the accent fill with a dark, semibold label. The action that produces the page's result
  (Run, Export, Create, Apply); one per view.
- **default** — the control fill and the button edge, a medium label. Every ordinary command.
- **quiet** — no fill and no edge until the pointer is on it, the secondary ink. Toolbars and the commands that
  sit beside a primary one (Clear, Sync from app).
- **destructive** — the default button with a red label. Deleting, clearing what cannot be got back; a
  confirming alert follows it.

A button's kind is its ``variant`` dynamic property, and :func:`qss` writes the rules for the four (included by
``ui.theme.qss``). Build a button with :func:`button` or one of the four named builders; change a built
button's kind with :func:`set_kind`, which re-polishes — a dynamic property is read only on a polish.

**A tool is not a setting.** A button that arms a pointer mode — *Delete cell*, *Crop cells*, *Lasso cells*: the
next click or drag on the picture does something different — is a default tool button that stays pressed in while
the mode is armed (:func:`mode_tool`, its selected look the ``:checked`` rule below), the way a drawing program's
tools are. Its label names the tool and never its state. An on/off *setting* is a switch or a checkbox and a choice
among a few is a segmented control (§2.5), never a pressed-in button.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

PRIMARY = "primary"
DEFAULT = "default"
QUIET = "quiet"
DESTRUCTIVE = "destructive"

KINDS = (PRIMARY, DEFAULT, QUIET, DESTRUCTIVE)


def button(text: str, kind: str = DEFAULT, parent: QWidget | None = None, *,
           on_click: Callable[[], object] | None = None, icon: str | None = None) -> QPushButton:
    """A push button of *kind*, labelled *text*, calling *on_click* when pressed. *icon* names a glyph in the
    drawn set (``widgets/icons.py``)."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a kind of button; the kinds are {KINDS}")
    b = QPushButton(text, parent)
    b.setProperty("variant", kind)
    if icon:
        from core.safe import swallow
        from widgets import icons
        with swallow("button icon"):
            dpr = b.devicePixelRatioF() or 1.0
            b.setIcon(icons.make_icon(icon, 14, dpr=dpr))
    if on_click is not None:
        b.clicked.connect(lambda _checked=False: on_click())
    return b


def primary(text: str, parent: QWidget | None = None, **kw) -> QPushButton:
    """The page's one action that produces its result."""
    return button(text, PRIMARY, parent, **kw)


def default(text: str, parent: QWidget | None = None, **kw) -> QPushButton:
    """An ordinary command."""
    return button(text, DEFAULT, parent, **kw)


def quiet(text: str, parent: QWidget | None = None, **kw) -> QPushButton:
    """A command that should not compete with the content or the primary action."""
    return button(text, QUIET, parent, **kw)


def destructive(text: str, parent: QWidget | None = None, **kw) -> QPushButton:
    """A command that removes something that cannot be got back."""
    return button(text, DESTRUCTIVE, parent, **kw)


def tool_button(parent: QWidget | None = None, kind: str = DEFAULT) -> QToolButton:
    """A tool button of *kind* — for a button that opens a menu, carries only an icon, or sits in a toolbar —
    styled by the same four rules as a push button. Its text, icon and menu are the caller's."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a kind of button; the kinds are {KINDS}")
    b = QToolButton(parent)
    b.setProperty("variant", kind)
    return b


def icon_button(name: str, tooltip: str, parent: QWidget | None = None, *, kind: str = QUIET,
                on_click: Callable[[], object] | None = None) -> QToolButton:
    """A tool button of *kind* that shows the drawn icon *name* (``widgets/icons.py``) and no text: remove (``x``),
    a step (``minus`` / ``plus``), a direction (the chevrons), more (``more-horizontal``), edit (``pencil``).
    *tooltip* is required — it is the button's only words, and its accessible name."""
    set_icon(tool := tool_button(parent, kind), name)
    tool.setToolTip(tooltip)
    tool.setAccessibleName(tooltip)
    if on_click is not None:
        tool.clicked.connect(lambda _checked=False: on_click())
    return tool


def set_icon(b, name: str, size: int = 14) -> None:
    """Show the drawn icon *name* on *b* in place of any text it had."""
    from widgets import icons
    b.setText("")
    b.setIcon(icons.make_icon(name, size, dpr=b.devicePixelRatioF() or 1.0))


def mode_tool(text: str, parent: QWidget | None = None) -> QToolButton:
    """A default-kind tool button labelled *text* that stays pressed in while its pointer mode is armed: checkable,
    its ``toggled(bool)`` the mode going on and off."""
    tool = tool_button(parent, DEFAULT)
    tool.setText(text)
    tool.setCheckable(True)
    tool.setProperty("modeTool", True)
    return tool


def set_kind(b, kind: str) -> None:
    """Change a built button's kind, and repaint it in the new one."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a kind of button; the kinds are {KINDS}")
    b.setProperty("variant", kind)
    b.style().unpolish(b)
    b.style().polish(b)
    b.update()


def qss(c, t) -> str:
    """The rules for the four kinds, from the token classes *c* (``Colors``) and *t* (``Typography``).
    Included by ``ui.theme.qss`` after the base button rule, which is what *default* draws."""
    sel = "QPushButton[variant=\"{k}\"], QToolButton[variant=\"{k}\"]"
    p, d, q, x = (sel.format(k=k) for k in KINDS)

    def state(selector: str, pseudo: str) -> str:
        return ", ".join(f"{part.strip()}:{pseudo}" for part in selector.split(","))

    return f"""
{p} {{
    background-color: {c.accent}; border-color: {c.accent}; color: {c.on_accent}; font-weight: {t.semibold};
}}
{state(p, "hover")} {{ background-color: {c.accent_hover}; border-color: {c.accent_hover}; }}
{state(p, "pressed")} {{ background-color: {c.accent_pressed}; border-color: {c.accent_pressed}; }}
{state(p, "disabled")} {{
    background-color: {c.control}; border-color: {c.hairline}; color: {c.text_disabled};
}}
{d} {{
    background-color: {c.control}; border-color: {c.button_edge}; color: {c.text_primary};
    font-weight: {t.medium};
}}
{state(d, "hover")} {{ border-color: {c.field_edge}; }}
{state(d, "disabled")} {{
    background-color: {c.control}; border-color: {c.hairline}; color: {c.text_disabled};
}}
{q} {{
    background-color: transparent; border-color: transparent; color: {c.text_secondary};
    font-weight: {t.medium};
}}
{state(q, "hover")} {{ background-color: {c.control}; color: {c.text_primary}; }}
{state(q, "disabled")} {{ background-color: transparent; border-color: transparent; color: {c.text_disabled}; }}
{x} {{
    background-color: {c.control}; border-color: {c.button_edge}; color: {c.danger}; font-weight: {t.medium};
}}
{state(x, "hover")} {{ border-color: {c.danger}; }}
QToolButton[modeTool="true"]:checked {{
    background-color: {c.selected}; border-color: {c.accent}; color: {c.selected_fg};
}}
{state(x, "disabled")} {{
    background-color: {c.control}; border-color: {c.hairline}; color: {c.text_disabled};
}}
"""
