"""Preview one frame the way the pipeline would process it.

The Analyze pane's Preview page and the Review pane's Playground both answer the
same question — *what will these settings do to my images?* — and they must
answer it identically to each other and to a batch run. So neither
reimplements anything: this module discovers a frame in an input folder, loads
it, and puts it through the pipeline's own
``process_microscopy.correct_single_frame`` and
``pipeline.stardist.segment_nuclei``.

Qt-free (``tests/test_architecture_contracts.py::test_services_have_no_qt_imports``)
and free of ``well_viewer`` imports, so it stays usable from a headless driver
and from either pane. TensorFlow and StarDist are imported **inside**
:func:`segment_frame`, never at module scope — the Analyze tab imports this at
GUI start-up, and importing TF there would add tens of seconds and gigabytes to
launching the app for every user, including those who never press Segment.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

_TIF_SUFFIXES = (".tif", ".tiff")

# The loaded StarDist model, at most once per GUI session and shared by both
# panes. Not per-pane state: it survives a dataset swap, must never be pickled,
# and a second copy would be another 1-2 GB for no benefit.
_GUI_STARDIST_MODEL = None

_MISSING_DEPS_MSG = (
    "StarDist is not installed in this environment ({missing} missing), so the "
    "preview cannot segment. The thresholds still apply to a pipeline run."
)


@dataclass(frozen=True)
class FrameRef:
    """One previewable image in an input folder.

    ``source`` is whatever ``process_microscopy._load_image_source`` accepts: a
    ``Path`` for a loose TIF, or a ``(zip_path, member)`` tuple for a TIF inside
    a per-well archive — the two layouts Analyze accepts as input.
    """

    source: object
    stem: str
    well: str = ""
    channel: str = ""
    fov: str = ""
    timepoint: str = ""

    def label(self) -> str:
        """Human-readable identity, falling back to the stem when the schema
        did not resolve the fields (a folder whose names don't parse still
        previews — it just cannot be filtered by well or channel)."""
        bits = [b for b in (self.well, self.channel,
                            f"fov{self.fov}" if self.fov else "",
                            f"t{self.timepoint}" if self.timepoint else "") if b]
        return " ".join(bits) if bits else self.stem


# ── discovery ────────────────────────────────────────────────────────────────

def _loose_sources(root: Path, limit: int) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for f in sorted(root.iterdir()):
        if f.is_file() and f.suffix.lower() in _TIF_SUFFIXES:
            out.append(f)
            if len(out) >= limit:
                break
    return out


def _zip_sources(root: Path, limit: int) -> list[tuple[Path, str]]:
    """A few members from each zip, so the sample spans wells rather than
    exhausting the first archive. Mirrors ``util.schema_infer._zip_stems_in``."""
    if not root.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    zips = sorted(root.glob("*.zip"))
    per_zip = max(4, (limit // max(1, len(zips))) if zips else limit)
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                taken = 0
                for member in zf.namelist():
                    if not member.lower().endswith(_TIF_SUFFIXES):
                        continue
                    # Archive members that are not images at all — directory
                    # entries, .DS_Store, AppleDouble sidecars — never reach
                    # here, but a name that parses to nothing still yields a
                    # usable FrameRef with empty fields rather than being
                    # dropped, so a folder with an unexpected naming scheme
                    # previews instead of appearing empty.
                    out.append((z, member))
                    taken += 1
                    if taken >= per_zip or len(out) >= limit:
                        break
        except (zipfile.BadZipFile, OSError):
            continue
        if len(out) >= limit:
            break
    return out


def sample_input_frames(input_dir, *, schema=None, sep: str = "_",
                        limit: int = 400) -> list[FrameRef]:
    """Frames available for preview in *input_dir*.

    Walks the same roots and in the same order as
    ``util.schema_infer.sample_stems`` — loose TIFs first, then zip members,
    across ``input_dir`` and its ``in/`` child — but keeps the loadable *source*
    beside each stem and parses the stem with the caller's schema so the result
    can be filtered by well / channel / FOV / timepoint.
    """
    import process_microscopy as pm

    base = Path(input_dir)
    sources: list = []
    for root in (base, base / "in"):
        sources = _loose_sources(root, limit)
        if sources:
            break
    if not sources:
        for root in (base, base / "in"):
            sources = _zip_sources(root, limit)
            if sources:
                break

    refs: list[FrameRef] = []
    for source in sources:
        name = source[1] if isinstance(source, tuple) else source
        stem = Path(name).stem
        try:
            fields = pm.parse_filename(stem, schema, sep)
        except Exception:
            fields = {}
        refs.append(FrameRef(
            source=source, stem=stem,
            well=str(fields.get("well", "") or ""),
            channel=str(fields.get("channel", "") or ""),
            fov=str(fields.get("fov", "") or ""),
            timepoint=str(fields.get("timepoint", "") or ""),
        ))
    return refs


def load_frame(ref: FrameRef):
    """The 2-D float32 pixels for *ref*, or None if unreadable.

    Delegates to ``process_microscopy._load_image_source``, which already
    handles both source shapes and is what a batch run reads with — so the
    preview cannot differ from the run by reading the file differently.
    """
    import process_microscopy as pm
    return pm._load_image_source(ref.source)


# ── the pipeline's own correction + segmentation ─────────────────────────────

def correction_needs_a_plate(flatfield_mode: str, background_method: str) -> list[str]:
    """The chosen correction modes that a single frame cannot honour, named.

    ``empty_well`` / ``basic`` estimate their field from a whole reference well
    sampled across the plate. A one-frame preview has no such well, so those
    modes are dropped — and the caller must *say so*, because a preview that
    silently rendered an uncorrected frame while the form still claimed a
    correction would be showing something the run will not produce.
    """
    dropped: list[str] = []
    if flatfield_mode in ("empty_well", "basic"):
        dropped.append(f"flat-field {flatfield_mode}")
    if background_method == "empty_well":
        dropped.append("background empty_well")
    return dropped


def correct_frame(arr, *, flatfield_mode: str = "none",
                  background_method: str = "none", radius: int = 50,
                  fft_small: float = 0.0, mesh_ratio: float = 3.0,
                  mesh_sigma: float = 3.0):
    """Apply the pipeline's per-frame correction, minus the modes a single frame
    cannot do (see :func:`correction_needs_a_plate`)."""
    import process_microscopy as pm

    ff_mode = "none" if flatfield_mode in ("empty_well", "basic") else flatfield_mode
    bg_method = "none" if background_method == "empty_well" else background_method
    return pm.correct_single_frame(
        arr, flatfield_mode=ff_mode, background_method=bg_method, radius=radius,
        fft_small=fft_small, mesh_ratio=mesh_ratio, mesh_sigma=mesh_sigma)


def missing_stardist_deps() -> list[str]:
    """Which of StarDist's imports are absent — probed, never imported."""
    import importlib.util
    return [name for name in ("stardist", "csbdeep", "tensorflow")
            if importlib.util.find_spec(name) is None]


