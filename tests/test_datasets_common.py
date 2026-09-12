"""Qt-free tests for the shared Datasets-pane helpers under ``datasets/core``.

Complements ``tests/test_concatenate_compute.py`` (which exercises these
through Concatenate's re-exports) with direct coverage of the generalised
API: multi-marker destination-state predicates and the field-agnostic
``rewrite_member_field``.
"""

from __future__ import annotations

import zipfile

import pytest

from datasets.core import containers as ct
from datasets.core import dataset_paths as dp
from datasets.core import member_names as mn
from tests.fixtures.dataset_builder import build_dataset
from util.timepoint import parse_hours
from util.well_token import canonical_well_label
from domain.image_identity import load_schema


def _schema():
    return load_schema({
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "separator": "_",
    })


# ── dataset_paths ────────────────────────────────────────────────────────────
def test_well_from_stem_pads_and_uppercases():
    assert dp.well_from_stem("measurements_a1") == "A01"
    assert dp.well_from_stem("measurements_B10") == "B10"
    assert dp.well_from_stem("no_well_here") is None


def test_resolve_dataset_requires_in_and_out(tmp_path):
    with pytest.raises(dp.DatasetPathsError, match="not a dataset"):
        dp.resolve_dataset(tmp_path, label="dataset")


def test_dataset_wells_union_of_in_and_out(tmp_path):
    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0", "24h"])
    paths = dp.resolve_dataset(tmp_path, label="d")
    assert dp.dataset_wells(paths) == {"A01", "B02"}


