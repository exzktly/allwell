"""The kept cell masks (``pipeline.nanowell_masks``): a segmenter call is answered from them only for the same input.

Unit tests over ``MaskStore`` with a counting stand-in segmenter. The run end to end — a well re-run after an anchor
was set segments nothing again — is ``test_nanowell_mask_reuse_run.py``.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from pipeline import nanowell_masks as masks


def _counting(identity="stand-in v1"):
    """A segmenter that labels every pixel above 0.5 as one cell, counting its calls, with an identity."""
    calls = []

    def segment(image, lowhigh=None, max_size_fraction=None):
        calls.append(("one", np.asarray(image).copy(), lowhigh, max_size_fraction))
        return (np.asarray(image) > 0.5).astype(np.int64) * 7

    def segment_many(images, lowhigh, max_size_fraction=None):
        calls.append(("many", len(images), lowhigh, max_size_fraction))
        return [(np.asarray(i) > 0.5).astype(np.int64) * 7 for i in images]

    segment.accepts_range = True
    segment.accepts_size_cap = True
    segment.segment_many = segment_many
    if identity is not None:
        segment.cache_identity = identity
    return segment, calls


def _image(seed=0, shape=(9, 11)):
    return np.random.default_rng(seed).random(shape)


def _store(tmp_path, source=True):
    path = masks.masks_path(tmp_path / "nanowells", "B02")
    return masks.MaskStore(path if source else None, path)


def test_a_second_run_over_the_same_pixels_segments_nothing(tmp_path):
    segment, calls = _counting()
    first = _store(tmp_path)
    got = first.wrap(segment, "0h")(_image(), lowhigh=(0.1, 0.9), max_size_fraction=1.0)
    first.save()
    assert len(calls) == 1 and (first.hits, first.misses) == (0, 1)

    second = _store(tmp_path)
    again = second.wrap(segment, "0h")(_image(), lowhigh=(0.1, 0.9), max_size_fraction=1.0)
    assert len(calls) == 1, "the same input was segmented again"
    assert (second.hits, second.misses) == (1, 0)
    np.testing.assert_array_equal(again, got)
    second.wrap(segment, "0h")(_image(), lowhigh=(0.1, 0.9), max_size_fraction=1.0)
    assert (second.hits, second.repeats, len(calls)) == (1, 1, 1)
    assert second.summary() == ("1 of 2 segmenter input(s) had their cell masks from an earlier run, 0 segmented, "
                                "1 repeated within this run")
    assert again.dtype == np.int64                   # as segment_sites takes it


def test_segment_many_sends_only_the_misses_and_keeps_their_order(tmp_path):
    segment, calls = _counting()
    first = _store(tmp_path)
    first.wrap(segment, "0h").segment_many([_image(1), _image(3)], (0.0, 1.0), max_size_fraction=1.0)
    first.save()
    calls.clear()
    second = _store(tmp_path)
    crops = [_image(1), _image(2), _image(3), _image(4)]
    out = second.wrap(segment, "0h").segment_many(crops, (0.0, 1.0), max_size_fraction=1.0)
    assert calls == [("many", 2, (0.0, 1.0), 1.0)]                  # images 2 and 4 alone
    for crop, labels in zip(crops, out):
        np.testing.assert_array_equal(labels, (crop > 0.5) * 7)
    assert (second.hits, second.misses) == (2, 2)


@pytest.mark.parametrize("change", ["one_pixel", "dtype", "shape", "lowhigh", "max_size_fraction", "identity"])
def test_any_change_to_what_decides_the_labels_segments_again(tmp_path, change):
    """Each input of the key, changed alone, must miss (``test_reuse_is_keyed_completely`` holds why each is keyed)."""
    segment, calls = _counting()
    first = _store(tmp_path)
    first.wrap(segment, "0h")(_image(), lowhigh=(0.1, 0.9), max_size_fraction=1.0)
    first.save()
    image, lowhigh, cap = _image(), (0.1, 0.9), 1.0
    if change == "one_pixel":
        image[4, 5] += 1e-9
    elif change == "dtype":
        image = image.astype(np.float32)
    elif change == "shape":
        image = image.reshape(11, 9)
    elif change == "lowhigh":
        lowhigh = (0.1, 0.9000001)
    elif change == "max_size_fraction":
        cap = None
    elif change == "identity":
        segment, calls = _counting("stand-in v2")
    calls.clear()
    second = _store(tmp_path)
    kwargs = {"lowhigh": lowhigh} | ({} if cap is None else {"max_size_fraction": cap})
    second.wrap(segment, "0h")(image, **kwargs)
    assert len(calls) == 1 and (second.hits, second.misses) == (0, 1), change


def test_a_segmenter_without_an_identity_is_never_cached(tmp_path):
    """A key without the segmenter in it could hand one segmenter's masks to another."""
    segment, calls = _counting(identity=None)
    store = _store(tmp_path)
    assert store.wrap(segment, "0h") is segment
    store.save()
    assert not store.target.exists()


