"""Home's saved-plot thumbnails, and the route from one to the gallery.

Two things are worth guarding here and they fail in opposite directions. The
strip must not appear at all in a build without Saved Plots — an empty "you have
saved no plots" panel in a build where saving one is impossible is a lie that
looks like a feature. And in a build with it, clicking a thumbnail has to
actually arrive at that plot: a link that lands the user at the top of the
gallery is indistinguishable, from the page, from one that did nothing.
"""

from __future__ import annotations

import types

import pytest

from well_viewer.features.home import collect as hc


def _ctx(tmp_path, *, has_feature=True):
    from well_viewer.core.app_runtime import AppRuntime

    dataset = types.SimpleNamespace(data_dir=tmp_path, persistence_doc=None)
    state = types.SimpleNamespace(dataset=dataset)
    app = types.SimpleNamespace(_app_state=state)
    features = types.SimpleNamespace(
        get=lambda name: object() if (has_feature and name == "Saved Plots")
        else None)
    ctx = types.SimpleNamespace(app=app, state=state, features=features,
                                runtime=AppRuntime(app))
    app._ctx = ctx
    return ctx


def _png() -> bytes:
    """A real PNG, so the tile's thumbnail path is exercised and not just its
    missing-image fallback — the two render differently and only one of them is
    what the user is meant to see."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import numpy as np

    buf = io.BytesIO()
    mpimg.imsave(buf, (np.linspace(0, 1, 1200).reshape(20, 20, 3) * 255)
                 .astype("uint8"), format="png")
    return buf.getvalue()


def _save(ctx, n: int) -> None:
    """Put *n* saved plots in the store, oldest first, each with a real file."""
    from well_viewer.features.saved_plots.state import SavedPlot, get_store

    store = get_store(ctx)
    store.ensure_loaded(ctx)
    png = _png()
    for i in range(n):
        store.add(ctx, SavedPlot(
            id=f"p{i}", created_at=f"2026-01-0{i % 9 + 1}T10:00:00",
            tab="Bar Plots", title=f"Plot {i}", png_filename=f"p{i}.png",
            params={}), png)


# ── what the strip reports ───────────────────────────────────────────────────

def test_a_build_without_saved_plots_has_no_strip_at_all(tmp_path):
    """Absent, not empty. There is nothing here the user could do about it."""
    strip = hc.saved_plots_strip(_ctx(tmp_path, has_feature=False))
    assert strip == {"available": False, "total": 0, "shown": []}


def test_an_empty_gallery_is_available_and_empty(tmp_path):
    """A different fact from the one above, and it has an action attached."""
    strip = hc.saved_plots_strip(_ctx(tmp_path))
    assert strip["available"] is True
    assert strip["total"] == 0 and strip["shown"] == []


def test_the_strip_shows_the_newest_plots_first(tmp_path):
    """Gallery order, so the strip and the gallery cannot disagree about which
    plot is 'the last one I saved'."""
    ctx = _ctx(tmp_path)
    _save(ctx, 3)
    shown = hc.saved_plots_strip(ctx)["shown"]
    assert [e["title"] for e in shown] == ["Plot 2", "Plot 1", "Plot 0"]


def test_the_strip_caps_what_it_shows_but_never_the_count(tmp_path):
    """The cap is the whole reason ``total`` is reported separately.

    A strip that showed twelve of ninety and said "12" would make the landing
    page quietly disagree with the gallery about how much work is saved.
    """
    ctx = _ctx(tmp_path)
    _save(ctx, hc.SAVED_PLOT_STRIP_LIMIT + 5)
    strip = hc.saved_plots_strip(ctx)
    assert strip["total"] == hc.SAVED_PLOT_STRIP_LIMIT + 5
    assert len(strip["shown"]) == hc.SAVED_PLOT_STRIP_LIMIT


def test_each_entry_carries_a_path_and_not_image_bytes(tmp_path):
    """Home reads the record and opens no files — the view loads the pixels."""
    ctx = _ctx(tmp_path)
    _save(ctx, 1)
    entry = hc.saved_plots_strip(ctx)["shown"][0]
    assert set(entry) == {"id", "title", "tab", "created_at", "image"}
    assert entry["image"].name == "p0.png"
    assert entry["image"].exists()


# ── the tile, and where it goes ──────────────────────────────────────────────

@pytest.mark.qt
def test_clicking_a_tile_opens_that_plot_and_not_merely_the_gallery(
        qapp, tmp_path, monkeypatch):
    """The click has to carry the plot's id all the way to the gallery.

    Asserting that the tile is *connected* would pass against a handler that
    navigates to Saved Plots and forgets which plot was asked for — the exact
    failure a user cannot tell from the strip not working.
    """
    from PySide6.QtWidgets import QWidget

    from well_viewer.features.home import controller as HC
    from well_viewer.features.home import tab_view

    opened = []
    monkeypatch.setattr(HC, "_focus_saved_plot",
                        lambda ctx, pid: opened.append(pid) or True)

    ctx = _ctx(tmp_path)
    _save(ctx, 2)
    entry = hc.saved_plots_strip(ctx)["shown"][0]
    parent = QWidget()
    tile = tab_view.build_saved_plot_tile(ctx, parent, entry)
    assert not tile.icon().isNull(), "the tile should carry the plot's thumbnail"
    tile.click()

    assert opened == [entry["id"]] == ["p1"]


@pytest.mark.qt
def test_a_tile_whose_image_is_gone_says_so_rather_than_drawing_nothing(
        qapp, tmp_path):
    from PySide6.QtWidgets import QWidget

    from well_viewer.features.home import tab_view

    entry = {"id": "p0", "title": "Plot 0", "tab": "Bar Plots",
             "created_at": "", "image": tmp_path / "never-written.png"}
    parent = QWidget()
    tile = tab_view.build_saved_plot_tile(_ctx(tmp_path), parent, entry)
    assert "image unavailable" in tile.text()
    assert tile.icon().isNull()


@pytest.mark.qt
def test_a_jump_that_could_not_land_is_reported(qapp, tmp_path, monkeypatch):
    """Silence here reads as 'the thumbnails do nothing'.

    ``focus_saved_plot`` returns False when the gallery holds no card for the id
    — the plot was deleted, or the page could not be selected — and the user is
    still looking at whatever page they were on. Saying so is the difference
    between a stale thumbnail and a broken feature.
    """
    from well_viewer.features.home import controller as HC

    said = []
    ctx = _ctx(tmp_path)
    ctx.notify = types.SimpleNamespace(set_status=said.append)
    monkeypatch.setattr(HC, "_focus_saved_plot", lambda ctx_, pid: False)

    HC.open_saved_plot(ctx, "gone")

    assert said and "could not be opened" in said[0]


def test_a_build_without_the_feature_never_reaches_the_gallery(tmp_path,
                                                               monkeypatch):
    """The guard is the registry lookup, not a try/except around the import."""
    from well_viewer.features.home import controller as HC

    reached = []
    monkeypatch.setattr(HC, "_focus_saved_plot",
                        lambda ctx_, pid: reached.append(pid) or True)
    ctx = _ctx(tmp_path, has_feature=False)
    ctx.notify = types.SimpleNamespace(set_status=lambda msg: None)

    HC.open_saved_plot(ctx, "p0")

    assert reached == []
