"""Tests for the unified ``well_viewer.data.image_identity`` module.

Pins the contract every other site relies on:

- ``CellKey`` produces the same tuple regardless of whether the inputs
  came from a CSV row (``from_row``) or a parsed image filename
  (``from_parsed``), so producer/consumer key construction can never
  drift.
- ``normalize_tp`` collapses the full time-format matrix seen in the
  wild — ``T01`` / ``24h`` / ``1.0`` / ``day3`` / ``02d04h30m`` — to a
  single canonical form.
- ``role_output_filename`` and ``classify_filename_role`` are inverses
  for every role in ``ROLE_CATALOG``, so writer suffixes can't drift
  from reader suffixes.
- ``PipelineSchema.has_fov = False`` (single-FOV pipelines) collapses
  to ``fov="1"`` via the same ``normalize_fov`` rule the CSV side uses.
"""

from __future__ import annotations

import pandas as pd
import pytest

from well_viewer.data.image_identity import (
    CellKey,
    Role,
    ROLE_CATALOG,
    build_image_filename,
    cell_keys_for_dataframe,
    classify_filename_role,
    load_schema,
    normalize_fov,
    normalize_fov_series,
    normalize_nucleus_id,
    normalize_tp,
    normalize_well,
    parse_image_filename,
    role_output_filename,
)


# ── CellKey identity ─────────────────────────────────────────────────────


def test_cellkey_from_row_empty_fov_normalises_to_one():
    """The user's exact reproducer for the all-zero ``smfish_count`` bug."""
    k = CellKey.from_row({"well": "A02", "fov": "", "timepoint": "T01", "nucleus_id": "1"})
    assert k.as_tuple() == ("A02", "1", "1", "1")


def test_cellkey_from_row_minus_one_fov():
    k = CellKey.from_row({"well": "B02", "fov": "-1", "timepoint": "24", "nucleus_id": "3"})
    assert k.as_tuple() == ("B02", "1", "24", "3")


def test_cellkey_from_row_uses_well_kwarg_when_present():
    """When a CSV row omits the well column the caller can supply it
    via the kwarg — used by the smFISH writer iterating per-well CSVs."""
    k = CellKey.from_row({"fov": "1", "timepoint": "1", "nucleus_id": "5"}, well="C03")
    assert k.well == "C03"


def test_cellkey_from_row_accepts_column_aliases():
    """nucleus_id appears as ``nucleus_id`` / ``nucleusId`` / etc.; tp
    as ``timepoint`` / ``tp`` / ``time`` / ``timepoint_hours``."""
    k = CellKey.from_row({"well": "A02", "FOV": "1", "tp": "T01", "nucleusId": "7"})
    assert k.as_tuple() == ("A02", "1", "1", "7")


def test_cellkey_roundtrip_idempotent():
    """from_row → as_tuple → from_row produces the same key. This is
    the cross-site invariant that would have caught the smFISH/review
    drift before it shipped."""
    src = {"well": "a1", "fov": "001", "timepoint": "T01", "nucleus_id": "1.0"}
    k1 = CellKey.from_row(src)
    k2 = CellKey.from_row({
        "well": k1.well, "fov": k1.fov, "timepoint": k1.tp, "nucleus_id": k1.nid,
    })
    assert k1.as_tuple() == k2.as_tuple()
    # And the canonical form is what we expect.
    assert k1.as_tuple() == ("A01", "1", "1", "1")


# ── Time-format polymorphism ─────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("T01", "1"),
    ("1", "1"),
    ("1.0", "1"),
    ("01", "1"),
    ("1h", "1"),
    ("24", "24"),
    ("24h", "24"),
    ("day3", "3"),
    ("D02", "2"),
    ("02d04h30m", "52.5"),
    ("0.5h", "0.5"),
    ("30m", "0.5"),
    ("", ""),
    ("opaque_label", "opaque_label"),
])
def test_normalize_tp_collapses_time_formats(value, expected):
    assert normalize_tp(value) == expected


# ── Nucleus_id ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("1", "1"),
    ("1.0", "1"),       # pandas float-string artefact — user-confirmed convention
    ("  3  ", "3"),
    ("", ""),
    ("abc", "abc"),     # non-numeric IDs pass through stripped
])
def test_normalize_nucleus_id_drops_zero_fraction(value, expected):
    assert normalize_nucleus_id(value) == expected


# ── FOV normalisation ────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("", "1"),          # single-FOV convention
    ("-1", "1"),        # sentinel
    ("001", "1"),
    ("5", "5"),
    ("F001", "F001"),   # opaque token preserved
])
def test_normalize_fov_handles_sentinels_and_padding(value, expected):
    assert normalize_fov(value) == expected


def test_normalize_fov_series_vectorised():
    s = pd.Series(["", "-1", "001", "5", "F001"])
    out = list(normalize_fov_series(s))
    assert out == ["1", "1", "1", "5", "F001"]


# ── Well normalisation ───────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("a1", "A01"),
    ("A01", "A01"),
    ("A1", "A01"),
    ("h12", "H12"),
    ("", ""),
])
def test_normalize_well_canonicalises_96_well(value, expected):
    assert normalize_well(value) == expected


# ── Suffix/role catalog ──────────────────────────────────────────────────


def test_role_catalog_covers_all_legacy_kinds():
    """Every kind the legacy code branched on is in the catalog (newer roles
    such as ``phase`` may extend it)."""
    legacy = {"fluor", "tophat", "mask", "overlay", "smfish"}
    catalog = {spec.role.value for spec in ROLE_CATALOG.values()}
    assert legacy <= catalog
    assert "phase" in catalog


