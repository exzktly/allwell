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
import threading
import time
from pathlib import Path

import process_microscopy as pm
# The cache, its lock and the pkg_resources shim live here now;
# ``pm`` re-exports them, and a re-export is a *binding* — patching
# ``sd_cache._STARDIST_LOCK_STALE`` would not be seen by the function that
# reads it, which resolves the name in this module's globals
# (ARCHITECTURE §6.4). The functions are still driven through ``pm``,
# because that is how ``services/frame_preview`` reaches them.
import pipeline.stardist_cache as sd_cache


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


# ── download serialisation (the concurrent-download race that corrupts cache) ──

def test_concurrent_loads_download_once(tmp_path, monkeypatch):
    monkeypatch.setenv("KERAS_HOME", str(tmp_path))
    warm = tmp_path / "models" / "warm.flag"

    class FakeSD:
        download_count = 0
        _dl_lock = threading.Lock()

        @classmethod
        def from_pretrained(cls, name):
            if not warm.exists():                     # cold cache → "download"
                with cls._dl_lock:
                    cls.download_count += 1
                time.sleep(0.05)                      # widen the race window
                warm.parent.mkdir(parents=True, exist_ok=True)
                warm.write_text("ok")
            return f"MODEL[{name}]"

    results: list = []
    threads = [threading.Thread(
        target=lambda: results.append(pm._load_stardist_pretrained(FakeSD)))
        for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # The lock serialises the cold download → exactly one, despite 8 callers.
    assert FakeSD.download_count == 1
    assert results == ["MODEL[2D_versatile_fluo]"] * 8


def test_lock_stale_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setenv("KERAS_HOME", str(tmp_path))
    lockdir = pm._stardist_cache_models_dir() / ".allwell_stardist_download.lock"
    lockdir.mkdir(parents=True)
    old = time.time() - sd_cache._STARDIST_LOCK_STALE - 10      # older than stale window
    os.utime(lockdir, (old, old))

    class FakeSD:
        @classmethod
        def from_pretrained(cls, name):
            return f"MODEL[{name}]"

    # A stale lock must be reclaimed, not block — the load returns promptly.
    out = pm._load_stardist_pretrained(FakeSD)
    assert out == "MODEL[2D_versatile_fluo]"
    assert not lockdir.exists()                          # reclaimed + released


def test_lock_dead_holder_reclaimed_instantly(tmp_path, monkeypatch):
    # The hang we hit: a previous run was killed while a worker held the lock,
    # leaking the lockdir. The rerun must reclaim it AT ONCE (the holder PID is
    # dead), not stall for the timeout — even with mtime-staleness disabled.
    monkeypatch.setenv("KERAS_HOME", str(tmp_path))
    monkeypatch.setattr(sd_cache, "_STARDIST_LOCK_STALE", 1e9)  # disable mtime fallback
    monkeypatch.setattr(sd_cache, "_STARDIST_LOCK_TIMEOUT", 30.0)  # would be slow if it waited
    monkeypatch.setattr(pm, "_pid_alive", lambda pid: False)  # recorded holder is dead
    lockdir = pm._stardist_cache_models_dir() / ".allwell_stardist_download.lock"
    lockdir.mkdir(parents=True)
    (lockdir / "pid.999999").touch()

    class FakeSD:
        @classmethod
        def from_pretrained(cls, name):
            return f"MODEL[{name}]"

    t0 = time.monotonic()
    out = pm._load_stardist_pretrained(FakeSD)
    assert out == "MODEL[2D_versatile_fluo]"
    assert time.monotonic() - t0 < 1.0                     # instant, not after timeout
    assert not lockdir.exists()                            # reclaimed + released


def test_lock_timeout_degrades_to_unlocked(tmp_path, monkeypatch):
    monkeypatch.setenv("KERAS_HOME", str(tmp_path))
    monkeypatch.setattr(sd_cache, "_STARDIST_LOCK_TIMEOUT", 0.2)
    monkeypatch.setattr(sd_cache, "_STARDIST_LOCK_STALE", 1e9)   # never treat as stale
    lockdir = pm._stardist_cache_models_dir() / ".allwell_stardist_download.lock"
    lockdir.mkdir(parents=True)                            # held by a (fake) live holder

    class FakeSD:
        @classmethod
        def from_pretrained(cls, name):
            return f"MODEL[{name}]"

    # Held lock + timeout → proceed WITHOUT it rather than hang/raise.
    out = pm._load_stardist_pretrained(FakeSD)
    assert out == "MODEL[2D_versatile_fluo]"
    assert lockdir.exists()                               # we didn't hold/remove it
