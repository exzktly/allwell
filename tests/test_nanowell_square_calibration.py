"""The central square's threshold calibration (``pipeline.nanowell_square_calibration``) on hand-built grid records.

The command labels each timepoint's square guess right or wrong from the anchors set by hand (same nanowell as the
click or not) and from the identity check (a square the run accepted, confirmed or contradicted *because of the
square*), and suggests the (margin, ratio) pair that accepts the most right guesses and no wrong one. The fixtures
put the right guesses below today's thresholds and a wrong guess where the obvious answer — the weakest right
guess's lead and ratio — would accept it, so a recommendation that ignored the wrong guesses reads differently.
"""

from __future__ import annotations

import json
import math
import random
from types import SimpleNamespace

import pytest

import pipeline.nanowell_square_calibration as cal
from pipeline.nanowell_lattice import fiducial_accepted
from util.nanowell_anchors import anchors_path, read_anchors, set_anchor

PITCH = 100.0


def _block(best, runner_up, guess, *, status="unaddressed", method=None):
    return {"status": status, "origin_method": method, "pitch_um": PITCH,
            "fiducial": {"best": best, "runner_up": runner_up, "position_um": list(guess) if guess else None}}


def _dataset(tmp_path, wells: dict, anchors=()):
    """``tmp/out/nanowells/<well>_grid.json`` for *wells* (``well -> {tp: block}``) and the anchors in ``tmp/in``."""
    folder = tmp_path / "out" / "nanowells"
    folder.mkdir(parents=True)
    for well, blocks in wells.items():
        (folder / f"{well}_grid.json").write_text(json.dumps({"well": well, "timepoints": blocks}))
    (tmp_path / "in").mkdir()
    for well, tp, xy in anchors:
        set_anchor(anchors_path(tmp_path / "in"), well, tp, xy)
    return tmp_path / "out", read_anchors(anchors_path(tmp_path / "in"))


#: Two right guesses refused today (lead under 0.15 or ratio under 3) and one wrong one between them: the weakest
#: right guess's lead (0.09) and ratio (1.5) together would accept it (lead 0.10, ratio 1.7).
WELLS = {
    "A01": {"0h": _block(0.27, 0.18, (1000.0, 1000.0))},         # right: lead 0.09, ratio 1.5 (anchored 30 um off)
    "A02": {"0h": _block(0.33, 0.20, (2000.0, 2000.0))},         # right: lead 0.13, ratio 1.65
    "B01": {"0h": _block(0.2429, 0.1429, (3000.0, 3000.0))},     # wrong: lead 0.10, ratio 1.7; clicked 2 pitches away
    "B02": {"0h": _block(0.30, 0.25, (4000.0, 4000.0))},         # unlabelled, refused today
}
ANCHORS = (("A01", "0h", (1030.0, 1000.0)), ("A02", "0h", (2000.0, 1990.0)), ("B01", "0h", (3200.0, 3000.0)))


def test_a_guess_is_right_when_it_is_the_nanowell_clicked_and_wrong_otherwise(tmp_path):
    out, anchors = _dataset(tmp_path, WELLS, ANCHORS)
    examples, counts = cal.collect(out, anchors)
    labels = {e.well: (e.label, e.evidence, round(e.distance_pitches, 2) if e.distance_pitches is not None else None)
              for e in examples}
    assert labels == {"A01": ("right", "anchor", 0.3), "A02": ("right", "anchor", 0.1),
                      "B01": ("wrong", "anchor", 2.0), "B02": (None, "", None)}
    assert counts == {"wells": 4, "timepoints": 4, "no_guess": 0, "anchor_without_guess": 0}


def test_a_timepoint_without_a_guess_is_counted_and_its_anchor_said_to_have_nothing_to_compare(tmp_path):
    out, anchors = _dataset(tmp_path, {"A01": {"0h": _block(0.2, None, None), "24h": _block(0.3, 0.1, None)}},
                            [("A01", "0h", (1.0, 1.0))])
    examples, counts = cal.collect(out, anchors)
    assert examples == []
    assert (counts["no_guess"], counts["anchor_without_guess"]) == (2, 1)