def test_save_keeps_untouched_timepoints_and_drops_what_a_segmented_timepoint_no_longer_uses(tmp_path):
    segment, _calls = _counting()
    first = _store(tmp_path)
    first.wrap(segment, "0h")(_image(1))
    first.wrap(segment, "24h")(_image(2))
    first.save()

    # A run that segments 0h only, from other pixels: 0h's old mask describes nothing it gives the segmenter now.
    second = _store(tmp_path)
    second.wrap(segment, "0h")(_image(3))
    second.save()

    third = _store(tmp_path)
    call = third.wrap(segment, "0h")
    call(_image(1))
    call(_image(2))                 # 24h's, kept because the second run never segmented 24h
    call(_image(3))
    assert (third.hits, third.misses) == (2, 1)


def test_an_unreadable_file_is_named_and_read_as_empty(tmp_path, caplog):
    path = masks.masks_path(tmp_path / "nanowells", "B02")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not an npz")
    segment, calls = _counting()
    with caplog.at_level(logging.WARNING, logger="pipeline.nanowell_masks"):
        store = masks.MaskStore(path, path)
    assert "could not be read" in caplog.text and str(path) in caplog.text
    store.wrap(segment, "0h")(_image())
    assert len(calls) == 1
    store.save()
    assert masks.MaskStore(path, path)._prior          # replaced by a readable one


def test_labels_round_trip_in_the_smallest_type_that_holds_them(tmp_path):
    big = np.zeros((4, 4), dtype=np.int64)
    big[0, 0], big[1, 1] = 70_000, 3

    def segment(image, lowhigh=None, max_size_fraction=None):
        return big
    segment.cache_identity = "big"
    store = _store(tmp_path)
    store.wrap(segment, "0h")(_image(shape=(4, 4)))
    store.save()
    reread = _store(tmp_path)
    (tp, (_blob, dtype, shape)), = reread._prior.values()
    assert (tp, np.dtype(dtype), shape) == ("0h", np.dtype(np.uint32), [4, 4])
    np.testing.assert_array_equal(reread.wrap(segment, "0h")(_image(shape=(4, 4))), big)


def _stub_torch(monkeypatch, *, bf16: bool = False, version: str = "2.3.0"):
    """A stand-in for Torch (not installed here): its version, and whether its GPU computes in bfloat16."""
    import sys
    from types import SimpleNamespace

    torch = SimpleNamespace(__version__=version, cuda=SimpleNamespace(is_bf16_supported=lambda: bf16))
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


def test_cellposes_identity_names_what_decides_its_labels(monkeypatch):
    """The production segmenter declares an identity, and each part of it changes it (stubbed: no Torch here)."""
    from types import SimpleNamespace

    from pipeline import cellpose, nanowell_measure as nm

    device = SimpleNamespace(type="cpu")
    _stub_torch(monkeypatch)
    monkeypatch.setattr(cellpose, "_cellpose_device", lambda: (device, "stub"))
    base = nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity()
    assert base == nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity()
    assert nm.cellpose_segmenter(21.0, model_type="cpsam").cache_identity() != base
    assert nm.cellpose_segmenter(20.0, model_type="cyto3").cache_identity() != base
    device.type = "cuda"
    assert nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity() != base
    device.type = "cpu"
    _stub_torch(monkeypatch, version="2.4.0")
    assert nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity() != base
    _stub_torch(monkeypatch)
    monkeypatch.setattr(masks, "code_digest", lambda *m: "edited")
    assert nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity() != base


def _precision_of(identity: str):
    return json.loads(identity).get("precision")


def test_the_precision_is_part_of_cellpose_s_identity_without_loading_the_model(monkeypatch):
    """Two GPUs of one device kind run the same model in different precisions — bfloat16 on an Ampere card,
    float32 on a T4 — and give different labels for the same crop, so masks sent between hosts
    (``services.remote_registration``) must not be answered across that line. The precision is the one
    ``cellpose.planned_precision`` decides, read with no model built: a re-run every crop of which is a kept mask
    never loads Cellpose."""
    from types import SimpleNamespace

    from pipeline import cellpose, nanowell_measure as nm

    def no_model(*_a, **_k):
        raise AssertionError("working out the identity loaded the Cellpose model")

    monkeypatch.setattr(cellpose, "_cellpose_model", no_model)
    monkeypatch.setattr(cellpose, "_CELLPOSE_MODELS", {})
    monkeypatch.setattr(cellpose, "cellpose_major", lambda version=None: 4)
    monkeypatch.setattr(cellpose, "_cellpose_device", lambda: (SimpleNamespace(type="cuda"), "stub"))
    _stub_torch(monkeypatch, bf16=True)
    assert _precision_of(nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity()) == "bfloat16"
    _stub_torch(monkeypatch, bf16=False)                # a T4: the same device kind, float32 with TF32
    assert _precision_of(nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity()) == "float32+tf32"
    _stub_torch(monkeypatch, bf16=True, version="2.0.1")   # below BF16_MIN_TORCH
    assert _precision_of(nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity()) == "float32+tf32"
    monkeypatch.setattr(cellpose, "_cellpose_device", lambda: (SimpleNamespace(type="cpu"), "stub"))
    assert _precision_of(nm.cellpose_segmenter(20.0, model_type="cpsam").cache_identity()) == "float32"


