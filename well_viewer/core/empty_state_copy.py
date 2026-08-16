"""The words every surface uses to say that no dataset is open.

One home for the sentence, because it is said in two different media: the
:class:`widgets.empty_state.EmptyState` placeholder that covers a tab's body,
and matplotlib text painted onto an otherwise-empty figure so an *exported* copy
states its own shortfall too. The canvas sites cannot reach the widget helper's
constants without dragging Qt into a renderer, so the strings live here, in a
module that imports nothing at all, and ``core.ui_helpers`` re-exports them for
the widget side.

Three spellings of one sentence were in the tree before this module existed, and
they had already drifted apart in punctuation. ``tests/test_no_dataset_copy.py``
fails on a fourth.
"""

from __future__ import annotations

#: What is wrong. A statement, not an instruction — the instruction follows.
NO_DATASET_TITLE = "Load a directory."

#: What to do about it, naming the control by the label it wears on screen.
#:
#: "The Open… button" rather than a bare "Open…": the trailing ellipsis is part
#: of the control's name (the convention for an item that opens a dialog rather
#: than acting immediately), and the rail head does read ``No dataset — Open…``.
#: But a sentence *beginning* with it reads as prose trailing off, which is how
#: it was reported. Naming the thing as a button settles what the dots are.
NO_DATASET_HINT = "The Open… button is at the top of the left rail (⌘O)."

#: The same two lines for a matplotlib canvas, which has no second label to put
#: the hint in.
NO_DATASET_MESSAGE = f"{NO_DATASET_TITLE}\n{NO_DATASET_HINT}"
