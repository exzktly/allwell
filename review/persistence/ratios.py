"""Ratio metric persistence — ``ratios`` section of ``persistence.json``.

Legacy ``ratios.json`` sidecars are migrated on first load by ``_doc``.
"""

from __future__ import annotations

import logging

from review.persistence import _doc
from review.domain.ratio_models import ratios_from_dict, ratios_to_dict

_logger = logging.getLogger("review")


def save_to_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    _doc.set_section(ctx, "ratios", ratios_to_dict(app._app_state.channel.ratio_metrics))


def load_from_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    data = _doc.get_section(ctx, "ratios")
    # A dataset with no ``ratios`` section has no ratios, which is not the same
    # as "leave whatever is loaded alone". Returning early here left the
    # previous plate's ratio metrics live as virtual channels across the swap:
    # every plot, the Image Table's channel list and Home's own ratio count
    # then described work done on a plate that is no longer open. This is the
    # hydration point, so the dataset is loaded and the channel-combo cascade
    # ``_set_ratio_metrics`` runs is safe to trigger here.
    app._set_ratio_metrics(ratios_from_dict(data) if data is not None else [])
