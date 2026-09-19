"""Nowhere may claim the focus column is comparable without the caveat.

The per-cell focus column is a transition *width* in pixels, which is what
makes one absolute cut able to serve a whole plate — and the measurement
behind that claim (tracker §D.2f, `pipeline.field_quality.edge_focus`) is
strong on every axis but one. Clipping biases the value **upward**, to 1.45×
when the ceiling cuts well into a nucleus, because a flat-topped nucleus
genuinely does have a steeper transition. `sat_frac` does not bound it: that
number is capped by the fraction of the frame the nuclei cover, so a sparse
plate can have every nucleus flat-topped while `sat_frac` reads two percent.

So a user carrying a cut between plates has to be told, and an earlier
adversarial review found the opposite: four tooltips, two docstrings, the
manual and ARCHITECTURE were all still asserting an unqualified "the same for
every plate taken the same way" that the branch's own measurement had
withdrawn, because the retraction reached the estimator's docstring and
nothing else. CLAUDE.md §7.5 #6 is the mechanism exactly.

**Why this is not a list of banned phrases.** A phrase ban passes against the
wording that actually shipped — "It is comparable between cells, fields, wells
and plates" was the tooltip, and it is a sentence this repository still wants
to be able to write, because it is true. What it may not do is stand alone. So
the rule is positional: a block of text that talks about focus *and* claims
comparability must also mention clipping, in that block or the next one.
"""
from __future__ import annotations

import ast
import re

from tests._repo_scan import REPO_ROOT, parse_source, python_sources, rel

#: The claim. Each is matched against lowercased text.
_CLAIM = re.compile(
    r"comparable between|comparable across|"
    r"same for every plate|every plate taken|"
    r"stable across every plate|carries between plates|"
    r"carry between plates|between plates taken|"
    r"depends (?:mostly )?on (?:the|your) microscope")

#: The caveat that has to ride with it. `sat_frac` is deliberately NOT here:
#: naming the number without saying it fails to bound the bias is the mistake
#: one level up, not the fix.
_CAVEAT = re.compile(r"clip|saturat|flat-topped|exposure permitting")

#: What makes a block about the focus *metric*. A bare "focus" is too loose:
#: `plot_state.focus_timepoint` is a shared timepoint that is "comparable
#: across tabs" and has nothing to do with any of this, and a guard that
#: flags it teaches the next reader to widen the exemption list instead of
#: the regex.
_FOCUS = re.compile(
    r"edge_focus|qc_edge_focus|focus_min|focus_gate|"
    r"out of focus|out-of-focus|focus (?:cut|column|value|metric|gate|"
    r"distribution|evidence|measurement)|per-cell focus|"
    r"focus per cell|boundary sharpness|defocus")

#: Documents held to the same rule. The tracker is exempt and says why in its
#: own prose: §D.2e and §D.2-1b **quote** the retracted claim in order to
#: record that it was retracted, which is the one context where the unqualified
#: sentence is the point.
_DOCS = ("_Docs/Markdowns/ARCHITECTURE.md", "_Docs/Markdowns/README_WellViewer.md")


def _offenders(blocks: list[tuple[str, int, str]]) -> list[str]:
    """Blocks claiming focus comparability with no caveat in reach.

    *blocks* is (where, line, text) in document order. The caveat may land in
    the following block, because a docstring routinely states the claim in one
    paragraph and qualifies it in the next.
    """
    bad = []
    for i, (where, line, text) in enumerate(blocks):
        # Normalised, because every one of these phrases wraps across lines in
        # some file, and a caveat split by a newline is still a caveat. The
        # first draft missed "exposure\n  permitting" for exactly that reason.
        low = " ".join(text.split()).lower()
        if not (_FOCUS.search(low) and _CLAIM.search(low)):
            continue
        reach = low
        if i + 1 < len(blocks):
            reach += " " + " ".join(blocks[i + 1][2].split()).lower()
        if not _CAVEAT.search(reach):
            bad.append(f"{where}:{line}: {' '.join(text.split())[:110]}")
    return bad


def _python_blocks(path) -> list[tuple[str, int, str]]:
    """Every string constant, plus every contiguous run of `#` comments."""
    where = rel(path)
    out: list[tuple[str, int, str]] = []
    for node in ast.walk(parse_source(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((where, node.lineno, node.value))
    run: list[str] = []
    start = 0
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            if not run:
                start = n
            run.append(stripped.lstrip("#").strip())
            continue
        if run:
            out.append((where, start, " ".join(run)))
            run = []
    if run:
        out.append((where, start, " ".join(run)))
    return sorted(out, key=lambda t: t[1])


def _markdown_blocks(path) -> list[tuple[str, int, str]]:
    where = rel(path)
    out, buf, start = [], [], 1
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if raw.strip():
            if not buf:
                start = n
            buf.append(raw)
            continue
        if buf:
            out.append((where, start, " ".join(buf)))
            buf = []
    if buf:
        out.append((where, start, " ".join(buf)))
    return out


def test_no_source_file_claims_comparability_without_the_clipping_caveat():
    bad: list[str] = []
    for path in python_sources():
        bad += _offenders(_python_blocks(path))
    assert not bad, (
        "these say the focus column carries between plates and do not say "
        "clipping is the exception:\n  " + "\n  ".join(bad))


def test_no_live_document_claims_comparability_without_the_clipping_caveat():
    bad: list[str] = []
    for name in _DOCS:
        bad += _offenders(_markdown_blocks(REPO_ROOT / name))
    assert not bad, (
        "these document the focus column as carrying between plates and do "
        "not say clipping is the exception:\n  " + "\n  ".join(bad))


def test_the_estimators_own_docstring_states_the_measured_table():
    """The one place the numbers live, so the surfaces can stay short.

    Without this, the sentence "clipping is the exception" is a claim with no
    measurement behind it anywhere in the tree, which is the shape §7.7 #5
    warns about: prose describing a property is worth what the property is
    worth, and writing it well is how that gets worse.
    """
    from pipeline.field_quality import edge_focus

    doc = (edge_focus.__doc__ or "").lower()
    for token in ("width", "saturation", "1.45x", "monotone", "gain and pedestal"):
        assert token in doc, f"edge_focus's docstring no longer states {token!r}"
