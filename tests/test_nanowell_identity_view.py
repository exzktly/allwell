"""The Nanowells page's Identity view (NANOWELL_IDENTITY_TRACKER D1-D5): one well's timepoint against its reference.

The dataset is real end to end: two timepoints of one rendered well (``tests/test_nanowell_identity._timepoint``),
written as the run writes them — the sites table, the grid record, the montage PNGs in ``<well>_out.zip`` — and
checked by the pipeline's own ``check_well_dir``, so the sidecar the view draws is the one a run leaves. Four wells:

* ``A01`` — the clean pair, ``confirmed``;
* ``A02`` — the same pair with every address of ``T1`` one lattice step off, ``contradicted`` (a ``step``);
* ``A03`` — the clean pair never checked: no sidecar;
* ``A04`` — ``T1``'s tile F3 placed one lattice step off with its sites, ``partial``: the tile's nanowells left out.

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
from review.features.nanowell import controller as nwc
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
    assert data.identity["A02"]["T1"][0] == ni.CONTRADICTED and data.identity["A04"]["T1"][0] == ni.PARTIAL
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
    fig, drawn = _draw(dataset, "A02", colour="distance")
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
    fig, _drawn = _draw(dataset, "A02", colour="distance")
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
    square = entry["square"]
    fig, drawn = _draw(dataset, "A01", payload=payload)
    assert drawn.mode == "diagnostics" and _by_gid(drawn.axes["surface"], "nw-identity-surface")
    assert _check(fig, "verdict") == f"Verdict: inconclusive — {reason}."
    # The numbers pass their thresholds but the check said no: its own reason is given.
    assert _check(fig, "square") == (f"Central square not found clearly: best match {square['peak']:.2f} (needs 0.15), "
                                     f"next best {square['runner_up']:.2f} — lead {square['margin']:.2f} (needs 0.10); "
                                     f"{reason}.")
    assert _check(fig, "addresses") == "Addresses not compared: the square was not found."
    assert _texts(fig, "nw-identity-state-verdict") == ["!"] and _texts(fig, "nw-identity-state-square") == ["✗"]
    assert "No address was compared, so there is no distance map" in _footer(fig)
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
def _check(fig, key) -> str:
    """One line of the verdict block as drawn: its rows joined (a long line is broken over rows at single spaces)."""
    return " ".join(_texts(fig, f"nw-identity-check-{key}"))


def _glyph(fig, key):
    (text,) = [t for t in fig.texts if t.get_gid() == f"nw-identity-state-{key}"]
    return text.get_text(), text.get_color()


def _xy(line):
    return float(line.get_xdata()[0]), float(line.get_ydata()[0])


def test_the_verdict_block_of_a_confirmed_pair_reads_each_check_against_its_threshold(dataset):
    from ui.theme import Colors

    out, _data, _ref, other = dataset
    payload = ni.read_sidecar(out, "A01")
    entry, square = payload["timepoints"]["T1"], payload["timepoints"]["T1"]["square"]
    moved = math.dist(square["found_um"], payload["timepoints"]["T0"]["square_um"])
    refit = square["refined"]
    fig, drawn = _draw(dataset, "A01", mode="diagnostics")
    expected = {
        "verdict": f"Verdict: confirmed — {entry['reason']}.",
        "square": f"Central square found clearly: best match {square['peak']:.2f} (needs 0.15), next best "
                  f"{square['runner_up']:.2f} — lead {square['margin']:.2f} (needs 0.10).",
        "motion": f"Plate moved {moved:.0f} µm and turned +2.0° since T0 (refit on {refit['sites']} nanowells: +0.0°, "
                  "scale ×1.000).",
        "square_agreement": f"Square the run picked at T1 agrees: {entry['square_agreement_pitches']:.2f} spacing apart "
                            "(limit 0.25).",
        "outline": f"Array outline agrees: {entry['centre_agreement_pitches']:.2f} spacing apart (limit 0.75).",
        "tiles": "Tiles placed as at T0.",
        "addresses": f"Addresses: {entry['compared']} of {entry['compared']} land on themselves, worst "
                     f"{entry['max_pitches']:.2f} spacing (limit 0.25).",
    }
    assert [c.key for c in drawn.diagnostics.checks] == list(expected)
    assert {key: _check(fig, key) for key in expected} == expected
    assert moved == pytest.approx(math.hypot(60.0, 40.0), abs=2.0)          # the plate moved (60, -40) µm
    passed = {key: _glyph(fig, key) for key in expected}
    assert passed == {**{key: ("✓", Colors.success) for key in expected},
                      "motion": ("•", passed["motion"][1])}               # no threshold: muted, not a pass
    assert passed["motion"][1] not in (Colors.success, Colors.danger, Colors.warn)


def test_the_verdict_block_of_a_stepped_pair_names_the_step_and_the_failure(dataset):
    from ui.theme import Colors

    out, _data, _ref, _other = dataset
    entry = ni.read_sidecar(out, "A02")["timepoints"]["T1"]
    fig, _drawn = _draw(dataset, "A02", mode="diagnostics")
    assert _check(fig, "verdict") == f"Verdict: contradicted — {entry['reason']}."
    assert _check(fig, "addresses") == (f"Addresses: {entry['compared']} of {entry['compared']} land on another "
                                        "nanowell: every address is one lattice step (+1, +0) off (100% of the "
                                        "sites).")
    assert _glyph(fig, "verdict") == ("✗", Colors.danger) and _glyph(fig, "addresses") == ("✗", Colors.danger)
    # The registration itself found the square: what failed is the names, not the match.
    assert _glyph(fig, "square") == ("✓", Colors.success) and _glyph(fig, "tiles") == ("✓", Colors.success)


def test_the_verdict_block_of_a_partial_pair_names_the_tile_left_out(dataset):
    from ui.theme import Colors

    out, _data, _ref, other = dataset
    entry = ni.read_sidecar(out, "A04")["timepoints"]["T1"]
    ((tile, um),) = entry["tiles"]["moved"]
    fig, _drawn = _draw(dataset, "A04", mode="diagnostics")
    assert tile == TILE
    assert _check(fig, "tiles") == (f"Tiles placed a lattice step differently than at T0: {TILE} {um:.0f} µm "
                                    f"({um / other.pitch:.2f} spacing); their nanowells are left out.")
    assert _glyph(fig, "tiles") == ("✗", Colors.danger) and _glyph(fig, "verdict") == ("!", Colors.warn)


def test_the_heat_map_marks_the_best_match_at_the_surface_s_peak_in_um_from_where_it_was_expected(dataset):
    out, _data, _ref, other = dataset
    payload = ni.read_sidecar(out, "A01")
    square, expected = payload["timepoints"]["T1"]["square"], payload["timepoints"]["T0"]["square_um"]
    fig, drawn = _draw(dataset, "A01", mode="diagnostics")
    ax = drawn.axes["surface"]
    (surface,) = _by_gid(ax, "nw-identity-surface")
    left, top, right, bottom = square["surface_box_um"]
    ex, ey = expected
    assert tuple(surface.get_extent()) == pytest.approx((left - ex, right - ex, bottom - ey, top - ey))
    (peak,) = _by_gid(ax, "nw-identity-peak")
    assert _xy(peak) == pytest.approx((square["found_um"][0] - ex, square["found_um"][1] - ey))
    # ... which is inside the surface's highest sample, mapped to µm from the expected square.
    grid = np.array([[np.nan if v is None else v for v in row] for row in square["surface"]], dtype=float)
    iy, ix = np.unravel_index(int(np.nanargmax(grid)), grid.shape)
    cw, ch = (right - left) / grid.shape[1], (bottom - top) / grid.shape[0]
    assert left - ex + ix * cw <= _xy(peak)[0] <= left - ex + (ix + 1) * cw
    assert top - ey + iy * ch <= _xy(peak)[1] <= top - ey + (iy + 1) * ch
    (where,) = _by_gid(ax, "nw-identity-expected")
    assert _xy(where) == (0.0, 0.0)
    (runner,) = _by_gid(ax, "nw-identity-runner-up")
    assert math.dist(_xy(runner), _xy(peak)) > 0.5 * other.pitch
    assert _texts(fig, "nw-identity-mark-label") == ["at T0", f"best {square['peak']:.2f}",
                                                     f"next {square['runner_up']:.2f}"]


def test_the_crops_show_the_square_looked_for_and_the_match_turned_into_the_reference_s_orientation(dataset):
    _out, data, ref, other = dataset
    payload = ni.read_sidecar(_out, "A01")
    centre = payload["timepoints"]["T0"]["square_um"]
    images = _montages(dataset, "A01")
    fig, drawn = _draw(dataset, "A01", mode="diagnostics", images=images)
    crops, half = drawn.crops, ni.PATCH_PITCHES * ref.pitch
    (looked,) = _by_gid(drawn.axes["looked"], "nw-identity-crop-looked")
    (found,) = _by_gid(drawn.axes["found"], "nw-identity-crop-found")
    assert tuple(looked.get_extent()) == pytest.approx((-half, half, half, -half))
    assert tuple(found.get_extent()) == pytest.approx((-half, half, half, -half))

    # Centred on the reference's square: the crop matches the montage's own pixels best with no shift at all.
    montage = images["reference"]
    n = crops.looked.shape[0]
    cx, cy = ((np.asarray(centre) - montage.origin_um) / montage.um_per_px - 0.5).round().astype(int)

    def agreement(a, b):
        keep = np.isfinite(a) & np.isfinite(b)
        return np.corrcoef(a[keep], b[keep])[0, 1]

    def at(dx, dy):
        x0, y0 = cx + dx - n // 2, cy + dy - n // 2
        return montage.image[y0:y0 + n, x0:x0 + n].astype(float)
    shifts = [(dx, dy) for dx in range(-4, 5) for dy in range(-4, 5)]
    best = max(shifts, key=lambda s: agreement(crops.looked, at(*s)))
    assert max(abs(best[0]), abs(best[1])) <= 1, best
    # Where it was found, read through the registration, looks like what was looked for; the compared montage read
    # at the same place without the registration does not (the plate moved 72 µm, most of a nanowell).
    other_m = images["compared"]
    ox, oy = ((np.asarray(centre) - other_m.origin_um) / other_m.um_per_px - 0.5).round().astype(int)
    unmapped = other_m.image[oy - n // 2:oy - n // 2 + n, ox - n // 2:ox - n // 2 + n].astype(float)
    assert agreement(crops.looked, crops.found) > 0.8 > 0.5 > agreement(crops.looked, unmapped)
    assert crops.found_at == pytest.approx(tuple(payload["timepoints"]["T1"]["square"]["found_um"]), abs=1e-6)
    (ring,) = _by_gid(drawn.axes["found"], "nw-identity-accepted")
    assert math.hypot(*_xy(ring)) / ref.pitch < 0.05                       # the run's own square agrees with it
    assert _texts(fig, "nw-identity-crop-key") == ["▢ dashed: the central square", "▢ the match",
                                                   "○ the square the run picked at T1"]
    assert _texts(fig, "nw-identity-panel-note-found") == ["T1, turned +2.0° into T0's orientation"]


def test_the_crops_say_they_wait_for_the_montages_and_the_rest_is_drawn(dataset):
    fig, drawn = _draw(dataset, "A01", mode="diagnostics", images={"why": "Reading the montages…"})
    assert _texts(fig, "nw-identity-panel-message") == ["Reading the montages…", "Reading the montages…"]
    assert _by_gid(drawn.axes["surface"], "nw-identity-surface") and _check(fig, "verdict").startswith("Verdict:")
    assert _texts(fig, "nw-identity-crop-key") == []


# ── the Distance map's Status colouring ──────────────────────────────────────
def _offsets(collection) -> set:
    return {tuple(np.round(p, 3)) for p in collection.get_offsets()}


def test_a_partial_well_s_status_puts_exactly_the_stepped_tile_s_nanowells_in_the_misregistered_class(dataset):
    _out, _data, ref, other = dataset
    broken = _tile_off(other)
    in_tile = {a for a, owner in broken.owners.items() if owner == TILE}
    status = ic.status_map(_pair(dataset, "A04"))
    assert status.members("misregistered") == in_tile
    assert status.members("kept") == (set(ref.sites) & set(broken.sites)) - in_tile
    assert status.members("not_compared") == set(ref.sites) - set(broken.sites) - in_tile
    assert status.members("wrong_name") == set() == status.members("imprecise")
    # Drawn: the Auto colouring of a timepoint that is not confirmed is the status, a count of each in the key.
    fig, drawn = _draw(dataset, "A04")
    assert drawn.status is not None and _by_gid(drawn.axes["map"], "nw-identity-fill") == []
    (hatched,) = _by_gid(drawn.axes["map"], "nw-identity-status-misregistered")
    pair = _pair(dataset, "A04")
    where = {a: ref.sites[a] if a in ref.sites else tuple(pair.inverse([broken.sites[a]])[0]) for a in in_tile}
    assert _offsets(hatched) == {tuple(np.round(p, 3)) for p in where.values()} and hatched.get_hatch() == "////"
    (kept,) = _by_gid(drawn.axes["map"], "nw-identity-status-kept")
    assert _offsets(kept) == {tuple(np.round(ref.sites[a], 3)) for a in status.members("kept")}
    counts = status.counts
    key = drawn.axes["map"].get_legend()
    assert {t.get_gid() for t in [*key.get_texts(), key.get_title()]} == {"nw-identity-status-key"}
    assert [t.get_text() for t in [*key.get_texts(), key.get_title()]] == [
        f"Lands on itself ({counts['kept']})", "Imprecise (0)", "Wrong name (0)",
        f"In a misregistered tile ({len(in_tile)})", f"Not compared ({counts['not_compared']})", "Nanowells"]
    assert "The wrong names and the misregistered tiles' nanowells are left out of every figure at T1" in _footer(fig)


def test_a_sidecar_without_an_excluded_list_still_finds_the_tile_and_a_stepped_well_is_all_wrong_names(dataset):
    out, _data, _ref, other = dataset
    payload = ni.read_sidecar(out, "A04")
    payload["timepoints"]["T1"].pop("excluded")
    old = ic.status_map(ic.identity_pair(dataset[1], "A04", "T1", payload))
    assert old.members("misregistered") == {a for a, o in _tile_off(other).owners.items() if o == TILE}
    stepped = ic.status_map(_pair(dataset, "A02"))
    entry = ni.read_sidecar(out, "A02")["timepoints"]["T1"]
    compared = {(q, r) for q, r, _d in entry["distances"]}
    # Every address both hold lands a step off, and each one only the reference holds that lands on a site the
    # compared timepoint names otherwise is listed failing with no distance: all wrong names, nothing kept.
    landed_elsewhere = {(f[0], f[1]) for f in entry["failing"] if f[2] is None}
    assert landed_elsewhere and not landed_elsewhere & compared
    assert stepped.members("wrong_name") == compared | landed_elsewhere and stepped.members("kept") == set()


def test_an_imprecise_nanowell_and_tile_are_drawn_in_the_warning_colour(dataset):
    from matplotlib.colors import to_rgba

    from ui.theme import Colors

    out, data, ref, _other = dataset
    payload = ni.read_sidecar(out, "A01")
    entry = payload["timepoints"]["T1"]
    loose, unlisted = (1, 1), (2, -1)
    entry["distances"] = [[q, r, 0.3 if (q, r) == loose else 0.6 if (q, r) == unlisted else d]
                          for q, r, d in entry["distances"]]
    entry["failing"] = [[1, 1, 0.3, 1, 1, "F0"]]                          # landed on itself, beyond the tolerance
    entry["tiles"]["imprecise"] = [["F1", 30.0]]
    status = ic.status_map(ic.identity_pair(data, "A01", "T1", payload))
    assert status.members("imprecise") == {loose}
    assert status.members("wrong_name") == {unlisted}                    # unlisted, beyond half a spacing
    fig, drawn = _draw(dataset, "A01", payload=payload, colour="status")
    (amber,) = _by_gid(drawn.axes["map"], "nw-identity-status-imprecise")
    assert _offsets(amber) == {tuple(np.round(ref.sites[loose], 3))}
    assert tuple(amber.get_facecolor()[0]) == pytest.approx(to_rgba(Colors.warn))
    (tile,) = _by_gid(drawn.axes["map"], "nw-identity-tile-imprecise")
    assert tile.get_edgecolor() == pytest.approx(to_rgba(Colors.warn))
    assert " F1 30 µm off" in _texts(fig, "nw-identity-tile-label")


def test_auto_colours_a_confirmed_timepoint_by_distance_and_the_switch_overrides_it(dataset):
    _fig, confirmed = _draw(dataset, "A01")
    assert confirmed.status is None and _by_gid(confirmed.axes["map"], "nw-identity-fill")
    _fig, chosen = _draw(dataset, "A04", colour="distance")
    assert chosen.status is None and _by_gid(chosen.axes["map"], "nw-identity-fill")
    _fig, status = _draw(dataset, "A01", colour="status")
    assert status.status is not None and status.status.counts["kept"] == len(status.status.addresses)


# ── zoom: the arithmetic ─────────────────────────────────────────────────────
def test_one_wheel_step_zooms_about_the_pointer_and_keeps_it_where_it_is_on_screen():
    from matplotlib.figure import Figure

    from review.features.nanowell import zoom_compute as zc

    xlim, ylim, at = (0.0, 100.0), (80.0, 0.0), (30.0, 20.0)              # y down, as every image here is drawn
    assert zc.wheel_limits(xlim, ylim, at, 1) == (pytest.approx((6.0, 86.0)), pytest.approx((68.0, 4.0)))
    assert zc.wheel_limits(xlim, ylim, at, -1) == (pytest.approx((-7.5, 117.5)), pytest.approx((95.0, -5.0)))
    ax = Figure(figsize=(4, 4)).add_axes([0.1, 0.1, 0.8, 0.8])
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    before = ax.transData.transform(at)
    new_x, new_y = zc.wheel_limits(xlim, ylim, at, 1)
    ax.set_xlim(*new_x)
    ax.set_ylim(*new_y)
    assert tuple(ax.transData.transform(at)) == pytest.approx(tuple(before))
    # Held at the limits against the full view: 500 times in, a further step in does nothing; out stops at 4x.
    tight = (50.0, 50.0 + 100.0 / zc.ZOOM_IN_LIMIT)
    assert zc.wheel_limits(tight, (0.2, 0.0), (50.1, 0.1), 1, home_xlim=xlim)[0] == pytest.approx(tight)
    wide = zc.wheel_limits((-100.0, 200.0), (0.0, 1.0), (50.0, 0.5), -3, home_xlim=xlim)[0]
    assert wide[1] - wide[0] == pytest.approx(100.0 / zc.ZOOM_OUT_LIMIT)


def test_a_drag_moves_the_limits_by_its_delta_and_linked_axes_name_each_other():
    from review.features.nanowell import zoom_compute as zc

    assert zc.pan_limits((0.0, 100.0), (80.0, 0.0), 10.0, -5.0) == ((-10.0, 90.0), (85.0, 5.0))
    links = (("reference", "compared"),)
    assert zc.linked(links, "compared") == ("reference", "compared") == zc.linked(links, "reference")
    assert zc.linked(links, "surface") == ("surface",)


class _Widgets:
    """The page's widget registry as the zoom reads it: only to repaint, and there is no canvas to repaint here."""
    @staticmethod
    def get_or_none(_name):
        return None


