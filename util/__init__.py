"""General-purpose Qt-free utilities shared across the app.

These carry no ``well_viewer`` / ``widgets`` / Qt imports and are not specific
to the analysis pipeline — they're used by the GUI, the pipeline, and the
services layer alike:

* :mod:`util.well_token` — canonical 96-well token parsing / labels.
* :mod:`util.aw_safe`    — logged exception-swallow helper (non-GUI side).
"""
