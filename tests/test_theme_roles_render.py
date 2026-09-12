"""The style hooks resolve to *different* paint, not just to matching selectors.

``test_theme_selectors`` proves a rule exists for every hook. That is a text
match: it would still pass if a rule were present but inert. These tests build
one widget at a time under the offscreen platform and read the resolved
palette back, so a rule that exists but changes nothing fails here.

Single widgets only — no ``WellViewerApp``. Anything touching the ``viewer_app``
fixture is auto-marked ``slow`` by ``conftest.py`` and excluded from the lane CI
runs, which would make these assertions invisible.
"""

import pytest

pytest.importorskip("PySide6")

from ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def styled_app(qapp):
    qapp.setStyleSheet(theme.qss())
    return qapp


def _fg(widget):
    """Resolved foreground of a polished widget, as '#rrggbb'."""
    widget.ensurePolished()
    return widget.palette().color(widget.foregroundRole()).name()


def _bg(widget):
    widget.ensurePolished()
    return widget.palette().color(widget.backgroundRole()).name()


@pytest.mark.parametrize("object_name", ["Muted", "MutedCaption", "Accent"])
def test_label_roles_differ_from_plain_body_text(styled_app, object_name):
    from PySide6.QtWidgets import QLabel

    plain = QLabel("x")
    styled = QLabel("x")
    styled.setObjectName(object_name)
    for w in (plain, styled):
        w.setStyleSheet("")  # inherit the app sheet only
    assert _fg(styled) != _fg(plain), (
        f"QLabel#{object_name} resolves to the same colour as an unnamed label; "
        "the rule exists but does not change anything."
    )


def test_muted_is_dimmer_than_secondary(styled_app):
    """The three text tiers must actually be three tiers."""
    from PySide6.QtWidgets import QLabel

    def lum(name):
        lbl = QLabel("x")
        lbl.setObjectName(name)
        c = lbl.palette().color(lbl.foregroundRole()) if not name else None
        lbl.ensurePolished()
        col = lbl.palette().color(lbl.foregroundRole())
        return 0.2126 * col.redF() + 0.7152 * col.greenF() + 0.0722 * col.blueF()

    primary, secondary, muted = lum(""), lum("Secondary"), lum("Muted")
    assert primary > secondary > muted, (
        f"text tiers are not ordered: primary={primary:.3f} "
        f"secondary={secondary:.3f} muted={muted:.3f}"
    )


@pytest.mark.parametrize("variant", ["primary", "danger", "toggle_accent"])
def test_button_variants_differ_from_default(styled_app, variant):
    from PySide6.QtWidgets import QPushButton

    plain = QPushButton("x")
    styled = QPushButton("x")
    styled.setProperty("variant", variant)
    assert (_bg(styled), _fg(styled)) != (_bg(plain), _fg(plain)), (
        f'variant="{variant}" paints identically to a default button.'
    )


def test_primary_reads_as_accent_on_both_button_classes(styled_app):
    """``QToolButton`` primaries rendered plain before the selector was widened."""
    from PySide6.QtWidgets import QPushButton, QToolButton

    for cls in (QPushButton, QToolButton):
        b = cls()
        b.setText("x")
        b.setProperty("variant", "primary")
        b.ensurePolished()
        assert _bg(b).lower() == theme.Colors.accent.lower(), (
            f"{cls.__name__} with variant='primary' is not accent-filled "
            f"(got {_bg(b)}); primaries must work on both button classes."
        )


# ── a stylesheet that reaches Qt as f-string source ─────────────────────────

def _stylesheet_sources():
    """Every stylesheet this app hands to Qt, with the name to blame.

    ``theme.qss()`` plus the widgets that set their own — a widget with a local
    sheet is not covered by the app-wide one, which is the whole reason it has
    one, so checking only ``qss()`` would miss exactly the sheets nothing else
    looks at.
    """
    from ui import theme as _theme
    from widgets.empty_state import EmptyState

    yield "theme.qss()", _theme.qss()
    yield "theme.link_row_qss()", _theme.link_row_qss("Probe")
    yield "EmptyState._build_qss()", EmptyState("probe")._build_qss()


@pytest.mark.parametrize("name", ["theme.qss()", "theme.link_row_qss()",
                                  "EmptyState._build_qss()"])
def test_no_stylesheet_reaches_qt_with_doubled_braces(qapp, name):
    """``{{`` in the output means an f-string escape survived into the QSS.

    Qt does not raise on a stylesheet it cannot parse — it writes "Could not
    parse stylesheet" to stderr and paints the widget unstyled, which under the
    offscreen platform nobody is watching. So the whole sheet silently does
    nothing, and every selector test still passes because they read the
    *source* text rather than the paint.

    The live case: a helper that returns QSS for interpolation into another
    f-string needs single braces, because f-string substitution inserts its
    value verbatim and does not process it again. Written with the doubled
    braces the helper's own body seems to call for, both stylesheets that used
    it came out unparseable.
    """
    sheets = dict(_stylesheet_sources())
    text = sheets[name]
    bad = [line for line in text.splitlines() if "{{" in line or "}}" in line]
    assert not bad, (
        f"{name} carries f-string escapes into the stylesheet Qt is given, so "
        f"Qt discards the whole sheet and paints unstyled:\n  "
        + "\n  ".join(bad[:8])
    )


def test_the_two_link_row_hosts_are_styled_identically(qapp):
    """One list shown in two states of one page must not drift into two looks.

    Home's Recent datasets card and ``EmptyState``'s entries draw the same rows.
    They cannot share an object name and cannot share a stylesheet host, so they
    share ``theme.link_row_qss`` — and this is what notices if one of them stops
    doing so.
    """
    from ui import theme as _theme

    home = _theme.link_row_qss("HomeRecentTile")
    entry = _theme.link_row_qss("EmptyStateEntry")
    assert (home.replace("HomeRecentTile", "X")
            == entry.replace("EmptyStateEntry", "X"))
    assert home in _theme.qss(), (
        "Home's card no longer takes its rows from link_row_qss, so the two "
        "lists can now be adjusted apart"
    )
    from widgets.empty_state import EmptyState
    assert entry in EmptyState("probe")._build_qss(), (
        "EmptyState's entries no longer take their rows from link_row_qss"
    )