def test_dataset_wells_includes_folder_mode_output(tmp_path):
    """Regression guard: dataset_wells's own docstring claims "every 96-well
    position present in a dataset's in or out", but its body used to union
    only in_containers/out_csvs/out_zips -- never out_folders (the
    compressed_output=False layout out_folders() reads, see
    has_folder_mode_output). A well present only as a folder-mode out/<WELL>/
    -- no zip, no CSV -- was silently missing from the result."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0"])
    (tmp_path / "out" / "H12").mkdir()  # folder-mode QC output, no zip/CSV
    paths = dp.resolve_dataset(tmp_path, label="d")
    assert dp.dataset_wells(paths) == {"A01", "H12"}


def test_out_csvs_duplicate_prefix_overwrites_by_glob_order(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0"], csv_prefix="measurements")
    (tmp_path / "out" / "run2_A01.csv").write_text("well,fov,timepoint,timepoint_hours\n")
    paths = dp.resolve_dataset(tmp_path, label="d")
    csvs = dp.out_csvs(paths)
    # Exactly one of the two candidate CSVs survives under the "A01" key —
    # documents the known limitation callers that care about a mixed-prefix
    # out/ folder (Validate) must not rely on this map for that purpose.
    assert csvs["A01"].name in ("measurements_A01.csv", "run2_A01.csv")
    assert len(list((tmp_path / "out").glob("*.csv"))) == 2


def test_destination_state_predicates_are_per_marker(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "in").mkdir(parents=True)
    (dest_root / "in" / "A01.zip").touch()
    dest = dp.DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")

    assert dp.destination_has_content(dest)
    assert not dp.destination_has_marker(dest, ".aw_subset_in_progress")
    assert dp.destination_has_unrecognized_content(dest, ".aw_subset_in_progress")

    (dest_root / ".aw_subset_in_progress").write_text("")
    assert dp.destination_has_marker(dest, ".aw_subset_in_progress")
    assert not dp.destination_has_marker(dest, ".aw_merge_in_progress")
    assert dp.destination_is_failed_run(dest, ".aw_subset_in_progress")
    assert not dp.destination_has_unrecognized_content(dest, ".aw_subset_in_progress")

    (dest_root / "out").mkdir()
    (dest_root / "out" / "pipeline_info.json").write_text("{}")
    assert dp.destination_is_complete_dataset(dest)
    assert not dp.destination_is_failed_run(dest, ".aw_subset_in_progress")


# ── containers ───────────────────────────────────────────────────────────────
def test_read_members_zip_and_folder_agree(tmp_path):
    zpath = tmp_path / "A01.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("a.tif", b"one")
        zf.writestr("b.tif", b"two")
    assert sorted(ct.read_members(zpath)) == [("a.tif", b"one"), ("b.tif", b"two")]

    folder = tmp_path / "A02"
    folder.mkdir()
    (folder / "a.tif").write_bytes(b"one")
    (folder / "b.tif").write_bytes(b"two")
    assert sorted(ct.read_members(folder)) == [("a.tif", b"one"), ("b.tif", b"two")]


def test_write_zip_drops_collisions_and_reports_them(tmp_path):
    dest = tmp_path / "out.zip"
    written, dropped = ct.write_zip(dest, [("a.tif", b"1"), ("a.tif", b"2"), ("b.tif", b"3")])
    assert written == {"a.tif", "b.tif"}
    assert dropped == {"a.tif"}
    with zipfile.ZipFile(dest) as zf:
        assert zf.read("a.tif") == b"1"  # first write wins


# ── per-member progress ──────────────────────────────────────────────────────
def test_read_members_reports_one_progress_call_per_member(tmp_path):
    """Both container layouts report the same ``(done, total)`` sequence, with
    the total known from the first call — the whole point of reading it from
    ``infolist()`` (or the folder walk) up front rather than counting as we go."""
    zpath = tmp_path / "A01.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("a.tif", b"one")
        zf.writestr("b.tif", b"two")
        zf.writestr("sub/", b"")  # directory entry — not a member
    seen: list[tuple[int, int]] = []
    ct.read_members(zpath, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]

    folder = tmp_path / "A02"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.tif").write_bytes(b"one")
    (folder / "sub" / "b.tif").write_bytes(b"two")
    seen.clear()
    ct.read_members(folder, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]


def test_write_zip_reports_progress_for_dropped_members_too(tmp_path):
    """A collision is skipped as a *write* but still consumed as a step
    through the caller's list, so the dial must not stall on it — otherwise a
    well whose members all collided would never reach full."""
    seen: list[tuple[int, int]] = []
    ct.write_zip(
        tmp_path / "out.zip",
        [("a.tif", b"1"), ("a.tif", b"2"), ("b.tif", b"3")],
        progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 3), (2, 3), (3, 3)]


# ── WellFraction ─────────────────────────────────────────────────────────────
def test_well_fraction_sweeps_zero_to_full_once_across_read_and_write():
    """The reason this class exists: two passes over the same well must fill
    one dial once, not fill it twice. With two equally weighted slices the
    read half lands on 50% and the write half carries it to 100%."""
    seen: list[tuple[str, int, int]] = []
    frac = ct.WellFraction(lambda w, d, t: seen.append((w, d, t)), "A01", 2)

    on_read = frac.slice_progress()
    on_read(1, 4)
    on_read(4, 4)
    assert seen[-1] == ("A01", 50, 100)

    on_write = frac.slice_progress()
    on_write(2, 4)
    on_write(4, 4)
    assert seen[-1] == ("A01", 100, 100)

    values = [done for _w, done, _t in seen]
    assert values == sorted(values)  # monotonic — never restarts mid-well


def test_well_fraction_skipped_and_unclaimed_slices_still_reach_full():
    """A pass with nothing to do (no QC archive, a filter that kept nothing, a
    byte-for-byte copy) owns its slice anyway, and ``finish`` closes out
    whatever is left — a finished well must read as finished, never as stuck
    part-way."""
    seen: list[tuple[str, int, int]] = []
    frac = ct.WellFraction(lambda w, d, t: seen.append((w, d, t)), "B02", 4)
    frac.skip(2)
    assert seen[-1] == ("B02", 50, 100)
    frac.finish()
    assert seen[-1] == ("B02", 100, 100)


def test_well_fraction_deduplicates_to_whole_steps():
    """A well holding thousands of files must not emit thousands of queued
    signals and plate repaints — updates collapse onto the 100-step scale."""
    seen: list[int] = []
    frac = ct.WellFraction(lambda _w, done, _t: seen.append(done), "C03", 1)
    on_read = frac.slice_progress()
    for i in range(1, 5001):
        on_read(i, 5000)
    assert len(seen) <= 101
    assert seen[-1] == 100


def test_clear_destination_removes_in_and_out(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "in").mkdir(parents=True)
    (dest_root / "out").mkdir(parents=True)
    (dest_root / "in" / "A01.zip").write_bytes(b"x")
    dest = dp.DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")
    ct.clear_destination(dest)
    assert not dest.in_dir.exists()
    assert not dest.out_dir.exists()


# ── member_names ─────────────────────────────────────────────────────────────
def test_rewrite_member_field_timepoint_shift():
    schema = _schema()

    def _shift(old_token):
        hours = parse_hours(old_token)
        if hours is None:
            return None
        return f"{hours + 24:g}h"

    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_A01_F001_0h.tif", schema=schema, channels=(), field="timepoint",
        new_value=_shift,
    )
    assert rewrote
    assert new_name == "exp_GFP_A01_F001_24h.tif"


def test_rewrite_member_field_well_remap():
    schema = _schema()

    def _remap(old_token):
        if canonical_well_label(old_token) != "A01":
            return None
        return "E01"

    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_A01_F001_0h.tif", schema=schema, channels=(), field="well",
        new_value=_remap,
    )
    assert rewrote
    assert new_name == "exp_GFP_E01_F001_0h.tif"

    # A member sitting in the wrong well's archive refuses rather than guesses.
    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_B02_F001_0h.tif", schema=schema, channels=(), field="well",
        new_value=_remap,
    )
    assert not rewrote
    assert new_name == "exp_GFP_B02_F001_0h.tif"


def test_rewrite_member_field_refuses_non_conformant_names():
    schema = _schema()
    new_name, rewrote = mn.rewrite_member_field(
        "notes.tif", schema=schema, channels=(), field="timepoint",
        new_value=lambda old: "99h",
    )
    assert not rewrote
    assert new_name == "notes.tif"

    new_name, rewrote = mn.rewrite_member_field(
        "readme.txt", schema=schema, channels=(), field="timepoint",
        new_value=lambda old: "99h",
    )
    assert not rewrote


def test_rewrite_member_field_preserves_channel_scoped_role_suffix():
    schema = _schema()
    new_name, rewrote = mn.rewrite_member_field(
        "exp_GFP_A01_F001_0h_tophat_gfp.tif", schema=schema, channels=("gfp",),
        field="timepoint", new_value=lambda old: "24h",
    )
    assert rewrote
    assert new_name == "exp_GFP_A01_F001_24h_tophat_gfp.tif"


def test_shorten_headline_replaces_full_path_with_basename():
    text = "Concatenated 3 well(s) into /home/user/data/plate7_merged (shifted +24h)."
    assert dp.shorten_headline(text, "/home/user/data/plate7_merged") == (
        "Concatenated 3 well(s) into plate7_merged (shifted +24h)."
    )


def test_shorten_headline_shortens_a_path_prefix_even_when_a_suffix_follows():
    # Import Loose's summary embeds dest_in (dest_root + "/in"), a longer
    # string than dest_root itself -- str.replace still finds it as a prefix.
    text = "Wrote 2 well(s) into /home/user/data/dest/in."
    assert dp.shorten_headline(text, "/home/user/data/dest") == "Wrote 2 well(s) into dest/in."


def test_shorten_headline_ignores_empty_paths():
    # In-place Import Loose emits dest_root="" -- "".replace(...) would
    # otherwise insert the basename between every character of the text.
    text = "Grouped loose TIFs in /home/user/data/loose into 2 per-well zip(s)."
    assert dp.shorten_headline(text, "", "/home/user/data/loose") == (
        "Grouped loose TIFs in loose into 2 per-well zip(s)."
    )


def test_progress_dial_paints_the_state_colour_for_the_done_part(qtbot):
    """The per-well dial is a two-tone pie of the well's own state colour. The
    done wedge is that colour — the same orange a solid processing well has
    always been — and the remainder is a lighter tint of it, both shaded by the
    same sphere gradient a plain well gets. The earlier form was inverted
    (done = darker(210), remainder = lighter(130)) and flat-filled, so a well
    that started progressing turned muddy brown AND changed from a sphere into
    a flat disc — reading as a different kind of object rather than the same
    well further along."""
    from PySide6.QtGui import QColor

    from ui import theme
    from widgets.well_plate_selector import _PlateGrid

    grid = _PlateGrid()
    qtbot.addWidget(grid)

    base = QColor(theme.Colors.warn)
    done, remainder = _dial_tones(grid, base)

    # Both halves carry the state's hue, shaded as a sphere — so neither is an
    # exact match for the flat base colour, and the assertions are about the
    # relationship rather than a literal value.
    assert done.hueF() == pytest.approx(base.hueF(), abs=0.02), "done keeps the hue"
    assert remainder.hueF() == pytest.approx(base.hueF(), abs=0.02), "so does the rest"
    assert remainder.lightnessF() > done.lightnessF(), "the rest is the lighter tint"
    # The inversion this replaced: the done part must not be the dark one. The
    # old finished tone was base.darker(210) — a muddy brown well below this.
    assert done.lightnessF() > QColor(base).darker(210).lightnessF()


def _dial_tones(grid, base):
    """Render a half-full dial and sample the two halves off the pixels.

    Sampling the real paint rather than intercepting brush calls: the dial is
    drawn clockwise from twelve o'clock, so at fraction 0.5 the right half is
    the done wedge and the left half the remainder. Both points sit well inside
    the rim.
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    img = QImage(40, 40, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        grid._paint_fraction(painter, QRectF(2, 2, 36, 36), QColor(base), 0.5, False)
    finally:
        painter.end()
    done = QColor(img.pixelColor(28, 20))       # right of centre
    remainder = QColor(img.pixelColor(12, 20))  # left of centre
    return done, remainder


def test_progress_dial_is_the_same_shape_as_a_plain_well(qtbot):
    """A well that starts progressing must not change what kind of object it
    is. The dial was flat-filled while every other well got a radial gradient,
    a rim at 0.04x width and a specular arc — so a progressing well appeared as
    a flat disc with an outer band beside spherical grey neighbours. Both now
    go through the same `_sphere_gradient` / `_paint_specular` helpers, so a
    dial is shaded, rimmed and highlighted like the well it replaces.

    "Like", not "identically": the dial's relief is damped, because the sphere's
    highlight was compressing its two tones together until the completed portion
    could not be told from the unfinished one. See the assertions below.
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter

    from ui import theme
    from widgets.well_plate_selector import _PlateGrid

    grid = _PlateGrid()
    qtbot.addWidget(grid)
    base = QColor(theme.Colors.warn)
    rect = QRectF(2, 2, 36, 36)

    def render(paint):
        img = QImage(40, 40, QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        try:
            paint(painter)
        finally:
            painter.end()
        return img

    plain = render(lambda pr: grid._paint_lit(pr, rect, base, False))
    # A fully-done dial: the whole face is the done tone, so it should render
    # as the plain well does rather than as a flat disc.
    full = render(lambda pr: grid._paint_fraction(pr, rect, base, 1.0, False))

    # Shaded, not flat: the lit upper-left is lighter than the lower-right in
    # both. The dial is deliberately *gentler* about it.
    def shading(img):
        return (QColor(img.pixelColor(14, 14)).lightnessF()
                - QColor(img.pixelColor(26, 26)).lightnessF())

    assert shading(plain) > 0.02, "a plain well is shaded as a sphere"
    assert shading(full) > 0.02, "and so is a dial — it is not a flat disc"
    # ...but not as much. A dial draws two tones in the same pixels, and the
    # sphere's highlight blends toward white, which compresses two tones
    # together: at the plain well's full relief the done and remaining halves of
    # a green well came within 25 luminance of each other under the highlight,
    # and the completed wedge read as washed pale. The dial gives up relief
    # (``_PlateGrid._DIAL_RELIEF``) so the boundary survives; a plain well has
    # no boundary to lose and keeps all of it. Asserted rather than tolerated,
    # so re-unifying the two cannot happen quietly.
    assert shading(full) < shading(plain), (
        "a dial trades relief for a legible boundary")
    assert shading(full) > shading(plain) * 0.25, (
        "but not so much that it stops reading as the same kind of object")
    # And the specular highlight is present in both.
    assert QColor(full.pixelColor(20, 8)).lightnessF() > \
        QColor(full.pixelColor(20, 20)).lightnessF()
