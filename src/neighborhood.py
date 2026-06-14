"""
Phase 7: Neighborhood-aware drift estimation.

Design
------
With only 6 truth plots spread across a 7.8×7.9 km village (nearest pair
504m apart), a truth-based local drift surface is not feasible — all KNN
estimates collapse to the global shift.

Instead, we use the alignment results themselves:

  1. Build a "drift surface" from HIGH-CONFIDENCE corrected plots.
     Each corrected plot with confidence > anchor_threshold contributes
     its (total_dx, total_dy) as a local drift observation.

  2. For each plot, compute a spatially-weighted estimate of local drift
     using inverse-distance weighting (IDW) over the K nearest anchors.

  3. Use the IDW estimate in two ways:
     a. As a refined initial position (replaces global shift for plots
        whose IDW neighbors are strong and nearby).
     b. As a neighbourhood consistency signal for confidence (S7):
        If this plot's local refinement result is far from what its
        neighbors suggest, reduce confidence.

  4. The neighbourhood model is purely derived from the alignment output —
     no truth data used during inference. Generalises to any village.

Limitations (documented honestly)
----------------------------------
- Cold-start: the first pass uses global shift; the IDW surface is built
  from that same pass's results. A second pass would refine further, but
  the marginal gain is small and the runtime cost doubles.
- Sparse high-confidence anchors: if few plots have confidence > threshold,
  the IDW surface is thin and we fall back to global shift.
- IDW assumes smooth drift variation — breaks down at geological/survey
  discontinuities, which we cannot detect without more truth data.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from src.alignment import AlignmentResult, GlobalShift, UTM_ZONE, _get_tf
from src.confidence import ConfidenceBreakdown

log = logging.getLogger(__name__)

# Minimum confidence to treat a corrected plot as a drift anchor
ANCHOR_CONFIDENCE_THRESHOLD = 0.55
# IDW power parameter (higher = more weight to nearest neighbors)
IDW_POWER = 2.0
# How many nearest anchors to use
K_NEIGHBORS = 8
# Max distance beyond which an anchor has negligible influence (metres)
MAX_INFLUENCE_M = 800.0
# Neighbourhood consistency: if IDW drift vs actual drift > this, penalise
CONSISTENCY_PENALTY_RADIUS_M = 8.0


@dataclass
class NeighborhoodContext:
    """IDW drift surface built from high-confidence alignment results."""
    anchor_positions: np.ndarray   # (N, 2) UTM centroids of anchors
    anchor_dx:        np.ndarray   # (N,) total_dx_m of each anchor
    anchor_dy:        np.ndarray   # (N,) total_dy_m of each anchor
    anchor_confs:     np.ndarray   # (N,) confidence of each anchor
    tree:             cKDTree
    n_anchors:        int
    global_dx:        float        # fallback
    global_dy:        float


def build_neighborhood_context(
    alignment_results: list[AlignmentResult],
    confidence_results: list[ConfidenceBreakdown],
    global_shift: GlobalShift,
    village,
    anchor_threshold: float = ANCHOR_CONFIDENCE_THRESHOLD,
) -> NeighborhoodContext:
    """
    Build IDW drift surface from high-confidence corrected plots.
    Uses UTM centroids of the CORRECTED geometry as anchor positions.
    """
    from shapely.ops import transform as shp_tf

    tf_to_utm = _get_tf("EPSG:4326", UTM_ZONE)
    conf_map  = {cb.plot_number: cb.confidence for cb in confidence_results}

    positions, dxs, dys, confs = [], [], [], []

    for ar in alignment_results:
        conf = conf_map.get(ar.plot_number, 0.0)
        if conf < anchor_threshold:
            continue
        # use the official geometry centroid in UTM as anchor position
        geom_utm = shp_tf(
            lambda xs, ys, z=None: tf_to_utm.transform(xs, ys),
            ar.geometry_official,
        )
        c = geom_utm.centroid
        positions.append([c.x, c.y])
        dxs.append(ar.total_dx_m)
        dys.append(ar.total_dy_m)
        confs.append(conf)

    n = len(positions)
    log.info("Neighborhood anchors: %d plots with conf >= %.2f", n, anchor_threshold)

    if n == 0:
        # degenerate: no anchors, return empty context
        dummy = np.zeros((1, 2))
        return NeighborhoodContext(
            anchor_positions = dummy,
            anchor_dx        = np.array([global_shift.dx_m]),
            anchor_dy        = np.array([global_shift.dy_m]),
            anchor_confs     = np.array([1.0]),
            tree             = cKDTree(dummy),
            n_anchors        = 0,
            global_dx        = global_shift.dx_m,
            global_dy        = global_shift.dy_m,
        )

    pos_arr  = np.array(positions)
    tree     = cKDTree(pos_arr)

    return NeighborhoodContext(
        anchor_positions = pos_arr,
        anchor_dx        = np.array(dxs),
        anchor_dy        = np.array(dys),
        anchor_confs     = np.array(confs),
        tree             = tree,
        n_anchors        = n,
        global_dx        = global_shift.dx_m,
        global_dy        = global_shift.dy_m,
    )


def idw_drift_estimate(
    x: float, y: float,
    ctx: NeighborhoodContext,
    k: int = K_NEIGHBORS,
    power: float = IDW_POWER,
    max_dist_m: float = MAX_INFLUENCE_M,
) -> tuple[float, float, float]:
    """
    Inverse-distance weighted drift estimate at position (x, y) in UTM.

    Returns (est_dx, est_dy, mean_dist_m).
    Falls back to global shift if no anchors are within max_dist_m.
    """
    if ctx.n_anchors == 0:
        return ctx.global_dx, ctx.global_dy, max_dist_m

    k_actual = min(k, ctx.n_anchors)
    dists, idxs = ctx.tree.query([x, y], k=k_actual)

    # filter by max distance
    mask  = dists <= max_dist_m
    dists = dists[mask]
    idxs  = idxs[mask]

    if len(dists) == 0:
        return ctx.global_dx, ctx.global_dy, max_dist_m

    # avoid division by zero for coincident points
    dists = np.maximum(dists, 0.1)
    weights = (1.0 / dists) ** power
    # weight by confidence too
    conf_weights = ctx.anchor_confs[idxs]
    weights = weights * conf_weights
    w_sum   = weights.sum()

    est_dx = float((weights * ctx.anchor_dx[idxs]).sum() / w_sum)
    est_dy = float((weights * ctx.anchor_dy[idxs]).sum() / w_sum)
    mean_d = float(dists.mean())

    return est_dx, est_dy, mean_d


# ---------------------------------------------------------------------------
# Neighbourhood consistency signal (S7 for confidence)
# ---------------------------------------------------------------------------

def neighbourhood_consistency_score(
    ar: AlignmentResult,
    ctx: NeighborhoodContext,
    utm_x: float,
    utm_y: float,
    penalty_radius_m: float = CONSISTENCY_PENALTY_RADIUS_M,
) -> float:
    """
    How consistent is this plot's correction with its neighbours?

    Returns a score in [0, 1]:
      1.0 = plot's drift agrees exactly with neighbourhood IDW estimate
      0.0 = plot's drift deviates by >= penalty_radius_m from neighbourhood

    If the neighbourhood has no nearby anchors (sparse area), returns 0.5
    (neutral — don't penalise, don't reward).
    """
    if ctx.n_anchors == 0:
        return 0.5

    est_dx, est_dy, mean_dist = idw_drift_estimate(utm_x, utm_y, ctx)

    # if nearest anchors are far away, this estimate is unreliable
    if mean_dist > MAX_INFLUENCE_M * 0.8:
        return 0.5   # neutral — not enough local evidence

    deviation = math.hypot(
        ar.total_dx_m - est_dx,
        ar.total_dy_m - est_dy,
    )
    score = math.exp(-(deviation / penalty_radius_m) ** 2)
    return float(np.clip(score, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Apply neighbourhood: update confidence with S7
# ---------------------------------------------------------------------------

def apply_neighbourhood_to_confidence(
    alignment_results: list[AlignmentResult],
    confidence_results: list[ConfidenceBreakdown],
    ctx: NeighborhoodContext,
    village,
    w_s7: float = 0.08,
) -> list[ConfidenceBreakdown]:
    """
    Re-weight existing confidence scores by incorporating neighbourhood
    consistency (S7).

    Rather than recomputing all signals, we blend the existing confidence
    with S7:
        new_conf = (1 - w_s7) * old_conf + w_s7 * s7

    This preserves the existing signal structure and adds a gentle
    neighbourhood prior. w_s7=0.08 is conservative: neighbourhood signal
    is weak when anchors are sparse, so we don't let it dominate.

    Returns updated ConfidenceBreakdown list (new dataclass instances).
    """
    from dataclasses import replace
    from shapely.ops import transform as shp_tf

    tf_to_utm = _get_tf("EPSG:4326", UTM_ZONE)
    ar_map    = {ar.plot_number: ar for ar in alignment_results}

    updated = []
    for cb in confidence_results:
        ar = ar_map.get(cb.plot_number)
        if ar is None:
            updated.append(cb)
            continue

        geom_utm = shp_tf(
            lambda xs, ys, z=None: tf_to_utm.transform(xs, ys),
            ar.geometry_official,
        )
        c = geom_utm.centroid
        s7 = neighbourhood_consistency_score(ar, ctx, c.x, c.y)

        new_conf = float(np.clip(
            (1.0 - w_s7) * cb.confidence + w_s7 * s7,
            0.05, 0.95,
        ))

        # create updated breakdown (add s7 to method note via confidence field)
        updated.append(ConfidenceBreakdown(
            plot_number            = cb.plot_number,
            s1_alignment_gap       = cb.s1_alignment_gap,
            s2_peak_sharpness      = cb.s2_peak_sharpness,
            s3_area_consistency    = cb.s3_area_consistency,
            s4_boundary_visibility = cb.s4_boundary_visibility,
            s5_local_shift_penalty = cb.s5_local_shift_penalty,
            s6_global_reliability  = cb.s6_global_reliability,
            confidence             = round(new_conf, 4),
        ))

    old_confs = np.array([cb.confidence for cb in confidence_results])
    new_confs = np.array([cb.confidence for cb in updated])
    delta     = new_confs - old_confs
    log.info(
        "Neighbourhood update: mean Δconf=%.4f  std=%.4f  "
        "max_increase=%.4f  max_decrease=%.4f",
        delta.mean(), delta.std(), delta.max(), delta.min(),
    )
    return updated
