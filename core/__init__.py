"""Shared Qt-free core modules used by *both* the analysis pipeline and the GUI.

These carry no ``well_viewer`` / ``widgets`` / Qt imports, so the pipeline
(`process_microscopy` + the `pipeline/` package) can import them on headless
remote workers, while the GUI imports them too — they are the single source of
truth both sides agree on. The self-sufficiency contract is enforced by
``tests/test_architecture_contracts.py``.

* :mod:`core.shape_features` — per-cell ``shape_*`` morphology columns.
* :mod:`core.auto_threshold` — Otsu / threshold helpers.
* :mod:`core.flatfield`      — flat-field illumination estimators.
* :mod:`core.well_token`     — well-token parsing / canonical labels.
* :mod:`core.aw_safe`        — Qt-free logged-swallow helper (pipeline/services).
"""
