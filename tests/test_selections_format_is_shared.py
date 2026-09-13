"""The seam between the shared block format and Review's plotting model.

`domain/selections_format.py` owns the on-disk ``sample_definitions`` block and
`review/domain/selections_model.py` owns what a selection means on a figure.
The split only holds if four things stay true, and none of them is visible to
any test that exercises one side alone.

**One palette, not two.** A selection's ``color`` is baked from the rank of its
lowest well and then *written to disk*. Review bakes it when someone makes a
selection; Datasets re-bakes it in ``merge_plates`` when a remap moves a
condition across the plate. If the two ever baked from different lists, the same
well would be written two colours into the same file and nothing would say so —
a merged plate would simply come back with colours the user did not choose.

That is not hypothetical. Until this module's subject was extracted, the palette
accessor borrowed ``review.domain.plate_layout.WELL_COLORS`` behind a
``try``/``except`` and fell back to a *ten*-colour list whenever the import
failed, which on a 96-well plate repeats a colour every eleventh well. The
fallback is gone and these tests are what keep a second list from growing back.

**No name defined on both sides.** A helper that exists in both modules is the
half-swept state CLAUDE.md §7.5 #5 describes: one concept, two spellings, and
neither one looks wrong at its own site.

**The logger stays parented under ``review``.** The format module's one warning
is the only notice a user gets that a v1 migration dropped a group member, and
the drawer they are watching takes its records from the ``review`` logger with
``propagate = False``. Naming the logger after the package it now lives in —
which is the obvious thing to do, and what shipped — routes it nowhere.

**``rank_color`` survives an empty palette.** An exhausted iterator is truthy
and lists to nothing, so the guard against it cannot be reasoned away as
unreachable; it was, once, and the branch went back in. No caller in the
application passes a palette, which is why this needs a test rather than a
comment.
"""

from __future__ import annotations

import ast
import inspect
import logging

from tests._repo_scan import REPO_ROOT, parse_source

from domain import selections_format as fmt
from domain.well_palette import WELL_COLORS_TUPLE
from review.domain import selections_model as model
from ui import theme


def test_the_palette_a_colour_is_baked_from_is_the_themes_own_list():
    """Not "it has some colours" — the list itself, in order.

    The wrong implementation this has to exclude is the one that shipped for
    years: a ten-entry fallback that is a perfectly good list of colours and
    the wrong one.
    """
    assert fmt.well_colors() == [str(c) for c in WELL_COLORS_TUPLE]
    assert fmt.well_colors() == [str(c) for c in theme.WELL_COLORS_TUPLE]
    assert len(fmt.well_colors()) == 96, (
        "one colour per 96-well plate position — a submultiple makes the "
        "colours repeat down each column, which is what "
        "tests/test_well_color_no_column_repeat.py explains at length")


def test_a_baked_colour_is_the_palette_entry_for_that_wells_rank():
    """The whole contract in one line: ``palette[well_rank(lowest well)]``.

    Spelled out per well rather than as a property, because "the colours
    differ" is satisfied by any palette at all.
    """
    palette = [str(c) for c in WELL_COLORS_TUPLE]
    for token, rank in [("A01", 0), ("A12", 11), ("B01", 12), ("H12", 95)]:
        assert fmt.well_rank(token) == rank
        sel = fmt.make_selection(name=token, wells=[token])
        assert sel["color"] == palette[rank], (
            f"{token} (rank {rank}) baked {sel['color']}, "
            f"palette[{rank}] is {palette[rank]}")


def test_a_selection_keeps_its_colour_through_a_block_round_trip():
    """The path Datasets actually drives, end to end.

    ``merge_sample_definitions`` drops a remapped selection's ``color`` so that
    ``to_block`` re-bakes it from where the wells landed; this pins that the
    re-bake lands on the palette entry for the new position rather than on
    whatever the old one was.
    """
    block = fmt.to_block([{"name": "Control", "wells": ["A01"]}])
    assert block["selections"][0]["color"] == str(WELL_COLORS_TUPLE[0])

    moved = dict(block["selections"][0])
    moved.pop("color")
    moved["wells"] = ["B01"]
    rebaked = fmt.to_block([moved])
    assert rebaked["selections"][0]["color"] == str(WELL_COLORS_TUPLE[12]), (
        "a condition remapped from A01 to B01 must take B01's rank colour; "
        "keeping A01's is the collision merge_plates_compute drops the field "
        "to avoid")