def _scroll(drawn, name, at, step=1):
    from matplotlib.backend_bases import MouseEvent
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    ax = drawn.axes[name]
    canvas = ax.figure.canvas if isinstance(ax.figure.canvas, FigureCanvasAgg) else FigureCanvasAgg(ax.figure)
    canvas.draw()
    x, y = ax.transData.transform(at)
    return MouseEvent("scroll_event", canvas, x, y, step=step)


def test_a_redraw_of_the_same_view_keeps_the_zoom_and_another_timepoint_starts_at_the_whole_view(dataset):
    from dataclasses import replace
    from types import SimpleNamespace

    from review.features.nanowell import _identity_zoom as izoom
    from review.features.nanowell.state import NanowellState

    _out, _data, ref, _other = dataset
    st, ctx = NanowellState(), SimpleNamespace(widgets=_Widgets())
    _fig, waiting = _draw(dataset, "A01", mode="sides")                   # the montages not read yet: sites only
    st.identity_drawn = waiting
    izoom.restore(st, waiting)
    at = ref.sites[(0, 0)]
    izoom.on_scroll(ctx, st, _scroll(waiting, "reference", at))
    zoomed = (waiting.axes["reference"].get_xlim(), waiting.axes["reference"].get_ylim())
    assert zoomed[0] != waiting.home["reference"][0]
    assert (waiting.axes["compared"].get_xlim(), waiting.axes["compared"].get_ylim()) == zoomed       # linked
    _fig, arrived = _draw(dataset, "A01", mode="sides", images=_montages(dataset, "A01"))
    izoom.restore(st, arrived)
    for name in ("reference", "compared"):
        assert (arrived.axes[name].get_xlim(), arrived.axes[name].get_ylim()) == tuple(map(pytest.approx, zoomed))
    # Another timepoint (or well, or mode) of the same figure starts at its whole view.
    _fig, later = _draw(dataset, "A01", mode="sides")
    later.pair = replace(later.pair, timepoint="T2")
    izoom.restore(st, later)
    assert st.identity_zoom == {}
    assert later.axes["reference"].get_xlim() == pytest.approx(later.home["reference"][0])


