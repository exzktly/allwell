"""The Analyze pane — the form, the run controls, and the live run log.

``AnalyzeTab`` is assembled in :mod:`analyze.tab` from mixins spread across this
package; the split is what keeps each module under the line budgets
``tests/test_monolith_budgets.py`` holds, not a division of responsibility that
callers need to know about.

This package deliberately exports nothing. ``allwell.py`` imports
``AnalyzeTab`` from :mod:`analyze.tab` inside the function that builds the pane,
so that Qt, the mixin chain and everything they pull in stay off the startup
path; re-exporting the class here would import all of it the moment anything
touched ``analyze``.
"""
