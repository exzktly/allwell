"""Pure logic + I/O helpers for the smFISH tab.

Holds the image-reference dataclass, member classifier, and zip scanning so
``well_viewer/tabs/smfish_tab_view.py`` only handles GUI work.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from well_viewer.data.image_discovery import ImgRef as SmfishImgRef
from well_viewer.data.image_resolver import output_suffixes_for_kind
from well_viewer.features.preview.controller import classify_member, read_member_bytes, scan_zip_members


logger = logging.getLogger("smfish_tab")

_IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def make_classifier(
    separator: str,
) -> Callable[[str, str, Optional[Callable[[str], tuple[str, str]]]], tuple[str, str, str]]:
    """Build a ``classify_member`` adapter that knows the schema separator."""

    def _classify(name: str, fluor_lower: str, fov_tp_extractor=None, _pipeline_info=None):
        mask_re = re.compile(
            r"(?:%s)$" % "|".join(re.escape(sfx) for sfx in output_suffixes_for_kind("mask")),
            re.I,
        )
        overlay_re = re.compile(
            r"(?:%s)$" % "|".join(re.escape(sfx) for sfx in output_suffixes_for_kind("overlay")),
            re.I,
        )
        tophat_re = re.compile(
            r"(?:%s)$" % "|".join(
                re.escape(sfx).replace(re.escape(fluor_lower), r"\w+")
                for sfx in output_suffixes_for_kind("fluor_processed", target_channel=fluor_lower)
            ),
            re.I,
        )

        def _legacy(stem: str) -> tuple[str, str]:
            parts = stem.split(separator)
            if len(parts) >= 2:
                return parts[-2], parts[-1]
            return "unknown", "unknown"

        return classify_member(
            name=name,
            fluor_lower=fluor_lower,
            mask_re=mask_re,
            overlay_re=overlay_re,
            tophat_fluor_re=tophat_re,
            fov_tp_extractor=fov_tp_extractor or _legacy,
        )

    return _classify


def scan_well_zip(
    *,
    zip_path: Path,
    channel: str,
    classifier,
    fov_tp_extractor=None,
):
    """Scan a per-well output zip and return (smfish-or-tophat, mask) maps."""
    _g, _ov, mask, tophat, smfish = scan_zip_members(
        zip_path=zip_path,
        fluor_lower=channel,
        image_exts=_IMAGE_EXTS,
        classify_member_fn=classifier,
        imgref_factory=lambda p, m: SmfishImgRef(zip_path=p, zip_member=m),
        logger=logger,
        fov_tp_extractor=fov_tp_extractor,
    )
    source = smfish if smfish else tophat
    return source, mask


def read_image_arrays(sm_ref: SmfishImgRef, mk_ref: SmfishImgRef):
    """Decode a (smFISH/log image, mask) pair into ``np.ndarray``s.

    Returns ``(log_img, labels, sorted_inside_label_vals)`` or ``None`` when
    either image fails to load.
    """
    sm_raw = read_member_bytes(zip_path=sm_ref.zip_path, member=sm_ref.zip_member, logger=logger)
    mk_raw = read_member_bytes(zip_path=mk_ref.zip_path, member=mk_ref.zip_member, logger=logger)
    if sm_raw is None or mk_raw is None:
        return None
    from tifffile import imread

    log_img = imread(io.BytesIO(sm_raw)).astype(np.float32)
    labels = imread(io.BytesIO(mk_raw))
    vals = log_img[labels > 0]
    sorted_vals = np.sort(vals) if vals.size else np.array([], dtype=np.float32)
    return log_img, labels, sorted_vals


# Re-exports — the canonical implementations now live in image_identity.
# Kept here so existing callers `from well_viewer.features.smfish.controller
# import normalize_well_token, normalize_id` keep working without churn.
from well_viewer.data.image_identity import (  # noqa: E402
    normalize_well_token,
    normalize_id,
)