# ── the rest of the page: state, the well list ───────────────────────────────
def test_the_mode_is_kept_with_the_page_s_settings_and_an_unknown_one_is_refused():
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.state import IDENTITY_MODES, NanowellState

    assert tuple(key for key, _label in MODES) == IDENTITY_MODES
    st = NanowellState()
    st.view, st.identity_mode, st.identity_colour = "identity", "sides", "status"
    again = NanowellState()
    again.apply_config(st.to_config())
    assert (again.view, again.identity_mode, again.identity_colour) == ("identity", "sides", "status")
    again.apply_config({"identity_mode": "hologram", "identity_colour": "rainbow"})
    assert (again.identity_mode, again.identity_colour) == ("distance", "auto")


def test_the_worst_verdict_and_the_timepoint_the_view_opens_on(dataset):
    _out, data, _ref, _other = dataset
    assert [ic.worst_verdict(data, w) for w in ("A01", "A02", "A03", "A04")] == [
        ni.CONFIRMED, ni.CONTRADICTED, ni.NOT_CHECKED, ni.PARTIAL]
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


def _wheel(xlim, ylim, at, steps):
    """The limits *steps* wheel notches should give, worked out here rather than by the code under test: each end's
    distance from the pointer times 0.8 per notch."""
    f = 0.8 ** steps
    (x0, x1), (y0, y1) = xlim, ylim
    return ((at[0] - (at[0] - x0) * f, at[0] + (x1 - at[0]) * f),
            (at[1] - (at[1] - y0) * f, at[1] + (y1 - at[1]) * f))


