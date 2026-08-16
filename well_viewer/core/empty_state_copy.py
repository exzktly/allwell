"""The words every surface uses to say that no dataset is open.

Imports nothing, so the matplotlib sites that paint the same sentence onto an
empty figure can reach it without dragging Qt into a renderer;
``core.ui_helpers`` re-exports it for the widget side. ARCHITECTURE.md
§ "Empty states" has why it is one module.
"""

from __future__ import annotations

#: What is wrong. A statement, not an instruction — the instruction follows.
NO_DATASET_TITLE = "Load a directory."

#: What to do about it, naming the control by the label it wears on screen.
#:
#: The rail control is called ``Open`` — no trailing ellipsis. The convention it
#: used to follow (dots for an item that opens a dialog rather than acting
#: immediately) cost more than it explained here: a sentence that *began* with
#: ``Open…`` read as prose trailing off rather than as a name, which is how the
#: defect was reported. Naming it as a button carries the same information that
#: the dots were there to carry.
NO_DATASET_HINT = "The Open button is at the top of the left rail (⌘O)."

#: The same two lines for a matplotlib canvas, which has no second label to put
#: the hint in.
NO_DATASET_MESSAGE = f"{NO_DATASET_TITLE}\n{NO_DATASET_HINT}"
