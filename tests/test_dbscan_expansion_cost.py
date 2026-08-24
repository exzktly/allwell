"""Colony detection expands each cluster once, not once per member.

``_dbscan`` rebuilt its ``seen`` set with ``set(nbrs)`` inside the expansion
loop — once for every core point it reached — so the work to grow one cluster
was quadratic in that cluster's size. Colony detection is called inline from
``features/spatial/controller.py``; there is no worker, so the cost lands on
the GUI thread on every redraw. Measured on a synthetic confluent field of
16 000 cells: 3.57 s before, 1.06 s after, with byte-identical labels.

Two tests, because the fix is an optimisation and an optimisation that changes
the answer is not a fix: one pins the labels, one pins the structure that makes
them cheap. The structural one is here rather than a timing assertion because a
wall-clock threshold in CI measures the runner's load as much as the algorithm.
"""

from __future__ import annotations

import ast
import inspect

import pytest

pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from well_viewer.domain import spatial_compute  # noqa: E402
from well_viewer.domain.spatial_compute import _dbscan  # noqa: E402


def _field(n: int) -> np.ndarray:
    """A clustered field: twenty tight colonies plus a fifth scattered noise."""
    rng = np.random.default_rng(7)
    centres = rng.uniform(0, 1000, size=(20, 2))
    bulk = n - n // 5
    return np.vstack([
        centres[rng.integers(0, 20, bulk)] + rng.normal(0, 18, (bulk, 2)),
        rng.uniform(0, 1000, (n // 5, 2)),
    ])


@pytest.mark.parametrize("n,clusters,noise", [(4000, 32, 484), (8000, 77, 386)])
def test_the_labels_are_the_ones_the_slow_version_produced(n, clusters, noise):
    labels = _dbscan(_field(n), eps=25.0, min_samples=5)
    assert int(labels.max()) + 1 == clusters
    assert int((labels == -1).sum()) == noise


def test_every_point_is_labelled_noise_or_a_real_cluster():
    labels = _dbscan(_field(2000), eps=25.0, min_samples=5)
    assert labels.min() >= -1
    assert set(np.unique(labels[labels >= 0])) == set(range(int(labels.max()) + 1))


def test_the_frontier_set_is_not_rebuilt_inside_the_expansion_loop():
    """The structural half: rebuilding ``seen`` per core point is the defect."""
    import textwrap

    src = textwrap.dedent(inspect.getsource(spatial_compute._dbscan))
    fn = ast.parse(src).body[0]

    loops = [n for n in ast.walk(fn) if isinstance(n, ast.While)]
    assert loops, "expected the frontier expansion to still be a while loop"

    for loop in loops:
        for node in ast.walk(loop):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "set"):
                raise AssertionError(
                    "_dbscan builds a set inside its expansion loop again — "
                    "that is the O(cluster size squared) rebuild this test "
                    "exists to keep out"
                )
