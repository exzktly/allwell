"""The Datasets tools read a well archive spelled ``A01.ZIP`` as the pipeline does.

``pipeline.input_layout.find_well_zips`` compares the archive suffix in any case, so ``A01.ZIP`` is a well the run
processes. The Datasets tools asked ``glob("*.zip")``, which is case-sensitive on Linux, and passed such a well over:
copying tools did not see its container, the image-stem scan read none of its images, and a converter's
destination check took a folder of upper-case archives for an empty one. They now ask
``util.dir_scan.zip_files_in``. (Review of the PRs merged 2026-10-25..2026-11-01, row R11.)
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from datasets.core import convert_marker, dataset_paths
from util.dir_scan import zip_files_in


def _plate(root: Path) -> Path:
    in_dir = root / "in"
    in_dir.mkdir(parents=True)
    with zipfile.ZipFile(in_dir / "A01.ZIP", "w") as zf:
        zf.writestr("exp_DAPI_A01_f1_t0.tif", b"x")
    (in_dir / "._A02.zip").write_bytes(b"sidecar")                 # an AppleDouble sidecar, not an archive
    (root / "out").mkdir()
    return in_dir


def test_zip_files_in_reads_any_case_and_skips_sidecars(tmp_path):
    in_dir = _plate(tmp_path)
    assert zip_files_in(in_dir) == [in_dir / "A01.ZIP"]
    assert zip_files_in(tmp_path / "missing") == []


def test_the_dataset_paths_helpers_see_an_upper_case_archive(tmp_path):
    in_dir = _plate(tmp_path)
    paths = dataset_paths.DatasetPaths(tmp_path, in_dir, tmp_path / "out")
    assert dataset_paths.in_containers(paths) == {"A01": in_dir / "A01.ZIP"}
    assert "exp_DAPI_A01_f1_t0" in dataset_paths.collect_input_image_stems(in_dir)


def test_a_converter_sees_the_archives_already_in_a_destination(tmp_path):
    _plate(tmp_path)
    assert convert_marker.converted_wells(tmp_path) == ["A01"]
    assert convert_marker.destination_problem(tmp_path, "convert-marker.json") != ""
