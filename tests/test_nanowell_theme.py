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