def test_accepts_is_the_pipeline_s_own_rule():
    """The calibration must judge a pair by the rule the run applies, or its suggestion means nothing to the run."""
    rng = random.Random(3)
    for _ in range(2000):
        best, runner = rng.uniform(0, 1), rng.uniform(0, 1)
        margin, ratio = rng.choice(cal.MARGINS), rng.choice(cal.RATIOS)
        example = cal.Example("A01", "0h", "", "", best, runner)
        assert cal.accepts(example, margin, ratio) == fiducial_accepted(
            SimpleNamespace(score=best, runner_up_score=runner), margin, ratio), (best, runner, margin, ratio)


def test_the_suggestion_accepts_no_wrong_guess_even_where_the_weakest_right_one_would(tmp_path):
    out, anchors = _dataset(tmp_path, WELLS, ANCHORS)
    examples, _ = cal.collect(out, anchors)
    current = (0.15, 3.0)
    now = cal.tally(examples, *current, current)
    assert (now.right, now.wrong) == (0, 0)
    # The weakest right guess's own lead and ratio would let the wrong one in: what the search must not suggest.
    naive = cal.tally(examples, 0.08, 1.5, current)
    assert (naive.right, naive.wrong) == (2, 1)
    pick = cal.recommend(examples, current)
    # Only one of the two right guesses can be kept with the wrong one out; the strictest pair that keeps one is
    # A02's (lead 0.13 > 0.12, ratio 1.65 >= 1.65), with B01 out on its lead of 0.10.
    assert (pick.margin, pick.ratio, pick.right, pick.wrong) == (0.12, 1.65, 1, 0)
    assert pick.refused_now == 1                           # A02; B02's lead of 0.05 stays refused


def test_identity_labels_only_squares_the_run_accepted_and_only_for_the_square(tmp_path, monkeypatch):
    blocks = {"0h": _block(0.8, 0.05, (1.0, 1.0), status="ok", method="fiducial"),
              "12h": _block(0.7, 0.1, (1.0, 1.0), status="ok", method="fiducial"),
              "24h": _block(0.6, 0.1, (1.0, 1.0), status="ok", method="fiducial"),
              "36h": _block(0.6, 0.1, (1.0, 1.0), status="ok", method="array_centre"),
              "48h": _block(0.5, 0.1, (1.0, 1.0), status="ok", method="fiducial")}
    out, anchors = _dataset(tmp_path, {"A01": blocks}, [("A01", "48h", (500.0, 1.0))])
    sidecar = {"timepoints": {
        "0h": {"verdict": "confirmed"},
        "12h": {"verdict": "contradicted", "square_agreement_pitches": 0.6},
        "24h": {"verdict": "contradicted", "square_agreement_pitches": 0.1},     # contradicted for another reason
        "36h": {"verdict": "confirmed"},                                        # not the square's doing
        "48h": {"verdict": "confirmed"}}}                                       # the anchor outranks it
    monkeypatch.setattr("pipeline.nanowell_identity.read_sidecar", lambda out_dir, well, **k: sidecar)
    examples, _ = cal.collect(out, anchors)
    assert {e.timepoint: (e.label, e.evidence) for e in examples} == {
        "0h": ("right", "identity"), "12h": ("wrong", "identity"), "24h": (None, ""), "36h": (None, ""),
        "48h": ("wrong", "anchor")}


def test_the_summary_says_what_today_s_thresholds_do_and_what_to_set(tmp_path):
    out, anchors = _dataset(tmp_path, WELLS, ANCHORS)
    examples, counts = cal.collect(out, anchors)
    text = "\n".join(cal.summary_lines(examples, counts, (0.15, 3.0)))
    assert "They refuse 4 of the 4 guesses." in text
    assert "Guesses checked against your anchors: 3 (2 on the nanowell you clicked, 1 elsewhere)." in text
    assert "Now: 0 of 2 right guesses accepted, 0 of 1 wrong ones." in text
    assert ("Suggested: lead > 0.12 and best >= 1.65 x runner-up — accepts 1 of 2 right guesses and none of the 1 "
            "wrong ones, and would accept 1 of the 4 guesses refused today.") in text
    assert "Only 1 wrong guess(es) to test against" in text


def test_thresholds_that_already_accept_every_right_guess_are_left_alone(tmp_path):
    wells = {"A01": {"0h": _block(0.8, 0.05, (1.0, 1.0), status="ok", method="manual")}}
    out, anchors = _dataset(tmp_path, wells, [("A01", "0h", (1.0, 1.0))])
    examples, counts = cal.collect(out, anchors)
    text = "\n".join(cal.summary_lines(examples, counts, (0.15, 3.0)))
    assert "nothing to change" in text and "Suggested" not in text


