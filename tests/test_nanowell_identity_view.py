"""The Nanowells page's Identity view (NANOWELL_IDENTITY_TRACKER D1-D5): one well's timepoint against its reference.

The dataset is real end to end: two timepoints of one rendered well (``tests/test_nanowell_identity._timepoint``),
written as the run writes them — the sites table, the grid record, the montage PNGs in ``<well>_out.zip`` — and
checked by the pipeline's own ``check_well_dir``, so the sidecar the view draws is the one a run leaves. Four wells:

* ``A01`` — the clean pair, ``confirmed``;
* ``A02`` — the same pair with every address of ``T1`` one lattice step off, ``contradicted`` (a ``step``);
* ``A03`` — the clean pair never checked: no sidecar;
* ``A04`` — ``T1``'s tile F3 placed one lattice step off with its sites, ``contradicted`` by the tile layout.

Every assertion is of a value drawn — a fill's number, an outline's position, a label's place, a marker's
coordinates, a text — against the number the sidecar holds (CLAUDE.md §7.8). One test drives the real page builder.
"""

from __future__ import annotations

import copy
import io
import json
import math
import zipfile

import numpy as np
import pandas as pd
import pytest

from pipeline import nanowell_identity as ni
from review.features.nanowell import _identity_controller as idc
from review.features.nanowell import _identity_render as ir
from review.features.nanowell import identity_compute as ic
from review.features.nanowell.collect import load_nanowell_data
from review.features.nanowell.image_compute import read_montage
from tests.test_nanowell_identity import TILE, _a1, _block, _relabel, _timepoint
from tests.test_nanowell_review_data import _dataset, _row

STARTS = {"T0": "2026-03-01T09:00:00", "T1": "2026-03-02T09:00:00"}
#: The renderer's pixel size and tile size (``_timepoint``'s plan: 2 x 2 tiles of 700 px at 4.3 um).
PX, TILE_PX = 4.3, 700


def _rows(t) -> list[dict]:
    out = []
    for (q, r), (x, y) in t.sites.items():
        row = _row(t.name, q, r, n=1, c=0.1)
        row.update(well_x_um=x, well_y_um=y, owner=t.owners.get((q, r), ""), nw_ring=ic.ring_of((q, r), t.kind))
        out.append(row)
    return out


def _tile_off(other):
    """*other* with tile F3 placed one lattice step off, its sites and addresses moved with it."""
    step = _a1(other)
    broken = copy.deepcopy(other)
    broken.sites, broken.owners = {}, {}
    for a, p in other.sites.items():
        moved = other.owners[a] == TILE
        key = (a[0] + 1, a[1]) if moved else a
        if moved or key not in broken.sites:
            broken.sites[key] = tuple(np.add(p, step)) if moved else p
            broken.owners[key] = other.owners[a]
    broken.tiles[TILE] = tuple(np.add(other.tiles[TILE], step))
    return broken


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """``out/`` with four wells as the module docstring says, each checked (A03 left unchecked)."""
    from skimage.io import imsave

    ref, _ = _timepoint("T0", 7.0, (0.0, 0.0), 1)
    other, _ = _timepoint("T1", 9.0, (60.0, -40.0), 2)
    wells = {"A01": {"T0": ref, "T1": other}, "A02": {"T0": ref, "T1": _relabel(other, lambda a: (a[0] + 1, a[1]))},
             "A03": {"T0": ref, "T1": other}, "A04": {"T0": ref, "T1": _tile_off(other)}}
    root = tmp_path_factory.mktemp("identity_view")
    out = _dataset(root, {w: [row for t in tps.values() for row in _rows(t)] for w, tps in wells.items()},
                   starts=STARTS, grids={w: {name: _block(t) for name, t in tps.items()} for w, tps in wells.items()})
    for well, tps in wells.items():
        with zipfile.ZipFile(out / f"{well}_out.zip", "w") as zf:
            for name, t in tps.items():
                buffer = io.BytesIO()
                imsave(buffer, t.image.astype(np.uint8), extension=".png", check_contrast=False)
                zf.writestr(f"{well}_{name}_montage.png", buffer.getvalue())
        pd.DataFrame([{"well": well, "fov": "F0", "timepoint": "T0", "nucleus_id": 1, "area_px": 50,
                       "GFP_mean_intensity": 10.0, "nw_q": 0, "nw_r": 0}]).to_csv(out / f"measurements_{well}.csv",
                                                                                  index=False)
    info = json.loads((out / "pipeline_info.json").read_text())
    info["acquisition"] = {tp: {"pixel_size_um": {"x": PX, "y": PX}, "tile_size_px": {"x": TILE_PX, "y": TILE_PX}}
                           for tp in STARTS}
    (out / "pipeline_info.json").write_text(json.dumps(info))
    for well in ("A01", "A02", "A04"):
        ni.check_well_dir(out, well, ["T0", "T1"])
    data = load_nanowell_data(out)
    assert data.identity["A01"]["T1"][0] == ni.CONFIRMED, data.identity["A01"]
    assert data.identity["A02"]["T1"][0] == ni.CONTRADICTED and data.identity["A04"]["T1"][0] == ni.CONTRADICTED
    assert data.identity["A03"]["T1"][0] == ni.NOT_CHECKED
    return out, data, ref, other


