"""Opening ``out/`` directly finds the ``in/`` beside it (ARCHITECTURE §6.2l).

The loader recognised only ``<root>`` holding ``in/`` + ``out/``; opening ``out/`` left ``in_dir`` unset and every
input-frame reader said its image was missing, naming ``in/`` as the remedy while it sat beside the folder.
"""

from __future__ import annotations

from pathlib import Path

from review.data.dataset_layout import resolve


def _pair(tmp_path: Path) -> Path:
    (tmp_path / "ds" / "in").mkdir(parents=True)
    (tmp_path / "ds" / "out").mkdir()
    return tmp_path / "ds"


def test_the_root_of_a_pair_reads_out_and_finds_in(tmp_path):
    root = _pair(tmp_path)
    layout = resolve(root)
    assert (layout.out_dir, layout.in_dir, layout.opened_root) == (root / "out", root / "in", True)


def test_out_opened_directly_finds_the_in_beside_it(tmp_path):
    root = _pair(tmp_path)
    layout = resolve(root / "out")
    assert (layout.out_dir, layout.in_dir, layout.opened_root) == (root / "out", root / "in", False)


def test_a_flat_folder_or_an_out_with_nothing_beside_it_has_no_input_folder(tmp_path):
    (tmp_path / "flat").mkdir()
    (tmp_path / "lone" / "out").mkdir(parents=True)
    assert resolve(tmp_path / "flat").in_dir is None
    assert resolve(tmp_path / "lone" / "out").in_dir is None


def test_the_loader_and_the_append_recovery_ask_the_same_resolver():
    import inspect

    from review.core import load_controller

    assert "resolve_layout(path)" in inspect.getsource(load_controller._load_path_announced)
    assert "resolve_layout(path)" in inspect.getsource(load_controller._recover_in_place_append)