def test_the_command(tmp_path, capsys):
    out, _ = _dataset(tmp_path, WELLS, ANCHORS)
    assert cal.main([str(out)]) == 0
    assert "Suggested: lead > 0.12" in capsys.readouterr().out
    assert cal.main([str(out), "--csv"]) == 0
    rows = capsys.readouterr().out.splitlines()
    assert rows[0] == ",".join(cal.CSV_COLUMNS) and len(rows) == 5
    assert any(r.startswith("B01,0h,unaddressed,,0.2429,0.1429,0.1,1.7,False,wrong,anchor,2.0") for r in rows), rows
    (tmp_path / "elsewhere").mkdir()
    assert cal.main([str(tmp_path / "elsewhere")]) == 2                           # no nanowells/ folder
    anchors_path(tmp_path / "in").write_text("{not json")
    assert cal.main([str(out)]) == 2
    assert "cannot be used" in capsys.readouterr().out


def test_the_output_folder_is_found_from_the_dataset_folder_or_its_nanowells_folder(tmp_path, capsys):
    out, _ = _dataset(tmp_path, WELLS, ANCHORS)
    # The last two as Windows' command line hands over "…\out\" typed with a trailing backslash.
    for given in (out, tmp_path, out / "nanowells", f'{out}"', f'{tmp_path}"'):
        assert cal.find_output_dir(given) == (out, ""), given
    assert cal.main([str(tmp_path)]) == 0
    assert "Suggested: lead > 0.12" in capsys.readouterr().out


def test_a_folder_that_is_not_a_dataset_says_what_it_resolved_to_and_holds(tmp_path):
    (tmp_path / "plate").mkdir()
    (tmp_path / "plate" / "notes.txt").write_text("x")
    found, why = cal.find_output_dir(tmp_path / "plate")
    assert found is None and "holds no nanowells/ folder" in why and "It holds: notes.txt" in why
    found, why = cal.find_output_dir(tmp_path / "missing")
    assert found is None and "does not exist" in why and "quote a path" in why


def test_an_unreadable_folder_is_named_as_unreadable(tmp_path, monkeypatch):
    import os

    (tmp_path / "out").mkdir()

    def refuse(path):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "listdir", refuse)
    found, why = cal.find_output_dir(tmp_path)
    assert found is None and "permission denied" in why and "Privacy & Security" in why


def test_where_the_plate_stands_is_read_as_review_reads_it(tmp_path, monkeypatch):
    """Each addressed timepoint by its identity verdict and why, the stage flag overruling a good image verdict as in
    Review, *not checked* without a sidecar, and each timepoint the run did not address by its status alone."""
    blocks = {"0h": _block(0.8, 0.05, (1.0, 1.0), status="ok", method="fiducial"),
              "12h": _block(0.7, 0.1, (1.0, 1.0), status="ok", method="fiducial"),
              "24h": _block(0.7, 0.1, (1.0, 1.0), status="ok", method="plate_rigid"),
              "36h": _block(0.7, 0.1, (1.0, 1.0), status="ok", method="fiducial"),
              "48h": _block(None, None, None)}
    out, _ = _dataset(tmp_path, {"A01": blocks, "A02": {"0h": _block(0.8, 0.05, (1.0, 1.0), status="ok",
                                                                      method="fiducial")}})
    sidecars = {"A01": {"timepoints": {
        "0h": {"verdict": "reference"},
        "12h": {"verdict": "inconclusive", "reason": "only 3 address(es) in common with the reference, 8 needed"},
        "24h": {"verdict": "confirmed"},
        "36h": {"verdict": "confirmed"}},
        "stage": {"timepoints": {"36h": {"ok": False, "reason": "the plate's rigid motion puts it 40 um away"}}}}}
    monkeypatch.setattr("pipeline.nanowell_identity.read_sidecar", lambda out_dir, well, **k: sidecars.get(well))
    assert cal.standing(out) == {
        ("ok (fiducial)", "reference", ""): 1,
        ("ok (fiducial)", "inconclusive", "too_few"): 1,
        ("ok (plate_rigid)", "confirmed", ""): 1,
        ("ok (fiducial)", "contradicted", "stage"): 1,
        ("unaddressed", "", ""): 1,
        ("ok (fiducial)", "not_checked", "no current identity result"): 1}
    lines = cal.standing_lines(out)
    assert lines[0] == "Where the 6 timepoint(s) stand (grid status, identity verdict, why):"
    assert "Partial" not in "\n".join(lines)                    # none here, so no partial table
    assert "      1  ok (fiducial) · inconclusive · too_few" in lines
    assert "      1  unaddressed" in lines


