# Nanowell geometry scientific self-review — PR 1

## Scope and provenance

This is an inline scientific self-review by the implementing assistant, not an
independent external review or a validation on microscopy data. The reviewed
implementation is commit `a8bbe41e0728b64282d81f3776b2adb6f6deac67` on
`claude/nanowell-1-geometry`. The review covers the Section G branch diff:
integer addressing, lattice fitting and indexing, independent origin resolution,
partial circular boundary fitting, model rosters, disk ownership and rigid plate
motion. It also examines the updated orientation limits and the synthetic tests.

There are no additional blocking findings within the specified point-set model.
The qualifications below are part of that conclusion. Real phase-image and
stage-coordinate validation remains unperformed; tracker Q7 and R8 retain it.
No Analyze, Review or image-detector integration is asserted.

## Addressing and coordinate conventions

Hexagonal rings use `max(|q|, |r|, |q+r|)` and six directed sides; square rings
use `max(|q|, |r|)` and their prescribed perimeter walk. These integer walks
are invertible independently of image coordinates. Exhaustive round trips through
ring 45 and explicit labels for both kinds' first two rings provide different
checks: round trips alone would also pass a consistently reversed square walk.
The prior square-direction mutation establishes that the explicit test detects it.

The image convention is x right, y down, so a screen-counter-clockwise rotation
has negative y displacement from east. Unmirroring is explicit, and basis vectors
are returned in the original input frame. The noisy-angle refusal tests support
the user's measured-angle limits of ±25° for hexagonal lattices and ±40° for
square lattices. These limits protect the band next to the axis ambiguity; they
cannot distinguish a lattice from the same lattice rotated by its symmetry period.
The later square-orientation cross-check remains assigned to N3a. This limitation
is documented and does not constitute an unresolved Section G specification.

## Lattice fitting, indexing and model rosters

Nearest-neighbour lengths and folded angular clusters supply a seed; periodic
coordinate consensus prevents a central outlier from choosing the phase.
Affine least squares estimates two independent basis vectors and an intercept.
Its residuals, length ratio and inter-vector angle therefore retain departure
from the ideal geometry rather than silently enforcing an ideal lattice.

Nearest-site selection compares Cartesian distances over adjacent integer
candidates. The quarter-pitch residual rule and duplicate selection prevent a
bad observation from receiving a fabricated address. The fitted phase origin
is a lattice site near the retained centroid, not evidence of the central square;
that distinction is enforced by the independently anchored recovery fixtures.
The shifted-window and moved-plate tests exercise actual fitting, resolution and
indexing, with prior mutations demonstrating failure under the wrong origin.

Roster enumeration uses the smallest singular value to bound the integer search:
if a site lies within the model disk, its coordinate norm cannot exceed the disk
radius divided by that singular value. Thus the finite enumeration does not omit
sites that satisfy the Cartesian radius criterion. Its input radius must be the
shared array-model radius. Independent clipped observations can change estimated
radii and marginal rosters; selecting the shared radius and checking cross-timepoint
agreement belong to N8/N8a. The primitive does not silently solve that integration
problem by quantising or carrying over estimates.

## Boundary and coverage geometry

The boundary fitter assumes that its inputs are lattice-filtered sites and that
a circular rim is observable. Convex-hull hypotheses and robust radial refinement
separate that rim from interior detections and clipping chords. The actual
120-degree lattice-cap regression is essential: continuous arc samples alone
missed the endpoint discretisation. The one-spacing angular allowance is explicit,
and the measured angular coverage remains reported rather than rounded up.
Synthetic success establishes this circle model, not reliable boundary detection
under every image artifact or clipping pattern.

Disk coverage uses vertical slabs whose tile membership is constant. Within each
slab, the disk's largest vertical cross-section occurs nearest its centre. That
section must be contained in a merged covered y interval. This checks the entire
disk, including interior coverage holes, while allowing uncovered bounding-box
corners outside the disk. Whole physical-disk union coverage determines fused
versus unimaged; the margin separately determines single-tile ownership.

## Rigid motion and uncertainty

Kabsch refinement imposes a proper rotation and translation, without a scale
parameter or reflection. Spatially coincident inputs cannot identify rotation and
are explicitly refused. The consensus re-fit allows genuine wells to rejoin,
with a conservative radial cutoff. Its Rayleigh scaling is appropriate to the
norm of two independent, equal-variance Gaussian residual components; it is not
an assertion that real microscope errors always follow that distribution.

For the conditional linearised rigid model, centre source coordinates and form
the Jacobian for two translations and one rotation. Translation columns are the
identity; the rotational column is the rotated perpendicular source position.
At the source centroid the cross terms vanish, leaving translation information
proportional to the number of wells and rotation information proportional to the
sum of squared distances from the centroid. Residual variance has `2n−3` degrees
of freedom. Propagating the three-parameter covariance to a fixed prediction
point gives the radial standard error used by `predict_origin`.

A separate numerical probe constructs the rotational Jacobian by finite
perturbations, inverts its normal matrix, and propagates the full covariance.
For twelve wells on a 9 mm grid with noisy target coordinates, the numerical
radial standard errors are 1.8934500933 µm at the centroid and 13.6232794540 µm
at a distant point. The reported values are 1.8934500933 and 13.6232794447 µm;
both agree within relative tolerance one part per million. This checks the
covariance interpretation rather than merely repeating the implemented formula.
The probe and its output accompany the session's close-out report.

The committed 500-fit simulation independently checks empirical prediction error
under 5 µm per-axis noise in both acquisitions. It requires nearly every fit to
retain every genuine well and near/far uncertainty within ten percent of empirical
radial RMS. Separate outlier and proper-rotation tests pin rejection and motion.
All these tests passed in the 327-test review run on the reviewed commit.

The resulting uncertainty is conditional on a fixed prediction input and assumes
independent isotropic residuals. It excludes measurement uncertainty of that
input, calibration uncertainty, systematic stage errors and correlated acquisition
errors. The three-sigma half-pitch gate is implemented as specified; it is not an
unconditional experimental guarantee. Measured-stage validation remains absent.

## Close-out checks

The reviewed commit passed 327 targeted tests, including the complete guard family,
all five Section G test files, remote manifest and exact runner contracts, import
scope checks and the established cloud-runtime regressions. Ruff and whitespace
checks passed. The final full fast lane is required after the close-out commit;
its actual counts, process status and tested SHA are delivered in the §4.2 session
report. This document does not substitute earlier tests for that final run.
