"""Wires the Convert ND2 page to its background worker.

Everything but the page and the :class:`~datasets.core.packed_convert.ConverterSpec`
is shared with the other packed-file converters — see that module for the
measurement behind the split.
"""

from __future__ import annotations

from datasets.core.packed_convert import (
    ConverterSpec,
    PackedConverterController,
)
from datasets.nd2_import.apply import convert
from datasets.nd2_import.nd2_import_compute import (
    ConversionParams,
    Nd2ImportError,
    plan_conversion,
    read_source,
)
from datasets.nd2_import.view import Nd2ImportPage
from pipeline.nd2_explode import Nd2ExplodeCancelled

ND2_CONVERTER = ConverterSpec(
    kind="nd2_import",
    label="nd2-import",
    noun="ND2",
    params_cls=ConversionParams,
    read_source=read_source,
    plan=plan_conversion,
    convert=convert,
    # ``ValueError`` as well as the tool's own error: ``resolve_well_map``
    # raises a plain one when the grid and the file disagree about how many
    # positions there are, and that is an answer to show the user rather than
    # a crash to log a traceback for.
    refusals=(Nd2ImportError, ValueError),
    cancelled=Nd2ExplodeCancelled,
)


class Nd2ImportController(PackedConverterController):
    """Behaviour for the Convert ND2 feature."""

    spec = ND2_CONVERTER

    def _build_page(self) -> Nd2ImportPage:
        return Nd2ImportPage()