def _pair(dataset, well, tp="T1"):
    out, data, _ref, _other = dataset
    return ic.identity_pair(data, well, tp, ni.read_sidecar(out, well))


def _figure():
    from matplotlib.figure import Figure

    return Figure(figsize=(8, 6.4))


def _texts(fig, gid):
    every = list(fig.texts) + [t for ax in fig.axes for t in ax.texts]
    return [t.get_text() for t in every if t.get_gid() == gid]


def _footer(fig) -> str:
    """The footer as one string: a line too long for the figure is broken over rows, each a text of its own, at
    single spaces — so the rows joined by one space are the lines joined by one space."""
    return " ".join(_texts(fig, "nw-identity-footer"))


def _draw(dataset, well, mode="distance", tp="T1", **kw):
    from review.features.nanowell.controller import draw_view

    out, data, _ref, _other = dataset
    fig = _figure()
    _hits, drawn = draw_view(fig, "identity", None, data, groups={well: [well]}, metric="n", channel=None,
                             axis="time", timepoint=tp, control="", baseline="earliest", cohort=False, well=well,
                             well_timepoint=tp, identity={"mode": mode, **kw} if (mode != "distance" or kw) else None)
    return fig, drawn


def _montages(dataset, well):
    _out, data, _ref, _other = dataset
    return {"reference": read_montage(data, well, "T0")[0], "compared": read_montage(data, well, "T1")[0]}


def _by_gid(ax, gid):
    return [c for c in list(ax.collections) + list(ax.patches) + list(ax.lines) + list(ax.images)
            if c.get_gid() == gid]


# ── the Distance map ─────────────────────────────────────────────────────────
def test_the_distance_map_fills_each_address_with_its_sidecar_distance_at_its_reference_position(dataset):
    out, _data, ref, _other = dataset
    entry = ni.read_sidecar(out, "A02")["timepoints"]["T1"]
    fig, drawn = _draw(dataset, "A02")
    (fill,) = _by_gid(drawn.axes["map"], "nw-identity-fill")
    distances = {(q, r): d for q, r, d in entry["distances"]}
    at = {tuple(np.round(p, 3)): float(v) for p, v in zip(fill.get_offsets(), fill.get_array())}
    expected = {tuple(np.round(ref.sites[a], 3)): d for a, d in distances.items()}
    assert at == pytest.approx(expected)
    # One step off: every address lands a pitch from itself, and every one is outlined as failing.
    assert min(distances.values()) > 0.9
    # The sidecar lists the first ``MAX_LISTED`` failing addresses; the map outlines all of them, the rest found by
    # their distance, so the outlines number the sidecar's ``failing_count``.
    (outline,) = _by_gid(drawn.axes["map"], "nw-identity-failing")
    listed = {(f[0], f[1]) for f in entry["failing"]}
    assert len(listed) == ni.MAX_LISTED < entry["failing_count"]
    failing = listed | {a for a, d in distances.items() if d > ni.TOLERANCE_PITCHES}
    assert {tuple(np.round(p, 3)) for p in outline.get_offsets()} == {tuple(np.round(ref.sites[a], 3))
                                                                       for a in failing}
    assert len(outline.get_offsets()) == entry["failing_count"] == entry["compared"]


