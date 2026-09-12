"""The threshold a run computed must not be stamped into a different plate.

``_smfish_apply_to_all`` hands the worker an ``after_csv_cb`` that persists the
run's threshold into ``pipeline_info.json``. ``save_threshold`` resolves that
path from ``ctx.state.dataset.data_dir`` *at the moment it is called*, and the
callback fires after a plate-length pass — long enough for the user to have
opened a different dataset in the meantime.

The worker already refuses to write CSVs into a swapped dataset, but it invokes
``after_csv_cb`` either way, because the wells it did write still have to be
picked up. So the CSV half of the run is protected and the sidecar half is not:
the abandoned run's threshold lands in the new plate's sidecar and is reported
nowhere.

These drive the real ``_smfish_apply_to_all`` and assert on the outbound call to
``save_threshold`` — not on any value computed inside the closure, which would
pass happily while the wrong file was being written (CLAUDE.md section 7.4 #1).
"""

from __future__ import annotations

import types

import pytest

from review.features.smfish import tab_view


class _Sig:
    """Stand-in for a Qt signal: records emissions, invokes queued callables."""

    def __init__(self, invoke: bool = False):
        self.emitted = []
        self._invoke = invoke

    def emit(self, *args):
        self.emitted.append(args)
        if self._invoke and args and callable(args[0]):
            args[0]()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """A ctx thin enough to build here, driving the real entry point."""
    plate_a = tmp_path / "plate_a"
    plate_b = tmp_path / "plate_b"
    for p in (plate_a, plate_b):
        p.mkdir()

    captured = {}

    def _fake_async(**kwargs):
        captured.update(kwargs)

    saved = []

    def _fake_save_threshold(ctx, channel, thr):
        saved.append((getattr(ctx.state.dataset, "data_dir", None), channel, thr))

    monkeypatch.setattr(tab_view, "apply_global_threshold_async", _fake_async)
    monkeypatch.setattr(tab_view, "_smfish_show_progress", lambda *a, **k: None)
    monkeypatch.setattr(tab_view, "_smfish_refresh_app_cache", lambda *a, **k: None)
    monkeypatch.setattr(tab_view.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(tab_view.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    import review.persistence.smfish as persist
    monkeypatch.setattr(persist, "save_threshold", _fake_save_threshold)

    smfish = types.SimpleNamespace(
        out_dir=plate_a,
        apply_running=False,
        channel_cb=types.SimpleNamespace(currentText=lambda: "w3cy5"),
        frame=None,
        cancel_event=None,
        well_to_zip={"A01": plate_a / "A01.zip"},
        classifier=object(),
        fov_tp_extractor=object(),
        bridge=types.SimpleNamespace(
            status=_Sig(), done=_Sig(), progress=_Sig(),
            # after_csv is queued to the GUI thread; invoking straight through
            # is what the real bridge does once it lands there.
            after_csv=_Sig(invoke=True),
        ),
    )
    ctx = types.SimpleNamespace(
        runtime=types.SimpleNamespace(smfish=smfish),
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(data_dir=plate_a, well_paths={}, cache={}),
        ),
    )
    monkeypatch.setattr(tab_view, "_smfish_get_threshold", lambda _ctx: 12.5)
    monkeypatch.setattr(tab_view, "_smfish_set_status", lambda *a, **k: None)
    return ctx, captured, saved, plate_a, plate_b


def test_the_threshold_is_persisted_when_the_dataset_did_not_change(harness):
    ctx, captured, saved, plate_a, _plate_b = harness
    tab_view._smfish_apply_to_all(ctx)
    captured["after_csv_cb"](0)
    assert saved == [(plate_a, "w3cy5", 12.5)]


def test_a_dataset_swap_mid_run_blocks_the_stamp(harness, caplog):
    ctx, captured, saved, _plate_a, plate_b = harness
    tab_view._smfish_apply_to_all(ctx)
    # The user opens another plate while the worker is still going.
    ctx.state.dataset.data_dir = plate_b
    with caplog.at_level("WARNING", logger="review"):
        captured["after_csv_cb"](0)
    assert saved == [], "the abandoned run stamped its threshold into the new plate"
    # And it must not be silent — a skipped persist the user cannot see is the
    # same class of defect as the wrong write.
    assert any("not persisted" in r.getMessage() for r in caplog.records)