def test_partial_and_centre_inconclusive_timepoints_are_measured(tmp_path, monkeypatch):
    """What decides whether a partial timepoint or one inconclusive on the array's outline needs a look: how much the
    first leaves out, and how far the second's outline lands. Shares are put either side of each band's edge."""
    ok = {"status": "ok", "origin_method": "fiducial"}
    blocks = {tp: dict(ok) for tp in ("t1", "t2", "t3", "t4", "t5", "t6")}
    out, _ = _dataset(tmp_path, {"A01": blocks})
    left = lambda n, why="wrong_name": [[0, i, why] for i in range(n)]               # noqa: E731
    sidecar = {"timepoints": {
        "t1": {"verdict": "partial", "compared": 200, "excluded": left(2)},                       # 1%: first band
        "t2": {"verdict": "partial", "compared": 200, "excluded": left(3)},                       # 1.5%: second
        "t3": {"verdict": "partial", "compared": 100, "excluded": left(30, "misregistered_tile")},  # 30%: fourth
        "t4": {"verdict": "inconclusive", "reason": CENTRE_REASON, "centre_agreement_pitches": 0.8},
        "t5": {"verdict": "inconclusive", "reason": CENTRE_REASON, "centre_agreement_pitches": 1.3},
        "t6": {"verdict": "inconclusive", "reason": "only 3 address(es) in common with the reference, 6 needed"}}}
    monkeypatch.setattr("pipeline.nanowell_identity.read_sidecar", lambda out_dir, well, **k: sidecar)
    found = cal.survey(out)
    assert found.centre == [0.8, 1.3]                             # t6 is inconclusive for another reason
    lines = cal.standing_lines(out)
    text = "\n".join(lines)
    assert ("Partial timepoints (3): nanowells left out per timepoint, min / median / max 2 / 3 / 30; as a share of "
            "those compared:") in text
    assert [ln for ln in lines if ln.endswith(("up to 1%", "up to 5%", "up to 20%", "up to half"))] == [
        "      1  up to 1%", "      1  over 1%, up to 5%", "      0  over 5%, up to 20%", "      1  over 20%, up to half"]
    assert "  nanowells left out, by why: misregistered_tile 30, wrong_name 5" in lines
    assert "Inconclusive on the array's fitted centre (2): distance in pitches, min / median / max 0.80 / 1.30 / 1.30:" \
        in text
    assert [ln for ln in lines if ln.endswith(("0.75 to 1.0", "1.0 to 1.25", "1.25 to 1.5"))] == [
        "      1  0.75 to 1.0", "      0  1.0 to 1.25", "      1  1.25 to 1.5"]


CENTRE_REASON = ("every address lands on itself, but the array's fitted centre lands 0.80 pitch from this timepoint's "
                 "once registered on the square: the square may have moved against the array (a decoy), or a fitted "
                 "rim is loose")


def test_the_packaged_app_reaches_the_command():
    """The launcher is never imported in a test (its docstring says why): its source and the spec are read."""
    from tests._repo_scan import REPO_ROOT

    source = (REPO_ROOT / "all_well_launcher.py").read_text(encoding="utf-8")
    assert f'sys.argv[1] == "{cal.FROZEN_SENTINEL}"' in source
    assert "sys.exit(calibration_main(sys.argv[2:]))" in source
    assert source.index(cal.FROZEN_SENTINEL) < source.index("from allwell import main")
    spec = (REPO_ROOT / "_Docs" / "_Installation" / "all_well.spec").read_text(encoding="utf-8")
    assert '"pipeline.nanowell_square_calibration"' in spec


@pytest.mark.parametrize("value", [None, "x", math.inf, math.nan])
def test_an_unreadable_score_is_no_guess(value):
    assert cal._number(value) is None
