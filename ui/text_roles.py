"""The six text roles: every piece of text in the interface is one of them.

The design language (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §2.3) gives text six roles, each with one
size, weight and ink, so that the same kind of text looks the same on every page:

==================  ===============================  ==================  ====================================
Role                Size, weight                     Ink                 Used for
==================  ===============================  ==================  ====================================
page title          17 semibold                      primary             one per page, top left
section heading     15 semibold, sentence case       primary             a card's title
eyebrow             12 semibold, capitals, tracked   secondary           a group inside a card or inspector
body / value        13 regular                       primary             text, and the value in a field
label / help        12 regular                       secondary           a field's label (above or beside it,
                                                                         no colon) and help text
number              12 monospace, tabular            primary             counts, readouts, paths
==================  ===============================  ==================  ====================================

Each builder returns a ``QLabel`` carrying a ``textRole`` dynamic property; :func:`qss` writes the one rule per
role that the application stylesheet (``ui.theme.qss``) includes. A label takes its look from its role, never
from an object name of its own or an inline font, so restyling a role restyles every label that has it.

**An unset value** reads :data:`NOT_SET` in the disabled ink: :func:`show_value` writes a value into a label,
or *not set* when there is none, and marks the label so the sheet draws it quietly. "(not set)", "none", "auto"
and "—" each stood for this on some page; one word in one ink is what lets a reader recognise it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

#: How a value that has not been given reads, everywhere.
NOT_SET = "not set"
#: A field that has no value because it does not apply here — a count cutoff on a channel with no count column, a
#: bound a quantity does not have. Not :data:`NOT_SET`, which says a value could be given and has not been: this one
#: cannot be. Drawn in the same quiet ink, through :func:`show_not_applicable`.
NOT_APPLICABLE = "n/a"
#: A value the dataset's own record does not hold — a run parameter an older pipeline did not write, a figure the
#: record cannot give. Not :data:`NOT_SET` (nobody was asked for it) and not :data:`NOT_APPLICABLE` (it would apply).
#: Drawn in the same quiet ink, through :func:`show_not_recorded`.
NOT_RECORDED = "not recorded"

#: The role names, as the ``textRole`` property spells them.
PAGE_TITLE = "pageTitle"
SECTION_HEADING = "sectionHeading"
EYEBROW = "eyebrow"
BODY = "body"
LABEL = "label"
HELP = "help"
NUMBER = "number"

ROLES = (PAGE_TITLE, SECTION_HEADING, EYEBROW, BODY, LABEL, HELP, NUMBER)

#: A line of text whose meaning has a colour (§2.2 rule 3): amber when it needs attention, red when something
#: failed. For a status line that is sometimes a hint and sometimes an alert — a field's validation, an import's
#: verdict — where a notice card would be out of scale. Information has no tone.
ATTENTION = "attention"
FAILURE = "failure"
TONES = (ATTENTION, FAILURE)


def _label(text: str, role: str, parent: QWidget | None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("textRole", role)
    return label


def page_title(text: str, parent: QWidget | None = None) -> QLabel:
    """The page's title: one per page, top left."""
    return _label(text, PAGE_TITLE, parent)


def section_heading(text: str, parent: QWidget | None = None) -> QLabel:
    """A card's title, in sentence case."""
    return _label(text, SECTION_HEADING, parent)


def eyebrow(text: str, parent: QWidget | None = None) -> QLabel:
    """A group's name inside a card, the inspector, or a list of tools. Written in capitals here, so the
    source keeps the sentence-case words a reader would search for."""
    return _label(text.upper(), EYEBROW, parent)


def body(text: str, parent: QWidget | None = None) -> QLabel:
    """Running text, or a value shown on its own."""
    return _label(text, BODY, parent)


def field_label(text: str, parent: QWidget | None = None) -> QLabel:
    """A field's label, set outside the field. No colon: the position says it labels the field."""
    return _label(text, LABEL, parent)


def help_text(text: str, parent: QWidget | None = None) -> QLabel:
    """A line of help under or beside what it explains. Wraps, since help is a sentence."""
    label = _label(text, HELP, parent)
    label.setWordWrap(True)
    return label


def number(text: str, parent: QWidget | None = None) -> QLabel:
    """A count, a readout or a path, in figures that line up."""
    return _label(text, NUMBER, parent)


def set_role(label: QLabel, role: str, *, tone: str | None = None) -> None:
    """Give a built *label* a text *role*, and *tone* (:data:`ATTENTION`, :data:`FAILURE`) or none; re-polishes,
    since a dynamic property is read only on a polish. For a label whose role or tone changes after it is shown."""
    if role not in ROLES:
        raise ValueError(f"{role!r} is not a text role; the roles are {ROLES}")
    if tone is not None and tone not in TONES:
        raise ValueError(f"{tone!r} is not a tone; the tones are {TONES}")
    label.setProperty("textRole", role)
    label.setProperty("textTone", tone or "")
    label.style().unpolish(label)
    label.style().polish(label)
    label.update()


def show_value(label: QLabel, value) -> None:
    """Write *value* into *label*, or :data:`NOT_SET` in the disabled ink when there is none (``None`` or an
    empty string). Re-polishes, since a dynamic property is only read again on a polish."""
    unset = value is None or (isinstance(value, str) and not value.strip())
    label.setText(NOT_SET if unset else str(value))
    if bool(label.property("unset")) != unset:
        label.setProperty("unset", unset)
        label.style().unpolish(label)
        label.style().polish(label)


def show_not_applicable(label: QLabel) -> None:
    """Write :data:`NOT_APPLICABLE` into *label*, in the disabled ink an unset value takes."""
    _show_quiet(label, NOT_APPLICABLE)


def show_not_recorded(label: QLabel) -> None:
    """Write :data:`NOT_RECORDED` into *label*, in the disabled ink an unset value takes."""
    _show_quiet(label, NOT_RECORDED)


def _show_quiet(label: QLabel, text: str) -> None:
    label.setText(text)
    if not label.property("unset"):
        label.setProperty("unset", True)
        label.style().unpolish(label)
        label.style().polish(label)


def qss(c, t) -> str:
    """The rules for the six roles and the unset value, from the token classes *c* (``Colors``) and *t*
    (``Typography``). Included by ``ui.theme.qss``."""
    mono = f'font-family: "{t.family_mono}";' if t.family_mono else ""
    return f"""
QLabel[textRole="{PAGE_TITLE}"] {{
    font-size: {t.h2_size}px; font-weight: {t.semibold}; color: {c.text_primary};
}}
QLabel[textRole="{SECTION_HEADING}"] {{
    font-size: {t.h3_size}px; font-weight: {t.semibold}; color: {c.text_primary};
}}
QLabel[textRole="{EYEBROW}"] {{
    font-size: {t.small_size}px; font-weight: {t.semibold}; color: {c.text_secondary}; letter-spacing: 1px;
}}
QLabel[textRole="{BODY}"] {{
    font-size: {t.body_size}px; font-weight: {t.regular}; color: {c.text_primary};
}}
QLabel[textRole="{LABEL}"], QLabel[textRole="{HELP}"] {{
    font-size: {t.small_size}px; font-weight: {t.regular}; color: {c.text_secondary};
}}
QLabel[textRole="{NUMBER}"] {{
    {mono} font-size: {t.small_size}px; font-weight: {t.regular}; color: {c.text_primary};
}}
QLabel[textTone="{ATTENTION}"] {{ color: {c.warn}; }}
QLabel[textTone="{FAILURE}"] {{ color: {c.danger}; }}
QLabel[unset="true"] {{
    color: {c.text_disabled};
}}
"""
