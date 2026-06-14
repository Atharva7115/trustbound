"""
Phase 5: Calibrated confidence model.

Confidence is a first-class prediction. It must mean something:
  high confidence → the correction is likely correct
  low confidence  → we are guessing; flag instead of correcting

Six signals, each in [0,1], combined with documented weights.
Every signal and its weight is justified below — nothing is a magic number.

Signal design principle: if in doubt, be conservative.
A flagged plot is a valid outcome. A wrong correction with high confidence
is the worst outcome (kills calibration score).

Signals
-------
S1  alignment_gap
    How much did the combined boundary+image score improve from the global
    shift to the best local position?  Larger gap = optimizer found a clear
    "snap point" = more trustworthy.
    Weight: 0.20  (moderate — sparse boundaries limit its range)

S2  peak_sharpness
    Ratio of best score to mean of top-5 candidates.
    Measures whether there is ONE clear winner or many equally-good positions.
    Flat landscape = ambiguous = low confidence.
    Weight: 0.20

S3  area_consistency
    How well does the predicted geometry area match the recorded area
    (cultivable + pot-kharaba)?  A mismatch means either we shifted to the
    wrong field or the records are unreliable.
    Weight: 0.25  (highest weight — independent of imagery quality)

S4  boundary_visibility
    Fraction of edge pixels in the plot's bounding box relative to the
    village-wide median.  Very few detected edges = imagery unclear or the
    plot is in a featureless area = lower confidence in local refinement.
    Weight: 0.15

S5  local_shift_penalty
    Large local refinements (>10m extra beyond global shift) suggest the
    optimizer wandered far from the prior and may have latched onto a
    neighbouring field boundary.  Penalise proportionally.
    Weight: 0.10

S6  global_shift_reliability
    How consistent is the global shift estimate?  Measured as
    1 - (spread / median_displacement).  High spread = the 6 truth plots
    show inconsistent drift = global prior is weak.
    Weight: 0.10  (same for every plot — village-level signal)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from src.alignment import (
    AlignmentResult, BoundaryRaster, GlobalShift, UTM_ZONE,
    _build_boundary_scorer, _get_tf, _reproject,
)

log = logging.getLogger(__name__)

# Signal weights — must sum to 1.0
W_ALIGNMENT_GAP         = 0.25
W_PEAK_SHARPNESS        = 0.10
W_AREA_CONSISTENCY      = 0.35
W_BOUNDARY_VISIBILITY   = 0.10
W_LOCAL_SHIFT_PENALTY   = 0.10
W_GLOBAL_RELIABILITY    = 0.10

assert abs(W_ALIGNMENT_GAP + W_PEAK_SHARPNESS + W_AREA_CONSISTENCY +
           W_BOUNDARY_VISIBILITY + W_LOCAL_SHIFT_PENALTY +
           W_GLOBAL_RELIABILITY - 1.0) < 1e-9, "Weights must sum to 1"

# Thresholds — documented, not magic
MAX_LOCAL_SHIFT_M   = 16.0   # search radius; beyond this confidence = 0
AREA_TOLERANCE      = 0.30   # ±30% area match is acceptable
MIN_CONFIDENCE      = 0.05   # floor so we never emit exactly 0
MAX_CONFIDENCE      = 0.95   # ceiling — we are never 100% certain


@dataclass
class ConfidenceBreakdown:
    """Per-signal confidence values and the final combined score."""
    plot_number:          str
    s1_alignment_gap:     float
    s2_peak_sharpness:    float
    s3_area_consistency:  float
    s4_boundary_visibility: float
    s5_local_shift_penalty: float
    s6_global_reliability:  float
    confidence:           float   # final, clipped to [MIN, MAX]

    def as_dict(self) -> dict:
        return {
            "s1_alignment_gap":       round(self.s1_alignment_gap, 4),
            "s2_peak_sharpness":      round(self.s2_peak_sharpness, 4),
            "s3_area_consistency":    round(self.s3_area_consistency, 4),
            "s4_boundary_visibility": round(self.s4_boundary_visibility, 4),
            "s5_local_shift_penalty": round(self.s5_local_shift_penalty, 4),
            "s6_global_reliability":  round(self.s6_global_reliability, 4),
            "confidence":             round(self.confidence, 4),
        }


# ---------------------------------------------------------------------------
# Village-level context (computed once)
# ---------------------------------------------------------------------------

@dataclass
class VillageContext:
    """Precomputed village-wide statistics used by per-plot confidence."""
    global_reliability:     float   # S6
    median_edge_density:    float   # reference for S4
    median_displacement_m:  float   # for S6 derivation


def build_village_context(
    global_shift: GlobalShift,
    braster: BoundaryRaster,
    plots,           # GeoDataFrame EPSG:4326
    sample_n: int = 200,
) -> VillageContext:
    """
    Compute village-wide statistics from a random sample of plots.
    - global_reliability: how consistent the drift estimate is
    - median_edge_density: baseline boundary coverage across the village
    """
    from shapely.ops import transform as shp_tf
    tf_to_utm = _get_tf("EPSG:4326", UTM_ZONE)

    # S6: reliability of the global shift
    median_dist = math.hypot(global_shift.dx_m, global_shift.dy_m)
    if median_dist > 0:
        reliability = max(0.0, 1.0 - global_shift.spread_m / median_dist)
    else:
        reliability = 0.5
    reliability = float(np.clip(reliability, 0.0, 1.0))

    # S4 baseline: median edge density across sample plots
    sample_idx = list(plots.index)
    rng        = np.random.default_rng(42)
    if len(sample_idx) > sample_n:
        sample_idx = rng.choice(sample_idx, size=sample_n, replace=False).tolist()

    densities = []
    for pn in sample_idx:
        geom_4326 = plots.loc[pn, "geometry"]
        geom_utm  = shp_tf(lambda xs, ys, z=None: tf_to_utm.transform(xs, ys), geom_4326)
        geom_3857 = _reproject(geom_utm, UTM_ZONE, "EPSG:3857")
        b         = geom_3857.buffer(5).bounds
        result    = braster.crop_array(b)
        if result is None:
            continue
        crop, _ = result
        if crop.size == 0:
            continue
        densities.append(float(crop.sum()) / crop.size)

    median_density = float(np.median(densities)) if densities else 0.05

    log.info(
        "VillageContext: reliability=%.3f  median_edge_density=%.4f  "
        "median_dist=%.1fm",
        reliability, median_density, median_dist,
    )

    return VillageContext(
        global_reliability    = reliability,
        median_edge_density   = max(median_density, 1e-6),
        median_displacement_m = median_dist,
    )


# ---------------------------------------------------------------------------
# Per-plot confidence
# ---------------------------------------------------------------------------

def compute_confidence(
    ar: AlignmentResult,
    plot_props: dict,
    braster: BoundaryRaster,
    iraster,          # ImageRaster | None
    ctx: VillageContext,
    search_radius_m: float = 16.0,
    band_m: float = 3.0,
) -> ConfidenceBreakdown:
    """
    Compute confidence for one aligned plot.

    Parameters
    ----------
    ar         : AlignmentResult from alignment pipeline
    plot_props : dict of plot properties (recorded_area_sqm, pot_kharaba_ha, …)
    braster    : in-memory boundary raster
    iraster    : in-memory image raster (or None)
    ctx        : village-level context
    """
    from shapely.affinity import translate
    from shapely.ops import transform as shp_tf

    tf_to_utm = _get_tf("EPSG:4326", UTM_ZONE)
    geom_utm  = shp_tf(
        lambda xs, ys, z=None: tf_to_utm.transform(xs, ys),
        ar.geometry_corrected,
    )
    shifted_base_utm = shp_tf(
        lambda xs, ys, z=None: tf_to_utm.transform(xs, ys),
        ar.geometry_official,
    )
    shifted_base_utm = translate(
        shifted_base_utm, ar.global_shift.dx_m, ar.global_shift.dy_m
    )

    # ── S1: alignment gap ────────────────────────────────────────────────────
    raw_gap = ar.local.score_gap
    # normalise: gap of 0.3 = full score, diminishing returns above
    s1 = float(np.clip(raw_gap / 0.30, 0.0, 1.0))

    # ── S2: peak sharpness ───────────────────────────────────────────────────
    s2 = _peak_sharpness(
        shifted_base_utm, braster, iraster,
        ar.local.extra_dx_m, ar.local.extra_dy_m,
        search_radius_m, band_m,
    )

    # ── S3: area consistency ─────────────────────────────────────────────────
    s3 = _area_consistency(geom_utm, plot_props)

    # ── S4: boundary visibility ───────────────────────────────────────────────
    s4 = _boundary_visibility(geom_utm, braster, ctx.median_edge_density)

    # ── S5: local shift penalty ───────────────────────────────────────────────
    extra_m = math.hypot(ar.local.extra_dx_m, ar.local.extra_dy_m)
    # penalty starts at 8m (half radius), reaches 0 at full radius
    s5 = float(np.clip(1.0 - (extra_m / MAX_LOCAL_SHIFT_M) ** 1.5, 0.0, 1.0))

    # ── S6: global reliability ────────────────────────────────────────────────
    s6 = ctx.global_reliability

    # ── Combined ──────────────────────────────────────────────────────────────
    raw = (
        W_ALIGNMENT_GAP       * s1 +
        W_PEAK_SHARPNESS      * s2 +
        W_AREA_CONSISTENCY    * s3 +
        W_BOUNDARY_VISIBILITY * s4 +
        W_LOCAL_SHIFT_PENALTY * s5 +
        W_GLOBAL_RELIABILITY  * s6
    )
    confidence = float(np.clip(raw, MIN_CONFIDENCE, MAX_CONFIDENCE))

    return ConfidenceBreakdown(
        plot_number           = ar.plot_number,
        s1_alignment_gap      = round(s1, 4),
        s2_peak_sharpness     = round(s2, 4),
        s3_area_consistency   = round(s3, 4),
        s4_boundary_visibility= round(s4, 4),
        s5_local_shift_penalty= round(s5, 4),
        s6_global_reliability = round(s6, 4),
        confidence            = round(confidence, 4),
    )


# ---------------------------------------------------------------------------
# Signal implementations
# ---------------------------------------------------------------------------

def _peak_sharpness(
    shifted_base_utm,
    braster: BoundaryRaster,
    iraster,
    best_dx: float,
    best_dy: float,
    search_radius_m: float,
    band_m: float,
    step_m: float = 2.0,
    top_k: int = 5,
) -> float:
    """
    Ratio: best_score / mean(top_k scores).
    Close to 1.0 = flat landscape = ambiguous.
    >1.3 = clear winner = higher confidence.
    """
    b_fn, _ = _build_boundary_scorer(shifted_base_utm, braster, search_radius_m, band_m)

    if iraster is not None:
        from src.image_signals import build_image_scorer
        i_fn, _ = build_image_scorer(shifted_base_utm, iraster, search_radius_m, band_m)
        w_b, w_i = 0.6, 0.4
    else:
        i_fn     = lambda px_dx, px_dy: 0.0
        w_b, w_i = 1.0, 0.0

    scores = []
    res = braster.res_m
    for tx in np.arange(-search_radius_m, search_radius_m + step_m, step_m):
        for ty in np.arange(-search_radius_m, search_radius_m + step_m, step_m):
            if math.hypot(tx, ty) > search_radius_m:
                continue
            px_dx = int(round(tx  / res))
            px_dy = int(round(-ty / res))
            sc    = w_b * b_fn(px_dx, px_dy) + w_i * i_fn(px_dx, px_dy)
            scores.append(sc)

    if not scores:
        return 0.5

    scores_arr = np.array(scores)
    best       = scores_arr.max()
    mean_all   = scores_arr.mean()
    std_all    = scores_arr.std()

    if mean_all < 1e-9:
        return 0.1   # all zeros — no signal

    # sharpness = how many std-deviations above mean is the best candidate
    # 0 std above mean → 0.0,  2+ std above mean → 1.0
    z_score = (best - mean_all) / (std_all + 1e-9)
    return float(np.clip(z_score / 2.0, 0.0, 1.0))


def _safe_float(val, default: float = 0.0) -> float:
    """Convert pandas value to float, treating NaN/None as default."""
    try:
        f = float(val)
        return default if (f != f) else f   # f != f is True for NaN
    except (TypeError, ValueError):
        return default


def _area_consistency(geom_utm, plot_props: dict) -> float:
    """
    Compare predicted area to recorded total area (cultivable + pot-kharaba).
    Returns 1.0 for perfect match, decays to 0 outside ±AREA_TOLERANCE.
    Returns 0.5 when recorded area is null (no penalisation, no reward).
    """
    rec  = _safe_float(plot_props.get("recorded_area_sqm"), 0.0)
    pkar = _safe_float(plot_props.get("pot_kharaba_ha"),    0.0) * 10_000
    total_rec = rec + pkar

    if total_rec <= 0:
        return 0.5   # unknown — neutral

    pred_area = geom_utm.area
    if pred_area <= 0:
        return 0.0

    ratio = pred_area / total_rec
    deviation = abs(ratio - 1.0)
    score     = math.exp(-(deviation / AREA_TOLERANCE) ** 2)
    return float(np.clip(score, 0.0, 1.0))


def _boundary_visibility(
    geom_utm,
    braster: BoundaryRaster,
    median_edge_density: float,
    pad_m: float = 5.0,
) -> float:
    """
    Edge pixel density inside the plot bounding box, relative to village median.
    Plots with very few detected edges get lower confidence in local refinement.
    Capped at 1.0 — being above median doesn't add extra confidence.
    """
    geom_3857 = _reproject(geom_utm, UTM_ZONE, "EPSG:3857")
    b         = geom_3857.buffer(pad_m).bounds
    result    = braster.crop_array(b)
    if result is None:
        return 0.3   # outside raster — penalise slightly

    crop, _ = result
    if crop.size == 0:
        return 0.3

    density = float(crop.sum()) / crop.size
    score   = density / (median_edge_density * 2.0)   # 2× median = full score
    return float(np.clip(score, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Village-wide confidence pass
# ---------------------------------------------------------------------------

def run_confidence(
    alignment_results: list[AlignmentResult],
    global_shift: GlobalShift,
    village,
    braster: BoundaryRaster,
    iraster,
) -> list[ConfidenceBreakdown]:
    """
    Compute confidence for every plot.
    Runs in a single pass after alignment — braster/iraster already in RAM.
    """
    ctx = build_village_context(
        global_shift, braster, village.plots, sample_n=200
    )

    plots = village.plots
    breakdowns = []
    for ar in alignment_results:
        props = plots.loc[ar.plot_number].to_dict() if ar.plot_number in plots.index else {}
        cb    = compute_confidence(
            ar, props, braster, iraster, ctx,
            search_radius_m=ar.local.search_radius_m,
            band_m=3.0,
        )
        breakdowns.append(cb)

    confs = [cb.confidence for cb in breakdowns]
    log.info(
        "Confidence: min=%.3f  median=%.3f  max=%.3f",
        min(confs), float(np.median(confs)), max(confs),
    )
    return breakdowns
