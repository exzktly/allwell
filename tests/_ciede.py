"""How a colour looks under colour-vision deficiency, and how far apart two look.

The perceptual half of the colour-blind guards: Machado, Oliveira & Fernandes
(2009) simulation applied in linear RGB, CIELAB, and CIEDE2000 (Sharma, Wu &
Dalal 2005), plus the WCAG contrast ratio. Shared by the run plate's guard
(``test_run_plate_is_colour_blind_safe.py``) and Home's
(``test_home_plate_is_colour_blind_safe.py``) so the two plates are measured by
the same arithmetic and their floors mean the same thing.

``tests/_cvd.py`` is a different, older tool — the Viénot/Brettel projection
with RGB distances — kept for the categorical-palette checks that were written
against it; its numbers are not comparable with these.
"""
from __future__ import annotations

import math

import numpy as np

#: The simulations a guard measures under. The anomalous trichromacies are the
#: dichromacies at severity 0.6 — far commoner than the full forms.
RED_GREEN = ("deuteranopia", "deuteranomaly", "protanopia", "protanomaly")
VISIONS = ("normal", *RED_GREEN, "tritanopia")


# Machado, Oliveira & Fernandes (2009), applied in linear RGB: full severity
# (the dichromacies) and 0.6 (the anomalous trichromacies, far more common).
MACHADO = {
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


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def rgb(hex_colour: str) -> np.ndarray:
    h = hex_colour.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float) / 255.0


def lab(colour: np.ndarray) -> np.ndarray:
    lin = srgb_to_linear(colour)
    xyz = np.array([[0.4124, 0.3576, 0.1805],
                    [0.2126, 0.7152, 0.0722],
                    [0.0193, 0.1192, 0.9505]]) @ lin
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def de2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
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


def seen(hex_colour: str, vision: str) -> np.ndarray:
    """*hex_colour* as seen under *vision* (one of :data:`VISIONS`), sRGB 0–1."""
    colour = rgb(hex_colour)
    if vision == "normal":
        return colour
    return linear_to_srgb(MACHADO[vision] @ srgb_to_linear(colour))


def contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        r, g, b_ = srgb_to_linear(rgb(h))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