def test_a_confirmed_well_has_no_failing_outline_and_its_fills_are_its_small_distances(dataset):
    out, _data, ref, _other = dataset
    entry = ni.read_sidecar(out, "A01")["timepoints"]["T1"]
    fig, drawn = _draw(dataset, "A01")
    assert _by_gid(drawn.axes["map"], "nw-identity-failing") == []
    (fill,) = _by_gid(drawn.axes["map"], "nw-identity-fill")
    assert sorted(fill.get_array()) == pytest.approx(sorted(d for _q, _r, d in entry["distances"]))
    assert drawn.distance.value_of((0, 0)) == pytest.approx(next(d for q, r, d in entry["distances"] if (q, r) == (0, 0)))


def test_the_distance_map_says_who_against_whom_and_why(dataset):
    out, _data, _ref, _other = dataset
    entry = ni.read_sidecar(out, "A02")["timepoints"]["T1"]
    fig, _drawn = _draw(dataset, "A02")
    assert _texts(fig, "nw-identity-title") == ["A02 · T1 vs T0 · contradicted"]
    assert _footer(fig).startswith(" ".join([
        f"Contradicted: {entry['reason']}",
        f"{entry['compared']:,} addresses compared · {entry['failing_count']:,} failing · median "
        f"{entry['median_pitches']:.3f} pitch · max {entry['max_pitches']:.3f} pitch",
        f"Diagnosis (step): {entry['diagnosis']['detail']}."]))
    # The tolerance is marked on the colour bar at the sidecar's value.
    bar_line = [ln for ax in fig.axes for ln in ax.lines if ln.get_gid() == "nw-identity-tolerance"]
    assert [tuple(ln.get_ydata()) for ln in bar_line] == [(0.25, 0.25)]


def test_the_moved_tile_is_drawn_where_the_registration_puts_it_in_the_reference_frame(dataset):
    out, _data, _ref, other = dataset
    entry = ni.read_sidecar(out, "A04")["timepoints"]["T1"]
    assert [m[0] for m in entry["tiles"]["moved"]] == [TILE]
    pair = _pair(dataset, "A04")
    fig, drawn = _draw(dataset, "A04")
    (moved,) = _by_gid(drawn.axes["map"], "nw-identity-tile-moved")
    left, top = _tile_off(other).tiles[TILE]
    side = TILE_PX * PX
    expected = pair.inverse([(left, top), (left + side, top), (left + side, top + side), (left, top + side)])
    assert moved.get_xy()[:4] == pytest.approx(expected)
    assert f"{TILE} moved {entry['tiles']['moved'][0][1]:.0f} µm" in " ".join(_texts(fig, "nw-identity-tile-label"))
    assert len(_by_gid(drawn.axes["map"], "nw-identity-tile")) == len(other.tiles) - 1


def test_the_stage_cross_check_is_a_footer_line_when_the_sidecar_has_one(dataset):
    out, _data, _ref, _other = dataset
    payload = ni.read_sidecar(out, "A01")
    payload["stage"] = {"reference": "T0", "timepoints": {"T1": {"ok": False, "residual_um": 171.6,
                                                                 "reason": "the well's address origin sits 172 um off"}}}
    fig, _drawn = _draw(dataset, "A01", payload=payload)
    assert ("Stage cross-check against T0: disagrees (residual 171.6 µm) — the well's address origin sits 172 um off."
            in _footer(fig))
    # The stage overrules the image verdict, as on every other figure (``nanowell_identity.verdicts``).
    assert _texts(fig, "nw-identity-title") == ["A01 · T1 vs T0 · contradicted"]


# ── messages ─────────────────────────────────────────────────────────────────
def test_a_well_never_checked_says_so_and_names_the_audit_command(dataset):
    out, _data, _ref, _other = dataset
    fig, drawn = _draw(dataset, "A03")
    assert drawn is None
    (said,) = _texts(fig, "nw-identity-message")
    assert "the nanowell identity check has not been run on these tables" in said
    assert f"python -m pipeline.nanowell_identity {out}" in said


def test_the_reference_timepoint_says_it_is_the_reference(dataset):
    fig, drawn = _draw(dataset, "A01", tp="T0")
    assert drawn is None
    (said,) = _texts(fig, "nw-identity-message")
    assert said.startswith("T0 is A01's reference timepoint")


