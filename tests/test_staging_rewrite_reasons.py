"""Why an upload rewrites a well archive, and when it should not bother.

Reported: *"it says it's rewriting already zipped zips instead of just
transferring them to the remote destination even though all channels are being
used. WHY!?"*

The log said *that* it was rewriting and never *why*. The only number that could
have answered it — the per-well kept/total tick — is emitted after the decision,
on a different line, and never names what the missing members were. A user
watching their plate get copied locally had no way to find out which channel
caused it.

Two things follow, and they are separate:

* the planner now **says what it dropped**, per rewritten well;
* it decides on **compressed bytes rather than member counts**, because
  rewriting costs a full local read and write of the archive and buys only a
  smaller upload — a trade that is plainly bad when the dropped frames are a
  rounding error next to the ones that stay.

**Fixtures here carry the mess of real archives** (CLAUDE.md §7.1 #4): a
`.DS_Store`, an AppleDouble sidecar, a `.png`, and a filename that decomposes to
nothing under the run's schema. Every existing staging test builds archives that
hold nothing but well-formed TIFs, which is exactly the corpus that validates a
happy path and hides everything else.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

# `REWRITE_MIN_SAVING` is introduced by this change, so it is imported inside
# the one test that needs it — a module-level import would make every test in
# the file die of an ImportError when run against pre-fix code, which reads in a
# terminal exactly like "it failed on the base branch" while measuring nothing
# (CLAUDE.md §7.5 #1).
from services.remote_staging import (
    _plan_staging,
    _required_tokens,
)

_SCHEMA = "experiment:channel:well:fov:timepoint"
_BIG = 400_000
_SMALL = 2_000


def _zip(path: Path, members: list[tuple[str, int]]) -> Path:
    """An archive whose members compress to roughly their stated size.

    Random-ish bytes rather than zeros: a zip of zeros compresses to nothing, so
    every `compress_size` would be ~equal and the byte threshold under test
    would never see the proportions the test is describing.
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, size in members:
            zf.writestr(name, bytes((i * 7 + 13) % 251 for i in range(size)))
    return path


def _plan(tmp_path: Path, archives: dict[str, list[tuple[str, int]]], **kw):
    for name, members in archives.items():
        _zip(tmp_path / name, members)
    lines: list[str] = []
    plans, up_to_date, zero = _plan_staging(
        tuple(sorted(tmp_path.glob("*.zip"))),
        keep_tokens=kw.pop("keep_tokens", _required_tokens("RFP", ["GFP"])),
        filename_schema=_SCHEMA, filename_sep="_",
        log_fn=lines.append, **kw,
    )
    return plans, "".join(lines), up_to_date


def _by_stem(plans):
    return {p.src.stem: p for p in plans}


# ── the invariant the whole diagnosis rests on ───────────────────────────────


def test_an_archive_is_rewritten_only_when_something_was_dropped(tmp_path):
    """`keep is None` means "upload the source untouched, copy nothing", and it
    has to hold exactly when nothing was dropped. Everything said about this
    code — including the answer to the report above — depends on that being a
    strict equivalence, and nothing pinned it.
    """
    plans, _log, _ = _plan(tmp_path, {
        # Every image is a selected channel; the junk is not an image at all.
        "A01.zip": [(".DS_Store", 300),
                    ("__MACOSX/._Exp_RFP_A01_f0_t0.tif", 200),
                    ("Exp_RFP_A01_f0_t0.tif", _BIG),
                    ("Exp_GFP_A01_f0_t0.tif", _BIG)],
        # A whole unselected channel, big enough to be worth shedding.
        "B02.zip": [("Exp_RFP_B02_f0_t0.tif", _BIG),
                    ("Exp_GFP_B02_f0_t0.tif", _BIG),
                    ("Exp_TL_B02_f0_t0.tif", _BIG),
                    ("Exp_TL_B02_f1_t0.tif", _BIG)],
    })
    for stem, plan in _by_stem(plans).items():
        dropped = plan.keep is not None and len(plan.keep) < plan.n_members
        assert (plan.keep is None) == (not dropped), stem


def test_junk_members_never_force_a_rewrite(tmp_path):
    """A `.DS_Store` and an AppleDouble sidecar are not channels, so the filter
    must have no opinion on them. Letting it drop them once made every archive
    on a plate look changed."""
    plans, log, _ = _plan(tmp_path, {
        "A01.zip": [(".DS_Store", 300),
                    ("__MACOSX/._Exp_RFP_A01_f0_t0.tif", 200),
                    ("notes.txt", 900),
                    ("Exp_RFP_A01_f0_t0.tif", _BIG),
                    ("Exp_GFP_A01_f0_t0.tif", _BIG)],
    })
    assert _by_stem(plans)["A01"].keep is None
    assert "A01" not in log, "a plate with nothing to drop must stay quiet"


# ── the log now answers "why" ────────────────────────────────────────────────


def test_a_rewrite_names_the_channel_that_caused_it(tmp_path):
    """The defect this file exists for. `TL` is in the files and not in the
    run's channel list, so the archive is rebuilt without it — and the log has
    to say so, because nothing else the run emits can."""
    plans, log, _ = _plan(tmp_path, {
        "B02.zip": [("Exp_RFP_B02_f0_t0.tif", _BIG),
                    ("Exp_GFP_B02_f0_t0.tif", _BIG),
                    ("Exp_TL_B02_f0_t0.tif", _BIG),
                    ("Exp_TL_B02_f1_t0.tif", _BIG)],
    })
    assert _by_stem(plans)["B02"].keep is not None
    assert "rewriting" in log
    assert "no channel match: TL (2)" in log, log
    assert "2 of 4 image(s) dropped" in log, log


