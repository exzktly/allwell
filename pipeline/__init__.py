"""Qt-free components of the microscopy analysis pipeline.

These modules were extracted from ``process_microscopy.py`` (the orchestrator +
CLI, which stays at the repo root). They carry no ``well_viewer`` / ``widgets``
/ Qt imports so the pipeline runs on headless remote workers and CI — the
contract enforced by ``tests/test_architecture_contracts.py``. The ML runtime
(TensorFlow for StarDist) is imported lazily inside the relevant functions, so
importing this package pulls it in only when a segmentation actually runs.

* :mod:`pipeline.background` — illumination / background-correction math.
* :mod:`pipeline.stardist`   — StarDist segmenters + per-worker model state (TF lazy).
* :mod:`pipeline.io`         — atomic image / CSV / overlay writers.
"""
