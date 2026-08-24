"""A path the form cannot resolve must offer no resume, not another plate's.

`_current_output_dir` had two answers where the question has three. `""` meant
both "the field is empty" and "the field names something I could not turn into
an output folder" — a path that does not exist, a volume that has gone away, a
planner that raised.

`_resumable` reads `""` as "no folder named, so offer the newest unfinished run
*anywhere*", which is right for an empty form and wrong for a typo'd path. The
banner then described another dataset's half-finished run as though it were the
plate on screen, and accepting it would have started an incremental run against
that dataset's output directory.

The empty-form behaviour is deliberate and documented in the manual, so it is
pinned here too: narrowing the offer to nothing in *both* cases would be a
different regression, and one nobody would notice until they wanted the feature.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


class _Form:
    """The surface `_current_output_dir` touches, and nothing else."""

    def __init__(self, text: str):
        self._text = text
        import analyze_suspend
        # `getattr` with a default, so this stub still stands up against code
        # that predates the constant — otherwise the test below raises
        # AttributeError instead of reaching its assertion, and proves nothing.
        self.UNRESOLVED = getattr(
            analyze_suspend.AnalyzeSuspendMixin, "UNRESOLVED", "\x00unresolved")

    class _Edit:
        def __init__(self, text):
            self._t = text

        def text(self):
            return self._t

    @property
    def _input_edit(self):
        return self._Edit(self._text)


def _resolve(text: str) -> str:
    import analyze_suspend

    form = _Form(text)
    return analyze_suspend.AnalyzeSuspendMixin._current_output_dir(form)


def test_an_empty_field_is_not_the_same_answer_as_an_unresolvable_one():
    import analyze_suspend

    assert _resolve("") == ""
    assert _resolve("   ") == ""

    got = _resolve("/nonexistent/definitely/not/a/plate")
    assert got == analyze_suspend.AnalyzeSuspendMixin.UNRESOLVED, (
        f"an unresolvable input resolved to {got!r}; if that is the empty "
        f"string, the resume banner treats it as 'no folder named' and offers "
        f"the newest unfinished run on any plate")


def test_an_unresolvable_folder_is_offered_nothing():
    """The behaviour, driven through `_resumable` itself."""
    import analyze_suspend

    mixin = analyze_suspend.AnalyzeSuspendMixin
    offered: list = []

    class _Stub:
        UNRESOLVED = mixin.UNRESOLVED

        def _current_output_dir(self):
            return mixin.UNRESOLVED

        def _suspended_offers(self, *, only_dir=""):
            offered.append(("suspend", only_dir))
            return []

        def _journal_offers(self, *, only_dir=""):
            offered.append(("journal", only_dir))
            return []

    assert mixin._resumable(_Stub()) is None, (
        "a form naming an input that cannot be resolved was offered a resume "
        "for some other plate")


def test_an_empty_form_still_gets_the_newest_run_anywhere():
    """The documented behaviour, kept. Refusing here too would be a different
    regression, invisible until someone wanted the feature."""
    import analyze_suspend

    mixin = analyze_suspend.AnalyzeSuspendMixin
    sentinel = object()

    class _Stub:
        UNRESOLVED = mixin.UNRESOLVED

        def _current_output_dir(self):
            return ""

        def _suspended_offers(self, *, only_dir=""):
            return []

        def _journal_offers(self, *, only_dir=""):
            return [_Offer()]

    class _Offer:
        at = 1.0
        from_journal = True

    got = mixin._resumable(_Stub())
    assert got is not None and got is not sentinel, (
        "an empty form no longer offers the newest unfinished run, which the "
        "manual describes as the feature")


def test_the_two_cases_do_not_share_an_answer():
    """Stated without naming the new constant, so it measures the behaviour.

    Every other test here refers to `UNRESOLVED`, which cannot exist on the
    code being fixed — so against that code they raise `AttributeError` and
    prove nothing (§7.3). This one asks only the question that matters: are
    "the field is empty" and "the field names something unresolvable" the same
    answer? They were, and that is the whole defect.
    """
    assert _resolve("/nonexistent/definitely/not/a/plate") != _resolve(""), (
        "an unresolvable input and an empty field give the same answer, so "
        "_resumable treats a typo'd path as 'no folder named' and offers the "
        "newest unfinished run on any plate")