def test_a_name_the_schema_cannot_read_is_reported_as_itself(tmp_path):
    """A guessed channel token would be worse than none: "this file's name means
    nothing under the schema you gave" is the actionable answer."""
    plans, log, _ = _plan(tmp_path, {
        "D04.zip": [("Exp_RFP_D04_f0_t0.tif", _BIG),
                    ("Exp_GFP_D04_f0_t0.tif", _BIG),
                    ("scan-settings.tif", _BIG)],
    })
    assert _by_stem(plans)["D04"].keep is not None
    assert "scan-settings.tif (1)" in log, log


def test_an_incremental_drop_names_fov_groups_not_channels(tmp_path):
    """Nothing about channels caused this one, and saying "no channel match"
    would send the user looking at the wrong control."""
    prior = tmp_path / "out"
    prior.mkdir()
    (prior / "measurements_E05.csv").write_text(
        "well,fov,timepoint\nE05,f0,t0\nE05,f1,t0\n")
    plans, log, _ = _plan(tmp_path, {
        "E05.zip": [(f"Exp_{c}_E05_{f}_t0.tif", _BIG)
                    for c in ("RFP", "GFP") for f in ("f0", "f1", "f2")],
    }, incremental=True, prior_output_dir=prior)

    assert _by_stem(plans)["E05"].keep is not None
    assert "already processed: 4 FOV-group(s)" in log, log
    assert "no channel match" not in log, log


# ── the trade is made on bytes, not on counts ────────────────────────────────


def test_a_saving_too_small_to_be_worth_a_local_copy_is_not_taken(tmp_path):
    """Two frames out of twelve can be 40% of an archive or 0.4% of it, and the
    member count cannot tell the difference. Rewriting reads and writes the
    whole archive locally before a byte is uploaded; the remote ignores channels
    it was not asked for anyway, so sending them costs bandwidth and nothing
    else."""
    plans, log, _ = _plan(tmp_path, {
        "C03.zip": [("Exp_RFP_C03_f0_t0.tif", _BIG),
                    ("Exp_GFP_C03_f0_t0.tif", _BIG),
                    ("Exp_TL_C03_f0_t0.tif", _SMALL)],
    })
    plan = _by_stem(plans)["C03"]
    assert plan.keep is None, "rewrote a whole archive to shed a rounding error"
    assert "uploading it as it is" in log, log
    assert "could be dropped" in log, log
    # And it says how little, so the decision is checkable rather than opaque.
    assert "%" in log


def test_a_saving_worth_having_still_rewrites(tmp_path):
    """The other side of the same gate — the threshold must not have quietly
    turned the filter off."""
    plans, log, _ = _plan(tmp_path, {
        "B02.zip": [("Exp_RFP_B02_f0_t0.tif", _BIG),
                    ("Exp_GFP_B02_f0_t0.tif", _BIG),
                    ("Exp_TL_B02_f0_t0.tif", _BIG)],
    })
    assert _by_stem(plans)["B02"].keep is not None
    assert "rewriting" in log


def test_the_threshold_is_a_named_constant_not_a_literal(tmp_path):
    """It is a judgement call that a site may want to move, and a number buried
    in a conditional is one nobody can find."""
    from services.remote_config import REWRITE_MIN_SAVING

    assert 0.0 < REWRITE_MIN_SAVING < 1.0


# ── the sibling predicate that disagreed ─────────────────────────────────────


def test_a_png_survives_a_rewrite(tmp_path):
    """`_is_image_member` claimed to mirror what the pipeline groups and did
    not: it listed .tif/.tiff where `find_image_groups_in_zip` also takes .png,
    .jpg and .jpeg. Such a member was invisible to the planner, so an archive
    rewritten for any other reason was written without it — input the remote
    would have processed, gone with nothing said."""
    plans, _log, _ = _plan(tmp_path, {
        "D04.zip": [("Exp_RFP_D04_f0_t0.tif", _BIG),
                    ("Exp_GFP_D04_f0_t0.tif", _BIG),
                    ("Exp_GFP_D04_f1_t0.png", _BIG),
                    ("Exp_TL_D04_f0_t0.tif", _BIG)],
    })
    plan = _by_stem(plans)["D04"]
    assert plan.keep is not None, "expected a rewrite to drop the TL frame"
    assert "Exp_GFP_D04_f1_t0.png" in plan.keep, (
        "a .png the remote pipeline would have processed was dropped")
    assert plan.n_members == 4, "the png has to count as an image at all"


@pytest.mark.parametrize("name,is_image", [
    ("Exp_GFP_A01_f0_t0.tif", True),
    ("Exp_GFP_A01_f0_t0.TIFF", True),
    ("Exp_GFP_A01_f0_t0.png", True),
    ("Exp_GFP_A01_f0_t0.jpeg", True),
    (".DS_Store", False),
    ("__MACOSX/._Exp_GFP_A01_f0_t0.tif", False),
    ("A01/", False),
    ("notes.txt", False),
])
def test_the_planner_and_the_pipeline_agree_on_what_an_image_is(name, is_image):
    """One question, one answer. These two lists drifted apart once already."""
    from process_microscopy import _GROUP_IMAGE_EXTS
    from services.remote_staging import _is_image_member

    assert _is_image_member(name) is is_image
    if is_image:
        assert Path(name).suffix.lower() in _GROUP_IMAGE_EXTS
