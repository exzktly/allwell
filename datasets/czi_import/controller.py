"""Wires the Convert CZI page to its background worker.

Everything but the page and the :class:`~datasets.core.packed_convert.ConverterSpec`
is shared with the other packed-file converters — see that module for the
measurement behind the split.
"""

from __future__ import annotations

from datasets.core.packed_convert import (
    ConverterSpec,
    PackedConverterController,
)
from datasets.czi_import.apply import convert
from datasets.czi_import.czi_import_compute import (
    ConversionParams,
    CziImportError,
    plan_conversion,
    read_source,
)
from datasets.czi_import.view import CziImportPage
from pipeline.czi_explode import CziExplodeCancelled

CZI_CONVERTER = ConverterSpec(
    kind="czi_import",
    label="czi-import",
    noun="CZI",
    params_cls=ConversionParams,
    read_source=read_source,
    plan=plan_conversion,
    convert=convert,
    # ``ValueError`` as well as the tool's own error: ``resolve_well_map``
    # raises a plain one when the grid and the file disagree about how many
    # positions there are, and that is an answer to show the user rather than
    # a crash to log a traceback for.
    refusals=(CziImportError, ValueError),
    cancelled=CziExplodeCancelled,
)


class CziImportController(PackedConverterController):
    """Behaviour for the Convert CZI feature."""

    spec = CZI_CONVERTER

    def _build_page(self) -> CziImportPage:
        return CziImportPage()
