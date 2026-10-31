"""The nanowell occupancy ramp (decision U7): ordinal, readable on its own ground, paired across modes."""

from __future__ import annotations

from ui.theme import CPub, Colors


def _luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_every_occupancy_class_reads_on_its_own_ground():
    assert all(_contrast(c, Colors.plot_bg) >= 3.0 for c in Colors.occupancy_ramp)
    assert all(_contrast(c, CPub.bg) >= 3.0 for c in CPub.occupancy_ramp)


def test_more_cells_means_more_contrast_from_class_one_up_on_both_grounds():
    screen = [_contrast(c, Colors.plot_bg) for c in Colors.occupancy_ramp[1:]]
    paper = [_contrast(c, CPub.bg) for c in CPub.occupancy_ramp[1:]]
    assert screen == sorted(screen) and paper == sorted(paper)


def test_the_ramp_has_one_shade_per_class_in_both_modes():
    assert len(Colors.occupancy_ramp) == len(CPub.occupancy_ramp) == 4
    assert len(set(Colors.occupancy_ramp)) == 4 and len(set(CPub.occupancy_ramp)) == 4


# ── the "Computing…" overlay ─────────────────────────────────────────────────
# It used to be bare body-size text on the figure, legible on neither plot
# ground and invisible on publication white. It is now a scrim with the
# message on it; these hold that the message reads on the scrim whatever the
# figure beneath is, and that the stylesheet really paints the scrim.

def _rgba(token: str) -> tuple[tuple[float, float, float], float]:
    r, g, b, a = (float(v) for v in token[token.index("(") + 1:token.index(")")].split(","))
    return (r, g, b), a


def _over(scrim: str, ground: str) -> str:
    (r, g, b), a = _rgba(scrim)
    under = [int(ground.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    mixed = [round(a * top + (1 - a) * bottom) for top, bottom in zip((r, g, b), under)]
    return "#" + "".join(f"{v:02X}" for v in mixed)


def test_the_computing_message_reads_over_any_figure_beneath_it():
    # The worst grounds are the lightest: publication white and the palest marks either mode draws.
    grounds = [Colors.plot_bg, CPub.bg, "#FFFFFF", *Colors.occupancy_ramp, *CPub.occupancy_ramp]
    worst = min(_contrast(Colors.text_primary, _over(Colors.busy_scrim, g)) for g in grounds)
    assert worst >= 4.5, f"the Computing message is only {worst:.2f}:1 on its scrim"


def test_the_stylesheet_paints_the_scrim_and_sizes_the_message(qapp):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QLabel, QWidget

    from ui import theme

    host = QWidget()
    host.setStyleSheet(theme.qss() + "QWidget#Ground { background: #FFFFFF; }")
    host.setObjectName("Ground")
    host.resize(200, 120)
    label = QLabel("Computing…", host)
    label.setObjectName("NanowellComputing")
    label.resize(host.size())
    image = host.grab().toImage()
    corner = QColor(image.pixel(5, 5))
    # White under the scrim comes out dark: the scrim is painted, not left transparent.
    assert corner.lightness() < 110, corner.name()
    assert label.font().pixelSize() == theme.Typography.h2_size
