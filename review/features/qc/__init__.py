"""QC layer — non-destructive, composable debris gating editable post-run.

See :mod:`review.features.qc.params` for the verdict model and
:mod:`review.features.qc.controller` for the app-facing projection /
re-render glue. The sidebar view lives in
:mod:`review.features.qc.tab_view`.
"""

from .params import QCParams, compute_qc_pass  # noqa: F401
