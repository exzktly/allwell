"""Qt-free components of the microscopy analysis pipeline.

These modules were extracted from ``process_microscopy.py`` (the orchestrator +
CLI, which stays at the repo root). They carry no ``review`` / ``widgets``
/ Qt imports so the pipeline runs on headless remote workers and CI — the
contract enforced by ``tests/test_architecture_contracts.py``. The ML runtimes
(Torch for Cellpose, TensorFlow for StarDist) are imported lazily inside the
relevant functions, so importing this package pulls in neither.

* :mod:`pipeline.background` — illumination / background-correction math.
* :mod:`pipeline.capacity`   — disk-space accounting: what a run will need on
  the scratch and output volumes, and what is left on them.
* :mod:`pipeline.cellpose`   — Cellpose segmenters (Torch lazy).
* :mod:`pipeline.stardist`   — StarDist segmenters + per-worker model state (TF lazy).
* :mod:`pipeline.io`         — atomic image / CSV / overlay writers.
"""