def test_a_model_that_fell_back_to_float32_writes_its_masks_under_float32(monkeypatch, tmp_path):
    """A GPU that offers bfloat16 plans it, but a model whose bfloat16 first pass failed is rebuilt in float32
    (``cellpose._bfloat16_or_float32``) and records so. The masks it makes are float32 masks: they are written
    under the float32 identity, so a later lookup by that identity finds them and a bfloat16 one does not."""
    from types import SimpleNamespace

    from pipeline import cellpose, nanowell_measure as nm

    models: dict = {}
    monkeypatch.setattr(cellpose, "_CELLPOSE_MODELS", models)
    monkeypatch.setattr(cellpose, "cellpose_major", lambda version=None: 4)
    monkeypatch.setattr(cellpose, "_cellpose_device", lambda: (SimpleNamespace(type="cuda"), "stub"))
    _stub_torch(monkeypatch, bf16=True)
    segment = nm.cellpose_segmenter(20.0, model_type="cpsam")
    planned = segment.cache_identity()
    assert _precision_of(planned) == "bfloat16"

    def fake_segment(image, *, diameter, lowhigh=None, max_size_fraction=None, **_options):
        # The first call builds the model, which falls back: what _cellpose_model leaves behind.
        models["cpsam"] = SimpleNamespace(**{cellpose.PRECISION_ATTR: "float32+tf32"})
        return (np.asarray(image) > 0.5).astype(np.int32) * 3

    monkeypatch.setattr(cellpose, "_segment_cellpose", fake_segment)
    store = _store(tmp_path)
    labels = store.wrap(segment, "0h")(_image(), lowhigh=(0.1, 0.9), max_size_fraction=1.0)
    store.save()

    built = segment.cache_identity()
    assert _precision_of(built) == "float32+tf32"
    kept = _store(tmp_path)._prior
    assert set(kept) == {masks.mask_key(built, _image(), (0.1, 0.9), 1.0)}
    np.testing.assert_array_equal(masks._unpack(kept[masks.mask_key(built, _image(), (0.1, 0.9), 1.0)][1]), labels)


def test_the_code_digest_moves_when_a_function_s_code_does(tmp_path, monkeypatch):
    import importlib

    monkeypatch.syspath_prepend(str(tmp_path))
    bodies = {"probe_a": ("x + 1", "1"), "probe_b": ("x + 2", "1"), "probe_c": ("x + 2", "2")}
    digests = {}
    for name, (f, m) in bodies.items():          # distinct names: a same-second rewrite could reuse a stale .pyc
        (tmp_path / f"{name}.py").write_text(f"def f(x):\n    return {f}\n\nclass C:\n    def m(self):\n"
                                             f"        return {m}\n")
        module = importlib.import_module(name)
        module.__name__ = "probe"                     # the digest names the module: compare the code alone
        for obj in (module.f, module.C):
            obj.__module__ = "probe"
        digests[name] = masks.code_digest(module)
    assert len(set(digests.values())) == 3, digests


def test_the_code_digest_moves_when_a_default_or_a_module_constant_does():
    """A code object holds neither its function's defaults nor the values of the globals it reads, only their
    names. The run leaves Cellpose's ``flow_threshold`` and ``cellprob_threshold`` at their signature defaults, so
    a digest of the code alone kept every stored mask when either was edited — labels the current code would not
    produce, reported as a successful run."""
    import types

    def module(src):
        m = types.ModuleType("probe")
        exec(src, m.__dict__)
        return m

    base = "LIMIT = 0.4\ndef seg(x, *, flow_threshold=0.4, other=(1, 'a')):\n    return x > flow_threshold * LIMIT\n"
    digests = {
        "base": masks.code_digest(module(base)),
        "again": masks.code_digest(module(base)),
        "default": masks.code_digest(module(base.replace("flow_threshold=0.4", "flow_threshold=0.6"))),
        "constant": masks.code_digest(module(base.replace("LIMIT = 0.4", "LIMIT = 0.9"))),
        "positional": masks.code_digest(module(base.replace("*, flow", "flow"))),
    }
    assert digests["base"] == digests["again"], "the digest must be the same for the same code"
    assert len({digests[k] for k in ("base", "default", "constant", "positional")}) == 4, digests


def test_the_code_digest_is_stable_for_defaults_whose_repr_names_an_address():
    import types

    m1, m2 = types.ModuleType("probe"), types.ModuleType("probe")
    for m in (m1, m2):
        exec("class Marker:\n    pass\ndef f(x, sentinel=Marker()):\n    return x\n", m.__dict__)
    assert masks.code_digest(m1) == masks.code_digest(m2)