def segment_frame(arr, *, prob_thresh=None, nms_thresh=None, tile_px: int = 0):
    """Segment one frame and return its int32 label image.

    Runs the pipeline's own :func:`pipeline.stardist.segment_nuclei`, so the
    input scrubbing, the ``[0, 1]`` clamp and the near-constant-field refusal
    that make StarDist's uncatchable ClipperLib abort unreachable all apply —
    which matters more in a GUI process than in a pipeline worker, because an
    abort here would take the whole application down rather than one process.
    """
    missing = missing_stardist_deps()
    if missing:
        raise RuntimeError(_MISSING_DEPS_MSG.format(missing=", ".join(missing)))

    global _GUI_STARDIST_MODEL
    import process_microscopy as pm
    from pipeline.stardist import StarDistParams, segment_nuclei

    if _GUI_STARDIST_MODEL is None:
        pm._ensure_pkg_resources_shim()
        from stardist.models import StarDist2D
        # The pipeline workers' loader: recovers from a cache left truncated by
        # an interrupted download, and serialises against a pipeline run that is
        # fetching the same weights right now.
        _GUI_STARDIST_MODEL = pm._load_stardist_pretrained(
            StarDist2D, "2D_versatile_fluo")

    return segment_nuclei(
        arr, model=_GUI_STARDIST_MODEL,
        params=StarDistParams(prob_thresh=prob_thresh, nms_thresh=nms_thresh,
                              tile_px=int(tile_px or 0)))