def _event(canvas, name, ax, at, **kw):
    from matplotlib.backend_bases import MouseEvent

    x, y = ax.transData.transform(at)
    return MouseEvent(name, canvas, x, y, **kw)


def _limits(ax):
    return ax.get_xlim(), ax.get_ylim()


def test_the_page_zooms_side_by_side_and_blink_through_its_canvas(page, qtbot):
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    canvas = ctx.widgets.get("_nw_canvas")
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    _choose(ctx, "_nw_identity_mode_seg", "sides", [k for k, _l in MODES])
    qtbot.waitUntil(lambda: st.identity_drawn is not None and st.identity_drawn.mode == "sides"
                    and len(st.identity_drawn.axes["reference"].images) == 1, timeout=20000)
    drawn = st.identity_drawn
    canvas.draw()
    left, right = drawn.axes["reference"], drawn.axes["compared"]
    home = _limits(left)
    at = drawn.pair.ref.sites[(0, 0)]
    before = left.transData.transform(at)
    canvas.callbacks.process("scroll_event", _event(canvas, "scroll_event", left, at, step=1))
    expected = _wheel(*home, at, 1)
    assert _limits(left) == (pytest.approx(expected[0]), pytest.approx(expected[1]))
    assert _limits(right) == _limits(left)                                 # both images move together
    assert tuple(left.transData.transform(at)) == pytest.approx(tuple(before), abs=1e-6)
    # The hover still finds the address under the pointer, zoomed.
    canvas.draw()
    canvas.callbacks.process("motion_notify_event", _event(canvas, "motion_notify_event", left,
                                                           drawn.pair.ref.sites[(1, 0)]))
    assert drawn.lit == (1, 0)
    # A drag pans by its delta in data units: the picture follows the pointer.
    zoomed = _limits(left)
    start, end = left.transData.transform(at), left.transData.transform(at) + (40.0, -25.0)
    inverse = left.transData.inverted()
    dx, dy = inverse.transform(end) - inverse.transform(start)
    canvas.callbacks.process("button_press_event", _event(canvas, "button_press_event", left, at, button=1))
    canvas.callbacks.process("motion_notify_event", _event(canvas, "motion_notify_event", left,
                                                           tuple(inverse.transform(end))))
    canvas.callbacks.process("button_release_event", _event(canvas, "button_release_event", left,
                                                            tuple(inverse.transform(end)), button=1))
    panned = ((zoomed[0][0] - dx, zoomed[0][1] - dx), (zoomed[1][0] - dy, zoomed[1][1] - dy))
    assert _limits(left) == (pytest.approx(panned[0]), pytest.approx(panned[1]))
    assert _limits(right) == _limits(left) and st.identity_press is None
    # A redraw of the same view (a setting changed, the montages arriving) keeps it.
    nwc.redraw(ctx)
    assert st.identity_drawn is not drawn
    drawn = st.identity_drawn
    assert _limits(drawn.axes["reference"]) == (pytest.approx(panned[0]), pytest.approx(panned[1]))
    # A double-click shows the whole view again.
    canvas.draw()
    canvas.callbacks.process("button_press_event", _event(canvas, "button_press_event", drawn.axes["compared"],
                                                          at, button=1, dblclick=True))
    assert _limits(drawn.axes["reference"]) == _limits(drawn.axes["compared"]) == drawn.home["reference"]
    assert st.identity_zoom == {}

    # Blink: another mode starts at the whole view; a zoom survives the frames flipping.
    _choose(ctx, "_nw_identity_mode_seg", "blink", [k for k, _l in MODES])
    qtbot.waitUntil(lambda: st.identity_drawn is not None and st.identity_drawn.mode == "blink", timeout=20000)
    blink = st.identity_drawn
    canvas.draw()
    ax = blink.axes["map"]
    assert _limits(ax) == blink.home["map"]
    canvas.callbacks.process("scroll_event", _event(canvas, "scroll_event", ax, at, step=2))
    zoomed = _limits(ax)
    assert zoomed == tuple(map(pytest.approx, _wheel(*blink.home["map"], at, 2)))
    idc.blink_tick(ctx)
    assert blink.frame == 1 and _limits(ax) == zoomed
    idc.step_blink(ctx, 0)
    assert _limits(ax) == zoomed


