"""When less was produced than was asked for, something says so.

Each of these paths produced a complete-looking artifact out of an incomplete
computation, and said nothing:

* both aggregation entry points return empty for a CSV with no ``area_px``
  column — correct, since cell area is the gate every downstream statistic is
  defined against, but silently: every plot drew nothing on a dataset that had
  loaded without complaint;
* ``directory_bytes`` counted an unreadable subtree as zero bytes, because
  ``os.walk`` swallows its own errors and the outer handler could never fire —
  and the figure feeds a pre-flight free-space check;
* ``replace_folder_with_archive`` reported ``folder_removed=True`` after an
  ``ignore_errors=True`` rmtree that had not removed anything;
* the smFISH threshold box read an unparseable value as ``0.0``, which is not
  neutral there — it is the lowest possible threshold, so a stray keystroke
  counted every spot in every well;
* Merge Plates bound ``copy_container_members``' dropped-member set to ``_``
  and discarded it, while Concatenate treats the identical event as a
  verification failure;
* Concatenate replaces dataset 2's sample definitions, notes and ratios with
  dataset 1's — the documented rule, applied without a word;
* the SPLOM drew the first six of N features with nothing on the figure saying
  the rest existed.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pandas")

import pandas as pd  # noqa: E402


# ── a CSV with no area column ───────────────────────────────────────────────

@pytest.fixture()
def loud_data_loading(caplog):
    from review.data import data_loading

    data_loading._missing_area_warned.clear()
    caplog.set_level(logging.WARNING)
    return data_loading


def test_a_frame_without_area_px_says_why_nothing_plots(loud_data_loading, caplog):
    df = pd.DataFrame({"timepoint_hours": [1.0], "gfp_mean_intensity": [5.0],
                       "Included": [1]})
    out = loud_data_loading.aggregate_with_threshold_df(df, threshold=0.0)
    assert out == []
    assert any("area_px" in r.getMessage() for r in caplog.records), caplog.text


def test_the_warning_is_once_per_entry_point(loud_data_loading, caplog):
    df = pd.DataFrame({"timepoint_hours": [1.0], "Included": [1]})
    for _ in range(5):
        loud_data_loading.aggregate_with_threshold_df(df, threshold=0.0)
    hits = [r for r in caplog.records if "area_px" in r.getMessage()]
    assert len(hits) == 1, f"{len(hits)} warnings for one missing column"


# ── sizing a tree that cannot be read ───────────────────────────────────────

def test_an_unreadable_subtree_is_reported(tmp_path, caplog, monkeypatch):
    import os

    from util import disk_space

    (tmp_path / "readable").mkdir()
    (tmp_path / "readable" / "a.bin").write_bytes(b"x" * 10)

    real_walk = os.walk

    def _walk(path, onerror=None, **kw):
        if onerror is not None:
            onerror(OSError(13, "Permission denied", str(tmp_path / "locked")))
        return real_walk(path, **kw)

    monkeypatch.setattr(disk_space.os, "walk", _walk)
    caplog.set_level(logging.WARNING)
    total = disk_space.directory_bytes(tmp_path)

    assert total == 10
    assert any("zero bytes" in r.getMessage() for r in caplog.records), caplog.text


# ── a folder that was not actually removed ──────────────────────────────────

def test_folder_removed_means_the_folder_is_gone(tmp_path):
    from util.zip_write import replace_folder_with_archive

    folder = tmp_path / "A01"
    folder.mkdir()
    img = folder / "a.tif"
    img.write_bytes(b"x")

    result = replace_folder_with_archive(folder, [img],
                                         remove_dir=lambda _p: None)
    assert result.folder_removed is False, (
        "the removal hook did nothing and the folder is still on disk, but the "
        "result claimed it had been removed"
    )
    assert folder.exists()


def test_folder_removed_is_true_when_it_really_went(tmp_path):
    from util.zip_write import replace_folder_with_archive

    folder = tmp_path / "A01"
    folder.mkdir()
    img = folder / "a.tif"
    img.write_bytes(b"x")

    result = replace_folder_with_archive(folder, [img])
    assert result.folder_removed is True
    assert not folder.exists()


# ── the smFISH threshold box ────────────────────────────────────────────────

def _smfish_ctx(text, last=None):
    import types

    return types.SimpleNamespace(
        runtime=types.SimpleNamespace(smfish=types.SimpleNamespace(
            threshold_edit=types.SimpleNamespace(text=lambda: text),
            last_valid_threshold=last,
        )),
    )


def test_an_unparseable_threshold_keeps_the_last_good_one(monkeypatch):
    from review.features.smfish import tab_view

    said: list[str] = []
    monkeypatch.setattr(tab_view, "_smfish_set_status",
                        lambda _c, msg: said.append(msg))

    ctx = _smfish_ctx("120", last=None)
    assert tab_view._smfish_get_threshold(ctx) == 120.0
    assert ctx.runtime.smfish.last_valid_threshold == 120.0

    ctx = _smfish_ctx("12o", last=120.0)
    assert tab_view._smfish_get_threshold(ctx) == 120.0, (
        "an unparseable box fell back to 0.0 — the lowest possible threshold, "
        "which counts every spot in every well"
    )
    assert said and "not a number" in said[-1]


# ── Merge Plates refuses to lose members ────────────────────────────────────

def test_merge_plates_fails_when_members_collide(monkeypatch, tmp_path):
    from datasets.merge_plates import apply as mp

    with pytest.raises(mp.MergePlatesError) as ei:
        mp._refuse_dropped("A1", "A01", frozenset({"img_t1.tif"}), "in/A01.zip")
    assert "did not survive" in str(ei.value)
    assert "img_t1.tif" in str(ei.value)


def test_merge_plates_says_nothing_when_nothing_was_dropped():
    from datasets.merge_plates import apply as mp

    assert mp._refuse_dropped("A1", "A01", frozenset(), "in/A01.zip") is None


# ── the SPLOM's dropped features ────────────────────────────────────────────

def test_the_splom_names_the_features_it_did_not_draw():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    from review.features.multivariate import renderer

    rng = np.random.default_rng(1)
    labels = [f"f{i}" for i in range(9)]
    res = {"data": {"X": rng.normal(size=(50, 9)), "feature_labels": labels,
                    "group": np.array(["g"] * 50),
                    "group_colors": {"g": "#4488cc"}}}
    fig = plt.figure()
    try:
        renderer._render_splom(fig, res)
        text = " ".join(t.get_text() for t in fig.texts)
        assert "first 6 of 9" in text, text
        for missing in labels[6:]:
            assert missing in text, f"{missing} was dropped without a word"
    finally:
        plt.close(fig)


def test_a_small_splom_carries_no_footnote():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    from review.features.multivariate import renderer

    rng = np.random.default_rng(1)
    res = {"data": {"X": rng.normal(size=(50, 3)),
                    "feature_labels": ["a", "b", "c"],
                    "group": np.array(["g"] * 50),
                    "group_colors": {"g": "#4488cc"}}}
    fig = plt.figure()
    try:
        renderer._render_splom(fig, res)
        text = " ".join(t.get_text() for t in fig.texts)
        assert "not shown" not in text
    finally:
        plt.close(fig)


# ── Concatenate's discarded dataset-2 blocks ────────────────────────────────

def test_the_plan_records_which_dataset_two_blocks_are_dropped():
    from datasets.concatenate.concatenate_compute import MergePlan

    assert "ds2_blocks_dropped" in MergePlan.__dataclass_fields__
    assert MergePlan.__dataclass_fields__["ds2_blocks_dropped"].default == ()


def _unused(_p: Path, _z=zipfile) -> None:  # keeps the imports honest
    return None
