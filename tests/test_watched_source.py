"""A converter handed a watched folder chooses its file when it runs.

The rule (``datasets.core.watched_source``): exactly one matching file is
used; none or several fail the job, naming what was found, because converting
one of several plates and reporting success is a complete-looking result for
part of the input. The last tests drive the real ND2 and CZI controllers'
``start_from_queue`` — the call the queue makes — and read what the page was
set to convert.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from datasets.core.watched_source import WatchedSourceError, matching_files, resolve_source

ND2 = (".nd2",)
OME = (".ome.tif", ".ome.tiff")


def test_the_one_file_in_the_folder_is_chosen(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "._run7.nd2").write_bytes(b"appledouble")
    target = tmp_path / "run7.ND2"
    target.write_bytes(b"x")
    assert resolve_source(str(tmp_path), ND2, folder_is_a_source=False,
                          noun="ND2") == str(target)


def test_a_file_in_a_subfolder_counts(tmp_path):
    (tmp_path / "pos").mkdir()
    target = tmp_path / "pos" / "plate.ome.tiff"
    target.write_bytes(b"x")
    (tmp_path / "plate.tif").write_bytes(b"not ome")
    assert resolve_source(str(tmp_path), OME, folder_is_a_source=False,
                          noun="OME-TIFF") == str(target)


def test_no_matching_file_fails_naming_the_format(tmp_path):
    (tmp_path / "a.czi").write_bytes(b"x")
    with pytest.raises(WatchedSourceError, match=r"holds no ND2 file \(\.nd2\)"):
        resolve_source(str(tmp_path), ND2, folder_is_a_source=False, noun="ND2")


def test_several_matching_files_fail_naming_them(tmp_path):
    for i in range(7):
        (tmp_path / f"plate{i}.nd2").write_bytes(b"x")
    with pytest.raises(WatchedSourceError) as info:
        resolve_source(str(tmp_path), ND2, folder_is_a_source=False, noun="ND2")
    message = str(info.value)
    assert "holds 7 ND2 files (plate0.nd2, plate1.nd2, plate2.nd2, plate3.nd2, " \
           "plate4.nd2, and 2 more)" in message


def test_a_file_and_a_folder_reading_converter_are_left_alone(tmp_path):
    single = tmp_path / "a.nd2"
    single.write_bytes(b"x")
    assert resolve_source(str(single), ND2, folder_is_a_source=False, noun="ND2") == str(single)
    (tmp_path / "b.nd2").write_bytes(b"x")
    assert resolve_source(str(tmp_path), (".czi",), folder_is_a_source=True,
                          noun="CZI") == str(tmp_path)


def test_matching_is_on_the_whole_name(tmp_path):
    (tmp_path / "x.ome.tif").write_bytes(b"x")
    (tmp_path / "y.tif").write_bytes(b"x")
    assert [p.name for p in matching_files(tmp_path, OME)] == ["x.ome.tif"]


# ── through the real controllers ──────────────────────────────────────────────

def _launched_source(controller, monkeypatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(controller, "_launch",
                        lambda: seen.append(controller.page.source_path()))
    return seen


def test_the_nd2_converter_converts_the_file_it_found(qtbot, tmp_path, monkeypatch):
    from datasets.nd2_import.controller import Nd2ImportController

    controller = Nd2ImportController()
    try:
        (tmp_path / "run7.nd2").write_bytes(b"x")
        seen = _launched_source(controller, monkeypatch)
        controller.start_from_queue({"source": str(tmp_path),
                                     "destination": str(tmp_path / "out")})
        assert seen == [str(tmp_path / "run7.nd2")]
        assert "the one ND2 file in the watched folder" in controller.page.log_text()
    finally:
        controller.shutdown()


def test_the_nd2_converter_fails_a_folder_of_several(qtbot, tmp_path, monkeypatch):
    from datasets.nd2_import.controller import Nd2ImportController

    controller = Nd2ImportController()
    try:
        (tmp_path / "a.nd2").write_bytes(b"x")
        (tmp_path / "b.nd2").write_bytes(b"x")
        seen = _launched_source(controller, monkeypatch)
        with qtbot.waitSignal(controller.jobFinished, timeout=2000) as blocker:
            controller.start_from_queue({"source": str(tmp_path),
                                         "destination": str(tmp_path / "out")})
        ok, message, path = blocker.args
        assert seen == [], "a conversion started on one of several plates"
        assert not ok and path == ""
        assert "holds 2 ND2 files (a.nd2, b.nd2)" in message
    finally:
        controller.shutdown()


def test_the_czi_converter_reads_the_watched_folder_whole(qtbot, tmp_path, monkeypatch):
    from datasets.czi_import.controller import CziImportController

    controller = CziImportController()
    try:
        (tmp_path / "a.czi").write_bytes(b"x")
        (tmp_path / "b.czi").write_bytes(b"x")
        seen = _launched_source(controller, monkeypatch)
        controller.start_from_queue({"source": str(tmp_path),
                                     "destination": str(tmp_path / "out")})
        assert seen == [str(tmp_path)]
    finally:
        controller.shutdown()


def test_every_packed_converter_says_what_it_reads():
    """A converter with no suffixes could never choose a file from a folder
    and would fail every watched conversion with "holds no file"."""
    from datasets.czi_import.controller import CZI_CONVERTER
    from datasets.nd2_import.controller import ND2_CONVERTER
    from datasets.ome_import.controller import OME_CONVERTER

    for spec in (CZI_CONVERTER, ND2_CONVERTER, OME_CONVERTER):
        assert spec.source_suffixes, spec.kind
