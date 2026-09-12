"""A plate's text columns are read through ``domain.text_columns``, not filled.

``data_loading._categorize_text_columns`` stores the six text columns of a
loaded plate as ``pd.Categorical``. That is invisible to a reader and fatal to a
filler: ``Series.fillna(value)`` validates *value* against the category list
whether or not any cell needs filling, so

    df["fov"].fillna("1")

raises ``TypeError: Cannot setitem on a Categorical with a new category (1)``
on every plate whose field labels do not happen to include the literal ``"1"``
— fields numbered from zero, zero-padded labels, a plate whose only field is
``2``. It is a crash on dataset load, in the Review pane, before the dataset
finishes opening.

That shipped. The commit that introduced the categories made ``""`` a category
for exactly this reason, having found ``fov_integrity.check_fov_labels``'s
``fillna("")``. It did not look for the other fill value: five call sites
normalise the FOV column with ``fillna("1")`` and every one of them was a
crash waiting for the first plate whose fields are not numbered from one.

So the rule is not "add the fill values to the categories" — the next fill value
someone writes would not be in the list either, and a category list wider than
the observed values makes ``groupby`` fabricate a group per unused category.
The rule is that these columns are normalised in one place,
``domain/text_columns.py``, whose helpers widen a categorical to
``object`` before touching it.

Decidable from the syntax tree: the column name is a literal at every call site
that matters.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: The six columns ``load_well_csv`` may store as categories. Restated here
#: rather than imported so this guard can be run against a base branch that
#: predates ``domain.text_columns`` — a guard that dies on an import measures
#: nothing, and reads in a terminal exactly like one that caught something.
#: ``test_the_column_roster_matches_the_app`` is what keeps the copy honest.
TEXT_COLUMNS = frozenset({
    "filename", "experiment", "channel", "well", "fov", "timepoint",
})

#: Methods that leave the categorical behind. A chain that passes through one
#: of these is holding an object/str series by the time ``fillna`` runs, which
#: is safe — ``df["fov"].astype(str).fillna("")`` cannot raise.
_ESCAPES = {"astype", "map", "apply", "to_numeric", "str", "replace", "where"}

_HELPERS = "as_text / text_column / fov_text / timepoint_text"


def _column_name(node: ast.AST) -> str | None:
    """The literal text-column name a ``Subscript`` reads, or ``None``.

    Handles both spellings that appear in the app: ``df["fov"]`` and
    ``df.loc[mask, "timepoint"]``, whose slice is a tuple.
    """
    if not isinstance(node, ast.Subscript):
        return None
    sl = node.slice
    parts: list[ast.AST] = list(sl.elts) if isinstance(sl, ast.Tuple) else [sl]
    for part in parts:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            if part.value.strip().lower() in TEXT_COLUMNS:
                return part.value
    return None


def _receiver_column(node: ast.AST) -> str | None:
    """The text column a call's receiver chain bottoms out at, if any.

    Walks back through the chain (``df["fov"].copy().fillna(...)`` is the same
    mistake as the direct call) and gives up at the first method that would
    have converted the categorical away.
    """
    seen = 0
    while seen < 8:
        seen += 1
        name = _column_name(node)
        if name is not None:
            return name
        if isinstance(node, ast.Call):
            node = node.func
            continue
        if isinstance(node, ast.Attribute):
            if node.attr in _ESCAPES:
                return None
            node = node.value
            continue
        return None
    return None


def offenders_in(source: str, path: str) -> list[str]:
    """Every direct fill of a text column in *source*."""
    try:
        tree = ast.parse(source, path)
    except SyntaxError:  # pragma: no cover - a file we cannot parse
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr != "fillna":
            continue
        column = _receiver_column(fn.value)
        if column is None:
            continue
        out.append(
            f"{path}:{node.lineno} `[{column!r}].fillna(...)` raises on a "
            f"plate whose {column!r} column is stored as categories — read it "
            f"through domain.text_columns ({_HELPERS})"
        )
    return out


def test_the_column_roster_matches_the_app() -> None:
    """The restated roster above is the loader's own set, not a stale copy."""
    from review.data.data_loading import _STRING_COLS
    from domain.text_columns import TEXT_COLUMNS as APP_COLUMNS

    assert set(TEXT_COLUMNS) == set(APP_COLUMNS) == set(_STRING_COLS), (
        "this guard's copy of the text-column roster has drifted from "
        "domain.text_columns.TEXT_COLUMNS"
    )


def test_text_columns_are_not_filled_in_place() -> None:
    offenders: list[str] = []
    for path in python_sources():
        offenders.extend(offenders_in(path.read_text(encoding="utf-8"), rel(path)))
    assert not offenders, (
        "A plate's text columns are categorical after loading, and `fillna` "
        "validates its fill value against the categories:\n  "
        + "\n  ".join(offenders)
    )