def _target_names(node: ast.AST) -> set[str]:
    """Names bound by an assignment target, unwrapping tuples and lists."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(e) for e in node.elts)) if node.elts else set()
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    return set()                      # an attribute or subscript binds no new name


def _module_level_names(rel: str) -> set[str]:
    """Every name the module binds at module scope — constants included.

    Constants are the half that matters most here and the half the first
    version of this missed: it collected only `def`/`class`, so adding a
    contradictory ``SCHEMA_VERSION = 3`` to Review's side left all five tests
    green. That constant is the on-disk version marker, so two spellings of it
    is the one duplication that could write a block nothing can read back.

    The second version fixed exactly that literal mutation and no more. A
    reviewer got three evasions past it in one pass — ``SCHEMA_VERSION, _x = 3,
    0`` (a tuple target), ``for SCHEMA_VERSION in (3,)`` (a loop target), and a
    ``def`` nested inside an ``if`` — because it matched four node types against
    ``tree.body`` and walked past every other way a module binds a name. Which
    is §7.5 #2 twice over: a guard hardened against the one case someone named
    is a guard hardened against one case.

    So this recurses through every statement that does **not** open a new scope
    — ``if`` / ``try`` / ``with`` / ``for`` / ``while`` and their ``else`` and
    handler bodies are all still module scope — and stops at ``def`` and
    ``class``, whose bodies are not. Stopping there is what keeps it from
    accusing two modules of sharing a name because each has a local called
    ``wells``; the sibling scan in ``test_first_party_imports_resolve`` uses a
    flat ``ast.walk`` instead, which is right for *its* question (what names can
    this module serve) because over-collecting there fails closed and here it
    would fail open in the noisiest possible way.

    **Imports are deliberately not collected.** The question here is which names
    a half *defines*, and an import is the opposite of that — Review's half
    importing ``well_rank`` and ``_clean_wells`` is precisely the borrow this
    file exists to bless. Counting them made the first run of the widened scan
    accuse the two halves of sharing ``re``, ``Any``, ``Iterable``,
    ``annotations`` and both borrowed helpers, which is the fail-open noise the
    paragraph above warns about, arrived at from the other direction.
    """
    names: set[str] = set()

    def visit(body: list) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue              # its body is a scope of its own
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    names.update(_target_names(target))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                names.update(_target_names(node.target))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                names.update(_target_names(node.target))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        names.update(_target_names(item.optional_vars))
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(node, field, None)
                if isinstance(inner, list):
                    visit(inner)
            for handler in getattr(node, "handlers", []) or []:
                if handler.name:
                    names.add(handler.name)
                visit(handler.body)

    visit(parse_source(REPO_ROOT / rel).body)
    return names


def test_neither_half_redefines_a_name_the_other_half_owns():
    """Review's half imports ``well_rank`` and ``_clean_wells`` from the format
    half rather than keeping a copy. A name defined on both sides means the two
    modules have started to drift apart while every test still passes."""
    shared = _module_level_names("domain/selections_format.py") & _module_level_names(
        "review/domain/selections_model.py")
    assert not shared, (
        "these names are defined in both domain/selections_format.py and "
        f"review/domain/selections_model.py: {', '.join(sorted(shared))}. "
        "One of them is a copy — import it from the format half instead.")


#: `domain/plot_groups.py` is the third module of the shared format and is
#: deliberately **not** compared against the other two, so the omission is a
#: recorded decision rather than something the next reader has to rediscover.
#: It carries its own `_unique_name` and `_mint_id`, and both answer a different
#: question from the ones in `selections_format`:
#:
#:     _unique_name("G", {"G"})  ->  "G 2"    here
#:                               ->  "G_v2"   in selections_format
#:     _mint_id(used)            ->  here
#:     _mint_id(used, factory)   ->  in selections_format, so a test can seed it
#:
#: A plot group is a saved well-set the user reloads by name, so it disambiguates
#: the way a file manager does; a selection's `_v2` suffix says a *stored* name
#: collided on load. Merging them would make one of the two lie about what it
#: means, and `_mint_id`'s signatures differ besides. The divergence predates
#: this branch — the branch only moved the two modules into one package, which
#: is what makes them look like they should agree.
def test_plot_groups_really_does_answer_those_questions_differently():
    """The liveness half of the note above.

    A written reason for an omission is worth exactly as much as the fact it
    cites, and it is the kind of fact that quietly stops being true — someone
    aligns the two conventions, and the comment goes on saying they diverge.
    So the divergence is measured rather than asserted in prose: if these two
    ever answer alike, this fails and the note comes out with it.
    """
    from domain import plot_groups as pg

    assert pg._unique_name("G", {"G"}) == "G 2"
    assert fmt._unique_name("G", {"G"}) == "G_v2"
    assert pg._unique_name("G", {"G"}) != fmt._unique_name("G", {"G"}), (
        "plot_groups and selections_format now disambiguate a colliding name "
        "the same way — delete the note above and fold the two helpers together")

    assert len(inspect.signature(pg._mint_id).parameters) == 1
    assert len(inspect.signature(fmt._mint_id).parameters) == 2


def test_reviews_half_borrows_the_two_helpers_it_shares():
    """The other direction of the same rule: the borrow is the mechanism the
    test above relies on, so it is pinned rather than assumed."""
    assert model.well_rank is fmt.well_rank
    assert model._clean_wells is fmt._clean_wells


def test_the_modules_logger_is_parented_under_review_so_the_drawer_sees_it():
    """A ``domain/`` module naming its logger after its own package is a bug.

    This module's one warning — a v1 group whose replicate set went missing
    during the migration — is the only notice a user gets that a saved group
    lost a member, and the surface they are watching while a dataset loads is
    Review's footer drawer. That handler is attached to the ``review`` logger
    with ``propagate = False`` (``review/core/status_view.py``), so a logger
    named ``domain.selections_format`` reaches it through nothing at all.

    That is what shipped when the module moved, and the fix was a rename with a
    comment beside it. A comment is not a guard: the next person to move a
    module out of ``review/`` and rename its logger to match its new package
    does exactly what this branch did, and reintroduces exactly this. So the
    invariant is asserted, and asserted *through the mechanism* — the record is
    pushed at the real handler rather than the name being string-matched.
    """
    assert fmt._logger.name.split(".")[0] == "review", (
        f"the logger is named {fmt._logger.name!r}; it must be parented under "
        "`review` so Review's footer drawer, whose handler sits on the `review` "
        "logger with propagate=False, receives its migration warning")

    seen: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    drawer = logging.getLogger("review")
    handler = _Sink()
    previous, drawer.propagate = drawer.propagate, False
    drawer.addHandler(handler)
    drawer.setLevel(logging.DEBUG)
    try:
        fmt.migrate_v1({"rep_sets": [{"name": "R1", "wells": ["A01"]}],
                        "groups": [{"name": "G1", "members": ["GONE"]}]},
                       bar_active_grp=0)
    finally:
        drawer.removeHandler(handler)
        drawer.propagate = previous

    assert any("unknown rep-set" in line for line in seen), (
        "a v1 group naming a replicate set that is not in the block must warn "
        f"where a Review user can see it; the drawer received {seen!r}")


def test_an_empty_palette_falls_back_rather_than_dividing_by_zero():
    """``rank_color``'s empty-palette branch, which was deleted once as
    unreachable on reasoning that holds only for a sequence.

    An *exhausted iterator* is truthy and lists to nothing, so it takes the
    ``if palette`` branch and arrives with an empty list; without the guard the
    ``% len(pal)`` below raises ``ZeroDivisionError``. No caller in the
    application passes a palette at all, which is exactly why this needs a test
    — deleting the branch again leaves the whole lane green.

    The colour it returns is deliberately **not** what the base branch returned.
    Base fell back to a ten-entry list that no longer exists, so it answered
    ``#5B9BF8``; this answers from the 96, and the modulus changing from 10 to
    96 moves every ``fallback_idx`` with it. That is the better answer for a
    value written to disk, and the point of pinning it is that it was chosen
    rather than inherited.
    """
    palette = [str(c) for c in WELL_COLORS_TUPLE]
    assert fmt.rank_color(["A01"], 0, iter([])) == palette[0]
    assert fmt.rank_color([], 13, iter([])) == palette[13 % 96]
    assert fmt.rank_color([], 97, iter([])) == palette[97 % 96]
    assert fmt.rank_color(["A01"], 0, (c for c in [])) == palette[0]
