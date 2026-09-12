"""Regression: a persisted QC debris gate applies on reopen (not just on
re-entry).

The QC debris bounds are saved to persistence. On reopen they were restored
into the gate's fields, but ``qc_gate_refresh_widgets`` set the enable checkbox
before the field values, firing the toggle handler → ``_apply`` → reading the
still-empty fields and clobbering the loaded bounds back to ``None``. The gate
then "appeared" populated but didn't actually filter until a field was
re-entered. Refreshing now blocks signals, so the loaded bounds survive.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_qc_gate_applies_on_reopen(loaded_app, mini_dataset):
    from review.features.qc.controller import set_qc_params, get_qc_params
    from review.domain.qc_params import QCParams
    from review.data.data_loading import df_included_mask
    from review.persistence.qc_params import save_to_data_dir

    app = loaded_app
    well = next(iter(app._app_state.dataset.well_paths))
    before = int(df_included_mask(app._get_rows(well)).sum())

    # Enable the gate with a min-area bound that excludes the smaller cells.
    set_qc_params(app._ctx, QCParams(enabled=True, min_area=250.0))
    gated = int(df_included_mask(app._get_rows(well)).sum())
    assert gated < before, "QC gate should exclude small cells"

    # Persist to disk, bypassing the debounce. Takes ``ctx``, not the app —
    # ``ctx.window`` is how it reaches the window, and on a real ReviewApp
    # that attribute is Qt's own ``QWidget.window`` method, so passing the app
    # fails downstream instead of at the boundary.
    save_to_data_dir(app._ctx)
    app._load_path(mini_dataset)   # reopen the dataset

    # The bounds must survive the reopen AND actually filter, without the user
    # re-entering anything.
    p = get_qc_params(app._ctx)
    assert p.enabled and p.min_area == 250.0
    assert int(df_included_mask(app._get_rows(well)).sum()) == gated