def test_the_page_keeps_the_zoom_when_the_montages_arrive_and_starts_another_well_whole(page, qtbot):
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    canvas = ctx.widgets.get("_nw_canvas")
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    assert ctx.widgets.get("_nw_identity_colour_seg").isVisibleTo(ctx.window)          # on the Distance map
    _choose(ctx, "_nw_identity_mode_seg", "diagnostics", [k for k, _l in MODES])
    assert not ctx.widgets.get("_nw_identity_colour_seg").isVisibleTo(ctx.window)
    drawn = st.identity_drawn
    assert drawn.mode == "diagnostics" and drawn.crops.looked is None                   # the montages on their way
    assert _page_texts(ctx, "nw-identity-panel-message") == ["Reading the montages…"] * 2
    canvas.draw()
    looked, surface = drawn.axes["looked"], drawn.axes["surface"]
    canvas.callbacks.process("scroll_event", _event(canvas, "scroll_event", looked, (20.0, -10.0), step=1))
    zoomed = _limits(looked)
    assert zoomed == tuple(map(pytest.approx, _wheel(*drawn.home["looked"], (20.0, -10.0), 1)))
    assert _limits(drawn.axes["found"]) == zoomed and _limits(surface) == drawn.home["surface"]
    qtbot.waitUntil(lambda: st.identity_drawn is not drawn and st.identity_drawn.crops.looked is not None,
                    timeout=20000)
    arrived = st.identity_drawn
    assert _limits(arrived.axes["looked"]) == _limits(arrived.axes["found"]) == tuple(map(pytest.approx, zoomed))
    # The heat map zooms on its own.
    canvas.draw()
    canvas.callbacks.process("scroll_event", _event(canvas, "scroll_event", arrived.axes["surface"], (0.0, 0.0),
                                                    step=1))
    assert _limits(arrived.axes["surface"]) == tuple(map(pytest.approx,
                                                         _wheel(*arrived.home["surface"], (0.0, 0.0), 1)))
    assert _limits(arrived.axes["looked"]) == tuple(map(pytest.approx, zoomed))
    # Another well starts at its whole view.
    well_cb = ctx.widgets.get("_nw_well_cb")
    well_cb.setCurrentIndex(well_cb.findData("A02"))
    other = st.identity_drawn
    assert other.pair.well == "A02" and st.identity_zoom == {}
    assert _limits(other.axes["looked"]) == other.home["looked"]


def dataset_out(ctx):
    return ctx.state.feature_slice("nanowell").data.out_dir


# ── going to one nanowell ────────────────────────────────────────────────────
def test_an_address_is_read_as_the_page_writes_it_and_anything_else_is_refused_with_what_is_accepted():
    from review.features.nanowell import identity_focus_compute as fc

    for text in ("-2,3", " -2, 3 ", "(-2,3)", "-2 3"):
        assert fc.parse_address(text) == (-2, 3), text
    # A ring label, by the run's own numbering: ring 1's first place is east of the centre, the next one on.
    assert fc.parse_address("0.0") == (0, 0)
    assert fc.parse_address("1.0") == (1, 0) and fc.parse_address("1.1") == (0, 1)
    assert fc.parse_address("1.7", "square") == (1, -1)
    with pytest.raises(ValueError, match="ring 1 has no place 6"):
        fc.parse_address("1.6")                                            # a hex ring 1 has six places, 0-5
    with pytest.raises(ValueError, match=r"'B3' is not a nanowell address: type q,r"):
        fc.parse_address("B3")
    with pytest.raises(ValueError):
        fc.parse_address("2,3,4")