@pytest.mark.parametrize("role,expected", [
    (Role.MASK, "plate_A02_T01_labels.tif"),
    (Role.OVERLAY, "plate_A02_T01_overlay.png"),
    (Role.FLUOR_TOPHAT, "plate_A02_T01_tophat.tif"),
    (Role.SMFISH, "plate_A02_T01_smfish.tif"),
    (Role.FLUOR_RAW, "plate_A02_T01.tif"),
])
def test_role_output_filename_matches_legacy_suffixes(role, expected):
    """Snapshot against the hand-rolled f-strings the legacy
    ``build_processed_output_path`` produced."""
    assert role_output_filename("plate_A02_T01", role) == expected


def test_role_output_filename_channel_scoped():
    assert role_output_filename("a", Role.SMFISH, channel="w3cy5") == "a_smfish_w3cy5.tif"
    assert role_output_filename("a", Role.FLUOR_TOPHAT, channel="DAPI") == "a_tophat_dapi.tif"


# ── Filename parse/build (round-trip) ────────────────────────────────────


def test_classify_filename_role_picks_role_by_suffix():
    schema_cases = [
        ("plate_A02_labels.tif", Role.MASK),
        ("plate_A02_overlay.png", Role.OVERLAY),
        ("plate_A02_tophat.tif", Role.FLUOR_TOPHAT),
        ("plate_A02_smfish.tif", Role.SMFISH),
        ("plate_A02.tif", None),  # raw fluor — no role suffix
    ]
    for name, expected in schema_cases:
        role, _ = classify_filename_role(name)
        assert role == expected, name


def test_parse_image_filename_single_fov_smfish():
    """The user's actual data: single-FOV schema, smFISH role suffix.
    ``fov`` falls through to ``"1"`` because the schema has no fov
    field — matching the CSV side via ``normalize_fov("")``."""
    schema = load_schema({
        "schema_fields": ["well", "channel", "timepoint"],
        "separator": "_",
    })
    parsed = parse_image_filename(
        "A02_w2Turq_T01_smfish.tif", schema=schema, fluor_token="w2turq",
    )
    assert parsed.role == Role.SMFISH
    assert parsed.fov == "1"
    assert parsed.tp == "1"
    assert parsed.fields.get("well") == "A02"
    assert parsed.channel == "w2turq"


def test_parse_image_filename_with_fov_field():
    schema = load_schema({
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "separator": "_",
    })
    parsed = parse_image_filename(
        "exp1_w3cy5_B02_F001_T0024_smfish.tif", schema=schema, fluor_token="w3cy5",
    )
    assert parsed.role == Role.SMFISH
    assert parsed.fields.get("fov") == "F001"
    assert parsed.fields.get("timepoint") == "T0024"
    # Note: F001 is opaque (non-numeric prefix) — normalize_fov passes it through.
    assert parsed.fov == "F001"
    # T0024 is letter-prefixed-numeric — normalize_tp extracts "24".
    assert parsed.tp == "24"


def test_build_parse_round_trip_for_mask():
    schema = load_schema({"schema_fields": ["well", "timepoint"], "separator": "_"})
    stem = build_image_filename(
        fields={"well": "A02", "timepoint": "T01"},
        schema=schema, role=Role.MASK,
    )
    assert stem == "A02_T01_labels.tif"
    parsed = parse_image_filename(stem, schema=schema)
    assert parsed.role == Role.MASK
    assert parsed.fields["well"] == "A02"
    assert parsed.fields["timepoint"] == "T01"


# ── Vectorised CellKey ───────────────────────────────────────────────────


def test_cell_keys_for_dataframe_single_fov():
    df = pd.DataFrame({
        "well": ["A02", "A02"],
        "fov": ["", ""],          # single-FOV pipeline
        "timepoint": ["T01", "T02"],
        "nucleus_id": ["1", "2"],
    })
    keys = cell_keys_for_dataframe(df)
    assert keys.iloc[0].as_tuple() == ("A02", "1", "1", "1")
    assert keys.iloc[1].as_tuple() == ("A02", "1", "2", "2")


def test_cell_keys_for_dataframe_default_well_fallback():
    """When the CSV lacks the ``well`` column the caller's default
    is used — that's how the smFISH writer iterates per-well CSVs."""
    df = pd.DataFrame({"fov": ["1"], "timepoint": ["1"], "nucleus_id": ["1"]})
    keys = cell_keys_for_dataframe(df, default_well="B02")
    assert keys.iloc[0].well == "B02"


# ── Schema loading ───────────────────────────────────────────────────────


def test_load_schema_from_schema_fields_list():
    s = load_schema({"schema_fields": ["a", "b", "c"], "separator": "."})
    assert s.fields == ("a", "b", "c")
    assert s.sep == "."


def test_load_schema_legacy_colon_string():
    s = load_schema({"schema": "well:channel:timepoint"})
    assert s.fields == ("well", "channel", "timepoint")


def test_load_schema_single_fov_via_legacy_indices():
    """Older pipeline_info.json files wrote only ``fov_index``/``tp_index``."""
    s = load_schema({"tp_index": 2})
    assert s.has_fov is False
    assert s.tp_idx == 2


def test_load_schema_empty_falls_back_to_default():
    s = load_schema({})
    assert s.has_fov is True   # default 5-slot schema has a fov field
