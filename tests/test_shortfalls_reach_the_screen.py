"""A shortfall the software has detected must reach the person looking at it.

Three of the review's remaining items share a shape. In each, the code already
*knows* it produced less than it was asked for — the inventory carries a
``partial`` flag, the projector returns the wells it could not judge, the
threshold map comes back ``None`` — and in each the knowledge stops one call
short of the screen. A verdict that exists and cannot be seen is worth very
little more than no verdict.

* **item 5** — ``ScanResult.partial`` says an archive or folder scan stopped
  part way. The inventory it returns holds real images and is an ordinary dict,
  so the Review Image FOV/timepoint menus and the Image Table grid built from it
  are simply short, and read as a complete picture of a smaller well.
* **item 6** — ``project_verdict_column`` drops ``QC_Pass`` / ``Crop_Pass`` from
  a frame whose verdict raised, which every consumer reads as *all-pass*. Those
  wells stop being gated at the moment the user changes the gate, and nothing
  said so.
* **item 7** — a threshold map that could not be *evaluated* was coerced to
  ``{}`` and painted as the same all-black silhouette the renderer paints for
  "measured, and no cell passed".

Every assertion here is on the outbound call — the text handed to the status
label, the message handed to the notification service, the pixels handed to the
display — and not on the value computed behind it. A test that reads the
internal value passes happily while the screen stays wrong.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6")

from well_viewer.data.zip_members import ScanResult


# ── a status label that records what it was told ─────────────────────────────


class _Status:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def setText(self, text) -> None:
        self.texts.append(str(text))

    @property
    def last(self) -> str:
        return self.texts[-1] if self.texts else ""


class _Widgets:
    def __init__(self, **named) -> None:
        self._named = named

    def get_or_none(self, name):
        return self._named.get(name)


def _render_ctx(*, fluor=None, binary=False):
    """The ctx ``draw_review_image`` reads, with no Qt objects in it.

    ``runtime.review_image.label`` is None, so ``render_review_image_display``
    returns before touching a pixmap; everything the status line depends on is
    still exercised.
    """
    status = _Status()
    state = SimpleNamespace(
        preview=SimpleNamespace(
            fluor=ScanResult() if fluor is None else fluor,
            overlay=ScanResult(), mask=ScanResult(), tophat_fluor=ScanResult(),
            selected_well="A01",
        ),
        review_image=SimpleNamespace(
            binary_mask=binary, selected_nucleus=None, tint_color=(255, 255, 255),
            show_outline=False, boundary_color=(0, 255, 0),
            selected_color=(255, 0, 0), zoom=1.0, pan_x=0.0, pan_y=0.0,
            base_pil=None, last_fluor_arr=None, include_edit_mode=False,
            crop_edit_mode=False, preserve_view_on_refresh=False,
        ),
        image_view=SimpleNamespace(lut_by_channel={}),
        channel=SimpleNamespace(
            active_image_channel="gfp", active_val_col="gfp_mean_intensity",
            active_channel="gfp", fluor_channels=["gfp"],
            active_metric="mean_intensity", ratio_index={}, threshold=-1.0,
        ),
        dataset=SimpleNamespace(cache={}, well_paths={}, pipeline_info={},
                                crop_region=None),
        cell_gating=SimpleNamespace(fluor_gates={}, thresh_frac_on={}),
    )
    runtime = SimpleNamespace(review_image=SimpleNamespace(
        label=None, canvas=None, base_rgb=None, scale=None, color_swatches={}))
    ctx = SimpleNamespace(
        state=state, runtime=runtime,
        widgets=_Widgets(_review_image_status=status),
        window=SimpleNamespace(),
        notify=SimpleNamespace(set_status=lambda *_a, **_k: None),
    )
    return ctx, status


_ARR = np.array([[10.0, 200.0], [30.0, 40.0]], dtype=np.float32)
_MASK = np.array([[1, 1], [2, 2]], dtype=np.int32)


# ── item 5: a truncated image scan must say so where the user is looking ─────


def test_a_complete_scan_leaves_the_review_image_status_alone():
    from well_viewer.features.image_browser.review_image import renderer as R

    ctx, status = _render_ctx(fluor=ScanResult({("1", "0"): "ref"}))
    R.draw_review_image(ctx, _ARR, _MASK, {})
    assert status.last == "Showing channel GFP with included cell boundaries."


def test_a_truncated_scan_says_so_on_the_review_image_status_line():
    from well_viewer.features.image_browser.review_image import renderer as R

    truncated = ScanResult({("1", "0"): "ref"})
    truncated.mark_partial("scanning A01.zip failed: Bad CRC-32")
    ctx, status = _render_ctx(fluor=truncated)
    R.draw_review_image(ctx, _ARR, _MASK, {})
    # The inventory is a perfectly ordinary one-frame dict — the FOV menu built
    # from it says "1" and looks right. Only this line can contradict it.
    assert "INCOMPLETE" in status.last
    assert "Bad CRC-32" in status.last


def test_the_mask_scan_being_truncated_is_reported_too():
    """Any of the four inventories being short makes the frame suspect."""
    from well_viewer.features.image_browser.review_image import renderer as R

    ctx, status = _render_ctx(fluor=ScanResult({("1", "0"): "ref"}))
    ctx.state.preview.mask.mark_partial("scanning A01_out.zip failed: truncated")
    R.draw_review_image(ctx, _ARR, _MASK, {})
    assert "INCOMPLETE" in status.last
    assert "truncated" in status.last


class _Combo:
    def __init__(self) -> None:
        self.values: list[str] = []
        self._text = ""

    def clear(self):
        self.values = []

    def addItems(self, items):
        self.values.extend(items)

    def currentText(self):
        return self._text

    def setCurrentText(self, t):
        self._text = str(t)

    def blockSignals(self, blocked):
        return False

    def count(self):
        return len(self.values)


class _Label:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, t):
        self.text = str(t)


def test_a_well_whose_scan_read_nothing_says_so_instead_of_the_previous_well(
        monkeypatch):
    """The "no images" path writes no status at all before this.

    It returns before the draw that would normally rewrite the line, so the
    canvas showed the newly-selected well while the line under it still
    described the previous one — and "this well has no images" and "the scan
    failed before it read one" arrived as the same silence.
    """
    from well_viewer.features.image_browser.review_image import renderer as R

    empty_but_failed = ScanResult()
    empty_but_failed.mark_partial("B02.zip could not be opened")
    monkeypatch.setattr(
        "well_viewer.data.image_discovery.find_well_images_and_masks",
        lambda *a, **k: (empty_but_failed, ScanResult(), ScanResult(), ScanResult()),
    )

    status = _Status()
    widgets = _Widgets(
        _review_image_status=status,
        _preview_well_lbl=_Label(), _review_image_well_lbl=_Label(),
        _review_image_fov_menu=_Combo(), _review_image_tp_menu=_Combo(),
    )
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            preview=SimpleNamespace(fluor={}, overlay={}, mask={},
                                    tophat_fluor={}, selected_well=None,
                                    update_in_progress=False),
            review_image=SimpleNamespace(preserve_view_on_refresh=False),
            channel=SimpleNamespace(active_image_channel="gfp"),
            dataset=SimpleNamespace(data_dir=None, in_dir=None,
                                    pipeline_info={}, fov_tp_extractor=None),
        ),
        widgets=widgets,
    )
    R._update_preview_impl(ctx, "B02")

    assert "No images could be read for B02." in status.last
    assert "B02.zip could not be opened" in status.last


def _loader_ctx(data_dir):
    return SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(
        data_dir=data_dir, in_dir=None, pipeline_info={}, fov_tp_extractor=None)))


def test_load_well_channel_reports_a_truncated_inventory_to_its_caller(monkeypatch):
    """The data layer's outbound call: the caller's pass-scoped set."""
    import well_viewer.data.image_discovery as ID
    from well_viewer.data.frame_load import load_well_channel

    truncated = ScanResult({("1", "0"): "ref"})
    truncated.mark_partial("scanning A01.zip failed: Bad CRC-32")
    monkeypatch.setattr(
        ID, "find_well_images_and_masks",
        lambda *a, **k: (truncated, ScanResult(), ScanResult(), ScanResult()),
    )
    partials: set = set()
    out = load_well_channel(_loader_ctx(None), {}, "A01", "gfp",
                            partial_scans=partials)
    assert out == {("1", "0"): "ref"}          # the images it did find are real
    assert [key for key, _r in partials] == [("A01", "gfp", "raw")]
    assert "Bad CRC-32" in next(r for _k, r in partials)


def test_a_cache_hit_still_reports_the_truncation(monkeypatch):
    """The flag rides on the cached inventory, and every cell drawn from it is
    equally short — so a second pass must not read as clean."""
    import well_viewer.data.image_discovery as ID
    from well_viewer.data.frame_load import load_well_channel

    truncated = ScanResult({("1", "0"): "ref"})
    truncated.mark_partial("scanning A01.zip failed: Bad CRC-32")
    monkeypatch.setattr(
        ID, "find_well_images_and_masks",
        lambda *a, **k: (truncated, ScanResult(), ScanResult(), ScanResult()),
    )
    cache: dict = {}
    load_well_channel(_loader_ctx(None), cache, "A01", "gfp", partial_scans=set())
    second: set = set()
    load_well_channel(_loader_ctx(None), cache, "A01", "gfp", partial_scans=second)
    assert [key for key, _r in second] == [("A01", "gfp", "raw")]


def test_a_complete_inventory_records_nothing(monkeypatch):
    import well_viewer.data.image_discovery as ID
    from well_viewer.data.frame_load import load_well_channel

    monkeypatch.setattr(
        ID, "find_well_images_and_masks",
        lambda *a, **k: (ScanResult({("1", "0"): "ref"}), ScanResult(),
                         ScanResult(), ScanResult()),
    )
    partials: set = set()
    load_well_channel(_loader_ctx(None), {}, "A01", "gfp", partial_scans=partials)
    assert partials == set()


def test_the_image_table_reader_names_the_wells_and_the_reason():
    from well_viewer.features.image_browser.image_table.image_compose import (
        partial_scan_report,
    )

    st = SimpleNamespace(partial_scans={
        (("A01", "gfp", "raw"), "scanning A01.zip failed: Bad CRC-32"),
        (("A01", "dapi", "raw"), "scanning A01.zip failed: Bad CRC-32"),
        (("B02", "gfp", "raw"), "scanning B02.zip failed: truncated"),
    })
    ctx = SimpleNamespace(state=SimpleNamespace(image_table=st))
    wells, reasons = partial_scan_report(ctx)
    assert wells == ["A01", "B02"]
    assert len(reasons) == 2


# ── item 7: unmeasured is not "nothing passed" ───────────────────────────────


def _white_pixels(ctx) -> int:
    rgb = ctx.runtime.review_image.base_rgb
    return int((rgb == 255).all(axis=2).sum())


def test_a_measured_threshold_with_no_passing_cell_still_draws_black():
    """The case the all-black silhouette is the RIGHT answer for."""
    from well_viewer.features.image_browser.review_image import renderer as R

    ctx, status = _render_ctx(binary=True)
    R.draw_review_image(ctx, _ARR, _MASK, {}, above_by_nid={1: False, 2: False})
    assert _white_pixels(ctx) == 0
    assert status.last.startswith("Binary mask: cells above threshold")


def test_an_unmeasured_threshold_does_not_draw_as_nothing_passing():
    from well_viewer.features.image_browser.review_image import renderer as R

    measured, _ = _render_ctx(binary=True)
    R.draw_review_image(measured, _ARR, _MASK, {},
                        above_by_nid={1: False, 2: False})

    unmeasured, status = _render_ctx(binary=True)
    R.draw_review_image(unmeasured, _ARR, _MASK, {}, above_by_nid=None)

    # Byte-identical frames for a measurement and a non-measurement is the
    # defect; the two must differ on screen as well as in the status line.
    assert not np.array_equal(measured.runtime.review_image.base_rgb,
                              unmeasured.runtime.review_image.base_rgb)
    assert "UNAVAILABLE" in status.last
    assert "could not be evaluated" in status.last


def test_a_passing_cell_is_still_drawn_white():
    from well_viewer.features.image_browser.review_image import renderer as R

    ctx, _status = _render_ctx(binary=True)
    R.draw_review_image(ctx, _ARR, _MASK, {}, above_by_nid={1: True, 2: False})
    assert _white_pixels(ctx) == 2


# ── item 6: a verdict that could not be re-projected reaches the user ────────


class _Notify:
    def __init__(self) -> None:
        self.status: list[str] = []
        self.toasts: list[tuple[str, str]] = []

    def set_status(self, message) -> None:
        self.status.append(str(message))

    def toast(self, message, *, kind="info", msec=3500) -> None:
        self.toasts.append((kind, str(message)))


def _verdict_ctx(cache):
    notify = _Notify()
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(
                cache=cache, crop_region=None, crop_scope_wells=None,
                crop_scope_fovs=None, crop_scope_tps=None, pipeline_info={},
            ),
            qc=SimpleNamespace(params=None),
        ),
        runtime=SimpleNamespace(qc=SimpleNamespace(changed_cb=None)),
        notify=notify, persistence=None, window=None,
    )
    return ctx, notify


def _frames():
    """Two wells: one the verdict can read, one it cannot.

    The centroid columns are real because ``set_crop_region`` opens a modal
    dialog for a dataset that has none, which is a different branch entirely.
    """
    common = {"nucleus_id": [1, 2], "Included": [1, 1],
              "shape_centroid_row": [1.0, 2.0], "shape_centroid_col": [1.0, 2.0]}
    good = pd.DataFrame({**common, "shape_area": [10.0, 20.0]})
    bad = pd.DataFrame({**common, "shape_area": ["x", "y"]})
    return {"A01": good, "A02": bad}


def test_a_qc_reprojection_failure_names_the_well_on_screen(monkeypatch, caplog):
    from well_viewer.features.qc import controller as QC
    from well_viewer.features.qc.params import QCParams

    real = QC.compute_qc_pass

    def flaky(df, params):
        if list(df["shape_area"]) == ["x", "y"]:
            raise KeyError("shape_area is not numeric")
        return real(df, params)

    monkeypatch.setattr(QC, "compute_qc_pass", flaky)
    ctx, notify = _verdict_ctx(_frames())
    ctx.state.qc.params = QCParams(enabled=True, min_area=5.0)
    with caplog.at_level(logging.ERROR, logger="well_viewer"):
        QC.recompute_qc_all(ctx)

    # The frame's own signal (the column is gone) is right and invisible: every
    # consumer reads an absent QC_Pass as all-pass.
    assert "QC_Pass" not in ctx.state.dataset.cache["A02"].columns
    assert any("A02" in m and "QC_Pass" in m for m in notify.status)
    assert any(kind == "error" and "A02" in m for kind, m in notify.toasts)


def test_a_clean_qc_reprojection_says_nothing(monkeypatch):
    from well_viewer.features.qc import controller as QC
    from well_viewer.features.qc.params import QCParams

    ctx, notify = _verdict_ctx({"A01": _frames()["A01"]})
    ctx.state.qc.params = QCParams(enabled=True, min_area=5.0)
    QC.recompute_qc_all(ctx)
    assert notify.status == [] and notify.toasts == []


def test_a_crop_reprojection_failure_survives_the_status_written_after_it(
        monkeypatch, caplog):
    """``set_crop_region`` writes its own outcome line one call later.

    Reporting the failure and then being overwritten within the same handler is
    the same defect one layer along, so the warning has to be part of the line
    that ends up on screen.
    """
    from well_viewer.features.crop import controller as CR

    def boom(_df, _region, _scope, well):
        if well == "A02":
            raise KeyError("shape_centroid_row")
        return pd.Series([1, 1])

    monkeypatch.setattr(CR, "compute_crop_pass", boom)
    ctx, notify = _verdict_ctx(_frames())
    with caplog.at_level(logging.ERROR, logger="well_viewer"):
        CR.set_crop_region(ctx, (0, 0, 4, 4))

    assert any(kind == "error" and "A02" in m for kind, m in notify.toasts)
    # The LAST status line — the one the user is left looking at — still carries
    # it, alongside the crop's own outcome.
    assert "Crop region set" in notify.status[-1]
    assert "A02" in notify.status[-1] and "Crop_Pass" in notify.status[-1]


def test_a_clean_crop_says_only_its_own_outcome(monkeypatch):
    from well_viewer.features.crop import controller as CR

    monkeypatch.setattr(CR, "compute_crop_pass",
                        lambda _df, _r, _s, _w: pd.Series([1, 1]))
    ctx, notify = _verdict_ctx({"A01": _frames()["A01"]})
    CR.set_crop_region(ctx, (0, 0, 4, 4))
    assert "Crop region set" in notify.status[-1]
    assert "WARNING" not in notify.status[-1]
