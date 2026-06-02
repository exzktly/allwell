"""Tracking engine adapter interface + shared data types (Qt-free).

One adapter contract sits in front of every engine so the worker (GUI) and the
headless ``--track`` post-pass share the same call::

    run(frames: list[FrameDetections], params: EngineParams) -> TrackResult

These types use only stdlib + numpy, so this module (and the engines that
import it) can be imported by the headless pipeline as well as the GUI. Engine
*implementations* guard their heavy imports (btrack / trackastra) so the app
runs without them — an engine errors only when actually selected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# Node identity within one (well, fov) track problem: (frame_ordinal, nucleus_id).
Node = Tuple[int, int]

UNTRACKED = -1


@dataclass(frozen=True)
class Detection:
    """One segmented object in one frame."""
    nid: int                       # nucleus_id within the frame
    row: float                     # centroid row (y, px)
    col: float                     # centroid col (x, px)
    features: dict = field(default_factory=dict)  # optional area/intensity/shape


@dataclass(frozen=True)
class FrameDetections:
    """All detections at one timepoint, in time order.

    ``image`` / ``mask`` are optional per-frame arrays (loaded from
    ``<well>_out.zip`` by the worker) that the mask-based engines (trackastra,
    OverLapTrack) consume; centroid engines (greedy, btrack) ignore them.
    """
    frame: int                     # 0-based ordinal (sorted by timepoint)
    timepoint: str                 # original timepoint label
    detections: List[Detection]
    image: object = None           # 2-D raw image (np.ndarray) or None
    mask: object = None            # 2-D integer label mask (np.ndarray) or None


@dataclass(frozen=True)
class EngineParams:
    """Engine-agnostic linking parameters (engine-specific extras in ``extra``)."""
    max_distance: float = 30.0     # max frame-to-frame linking distance (px)
    max_gap_frames: int = 1        # gap-closing horizon (frames)
    allow_division: bool = False   # enable lineage branching where supported
    extra: dict = field(default_factory=dict)


@dataclass
class TrackResult:
    """Engine output: node→track map + lineage.

    ``assignments`` maps each ``(frame, nid)`` node to a positive ``track_id``.
    ``parents`` maps a ``track_id`` to its lineage-parent ``track_id`` (or
    :data:`UNTRACKED`). ``tree_ids`` maps a ``track_id`` to its lineage-tree
    root id (== the track's own id when it has no parent).
    """
    assignments: Dict[Node, int] = field(default_factory=dict)
    parents: Dict[int, int] = field(default_factory=dict)
    tree_ids: Dict[int, int] = field(default_factory=dict)

    def track_id_for(self, frame: int, nid: int) -> int:
        return self.assignments.get((int(frame), int(nid)), UNTRACKED)

    def parent_of(self, track_id: int) -> int:
        return self.parents.get(int(track_id), UNTRACKED)

    def tree_of(self, track_id: int) -> int:
        return self.tree_ids.get(int(track_id), int(track_id))

    @property
    def n_tracks(self) -> int:
        return len(set(self.assignments.values()))

    def finalize(self) -> "TrackResult":
        """Fill ``tree_ids`` for every track by walking ``parents`` to the root,
        and default missing parents to :data:`UNTRACKED`. Idempotent."""
        track_ids = set(self.assignments.values())
        for tid in track_ids:
            self.parents.setdefault(tid, UNTRACKED)
        for tid in track_ids:
            root = tid
            seen = set()
            while self.parents.get(root, UNTRACKED) != UNTRACKED and root not in seen:
                seen.add(root)
                root = self.parents[root]
            self.tree_ids[tid] = root
        return self