def test_a_focused_view_keeps_the_full_view_s_shape_and_direction_and_never_widens_past_it():
    from review.features.nanowell import identity_focus_compute as fc

    # The full view is 100 wide and 80 tall, y down: half 10 makes the shorter side 20, a quarter of it.
    assert fc.focus_limits((0.0, 100.0), (80.0, 0.0), (50.0, 40.0), 10.0) == ((37.5, 62.5), (50.0, 30.0))
    assert fc.focus_limits((0.0, 100.0), (80.0, 0.0), (50.0, 40.0), 500.0) == ((0.0, 100.0), (80.0, 0.0))


def _centre(ax):
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    return (x0 + x1) / 2, (y0 + y1) / 2


def _focus_draw(dataset, well, text, mode="distance", **kw):
    from types import SimpleNamespace

    from review.features.nanowell import _identity_zoom as izoom
    from review.features.nanowell.state import NanowellState

    fig, drawn = _draw(dataset, well, mode=mode, focus=text, **kw)
    st = NanowellState()
    izoom.restore(st, drawn)
    return fig, drawn, st, SimpleNamespace(widgets=_Widgets())


@pytest.mark.parametrize("well, lands_on", [("A01", (0, 0)), ("A02", (-1, 0))])
def test_the_distance_map_goes_to_the_address_and_crosses_where_the_compared_one_of_that_name_lands(
        dataset, well, lands_on):
    # A02's compared timepoint calls every nanowell by its neighbour's name (q + 1), so its 0,0 is the reference's
    # -1,0: the ring (the reference's 0,0) and the cross (the compared 0,0 mapped back) are a spacing apart there.
    _out, _data, ref, _other = dataset
    fig, drawn, st, _ctx = _focus_draw(dataset, well, "0,0")
    ax = drawn.axes["map"]
    [ring] = _by_gid(ax, "nw-identity-focus-ring")
    [cross] = _by_gid(ax, "nw-identity-focus-cross")
    assert ring.center == pytest.approx(ref.sites[(0, 0)])
    assert (cross.get_xdata()[0], cross.get_ydata()[0]) == pytest.approx(ref.sites[lands_on], abs=0.1 * ref.pitch)
    # The view is centred on it, about nine nanowells across its shorter side, and that zoom is kept.
    assert _centre(ax) == pytest.approx(ref.sites[(0, 0)])
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    assert min(abs(x1 - x0), abs(y1 - y0)) == pytest.approx(8 * ref.pitch)
    assert st.identity_zoom["map"] == (ax.get_xlim(), ax.get_ylim())
    [said] = _texts(fig, "nw-identity-focus")
    distance = next(d for q, r, d in drawn.pair.entry["distances"] if (q, r) == (0, 0))
    assert said == f"Nanowell 0,0: lands {distance:.2f} spacing from itself at T1."


def test_side_by_side_rings_the_address_on_each_montage_and_crosses_the_registration_s_prediction(dataset):
    _out, _data, ref, other = dataset
    _fig, drawn, _st, _ctx = _focus_draw(dataset, "A01", "1,0", mode="sides")
    left, right = drawn.axes["reference"], drawn.axes["compared"]
    [ring_ref] = _by_gid(left, "nw-identity-focus-ring")
    [ring_cmp] = _by_gid(right, "nw-identity-focus-ring")
    [cross] = _by_gid(right, "nw-identity-focus-cross")
    assert ring_ref.center == pytest.approx(ref.sites[(1, 0)])
    assert ring_cmp.center == pytest.approx(other.sites[(1, 0)])
    predicted = drawn.pair.forward([ref.sites[(1, 0)]])[0]
    assert (cross.get_xdata()[0], cross.get_ydata()[0]) == pytest.approx(tuple(predicted))
    # Linked: both centre on the reference's site, wide enough to hold the compared one too though the plate moved.
    assert _centre(left) == _centre(right) == pytest.approx(ref.sites[(1, 0)])
    gap = float(np.hypot(*np.subtract(other.sites[(1, 0)], ref.sites[(1, 0)])))
    (x0, x1), (y0, y1) = left.get_xlim(), left.get_ylim()
    assert min(abs(x1 - x0), abs(y1 - y0)) == pytest.approx(2 * max(4 * ref.pitch, gap + ref.pitch))


def test_blink_goes_to_the_address_and_its_marks_stay_through_the_frames(dataset):
    _out, _data, ref, _other = dataset
    _fig, drawn, _st, _ctx = _focus_draw(dataset, "A01", "0,1", mode="blink", images=_montages(dataset, "A01"))
    ax = drawn.axes["map"]
    assert _centre(ax) == pytest.approx(ref.sites[(0, 1)])
    [ring] = _by_gid(ax, "nw-identity-focus-ring")
    ir.show_frame(drawn, 1)
    assert ring.get_visible() and _centre(ax) == pytest.approx(ref.sites[(0, 1)])


def test_an_address_nobody_holds_or_cannot_read_is_said_and_the_view_stays_whole(dataset):
    for text, said in (("40,40", "Nanowell 40,40 is not addressed at T0 or at T1."),
                       ("B3", "'B3' is not a nanowell address: type q,r as the addresses are written (for example "
                              "-2,3), or a ring label such as 3.7.")):
        fig, drawn, st, _ctx = _focus_draw(dataset, "A01", text)
        assert _texts(fig, "nw-identity-focus") == [said]
        assert _by_gid(drawn.axes["map"], "nw-identity-focus-ring") == []
        assert (drawn.axes["map"].get_xlim(), drawn.axes["map"].get_ylim()) == drawn.home["map"]
        assert st.identity_zoom == {}


def test_without_an_address_nothing_is_marked_and_batch_figures_are_unchanged(dataset):
    fig, drawn = _draw(dataset, "A01")
    assert drawn.focus is None and _texts(fig, "nw-identity-focus") == []


