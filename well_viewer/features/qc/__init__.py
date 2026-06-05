"""QC layer — non-destructive, composable debris gating editable post-run.

See :mod:`well_viewer.features.qc.params` for the verdict model and
:mod:`well_viewer.features.qc.controller` for the app-facing projection /
re-render glue. The sidebar view lives in
:mod:`well_viewer.features.qc.tab_view`.
"""

from .params import QCParams, compute_qc_pass  # noqa: F401
