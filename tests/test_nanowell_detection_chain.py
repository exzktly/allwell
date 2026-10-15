"""The chain end to end on rendered tiles: image in, addresses out, checked against the array that was drawn."""

import numpy as np
import pytest

from pipeline.nanowell_detect import (
    check_orientation, detect_sites, estimate_lattice_fft, refine_sites, score_fiducial,
)
from pipeline.nanowell_lattice import index_points, resolve_origin
from pipeline.nanowell_sites import check_format
from tests._nanowell_synth import TilePlan, render_tiles

FORMATS = {"100um": (172.0, 100.0, 4.3), "50um": (86.0, 50.0, 2.15)}


def _render(fmt, *, rotation, bubble=None, square_hidden=False, seed=1):
    pitch, diameter, px = FORMATS[fmt]
    extent = 900 * px
    radius = 0.42 * extent
    discs = []
    if bubble:
        discs.append((extent / 2 + 0.4 * radius, extent / 2 + 0.17 * radius, radius * np.sqrt(bubble)))
    rendered = render_tiles(TilePlan(1, 1, (900, 900), 0.0, px), pitch_um=pitch, diameter_um=diameter,
                            radius_um=radius, rotation_deg=rotation, noise_sigma=0.05, seed=seed, blur_px=1.0,
                            bubble=(extent / 2, extent / 2, 0.8 * pitch) if square_hidden else (discs[0] if discs else None))
    return rendered, rendered.images["F0"], pitch, diameter, px


def _addresses(rendered, image, pitch, diameter, px, *, margin=0.3):
    """Run the chain; returns (detected centres, the addresses assigned to them, the resolution)."""
    estimate = estimate_lattice_fft(image, px, expected_pitch_um=pitch)
    assert estimate.ok and check_format(estimate.pitch_um, diameter).status != "format_mismatch"
    detection = detect_sites(image, px, diameter, expected_pitch_um=estimate.pitch_um)
    assert detection.ok, detection.reason
    candidate, scores = score_fiducial(image, px, diameter, detection.points_um,
                                       centre_um=detection.boundary_centre_um, search_radius_um=2.2 * pitch)
    resolved = resolve_origin(detection.fit, fiducial=candidate, array_centre=detection.boundary_centre_um,
                              fiducial_margin=margin)
    assert resolved.status == "anchored", resolved.reason
    indexed = index_points(detection.points_um, detection.fit, resolved.origin_xy)
    return detection, indexed, resolved, scores


@pytest.mark.parametrize("fmt", sorted(FORMATS))
@pytest.mark.parametrize("rotation", [7.0, -20.0])
def test_every_detected_nanowell_gets_the_address_it_was_drawn_with(fmt, rotation):
    rendered, image, pitch, diameter, px = _render(fmt, rotation=rotation, bubble=0.10)
    detection, indexed, resolved, scores = _addresses(rendered, image, pitch, diameter, px)
    assert resolved.method == "fiducial"                      # the square is visible and wins
    truth = {tuple(map(float, site)): address for site, address in zip(rendered.sites_um, rendered.array.addresses)}
    sites = np.array(list(truth))
    wrong = unaddressed = 0
    for point, entry in zip(detection.points_um, indexed):
        nearest = tuple(sites[np.argmin(np.linalg.norm(sites - point, axis=1))])
        if entry.q is None:
            unaddressed += 1
        elif (entry.q, entry.r) != truth[nearest]:
            wrong += 1
    assert wrong == 0
    assert unaddressed <= 0.02 * len(indexed)
    # The orientation check agrees with an unfolded axis.
    square = max(scores, key=lambda s: s.coherence)
    assert check_orientation(detection.fit, square.orientation_deg).ok


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_with_the_square_under_a_bubble_the_array_centre_gives_the_same_addresses(fmt):
    rendered, image, pitch, diameter, px = _render(fmt, rotation=7.0, square_hidden=True)
    detection, indexed, resolved, _ = _addresses(rendered, image, pitch, diameter, px)
    assert resolved.method == "array_centre"
    truth = {tuple(map(float, site)): address for site, address in zip(rendered.sites_um, rendered.array.addresses)}
    sites = np.array(list(truth))
    mismatches = [1 for point, entry in zip(detection.points_um, indexed)
                  if entry.q is not None and (entry.q, entry.r) != truth[tuple(sites[np.argmin(np.linalg.norm(sites - point, axis=1))])]]
    assert mismatches == []
    assert np.linalg.norm(np.subtract(resolved.origin_xy, rendered.array.origin)) < 0.1 * pitch


def test_a_rotation_beyond_the_lattices_limit_is_refused_at_the_square_not_addressed_wrongly():
    # Rotated 40 degrees the lattice is fitted at -20: the chain would hand out addresses a sixth of
    # a turn off, and no step before the orientation check notices.
    rendered, image, pitch, diameter, px = _render("100um", rotation=40.0)
    detection, indexed, resolved, scores = _addresses(rendered, image, pitch, diameter, px)
    truth = {tuple(map(float, site)): address for site, address in zip(rendered.sites_um, rendered.array.addresses)}
    sites = np.array(list(truth))
    mismatched = sum(1 for point, entry in zip(detection.points_um, indexed)
                     if entry.q is not None and (entry.q, entry.r) != truth[tuple(sites[np.argmin(np.linalg.norm(sites - point, axis=1))])])
    assert mismatched > 0.5 * len(indexed)                     # the addresses really are wrong ...
    square = max(scores, key=lambda s: s.coherence)
    assert check_orientation(detection.fit, square.orientation_deg).ok is False         # ... and this is what says so


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_fitted_geometry_agrees_with_the_drawn_nanowells_across_the_chain(fmt):
    rendered, image, pitch, diameter, px = _render(fmt, rotation=7.0, bubble=0.10)
    detection, indexed, resolved, _ = _addresses(rendered, image, pitch, diameter, px)
    geometries = refine_sites(image, px, detection.points_um, diameter)
    fitted = [g for g in geometries if g.geometry == "fitted"]
    assert len(fitted) > 0.9 * len(geometries)
    assert np.median([g.radius_um for g in fitted]) == pytest.approx(diameter / 2, rel=0.01)
    assert check_format(detection.fit.pitch, diameter, [g.radius_um for g in fitted]).ok
