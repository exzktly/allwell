"""The Analyze pane's Qt-free half: parsing, validation and run description.

Everything here is testable without building a window, which is the point of the
directory rather than a side effect of it. `analyze/` holds sixteen modules and
twelve of them import Qt; before this package existed nothing in the listing
said which four did not, so the cheap tests and the expensive ones looked alike.

The same split has two other spellings in this repository, and all three mean
the same thing: `review/domain/` and `review/data/` are directories, and a
Datasets tool puts its pure logic in `<tool>/<tool>_compute.py`. A pane
separates its Qt-free logic under a name that says so; which spelling depends on
whether the pane's logic is one body (a directory) or one file per tool (a
suffix).

Nothing here may import `PySide6`, and `tests/test_qt_free_layers.py` says so.
That is the whole contract: a module that grows a widget has stopped being this
half of the pane and belongs beside `analyze/tab.py`.
"""
