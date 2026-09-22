"""Wires the Convert OME-TIFF page to its background worker.

Everything but the page and the :class:`~datasets.core.packed_convert.ConverterSpec`
is shared with the other packed-file converters — see that module for the
measurement behind the split.
"""

from __future__ import annotations

from datasets.core.packed_convert import (
    ConverterSpec,
    PackedConverterController,
)
from datasets.ome_import.apply import convert
from datasets.ome_import.ome_import_compute import (
    ConversionParams,
    OmeImportError,
    plan_conversion,
    read_source,
)
from datasets.ome_import.view import OmeImportPage
from pipeline.ome_explode import OmeExplodeCancelled

OME_CONVERTER = ConverterSpec(
    kind="ome_import",
    label="ome-import",
    noun="OME",
    params_cls=ConversionParams,
    read_source=read_source,
    plan=plan_conversion,
    convert=convert,
    refusals=(OmeImportError,),
    cancelled=OmeExplodeCancelled,
)


class OmeImportController(PackedConverterController):
    """Behaviour for the Convert OME-TIFF feature."""

    spec = OME_CONVERTER

    def _build_page(self) -> OmeImportPage:
        return OmeImportPage()