def test_the_page_s_nanowell_box_goes_to_the_address_keeps_it_across_timepoints_and_clears(page, qtbot):
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    edit = ctx.widgets.get("_nw_identity_focus_edit")
    assert edit.isVisibleTo(ctx.window) and edit.isEnabled()
    edit.setText("1,0")
    assert st.identity_focus == ""                                          # typing alone goes nowhere
    edit.returnPressed.emit()
    drawn = st.identity_drawn
    ref_site = drawn.pair.ref.sites[(1, 0)]
    assert st.identity_focus == "1,0" and _centre(drawn.axes["map"]) == pytest.approx(ref_site)
    # A redraw keeps it; leaving the timepoint and coming back centres on the address again.
    nwc.redraw(ctx)
    assert _centre(st.identity_drawn.axes["map"]) == pytest.approx(ref_site)
    nwc.set_timepoint(ctx, "T0")                                            # the reference: a message, nothing drawn
    assert st.identity_drawn is None
    nwc.set_timepoint(ctx, "T1")
    assert _centre(st.identity_drawn.axes["map"]) == pytest.approx(ref_site)
    # Diagnostics has no array to go to: the box is greyed and says why.
    _choose(ctx, "_nw_identity_mode_seg", "diagnostics", [k for k, _l in MODES])
    assert not edit.isEnabled() and "not the array" in edit.toolTip()
    _choose(ctx, "_nw_identity_mode_seg", "distance", [k for k, _l in MODES])
    assert edit.isEnabled()
    # Clearing the box goes back to the whole view.
    edit.clear()
    drawn = st.identity_drawn
    assert st.identity_focus == "" and drawn.focus is None
    assert (drawn.axes["map"].get_xlim(), drawn.axes["map"].get_ylim()) == drawn.home["map"]


# ── the address under the pointer ────────────────────────────────────────────
def test_the_pointer_names_the_address_under_it_by_the_shown_frame_s_own_names(dataset):
    _out, _data, ref, other = dataset
    fig, drawn = _draw(dataset, "A01")
    label = drawn.axes["map"]._aw_hover_label
    assert label(*ref.sites[(1, 0)]) == "T0 · nanowell 1,0 · ring 1.0"
    assert label(*ref.sites[(0, -1)]) == "T0 · nanowell 0,-1 · ring 1.4"
    midway = np.add(ref.sites[(0, 0)], ref.sites[(1, 0)]) / 2 + (0.0, 0.3 * ref.pitch)
    assert label(*midway) is None                                           # between nanowells: nothing named
    # Blink on A02, whose compared timepoint calls each nanowell by its neighbour's name: at the reference's 0,0 the
    # reference frame says 0,0 and the compared frame says 1,0 — the wrong name, as that frame labels it.
    _fig, blink = _draw(dataset, "A02", mode="blink", images=_montages(dataset, "A02"))
    label = blink.axes["map"]._aw_hover_label
    assert label(*ref.sites[(0, 0)]) == "T0 · nanowell 0,0 · ring 0.0"
    ir.show_frame(blink, 1)
    assert label(*ref.sites[(0, 0)]) == "T1 · nanowell 1,0 · ring 1.0"
    # Side by side: each montage names its own timepoint's addresses, in its own frame.
    _fig, sides = _draw(dataset, "A01", mode="sides")
    assert sides.axes["compared"]._aw_hover_label(*other.sites[(0, 1)]) == "T1 · nanowell 0,1 · ring 1.1"
    _fig, diag = _draw(dataset, "A01", mode="diagnostics")
    assert not any(hasattr(ax, "_aw_hover_label") for ax in diag.axes.values())


def test_the_canvas_readout_leads_with_what_the_axes_names_under_the_pointer(qtbot):
    from matplotlib.backend_bases import MouseEvent
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure

    from widgets.mpl_toolbar import MplToolbar

    fig = Figure(figsize=(4, 3))
    canvas = FigureCanvasQTAgg(fig)
    ax = fig.add_subplot()
    ax.plot([0, 10], [0, 10])
    ax._aw_hover_label = lambda x, y: "nanowell 2,-1" if x > 5 else None
    toolbar = MplToolbar(canvas)
    qtbot.addWidget(toolbar)
    canvas.draw()
    for at, text in (((8.0, 4.0), "nanowell 2,-1   ·   x = 8   ·   y = 4"), ((2.0, 4.0), "x = 2   ·   y = 4")):
        x, y = ax.transData.transform(at)
        toolbar._on_mouse_move(MouseEvent("motion_notify_event", canvas, x, y))
        assert toolbar._chip.text() == text
    toolbar._chip.hide()


# ── Origin, Go to nanowell, Exclude ──────────────────────────────────────────
def test_origin_goes_to_nanowell_0_0(page, qtbot):
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    ctx.widgets.get("_nw_identity_origin_btn").click()
    assert st.identity_focus == "0,0" and ctx.widgets.get("_nw_identity_focus_edit").text() == "0,0"
    drawn = st.identity_drawn
    assert _centre(drawn.axes["map"]) == pytest.approx(drawn.pair.ref.sites[(0, 0)])


@pytest.mark.parametrize("button", ["_nw_strip_go_to_btn", "_nw_popout_go_to_btn"])
def test_go_to_nanowell_shows_the_crops_nanowell_on_blink_zoomed_onto_it(page, qtbot, button):
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "distribution", [k for k, _l in VIEWS])
    st.selected_sites = [("A02", 1, -1)]
    nwc.pop_out_crops(ctx)                                                  # builds the pop-out and its button
    ctx.widgets.get(button).click()
    assert (st.view, st.well, st.identity_mode, st.identity_focus) == ("identity", "A02", "blink", "1,-1")
    assert ctx.widgets.get("_nw_view_seg").currentData() == "identity"
    qtbot.waitUntil(lambda: st.identity_drawn is not None and st.identity_drawn.mode == "blink", timeout=20000)
    drawn = st.identity_drawn
    assert drawn.pair.well == "A02"
    assert _centre(drawn.axes["map"]) == pytest.approx(drawn.pair.ref.sites[(1, -1)])
    ctx.widgets.get("_nw_popout").hide()


def test_go_to_nanowell_without_a_selection_says_what_to_do(page, qtbot):
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    st.selected_sites = []
    view = st.view
    nwc.go_to_site_in_blink(ctx)
    assert st.view == view and st.identity_focus == ""


