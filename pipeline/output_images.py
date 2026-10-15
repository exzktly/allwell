"""The per-FOV image outputs a run archives, and the two policies on how.

Every image a run writes beside its CSV goes through
:func:`write_processed_images`: the background-corrected ``_tophat`` frames,
the smFISH and cytoplasm intermediates, the label image, and the overlay PNG.
Those files are most of what a ``<well>_out.zip`` holds, so two run options
decide how much of it there is.

**Phase output** (``--phase_output``). The phase / brightfield channel is never
corrected — :func:`process_microscopy._correct_image_group` passes its raw
frame straight through — so its ``_tophat`` file holds exactly the pixels of
the input image it was read from. ``reference`` (the default) writes no
``_tophat`` for that channel at all: the input archive ``in/<well>.zip``
already holds the frame, and Review's frame resolver falls back from a missing
``_tophat`` to the raw member of that archive (``tophat`` precedes
``fluor_raw`` in ``review.data.image_resolver.CHANNEL_FRAME_PRECEDENCE``).
``copy`` writes it as before. The choice is recorded in ``pipeline_info.json``
as ``phase_output`` so a reader can tell "absent on purpose" from "missing".

**Tophat storage** (``--tophat_dtype``). The corrected frames are computed in
float32, but a camera writes integers, and a float32 file holding counts is
twice the bytes and compresses far worse: the fractional digits background
subtraction leaves behind are close to incompressible. ``source`` (the
default) rounds a corrected frame to the integer type its input was stored in
when, and only when, that is lossless up to rounding — see
:func:`storage_array`. ``float32`` keeps every file float32.

Neither option changes a measured number. Quantification reads the in-memory
float arrays (``process_microscopy._quantify_image_group``), never these files.
What a reader of the files sees is at most a half-count rounding of a
corrected pixel, and a phase frame read from ``in/`` instead of ``out/``.

Qt-free and pipeline-only, like the rest of ``pipeline/``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from pipeline.io import _safe_imwrite, save_overlay
from pipeline.output_policy import (
    DEFAULT_PHASE_OUTPUT,
    DEFAULT_TOPHAT_DTYPE,
    PHASE_OUTPUT_REFERENCE,
    TOPHAT_DTYPE_SOURCE,
)

log = logging.getLogger("process_microscopy")

#: The integer storage types a ceiling may name, keyed by that ceiling. Only
#: the two a microscope writes: float32 holds every value of both exactly
#: (24-bit mantissa), which is what makes the round trip a pure rounding. A
#: ``uint32`` source is not listed because float32 cannot hold its values
#: exactly, so "store it as its source type" would be a second lossy step.
_INTEGER_TYPES_BY_CEILING: dict[float, type] = {
    float(np.iinfo(np.uint8).max): np.uint8,
    float(np.iinfo(np.uint16).max): np.uint16,
}


def storage_array(arr: np.ndarray, ceiling: float | None, *, mode: str) -> tuple[np.ndarray, str]:
    """The array to write for one corrected frame, and why it has that type.

    *arr* is the corrected frame (already clipped at zero by the caller);
    *ceiling* is the maximum of the integer type its input was stored in, as
    :func:`pipeline.io.load_2d_gray_with_ceiling` reports it — ``None`` for a
    float input, where there is no integer type to return to.

    Returns ``(array, reason)``. The array is float32 unless every one of
    these holds, in which case it is the input's integer type with each value
    rounded to the nearest count:

    * *mode* is ``source``;
    * the input was ``uint8`` or ``uint16`` (a float input — a frame already
      normalised to 0–1, say — would be rounded to nothing);
    * every value is finite (a NaN or an infinity has no integer spelling);
    * the largest value, rounded, fits the type. A flat-field division can
      lift a corrected pixel above the camera's ceiling, and clipping it there
      would invent a saturation the data does not contain — so that frame
      stays float32, and *reason* says so, rather than losing the top.

    *reason* is ``""`` when the frame was converted and a short phrase naming
    the rule otherwise, for the run log.
    """
    as_float = np.asarray(arr, dtype=np.float32)
    if mode != TOPHAT_DTYPE_SOURCE:
        return as_float, "float32 requested"
    target = _INTEGER_TYPES_BY_CEILING.get(float(ceiling)) if ceiling is not None else None
    if target is None:
        return as_float, "input is not uint8/uint16"
    if as_float.size == 0:
        return as_float.astype(target), ""
    if not np.isfinite(as_float).all():
        return as_float, "frame holds non-finite values"
    rounded = np.rint(as_float)
    if float(rounded.min()) < 0.0:
        return as_float, "frame holds negative values"
    top = float(rounded.max())
    if top > float(ceiling):
        return as_float, f"max {top:g} exceeds the input's {float(ceiling):g} ceiling"
    return rounded.astype(target), ""


def is_phase_token(token: str, phase_token: str) -> bool:
    """Whether *token* is the run's phase / brightfield channel.

    The same case-insensitive comparison
    :func:`process_microscopy._correct_image_group` uses to decide which
    channel to pass through uncorrected, so the writer skips exactly the
    channel whose pixels the input archive already holds.
    """
    ph = str(phase_token or "").strip().lower()
    return bool(ph) and str(token or "").strip().lower() == ph


def build_processed_output_path(
    src_path: Path,
    *,
    output_dir: Path,
    suffix: str = "",
    ext_override: str | None = None,
) -> Path:
    """Build output path as input stem + suffix + extension.

    Pass ``suffix="_tophat"`` / ``"_labels"`` etc. directly. The default
    extension is copied from *src_path* so processed outputs preserve the
    source file type (``.tif`` / ``.tiff`` etc.) unless explicitly overridden.

    (The reader-side ``image_identity.ROLE_CATALOG`` is the canonical suffix
    table. A previous "preferred" catalog-driven branch here imported it from
    ``review`` — a violation of the §6.2 pipeline-self-sufficiency contract,
    and dead anyway since no caller passed ``role=``. If catalog-driven *writing*
    is ever revived, move the catalog to a Qt-free ``*_core`` module first.)
    """
    ext = str(ext_override) if ext_override is not None else str(src_path.suffix)
    if not ext:
        ext = ".tif"
    return output_dir / f"{src_path.stem}{suffix}{ext}"


def _write_corrected(path: Path, arr: np.ndarray, ceiling: float | None,
                     *, mode: str, label: str) -> None:
    """Write one ``_tophat`` frame under the storage policy, logging a fallback."""
    out, reason = storage_array(np.clip(arr, 0, None), ceiling, mode=mode)
    if reason and mode == TOPHAT_DTYPE_SOURCE:
        log.info("  tophat %s: stored float32 (%s)", label, reason)
    _safe_imwrite(path, out)


def write_processed_images(
    *, save_tophat_images, nuclear_path, fluor_paths, fluor_tokens, output_dir,
    nir_raw, nir_corr, fluor_tophat, fluor_corr, smfish_set, segmentation_method,
    nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask, labels,
    background_method, nuclear_token="", phase_token="",
    phase_output=DEFAULT_PHASE_OUTPUT, tophat_dtype=DEFAULT_TOPHAT_DTYPE,
    nuclear_ceiling=None, fluor_ceilings=None,
):
    """Write the background-corrected tifs, label image, and overlay.

    *phase_output* and *tophat_dtype* are the two storage policies the module
    docstring describes. *nuclear_ceiling* / *fluor_ceilings* are the integer
    ceilings of each channel's input file (``None`` for a float input, or when
    the caller does not know), which :func:`storage_array` needs to decide
    whether a frame may go back to its input's type. The defaults reproduce
    nothing older than the policies themselves: a caller that passes no
    ceilings gets float32 everywhere, which is the safe direction.

    ``_smfish`` and ``_cytoplasm_tophat`` are always float32. A Laplacian-of-
    Gaussian response is small and fractional by construction, and rounding it
    to counts would erase the spots it exists to show.
    """
    # Keep schema tokens in output stems so output names stay harmonized with
    # input naming conventions and schema-driven resolvers.
    #
    # NB: the ``_tophat`` suffix is **legacy naming** — these are the
    # *background-corrected* images (nir_corr / fluor_tophat), i.e. the output of
    # whatever --background method ran (top_hat / sliding_disk / empty_well /
    # none, after any flat-field), not specifically a top-hat. The suffix is kept
    # because the Review side reads ``_tophat`` files; only the label is stale.
    _t = time.perf_counter()
    reference_phase = phase_output == PHASE_OUTPUT_REFERENCE
    if fluor_ceilings is None:
        fluor_ceilings = [None] * len(fluor_paths)
    if save_tophat_images:
        if reference_phase and is_phase_token(nuclear_token, phase_token):
            log.info("  tophat %s: not written (phase channel — referenced from the "
                     "input archive)", str(nuclear_token).lower())
        else:
            _write_corrected(
                build_processed_output_path(nuclear_path, output_dir=output_dir,
                                            suffix="_tophat"),
                nir_corr, nuclear_ceiling, mode=tophat_dtype,
                label=str(nuclear_token or "nuclear").lower())
        for fc, tok, fp, ceiling in zip(fluor_tophat, fluor_tokens, fluor_paths,
                                        fluor_ceilings):
            if reference_phase and is_phase_token(tok, phase_token):
                log.info("  tophat %s: not written (phase channel — referenced from "
                         "the input archive)", str(tok).lower())
                continue
            _write_corrected(
                build_processed_output_path(fp, output_dir=output_dir,
                                            suffix="_tophat"),
                fc, ceiling, mode=tophat_dtype, label=str(tok).lower())
        for i, (tok, fp) in enumerate(zip(fluor_tokens, fluor_paths)):
            if tok in smfish_set:
                smfish_path = build_processed_output_path(
                    fp,
                    output_dir=output_dir,
                    suffix="_smfish",
                )
                _safe_imwrite(smfish_path, np.clip(fluor_corr[i], 0, None).astype(np.float32))
        if segmentation_method == "stardist_seeded_watershed_cell":
            assert nuclear_points is not None
            assert cytoplasm_tophat is not None
            assert cytoplasm_binary_mask is not None
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_nuclear_points",
                ),
                nuclear_points.astype(np.int32),
            )
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_cytoplasm_tophat",
                ),
                np.clip(cytoplasm_tophat, 0, None).astype(np.float32),
            )
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_cytoplasm_otsu_mask",
                ),
                cytoplasm_binary_mask.astype(np.uint8),
            )
        log.info("  bg-corrected tifs written  (method=%s, suffix=_tophat [legacy], "
                 "storage=%s, phase=%s)", background_method, tophat_dtype, phase_output)
    _safe_imwrite(
        build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_labels",
        ),
        labels,
    )
    save_overlay(
        nir_raw,
        labels,
        build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_overlay",
            ext_override=".png",
        ),
    )
    log.info("  save:        %.1f s", time.perf_counter() - _t)
