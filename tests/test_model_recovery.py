"""StarDist pretrained-model load resilience.

A run interrupted mid-download (e.g. an earlier failure killed the pool) can
leave a truncated ``weights_best.h5`` in the keras cache; the next run reuses it
and h5py dies with ``OSError: Unable to synchronously open file (truncated
file: eof = …)``. ``_load_stardist_pretrained`` must purge the corrupt cache and
re-download exactly once — without re-downloading on unrelated (e.g. network)
errors. These tests use a fake StarDist class and a temp keras cache, so they
need neither TensorFlow nor StarDist.
"""

from __future__ import annotations

import os
from pathlib import Path

import process_microscopy as pm


def _seed_cache(keras_home: Path) -> Path:
    model_dir = keras_home / "models" / "StarDist2D" / "2D_versatile_fluo"
    model_dir.mkdir(parents=True)
    (model_dir / "weights_best.h5").write_bytes(b"\x89HDF\r\n\x1a\n truncated")
    (model_dir / "config.json").write_text("{}")
    return model_dir


def test_recovers_from_truncated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KERAS_HOME", str(tmp_path))
    model_dir = _seed_cache(tmp_path)

    class FakeSD:
        calls = 0

        @classmethod
        def from_pretrained(cls, name):
            cls.calls += 1
            if cls.calls == 1:
                raise OSError(
                    "Unable to synchronously open file (truncated file: eof = 1024)")
            return f"MODEL[{name}]"

    out = pm._load_stardist_pretrained(FakeSD, "2D_versatile_fluo")
    assert out == "MODEL[2D_versatile_fluo]"
    assert FakeSD.calls == 2                 # purged + retried exactly once
    assert not model_dir.exists()            # corrupt cache removed


def test_does_not_retry_on_network_error(tmp_path, monkeypatch):
    monkeypatch.setenv("KERAS_HOME", str(tmp_path))
    model_dir = _seed_cache(tmp_path)

    class FakeNet:
        calls = 0

        @classmethod
        def from_pretrained(cls, name):
            cls.calls += 1
            raise OSError("Network is unreachable")

    import pytest
    with pytest.raises(OSError, match="Network is unreachable"):
        pm._load_stardist_pretrained(FakeNet, "2D_versatile_fluo")
    assert FakeNet.calls == 1                 # no purge, no retry
    assert model_dir.exists()                 # cache left untouched


def test_purge_targets_only_model_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KERAS_HOME", str(tmp_path))
    model_dir = _seed_cache(tmp_path)
    # an unrelated keras datasets file must survive
    other = tmp_path / "datasets" / "keep.txt"
    other.parent.mkdir(parents=True)
    other.write_text("keep me")

    removed = pm._purge_stardist_cache()
    assert str(model_dir) in removed
    assert not model_dir.exists()
    assert other.exists()