def test_a_well_excluded_on_the_band_leaves_every_figure_and_comes_back(page, qtbot):
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    well_cb = ctx.widgets.get("_nw_well_cb")
    well_cb.setCurrentIndex(well_cb.findData("A02"))
    excluded_btn = ctx.widgets.get("_nw_excluded_wells_btn")
    assert not excluded_btn.isVisibleTo(ctx.window)
    ctx.widgets.get("_nw_exclude_well_btn").click()
    assert st.excluded_wells == ["A02"] and nwc.page_wells(ctx) == ["A01"]
    assert [well_cb.itemData(i) for i in range(well_cb.count())] == ["A01"]
    assert st.identity_drawn.pair.well == "A01"
    footer = " ".join(_page_texts(ctx, "nw-identity-footer"))
    assert "Excluded on this page: A02 (restore from Excluded on the band)." in footer
    assert excluded_btn.isVisibleTo(ctx.window) and excluded_btn.text() == "Excluded (1) ▾"
    assert st.to_config()["excluded_wells"] == ["A02"]
    # Excluding the last one empties the page and says why.
    ctx.widgets.get("_nw_exclude_well_btn").click()
    assert nwc.nanowell_empty_reason(ctx).title == "Every selected well is excluded on this page."
    [restore_a01, restore_a02, _sep, restore_all] = excluded_btn.menu().actions()
    assert (restore_a01.text(), restore_a02.text(), restore_all.text()) == (
        "Restore A01", "Restore A02", "Restore every well")
    restore_a02.trigger()
    assert st.excluded_wells == ["A01"] and nwc.page_wells(ctx) == ["A02"]
    excluded_btn.menu().actions()[0].trigger()
    assert st.excluded_wells == [] and nwc.page_wells(ctx) == ["A01", "A02"]
    assert not excluded_btn.isVisibleTo(ctx.window)
    assert "Excluded on this page" not in " ".join(_page_texts(ctx, "nw-identity-footer"))


def test_the_excluded_wells_are_kept_with_the_page_s_settings_and_junk_is_dropped():
    from review.features.nanowell.state import NanowellState

    st = NanowellState()
    st.apply_config({"excluded_wells": ["B02", "A01", "A01", 3, ""]})
    assert st.excluded_wells == ["A01", "B02"]
    st.apply_config({"excluded_wells": "A01"})
    assert st.excluded_wells == []


# ── a timepoint contradicted as a whole (tracker L20) ────────────────────────
#: What ``nanowell_identity.compare`` says when every address lands on itself but the square it registered on is not
#: the reference's: the whole timepoint is contradicted though no distance fails.
SQUARE_ELSEWHERE = ("the square the run accepted at this timepoint is 1.20 pitch from where the reference's square "
                    "correlates: the run took something else for the square")


def _wholly_contradicted(dataset, well="A01"):
    out, _data, _ref, _other = dataset
    payload = ni.read_sidecar(out, well)
    payload["timepoints"]["T1"].update(verdict=ni.CONTRADICTED, reason=SQUARE_ELSEWHERE)
    return payload


def test_a_wholly_contradicted_timepoint_paints_no_nanowell_as_landing_on_itself(dataset):
    """Every nanowell of a contradicted timepoint is left out of every figure, so none is "Lands on itself" (the
    green of a nanowell that is used): those that do land on themselves are their own class, and the wrong names,
    misregistered tiles and the not compared keep theirs."""
    _out, data, ref, other = dataset
    confirmed = ic.status_map(_pair(dataset, "A01"))
    kept = confirmed.members("kept")
    assert kept and confirmed.members("timepoint_out") == set()
    status = ic.status_map(ic.identity_pair(data, "A01", "T1", _wholly_contradicted(dataset)))
    assert status.members("kept") == set() == status.members("imprecise")
    assert status.members("timepoint_out") == kept
    assert status.members("not_compared") == confirmed.members("not_compared")
    # The stage cross-check overriding a partial verdict contradicts the whole timepoint too.
    out = dataset[0]
    payload = ni.read_sidecar(out, "A04")
    payload["stage"] = {"timepoints": {"T1": {"ok": False, "reason": "the stage moved"}}}
    partial = ic.status_map(_pair(dataset, "A04"))
    staged = ic.status_map(ic.identity_pair(data, "A04", "T1", payload))
    assert staged.members("kept") == set() and staged.members("timepoint_out") == partial.members("kept")
    assert staged.members("misregistered") == partial.members("misregistered")


def test_the_page_draws_a_wholly_contradicted_timepoint_crossed_out_and_says_every_nanowell_is_left_out(
        qtbot, dataset, tmp_path):
    import shutil

    from matplotlib.colors import to_rgba

    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_page import _loaded, _page
    from ui.theme import Colors

    pytest.importorskip("PySide6")
    root = tmp_path / "ds"
    shutil.copytree(dataset[0].parent, root)
    out = root / "out"
    payload = _wholly_contradicted(dataset)
    ni.sidecar_path(out, "A01").write_text(json.dumps(payload), encoding="utf-8")
    assert ni.read_sidecar(out, "A01") is not None                        # still current: only the verdict moved
    ctx = _page(qtbot, out)
    ctx.state.selection.selected_wells = {"A01"}
    _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    assert _page_texts(ctx, "nw-identity-title") == ["A01 · T1 vs T0 · contradicted"]
    ax = ctx.widgets.get("_nw_fig").axes
    status = ctx.state.feature_slice("nanowell").identity_drawn.status
    assert status is not None and status.members("kept") == set()
    collections = [c for a in ax for c in a.collections]
    assert not [c for c in collections if c.get_gid() == "nw-identity-status-kept"]
    (crossed,) = [c for c in collections if c.get_gid() == "nw-identity-status-timepoint_out"]
    assert len(crossed.get_offsets()) == len(status.members("timepoint_out")) > 0
    assert crossed.get_hatch() == "xx" and tuple(crossed.get_facecolor()[0]) == pytest.approx(to_rgba(Colors.danger))
    legend = [t.get_text() for a in ax if a.get_legend() for t in a.get_legend().get_texts()]
    assert "Lands on itself (0)" in legend
    assert f"Lands on itself, timepoint left out ({len(status.members('timepoint_out'))})" in legend
    footer = " ".join(_page_texts(ctx, "nw-identity-footer"))
    assert ("T1 is contradicted as a whole, so every one of its nanowells is left out of every figure, those that "
            "land on themselves included (cross-hatched).") in footer, footer