def test_an_inconclusive_timepoint_draws_its_surface_and_its_reason(dataset):
    out, _data, _ref, _other = dataset
    payload = ni.read_sidecar(out, "A01")
    entry = payload["timepoints"]["T1"]
    reason = "the square's best match leads the next place by only 0.05"
    for key in ("distances", "failing", "compared", "median_pitches", "max_pitches", "diagnosis"):
        entry.pop(key)
    entry.update(verdict=ni.INCONCLUSIVE, reason=reason)
    entry["square"].update(ok=False, reason=reason)
    fig, drawn = _draw(dataset, "A01", payload=payload)
    assert drawn.mode == "diagnostics" and _by_gid(drawn.axes["surface"], "nw-identity-surface")
    assert _footer(fig).startswith(f"Peak {entry['square']['peak']:.3f}")
    assert f"Inconclusive: {reason}" in _footer(fig)
    assert _texts(fig, "nw-identity-title") == ["A01 · T1 vs T0 · inconclusive"]


# ── Blink ────────────────────────────────────────────────────────────────────
def test_the_blink_frame_maps_a_confirmed_pair_onto_the_reference_s_pixels(dataset):
    """Resampled through the registration, the compared montage lies on the reference's lattice: inside both, the
    frames agree pixel for pixel far better than the same montage read without the mapping (the plate moved 72 um
    and turned 2 degrees, so read at the reference's own coordinates it is off by most of a nanowell)."""
    from dataclasses import replace

    pair = _pair(dataset, "A01")
    images = _montages(dataset, "A01")

    def agreement(frames):
        both = np.isfinite(frames.compared) & np.isfinite(frames.reference)
        return np.corrcoef(frames.reference[both], frames.compared[both])[0, 1]

    frames = ic.blink_frames(pair, images["reference"], images["compared"])
    unmapped = ic.blink_frames(replace(pair, forward=lambda p: np.asarray(p, dtype=float).reshape(-1, 2)),
                               images["reference"], images["compared"])
    assert frames.covered > 0.9
    assert frames.reference.shape == (665, 665)                      # 1330 px a side, halved to fit BLINK_MAX_SIDE
    assert agreement(frames) > 0.8 and agreement(unmapped) < 0.5, (agreement(frames), agreement(unmapped))


@pytest.mark.parametrize("well, apart_pitches", [("A01", 0.0), ("A02", 1.0)])
def test_each_frame_labels_its_own_addresses_so_a_stepped_one_jumps_by_a_pitch(dataset, well, apart_pitches):
    _out, _data, ref, _other = dataset
    images = _montages(dataset, well)
    fig, drawn = _draw(dataset, well, mode="blink", images=images)
    labels = [{t.get_text(): t.get_position() for t in drawn.axes["map"].texts
               if t.get_gid() == f"nw-identity-label-{k}"} for k in (0, 1)]
    gap = math.dist(labels[0]["0,0"], labels[1]["0,0"]) / ref.pitch
    assert gap == pytest.approx(apart_pitches, abs=0.05)
    assert labels[0]["0,0"] == pytest.approx(ref.sites[(0, 0)])


def test_the_frames_show_one_at_a_time_and_step_deterministically(dataset):
    images = _montages(dataset, "A01")
    _fig, drawn = _draw(dataset, "A01", mode="blink", images=images)

    def shown():
        return [k for k, artists in enumerate(drawn.frames) if all(a.get_visible() for a in artists)
                and not any(a.get_visible() for other in drawn.frames if other is not artists for a in other)]
    assert shown() == [0]
    assert ir.show_frame(drawn, 1) == 1 and shown() == [1]
    assert ir.show_frame(drawn, 2) == 0 and shown() == [0]


def test_blink_waits_for_the_montages_and_says_so(dataset):
    fig, drawn = _draw(dataset, "A01", mode="blink", images={"why": "Reading the montages…"})
    assert drawn is None and _texts(fig, "nw-identity-message") == ["Reading the montages…"]


