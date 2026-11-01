"""The run plate stays readable under red/green colour vision deficiency.

Analyze's run plate paints seven well states at once. Two reports drove the
palette. First, built from the general status tokens, "complete" green and
"failed" red simulated to the same olive under deuteranopia. Then, with the
first ``run_*`` palette, "complete" green (#4ADE80) and "processing" amber
(#F5B700) were still reported as hard to tell apart. They had the same
lightness (ΔL ≈ 1), so for a red/green deficient viewer only a weak hue cue
remained: CIEDE2000 ≈ 10 under full protanopia. The guard at the time measured
CIE76, which overstates yellow-against-green differences, and passed them at
30+.

These tests hold the palette and its use:

* every pair of states stays apart by CIEDE2000 under normal vision and under
  deuteranopia and protanopia at full and partial severity (Machado et al.
  2009; severity 0.6 stands for the anomalous trichromacies, which are the
  common forms), and more loosely under tritanopia;
* "complete" and "processing", the two states a running plate shows most,
  differ in *lightness*, which survives any hue confusion;
* every state reads against the dark panel the plate sits on, in both themes;
* the plate and its legend actually paint with that palette, by value.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from ui import theme

RUN_TOKENS = (
    "run_pending", "run_uploaded", "run_processing", "run_on_server",
    "run_done", "run_failed", "run_partial",
)

# Machado, Oliveira & Fernandes (2009), applied in linear RGB: full severity
# (the dichromacies) and 0.6 (the anomalous trichromacies, far more common).
_MACHADO = {
    "deuteranopia": np.array([[0.367322, 0.860646, -0.227968],
                              [0.280085, 0.672501, 0.047413],
                              [-0.011820, 0.042940, 0.968881]]),
    "deuteranomaly": np.array([[0.547494, 0.607765, -0.155259],
                               [0.181692, 0.781742, 0.036566],
                               [-0.010410, 0.027275, 0.983136]]),
    "protanopia": np.array([[0.152286, 1.052583, -0.204868],
                            [0.114503, 0.786281, 0.099216],
                            [-0.003882, -0.048116, 1.051998]]),
    "protanomaly": np.array([[0.385450, 0.769005, -0.154455],
                             [0.100526, 0.829802, 0.069673],
                             [-0.007442, -0.022190, 1.029632]]),
    "tritanopia": np.array([[1.255528, -0.076749, -0.178779],
                            [-0.078411, 0.930809, 0.147602],
                            [0.004733, 0.691367, 0.303900]]),
}
RED_GREEN = ("deuteranopia", "deuteranomaly", "protanopia", "protanomaly")

# Minimum CIEDE2000 distance between any two states. The previous palette's
# worst pair was 10.3 (complete vs processing, protanopia). Tritanopia is far
# rarer and its axis (blue/yellow) is not the one this palette is fixed for,
# so it carries a lower bar rather than none.
_MIN_DE2000 = {"normal": 17.0, **{v: 17.0 for v in RED_GREEN}, "tritanopia": 12.0}
#: The least lightness difference (CIE L*) between "complete" and "processing"
#: under any red/green vision. Lightness is the cue hue confusion cannot take.
_MIN_DONE_VS_PROCESSING_L = 15.0
#: WCAG 2.1 SC 1.4.11's floor for graphical objects, against the panel the
#: plate sits on in each shipped theme.
_MIN_CONTRAST = 3.0
_PANELS = {"blue": "#131A24", "grey": "#24272C"}


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def _rgb(hex_colour: str) -> np.ndarray:
    h = hex_colour.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float) / 255.0


def _lab(rgb: np.ndarray) -> np.ndarray:
    lin = _srgb_to_linear(rgb)
    xyz = np.array([[0.4124, 0.3576, 0.1805],
                    [0.2126, 0.7152, 0.0722],
                    [0.0193, 0.1192, 0.9505]]) @ lin
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def _de2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
    """CIEDE2000 (Sharma, Wu & Dalal 2005)."""
    (l1, a1, b1), (l2, a2, b2) = lab1, lab2
    c_bar = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1 = math.degrees(math.atan2(b1, a1p)) % 360
    h2 = math.degrees(math.atan2(b2, a2p)) % 360
    dh = 0.0 if c1p * c2p == 0 else (h2 - h1 + 180) % 360 - 180
    d_l, d_c = l2 - l1, c2p - c1p
    d_h = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    l_bar, cp_bar = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_bar = h1 + h2
    elif abs(h1 - h2) <= 180:
        h_bar = (h1 + h2) / 2
    else:
        h_bar = (h1 + h2 + 360) / 2 if h1 + h2 < 360 else (h1 + h2 - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(h_bar - 30)) + 0.24 * math.cos(math.radians(2 * h_bar))
         + 0.32 * math.cos(math.radians(3 * h_bar + 6)) - 0.20 * math.cos(math.radians(4 * h_bar - 63)))
    d_theta = 30 * math.exp(-(((h_bar - 275) / 25) ** 2))
    r_c = 2 * math.sqrt(cp_bar ** 7 / (cp_bar ** 7 + 25 ** 7))
    s_l = 1 + 0.015 * (l_bar - 50) ** 2 / math.sqrt(20 + (l_bar - 50) ** 2)
    s_c, s_h = 1 + 0.045 * cp_bar, 1 + 0.015 * cp_bar * t
    r_t = -math.sin(math.radians(2 * d_theta)) * r_c
    return math.sqrt((d_l / s_l) ** 2 + (d_c / s_c) ** 2 + (d_h / s_h) ** 2 + r_t * (d_c / s_c) * (d_h / s_h))


def _seen(hex_colour: str, vision: str) -> np.ndarray:
    rgb = _rgb(hex_colour)
    if vision == "normal":
        return rgb
    return _linear_to_srgb(_MACHADO[vision] @ _srgb_to_linear(rgb))


def _closest_pair(colours: dict[str, str], vision: str) -> tuple[float, str, str]:
    return min(
        (_de2000(_lab(_seen(colours[a], vision)), _lab(_seen(colours[b], vision))), a, b)
        for a, b in itertools.combinations(colours, 2)
    )


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        r, g, b_ = _srgb_to_linear(_rgb(h))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _palette() -> dict[str, str]:
    return {name: getattr(theme.Colors, name) for name in RUN_TOKENS}


@pytest.mark.parametrize("vision", sorted(_MIN_DE2000))
def test_every_pair_of_run_states_stays_apart(vision):
    palette = _palette()
    d, a, b = _closest_pair(palette, vision)
    assert d >= _MIN_DE2000[vision], (
        f"under {vision}, {a} ({palette[a]}) and {b} ({palette[b]}) are only "
        f"CIEDE2000 {d:.1f} apart; the run plate needs ≥ {_MIN_DE2000[vision]}"
    )


@pytest.mark.parametrize("vision", ("normal", *RED_GREEN))
def test_complete_and_processing_differ_in_lightness(vision):
    done, busy = theme.Colors.run_done, theme.Colors.run_processing
    d_l = abs(_lab(_seen(done, vision))[0] - _lab(_seen(busy, vision))[0])
    assert d_l >= _MIN_DONE_VS_PROCESSING_L, (
        f"under {vision}, complete ({done}) and processing ({busy}) differ by only "
        f"ΔL* {d_l:.1f}; they need ≥ {_MIN_DONE_VS_PROCESSING_L}"
    )


@pytest.mark.parametrize("panel", sorted(_PANELS))
def test_every_state_reads_against_the_panel(panel):
    weak = {name: round(_contrast(h, _PANELS[panel]), 2) for name, h in _palette().items()
            if _contrast(h, _PANELS[panel]) < _MIN_CONTRAST}
    assert not weak, f"under {_MIN_CONTRAST}:1 against the {panel} theme's panel: {weak}"


def test_the_previous_palette_is_what_this_guard_rejects():
    """The bar is not one any palette clears: the palette this replaced fails it
    on exactly the reported pair — complete vs processing, CIEDE2000 ≈ 10 under
    protanopia — and fails the lightness test with ΔL* ≈ 1."""
    previous = {"run_pending": "#5F6B7C", "run_uploaded": "#C4B5FD", "run_processing": "#F5B700",
                "run_on_server": "#3B6FE8", "run_done": "#4ADE80", "run_failed": "#B91C1C",
                "run_partial": "#F5F5F5"}
    d, a, b = _closest_pair(previous, "protanopia")
    assert {a, b} == {"run_done", "run_processing"} and d < _MIN_DE2000["protanopia"], (a, b, d)
    d_l = abs(_lab(_rgb(previous["run_done"]))[0] - _lab(_rgb(previous["run_processing"]))[0])
    assert d_l < 2.0


@pytest.mark.parametrize("remote", [False, True])
def test_the_legend_paints_with_the_run_palette(qapp, remote):
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    dash._remote = remote
    _title, items = dash._legend_items()
    expected = {getattr(theme.Colors, n) for n in RUN_TOKENS}
    if not remote:
        expected.discard(theme.Colors.run_uploaded)
    assert {colour for colour, _label in items} == expected


def test_every_well_state_paints_with_the_run_palette(qapp):
    """Drive the plate through each state and collect what ``_well_color``
    returns; every colour reached must be a ``run_*`` token, and every
    token must be reached, so a branch reverting to a general status colour
    (the green/red pair this exists to retire) fails by value."""
    from widgets.run_dashboard import RunDashboard

    palette = {getattr(theme.Colors, n): n for n in RUN_TOKENS}
    reached: set[str] = set()

    dash = RunDashboard()
    dash.reset(remote=True, total=6)
    steps = {
        "A01": ["group"],                  # pending
        "A02": ["upload"],                 # uploaded, waiting its turn
        "A03": ["upload", "active"],       # processing
        "A04": ["process"],                # on the server
        "A05": ["process", "download"],    # downloaded
        "A06": ["process", "error"],       # failed
    }
    for w, stages in steps.items():
        for stage in stages:
            dash.set_well_stage(w, stage)
        reached.add(dash._well_color(w))

    local = RunDashboard()
    local.reset(remote=False, total=2)
    local.set_well_stage("B01", "threshold")              # complete
    local.set_well_partial("B02", "2 FOV-groups failed")
    local.set_well_stage("B02", "process")                # partial
    for w in ("B01", "B02"):
        reached.add(local._well_color(w))

    stray = {c for c in reached if c not in palette}
    assert not stray, f"well colours outside the run palette: {sorted(stray)}"
    assert set(palette.values()) == {palette[c] for c in reached}
