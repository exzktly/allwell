"""Generate static weight instances of the bundled variable fonts.

The app ships Inter and JetBrains Mono as *variable* TTFs (a single file with a
``wght`` axis). Qt's ``QFontDatabase.addApplicationFont`` registers variable
fonts unreliably on Windows — the family silently fails to load and the app
falls back to the platform default face. Static, single-weight instances load
consistently everywhere, so we instance the variable sources into the discrete
weights the UI actually requests (Typography.regular/medium/semibold/bold) and
register those instead.

Run from the repo root:

    python scripts/make_static_fonts.py

It reads ``assets/fonts/{Inter,JetBrainsMono}.ttf`` (the variable sources, kept
in-repo as the regeneration source) and writes the static instances alongside
them (``Inter-Regular.ttf`` … ``Inter-Bold.ttf`` etc.). ``app_fonts`` registers
the static files; the variable sources stay unregistered.
"""

from __future__ import annotations

from pathlib import Path

from fontTools import ttLib
from fontTools.varLib import instancer

_FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"

# (source variable file, opsz pin or None, {weight: output-suffix})
_JOBS = (
    ("Inter.ttf", 14.0, {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}),
    ("JetBrainsMono.ttf", None, {400: "Regular", 700: "Bold"}),
)


def _make(src_name: str, opsz: float | None, weights: dict[int, str]) -> None:
    src = _FONTS / src_name
    stem = src.stem
    for wght, suffix in weights.items():
        font = ttLib.TTFont(str(src))
        axes: dict[str, float] = {"wght": float(wght)}
        if opsz is not None:
            axes["opsz"] = opsz
        instancer.instantiateVariableFont(font, axes, inplace=True, updateFontNames=True)
        out = _FONTS / f"{stem}-{suffix}.ttf"
        font.save(str(out))
        print(f"  wrote {out.name}")


def main() -> None:
    for src_name, opsz, weights in _JOBS:
        print(src_name)
        _make(src_name, opsz, weights)


if __name__ == "__main__":
    main()