# ── Side by side: the hit test ───────────────────────────────────────────────
def test_the_hover_finds_the_address_and_where_the_registration_says_it_lands(dataset):
    _out, _data, ref, other = dataset
    pair = _pair(dataset, "A01")
    x, y = ref.sites[(2, -1)]
    hit = ic.hover_target("reference", x + 0.2 * ref.pitch, y - 0.1 * ref.pitch, pair)
    assert hit.address == (2, -1) and hit.ref_xy == pytest.approx(ref.sites[(2, -1)])
    assert hit.other_xy == pytest.approx(other.sites[(2, -1)])
    assert math.dist(hit.predicted_xy, other.sites[(2, -1)]) / other.pitch < 0.05
    # The centre of three neighbouring nanowells is 1/sqrt(3) of a pitch from each: further than half a pitch.
    between = np.mean([ref.sites[a] for a in ((2, -1), (3, -1), (2, 0))], axis=0)
    assert ic.hover_target("reference", *between, pair) is None
    # On the stepped well the compared side's (0, 0) is a pitch from where the reference's (0, 0) lands.
    stepped = _pair(dataset, "A02")
    sx, sy = stepped.other.sites[(0, 0)]
    hit = ic.hover_target("compared", sx, sy, stepped)
    assert hit.address == (0, 0)
    assert math.dist(hit.other_xy, hit.predicted_xy) / other.pitch == pytest.approx(1.0, abs=0.05)


def test_the_hover_lights_both_sides_and_crosses_the_prediction(dataset):
    _out, _data, ref, other = dataset
    pair = _pair(dataset, "A01")
    _fig, drawn = _draw(dataset, "A01", mode="sides", images=_montages(dataset, "A01"))
    hit = ic.hover_target("compared", *other.sites[(1, 1)], pair)
    assert ir.show_hover(drawn, hit)
    assert drawn.hover["reference"].center == pytest.approx(ref.sites[(1, 1)])
    assert drawn.hover["compared"].center == pytest.approx(other.sites[(1, 1)])
    cross = drawn.hover["predicted"]
    assert (cross.get_xdata()[0], cross.get_ydata()[0]) == pytest.approx(hit.predicted_xy)
    assert drawn.hover["label"].get_text().startswith("1,1 · lands 0.0")
    assert ir.show_hover(drawn, None) and not drawn.hover["reference"].get_visible()


# ── Diagnostics ──────────────────────────────────────────────────────────────
def test_the_diagnostics_mark_the_peak_at_the_sidecar_s_match_and_print_the_transform(dataset):
    out, _data, _ref, other = dataset
    entry = ni.read_sidecar(out, "A01")["timepoints"]["T1"]
    square = entry["square"]
    fig, drawn = _draw(dataset, "A01", mode="diagnostics")
    ax = drawn.axes["surface"]
    (peak,) = _by_gid(ax, "nw-identity-peak")
    assert (peak.get_xdata()[0], peak.get_ydata()[0]) == pytest.approx(tuple(square["found_um"]))
    (runner,) = _by_gid(ax, "nw-identity-runner-up")
    assert math.dist((runner.get_xdata()[0], runner.get_ydata()[0]), square["found_um"]) > 0.5 * other.pitch
    (surface,) = _by_gid(ax, "nw-identity-surface")
    left, top, right, bottom = square["surface_box_um"]
    assert tuple(surface.get_extent()) == pytest.approx((left, right, bottom, top))
    footer = _footer(fig)
    assert footer.startswith(f"Peak {square['peak']:.3f} (at least {ni.MIN_PEAK:g} needed) · runner-up "
                             f"{square['runner_up']:.3f} · margin {square['margin']:.3f} (at least {ni.MIN_MARGIN:g} "
                             "needed): the square is found.")
    shift = np.subtract(square["found_um"], ni.read_sidecar(out, "A01")["timepoints"]["T0"]["square_um"])
    assert (f"Transform, reference square to the match: turn {square['rotation_deg']:+.2f}°, scale "
            f"{square['scale']:.4f}, translation ({shift[0]:+.1f}, {shift[1]:+.1f}) µm.") in footer
    assert f"is {entry['square_agreement_pitches']:.3f} pitch from the match" in footer


# ── the rest of the page: state, the well list ───────────────────────────────
def test_the_mode_is_kept_with_the_page_s_settings_and_an_unknown_one_is_refused():
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.state import IDENTITY_MODES, NanowellState

    assert tuple(key for key, _label in MODES) == IDENTITY_MODES
    st = NanowellState()
    st.view, st.identity_mode = "identity", "sides"
    again = NanowellState()
    again.apply_config(st.to_config())
    assert (again.view, again.identity_mode) == ("identity", "sides")
    again.apply_config({"identity_mode": "hologram"})
    assert again.identity_mode == "distance"


