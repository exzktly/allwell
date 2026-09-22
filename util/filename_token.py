"""Reduce a metadata label to a token that survives a filename.

Every image an importer writes is named to the pipeline's filename schema —
``<experiment>_<channel>_<well>_f<fov>_<timepoint>.tif`` by default — and that
name is split back apart on the separator downstream. So a channel called
``"DAPI 405"`` or ``"GFP_2"`` cannot go into a token slot as it stands: the
space makes a name awkward to handle and the underscore makes it *two* tokens,
which shifts every field after it by one and silently renames the well.

One helper, in ``util/`` rather than beside either importer, because both the
OME-TIFF and the CZI converter ask exactly this question of exactly this kind of
label. When it lived in :mod:`pipeline.ome_explode` the CZI reader's choice was
between importing a private name and writing a second definition of the same
rule — and two definitions of one rule drift, which for this rule means two
importers naming the same channel two different things.
"""

from __future__ import annotations

import re

#: Everything that is not an ASCII alphanumeric, collapsed away entirely rather
#: than replaced with a separator — a replacement character would itself have to
#: be one the schema does not split on, and there is no such character that is
#: also legal in a filename on every platform the app runs on.
_UNSAFE = re.compile(r"[^0-9A-Za-z]+")


def safe_token(value: object, fallback: str) -> str:
    """*value* with everything but ``[0-9A-Za-z]`` stripped, or *fallback*.

    *fallback* is returned when *value* is empty, ``None``, or consists
    entirely of punctuation — a label like ``"---"`` reduces to nothing, and an
    empty token slot would collapse the filename's field count exactly as an
    extra separator would.
    """
    token = _UNSAFE.sub("", str(value if value is not None else ""))
    return token or fallback
