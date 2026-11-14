"""Properties' Legend font size scales a figure-level key from the size it was drawn at, title included.

Found in review: the style pass set every ``fig.legend`` entry to the Legend font size preference outright — 12 pt
by default, whether or not the user ever touched it — while the Nanowells page draws its keys at 7 pt and lays its
panels out around that. A 16-entry composite key grew from 931 to 1,430 px on a 900 px figure, and its title stayed
7 pt beside 12 pt entries. The preference now scales each text from the size its renderer drew it at: the default
leaves the key as drawn, and a larger value raises the entries and the title together.

The wrong implementations this rejects: the old one (entries at 12 pt — fails the first test), and one that skips
the title (fails the second).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from review.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs  # noqa: E402
from review.features.nanowell import composite_compute as cc  # noqa: E402
from review.features.nanowell._composite_render import render_composite  # noqa: E402
from review.features.nanowell.window_compute import WindowRow  # noqa: E402


def _composite_with_a_long_key():
    rows = [WindowRow((f"{'ABCDEFGH'[i // 4]}0{i % 4 + 1}", 0, 0), i + 1, 1.0, {}) for i in range(16)]
    fig = Figure(figsize=(9.0, 6.0))
    FigureCanvasAgg(fig)
    key = tuple((f"HeLa siKRAS {i} nM", "#%02x0000" % (i * 15)) for i in range(16))
    render_composite(fig, cc.composite(rows), {}, "T1", n_total=16,
                     inks={r.site[0]: "#ff0000" for r in rows}, key=key, key_title="Cell line")
    leg = next(leg for leg in fig.legends if leg.get_gid() == "nw-composite-key")
    return fig, leg


def _sizes(leg):
    return {t.get_fontsize() for t in leg.get_texts()}, leg.get_title().get_fontsize()


def test_the_default_preference_leaves_the_key_as_its_page_drew_it():
    fig, leg = _composite_with_a_long_key()
    drawn = _sizes(leg)
    fig.canvas.draw()
    width = leg.get_window_extent(fig.canvas.get_renderer()).width
    apply_export_style_prefs(fig, dict(DEFAULT_EXPORT_STYLE_PREFS))
    assert _sizes(leg) == drawn, f"drawn at {drawn}, styled to {_sizes(leg)}"
    fig.canvas.draw()
    assert abs(leg.get_window_extent(fig.canvas.get_renderer()).width - width) < 1.0


def test_a_larger_preference_scales_entries_and_title_together_and_does_not_compound():
    fig, leg = _composite_with_a_long_key()
    (entry,), title = _sizes(leg)
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["legend_font_size"] = 2 * DEFAULT_EXPORT_STYLE_PREFS["legend_font_size"]
    for _ in range(2):  # a second pass must not double it again
        apply_export_style_prefs(fig, prefs)
    assert _sizes(leg) == ({2 * entry}, 2 * title)
    apply_export_style_prefs(fig, dict(DEFAULT_EXPORT_STYLE_PREFS))
    assert _sizes(leg) == ({entry}, title)