def test_the_worst_verdict_and_the_timepoint_the_view_opens_on(dataset):
    _out, data, _ref, _other = dataset
    assert [ic.worst_verdict(data, w) for w in ("A01", "A02", "A03", "A04")] == [
        ni.CONFIRMED, ni.CONTRADICTED, ni.NOT_CHECKED, ni.CONTRADICTED]
    assert ic.default_timepoint(data, "A01", ["T0", "T1"]) == "T1"         # not the reference compared with itself


# ── the real page ────────────────────────────────────────────────────────────
@pytest.fixture
def page(qtbot, dataset):
    pytest.importorskip("PySide6")
    from tests.test_nanowell_page import _page

    ctx = _page(qtbot, dataset[0])
    ctx.state.selection.selected_wells = {"A01", "A02"}
    return ctx


def _choose(ctx, name, key, keys):
    ctx.widgets.get(name).setCurrentIndex(list(keys).index(key))          # emits currentChanged, as a click does


def _page_texts(ctx, gid):
    return _texts(ctx.widgets.get("_nw_fig"), gid)


def test_the_page_s_identity_view_is_chosen_on_its_switch_and_draws_the_chosen_well(page, qtbot):
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    assert st.view == "identity"
    well_cb = ctx.widgets.get("_nw_well_cb")
    assert [well_cb.itemText(i) for i in range(well_cb.count())] == ["A01 · confirmed", "A02 · contradicted"]
    assert ctx.widgets.get("_nw_identity_mode_seg").isVisibleTo(ctx.window)
    assert ctx.widgets.get("_nw_tp_cb").isVisibleTo(ctx.window) and well_cb.isVisibleTo(ctx.window)
    assert _page_texts(ctx, "nw-identity-title") == ["A01 · T1 vs T0 · confirmed"]
    well_cb.setCurrentIndex(well_cb.findData("A02"))
    assert _page_texts(ctx, "nw-identity-title") == ["A02 · T1 vs T0 · contradicted"]
    entry = ni.read_sidecar(dataset_out(ctx), "A02")["timepoints"]["T1"]
    assert f"Diagnosis (step): {entry['diagnosis']['detail']}." in " ".join(_page_texts(ctx, "nw-identity-footer"))
    # Blink: the timer runs while the frames are on screen; a tick shows the other frame; another view stops it.
    timer = ctx.widgets.get("_nw_blink_timer")
    _choose(ctx, "_nw_identity_mode_seg", "blink", [k for k, _l in MODES])
    qtbot.waitUntil(lambda: st.identity_drawn is not None and st.identity_drawn.mode == "blink", timeout=20000)
    assert timer.isActive() and st.identity_drawn.frame == 0
    idc.blink_tick(ctx)
    assert st.identity_drawn.frame == 1
    assert idc.step_blink(ctx, 0) == 0
    _choose(ctx, "_nw_view_seg", "distribution", [k for k, _l in VIEWS])
    assert not timer.isActive() and st.identity_drawn is None
    assert st.to_config()["identity_mode"] == "blink"


def test_a_hidden_page_stops_the_blink_on_its_next_tick(page, qtbot):
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    _choose(ctx, "_nw_identity_mode_seg", "blink", [k for k, _l in MODES])
    qtbot.waitUntil(lambda: st.identity_drawn is not None and st.identity_drawn.mode == "blink", timeout=20000)
    timer = ctx.widgets.get("_nw_blink_timer")
    assert timer.isActive()
    ctx.window.hide()
    idc.blink_tick(ctx)
    assert not timer.isActive()


def test_the_pointer_on_the_page_s_side_by_side_lights_the_address(page, qtbot):
    from matplotlib.backend_bases import MouseEvent

    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    _choose(ctx, "_nw_identity_mode_seg", "sides", [k for k, _l in MODES])
    qtbot.waitUntil(lambda: st.identity_drawn is not None and st.identity_drawn.mode == "sides"
                    and len(st.identity_drawn.axes["reference"].images) == 1, timeout=20000)
    drawn = st.identity_drawn
    canvas = ctx.widgets.get("_nw_canvas")
    canvas.draw()
    ax = drawn.axes["reference"]
    x, y = ax.transData.transform(drawn.pair.ref.sites[(0, 0)])
    canvas.callbacks.process("motion_notify_event", MouseEvent("motion_notify_event", canvas, x, y))
    assert drawn.lit == (0, 0)
    assert drawn.hover["compared"].center == pytest.approx(drawn.pair.other.sites[(0, 0)])


def dataset_out(ctx):
    return ctx.state.feature_slice("nanowell").data.out_dir
