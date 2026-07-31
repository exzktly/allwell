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

import theme  # noqa: E402


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
