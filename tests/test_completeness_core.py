"""Pure-logic tests for ``pipeline.completeness`` — the one definition of
"is this well complete".

No Qt, no app fixture, no imaging stack: the module is stdlib plus
``pipeline.incremental_keys``, which is the whole point of it living under
``pipeline/`` where a run on a remote host can import it.

The read-count test at the end is the load-bearing one. Validate's own scan
pins its per-well disk reads exactly (``tests/test_validate_compute.py``
asserts two zip opens and one CSV parse per well), and it can only keep doing
that if the shared predicate reads nothing the caller has not already handed
it.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pipeline.completeness import (
    HEALTH_STATES,
    WellCoverage,
    coverage_for_well,
    csv_keys_from_rows,
    done_keys,
    health,
    missing_keys,
    reduce_csv_state,
)

SCHEMA = ["exp", "channel", "well", "fov", "timepoint"]
SEP = "_"


def _write_zip(path: Path, names) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"")
    return path


def _image(fov: str, tp: str, *, well: str = "A01", channel: str = "DAPI") -> str:
    return f"exp1_{channel}_{well}_{fov}_{tp}.tif"


# ── the state roster ────────────────────────────────────────────────────────

def test_health_states_are_the_four_the_plate_paints():
    assert HEALTH_STATES == ("complete", "partial", "missing", "no_output")


# ── csv_keys_from_rows ──────────────────────────────────────────────────────

def test_csv_keys_are_read_verbatim_from_the_two_columns():
    rows = [{"fov": "F001", "timepoint": "24h"}, {"fov": "F002", "timepoint": "24h"}]
    assert csv_keys_from_rows(rows, ["fov", "timepoint"]) == {
        ("F001", "24h"), ("F002", "24h")}


def test_many_rows_of_one_group_collapse_to_one_key():
    rows = [{"fov": "F001", "timepoint": "0"} for _ in range(50)]
    assert csv_keys_from_rows(rows, ["fov", "timepoint"]) == {("F001", "0")}


@pytest.mark.parametrize("fieldnames", [["fov"], ["timepoint"], [], ["nucleus_id"]])
def test_a_csv_without_both_columns_contributes_no_keys(fieldnames):
    # Half a key is worse than none: ("F001", "") would diff as a real group
    # the input never held.
    rows = [{"fov": "F001", "timepoint": "24h"}]
    assert csv_keys_from_rows(rows, fieldnames) == set()


# ── reduce_csv_state ────────────────────────────────────────────────────────

@pytest.mark.parametrize(("states", "expected"), [
    ([], "missing"),
    (["ok"], "ok"),
    (["unreadable", "ok"], "ok"),
    (["unreadable", "empty"], "empty"),
    (["unreadable"], "unreadable"),
    (["empty"], "empty"),
])
def test_csv_state_reduction_prefers_the_least_alarming_true_answer(states, expected):
    assert reduce_csv_state(states) == expected


# ── done_keys ───────────────────────────────────────────────────────────────

def test_done_unions_the_csv_with_both_image_sources(tmp_path):
    out_zip = _write_zip(tmp_path / "A01_out.zip", [_image("F002", "24h")])
    out_folder = tmp_path / "A01"
    out_folder.mkdir()
    (out_folder / _image("F003", "24h")).touch()

    assert done_keys(
        csv_keys={("F001", "24h")}, out_zip=out_zip, out_folder=out_folder,
        schema=SCHEMA, sep=SEP,
    ) == {("F001", "24h"), ("F002", "24h"), ("F003", "24h")}


def test_image_evidence_counts_even_when_the_csv_is_empty(tmp_path):
    # A finished group in the archive is a group an incremental run will not
    # redo, whatever the CSV says — the union has to see it.
    out_zip = _write_zip(tmp_path / "A01_out.zip", [_image("F001", "0")])
    assert done_keys(
        csv_keys=set(), out_zip=out_zip, out_folder=None, schema=SCHEMA, sep=SEP,
    ) == {("F001", "0")}


def test_done_with_no_output_evidence_at_all_is_the_csv_alone():
    assert done_keys(
        csv_keys={("F001", "0")}, out_zip=None, out_folder=None,
        schema=SCHEMA, sep=SEP,
    ) == {("F001", "0")}


# ── missing_keys ────────────────────────────────────────────────────────────

def test_missing_is_the_plain_difference_when_the_schema_has_fov():
    intent = {("F001", "24h"), ("F002", "24h"), ("F003", "24h")}
    done = {("F001", "24h")}
    assert missing_keys(intent, done, fov_field_present=True) == {
        ("F002", "24h"), ("F003", "24h")}


def test_extra_done_keys_never_become_missing_ones():
    # done ⊄ intent is a schema-mismatch symptom Validate reports separately;
    # it must not make the difference go negative or wrap around.
    assert missing_keys({("F001", "0")}, {("F001", "0"), ("F009", "0")},
                        fov_field_present=True) == set()


def test_single_fov_datasets_diff_on_timepoint_alone():
    # Without a 'fov' field the intent side parses "" for every key while a
    # written CSV row's own column is normalised to "1". Diffing the raw pairs
    # would report every well missing everything.
    intent = {("", "0"), ("", "24h"), ("", "48h")}
    done = {("1", "0"), ("1", "24h")}
    assert missing_keys(intent, done, fov_field_present=False) == {("", "48h")}


def test_single_fov_completeness_is_not_waived_wholesale():
    # The regression this shape exists to prevent: a well holding one timepoint
    # of three must not read identically to a well holding all three.
    intent = {("", "0"), ("", "24h"), ("", "48h")}
    assert missing_keys(intent, {("1", "0")}, fov_field_present=False) == {
        ("", "24h"), ("", "48h")}
    assert missing_keys(intent, {("1", "0"), ("1", "24h"), ("1", "48h")},
                        fov_field_present=False) == set()


# ── health ──────────────────────────────────────────────────────────────────

def _health(**kw) -> str:
    base = dict(missing=(), csv_state="ok", has_in_container=True,
                has_out_zip=True, has_out_folder=False)
    base.update(kw)
    return health(**base)


def test_health_partial_wins_over_everything_else():
    assert _health(missing=(("F001", "0"),)) == "partial"
    # Even with an unreadable CSV: what is missing is the more actionable fact.
    assert _health(missing=(("F001", "0"),), csv_state="unreadable") == "partial"


def test_health_complete_needs_a_readable_csv():
    assert _health() == "complete"
    assert _health(csv_state="empty") == "missing"
    assert _health(csv_state="missing") == "missing"
    assert _health(csv_state="unreadable") == "missing"


def test_health_no_output_means_no_evidence_at_all():
    assert _health(csv_state="missing", has_in_container=False,
                   has_out_zip=False, has_out_folder=False) == "no_output"
    # A folder-mode output is evidence too.
    assert _health(csv_state="missing", has_in_container=False,
                   has_out_zip=False, has_out_folder=True) == "missing"


def test_every_health_answer_is_one_of_the_declared_states():
    for csv_state in ("ok", "empty", "unreadable", "missing"):
        for flags in ((True, True, False), (False, False, False), (False, True, True)):
            for missing in ((), (("F001", "0"),)):
                answer = _health(
                    missing=missing, csv_state=csv_state,
                    has_in_container=flags[0], has_out_zip=flags[1],
                    has_out_folder=flags[2])
                assert answer in HEALTH_STATES


# ── coverage_for_well ───────────────────────────────────────────────────────

def test_coverage_reports_both_covered_numbers_separately(tmp_path):
    # The state the two numbers exist to tell apart: a whole archive behind an
    # empty CSV. covered_any says a re-run recovers nothing; covered_csv says
    # every plot reading this well sees nothing.
    members = [_image("F001", "0"), _image("F002", "0")]
    out_zip = _write_zip(tmp_path / "A01_out.zip", members)

    cov = coverage_for_well(
        "A01", in_members=set(members), csv_keys=set(), csv_state="empty",
        out_zip=out_zip, out_folder=None, schema=SCHEMA, sep=SEP,
        fov_field_present=True)

    assert (cov.intent, cov.covered_any, cov.covered_csv) == (2, 2, 0)
    assert cov.missing == ()
    assert cov.complete is True
    assert health(missing=cov.missing, csv_state=cov.csv_state,
                  has_in_container=cov.has_in_container,
                  has_out_zip=cov.has_out_zip,
                  has_out_folder=cov.has_out_folder) == "missing"


def test_coverage_with_an_unlistable_input_reports_no_missing_groups(tmp_path):
    # in_members=None is "the container is absent or could not be read". Such a
    # well must not be reported as missing output it was never asked to produce.
    out_zip = _write_zip(tmp_path / "A01_out.zip", [_image("F001", "0")])
    cov = coverage_for_well(
        "A01", in_members=None, csv_keys={("F001", "0")}, csv_state="ok",
        out_zip=out_zip, out_folder=None, schema=SCHEMA, sep=SEP,
        fov_field_present=True)
    assert cov.intent == 0
    assert cov.missing == ()
    assert cov.has_in_container is False


def test_coverage_missing_pairs_are_sorted_for_a_stable_record(tmp_path):
    members = [_image("F003", "0"), _image("F001", "0"), _image("F002", "0")]
    cov = coverage_for_well(
        "A01", in_members=set(members), csv_keys={("F002", "0")}, csv_state="ok",
        out_zip=None, out_folder=None, schema=SCHEMA, sep=SEP,
        fov_field_present=True)
    assert cov.missing == (("F001", "0"), ("F003", "0"))


def test_coverage_carries_the_failed_group_labels_it_is_handed():
    cov = coverage_for_well(
        "A01", in_members=None, csv_keys=set(), csv_state="missing",
        out_zip=None, out_folder=None, schema=SCHEMA, sep=SEP,
        fov_field_present=True, failed=["F003_24h"])
    assert cov.failed == ("F003_24h",)
    assert isinstance(cov, WellCoverage)


def test_coverage_opens_the_output_container_and_nothing_else(tmp_path, monkeypatch):
    """The constraint that lets Validate delegate without changing its read count.

    ``coverage_for_well`` is handed the input member names and the CSV keys, so
    the only file it may touch is the output container. If that ever stops
    being true, ``tests/test_validate_compute.py``'s exact
    ``2 * n_wells`` / ``n_wells`` ratchet breaks — and this test says why first.
    """
    members = [_image("F001", "0")]
    in_zip = _write_zip(tmp_path / "A01.zip", members)
    out_zip = _write_zip(tmp_path / "A01_out.zip", members)
    csv_path = tmp_path / "measurements_A01.csv"
    csv_path.write_text("fov,timepoint\nF001,0\n")

    opened: list[str] = []
    real_zipfile = zipfile.ZipFile

    class _CountingZipFile(real_zipfile):
        def __init__(self, file, *a, **kw):
            opened.append(str(file))
            super().__init__(file, *a, **kw)

    monkeypatch.setattr(zipfile, "ZipFile", _CountingZipFile)

    opened_text: list[str] = []
    real_open = Path.open

    def _counting_open(self, *a, **kw):
        opened_text.append(str(self))
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", _counting_open)

    coverage_for_well(
        "A01", in_members=set(members), csv_keys={("F001", "0")}, csv_state="ok",
        out_zip=out_zip, out_folder=None, schema=SCHEMA, sep=SEP,
        fov_field_present=True)

    assert opened == [str(out_zip)], (
        f"expected only the output container to be opened, got {opened}")
    assert str(in_zip) not in opened
    assert opened_text == [], f"no file should be opened as text, got {opened_text}"
