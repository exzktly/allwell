"""The run plate stays readable under red/green colour vision deficiency.

Analyze's run plate paints seven well states at once. Built from the general
status tokens, "complete" green and "failed" red simulated to the same olive
under deuteranopia (ΔE76 ≈ 9), and "uploaded" / "on server" merged under
protanopia (≈ 8). The ``run_*`` tokens in ``ui/theme.py`` replace them; these
tests hold both halves of that fix:

* the palette itself keeps every pair of states apart under normal vision,
  deuteranopia and protanopia (and, more loosely, tritanopia), measured with
  the Machado et al. (2009) full-severity simulation matrices; and
* the plate and its legend actually paint with that palette — a well-colour
  branch or legend row that drifts back to ``success`` / ``danger`` fails here,
  because the colours it reaches are compared with the palette by value.
"""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from ui import theme

RUN_TOKENS = (
    "run_pending", "run_uploaded", "run_processing", "run_on_server",
    "run_done", "run_failed", "run_partial",
)

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear RGB.
_MACHADO = {
    "deuteranopia": np.array([[0.367322, 0.860646, -0.227968],
                              [0.280085, 0.672501, 0.047413],
                              [-0.011820, 0.042940, 0.968881]]),
    "protanopia": np.array([[0.152286, 1.052583, -0.204868],
                            [0.114503, 0.786281, 0.099216],
                            [-0.003882, -0.048116, 1.051998]]),
    "tritanopia": np.array([[1.255528, -0.076749, -0.178779],
                            [-0.078411, 0.930809, 0.147602],
                            [0.004733, 0.691367, 0.303900]]),
}

# Minimum CIE76 distance between any two states. Below ~10 two small wells
# read as one colour; the old palette's worst pair was 7. Tritanopia is far
# rarer and its axis (blue/yellow) is not the one this palette was fixed for,
# so it carries a lower bar rather than none.
_MIN_DELTA_E = {"normal": 30.0, "deuteranopia": 30.0, "protanopia": 30.0, "tritanopia": 18.0}


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


def _seen(hex_colour: str, vision: str) -> np.ndarray:
    rgb = _rgb(hex_colour)
    if vision == "normal":
        return rgb
    return _linear_to_srgb(_MACHADO[vision] @ _srgb_to_linear(rgb))


def _closest_pair(colours: dict[str, str], vision: str) -> tuple[float, str, str]:
    return min(
        (float(np.linalg.norm(_lab(_seen(colours[a], vision)) - _lab(_seen(colours[b], vision)))), a, b)
        for a, b in itertools.combinations(colours, 2)
    )


@pytest.mark.parametrize("vision", sorted(_MIN_DELTA_E))
def test_every_pair_of_run_states_stays_apart(vision):
    palette = {name: getattr(theme.Colors, name) for name in RUN_TOKENS}
    d, a, b = _closest_pair(palette, vision)
    assert d >= _MIN_DELTA_E[vision], (
        f"under {vision}, {a} ({palette[a]}) and {b} ({palette[b]}) are only "
        f"ΔE {d:.1f} apart; the run plate needs ≥ {_MIN_DELTA_E[vision]}"
    )


def test_the_old_palette_is_what_this_guard_rejects():
    """The bar above is not one any palette clears: the status tokens the plate
    used to paint with fail it under deuteranopia on complete vs failed."""
    old = {"done": "#4ADE80", "failed": "#F87171", "processing": "#F59E0B",
           "on_server": "#6B8AFD", "uploaded": "#B98CF7", "pending": "#5F6B7C",
           "partial": "#22D3EE"}
    d, a, b = _closest_pair(old, "deuteranopia")
    assert {a, b} == {"done", "failed"} and d < 10


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
