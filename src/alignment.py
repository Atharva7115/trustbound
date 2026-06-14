"""
Phase 3: Alignment pipeline.

Two-stage correction:
  Stage 1 — Global shift: median centroid drift estimated from example truths.
  Stage 2 — Local refinement: grid search over boundary-hint perimeter score
             to snap the globally-shifted polygon to visible field edges.

The boundary signal is the fraction of edge pixels (boundaries.tif == 255)
that fall inside a thin band around the polygon perimeter. High score means
the polygon edge aligns with a detected field boundary.

Performance: the entire boundaries raster is read into RAM once as a numpy
array (BoundaryRaster). All scoring uses in-memory slicing — no per-candidate
rasterio I/O. This brings runtime from ~13 min down to ~1 min for 2457 plots.

Nothing here touches example truths during inference — they are only used
once, offline, to derive the global shift estimate.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_bounds as raster_tfb
from rasterio.windows import from_bounds as raster_win
from shapely.affinity import translate
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_tf

log = logging.getLogger(__name__)

UTM_ZONE = "EPSG:32643"   # covers Nashik / Maharashtra


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GlobalShift:
    dx_m: float          # metres east  (UTM)
    dy_m: float          # metres north (UTM)
    n_samples: int       # number of truth plots used
    spread_m: float      # median absolute deviation of residuals — measures reliability


@dataclass
class LocalResult:
    extra_dx_m: float    # refinement delta on top of global shift
    extra_dy_m: float    # refinement delta on top of global shift
    score_before: float  # perimeter boundary score before local search
    score_after: float   # perimeter boundary score after local search
    score_gap: float     # score_after - score_before  (signal strength)
    search_radius_m: float
    step_m: float


@dataclass
class AlignmentResult:
    plot_number: str
    global_shift: GlobalShift
    local: LocalResult
    geometry_official: BaseGeometry   # original lon/lat
    geometry_corrected: BaseGeometry  # final corrected lon/lat
    total_dx_m: float                 # global + local, in UTM metres
    total_dy_m: float
    total_dist_m: float


# ---------------------------------------------------------------------------
# Global shift estimation
# ---------------------------------------------------------------------------

def estimate_global_shift(village) -> GlobalShift:
    """
    Derive a single village-wide translation from the example truths.
    Uses median centroid displacement in local UTM — robust to outliers.

    This is the ONLY place example truths are used. Inference never sees them.
    """
    if village.example_truths is None:
        raise ValueError(f"{village.slug}: no example_truths to estimate shift from")

    plots_u  = village.plots.to_crs(UTM_ZONE)
    truths_u = village.example_truths.to_crs(UTM_ZONE)

    dxs, dys = [], []
    for pn in village.example_truths.index:
        if pn not in plots_u.index:
            continue
        o = plots_u.loc[pn, "geometry"].centroid
        t = truths_u.loc[pn, "geometry"].centroid
        dxs.append(t.x - o.x)
        dys.append(t.y - o.y)

    if not dxs:
        raise ValueError("No overlapping plots between truths and cadastre")

    mdx = statistics.median(dxs)
    mdy = statistics.median(dys)

    # spread = median absolute deviation of total displacement residuals
    residuals = [math.hypot(dx - mdx, dy - mdy) for dx, dy in zip(dxs, dys)]
    spread    = statistics.median(residuals)

    log.info("Global shift: dx=%.2fm dy=%.2fm  n=%d  spread=%.2fm",
             mdx, mdy, len(dxs), spread)

    return GlobalShift(dx_m=mdx, dy_m=mdy, n_samples=len(dxs), spread_m=spread)


# ---------------------------------------------------------------------------
# Boundary-perimeter scoring
# ---------------------------------------------------------------------------

_tf_cache: dict[str, Transformer] = {}


def _get_tf(src_crs: str, dst_crs: str) -> Transformer:
    key = f"{src_crs}->{dst_crs}"
    if key not in _tf_cache:
        _tf_cache[key] = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    return _tf_cache[key]


def _reproject(geom: BaseGeometry, src_crs: str, dst_crs: str) -> BaseGeometry:
    tf = _get_tf(src_crs, dst_crs)
    return shp_tf(lambda xs, ys, z=None: tf.transform(xs, ys), geom)


# ---------------------------------------------------------------------------
# In-memory boundary raster (read once, reuse for all plots)
# ---------------------------------------------------------------------------

@dataclass
class BoundaryRaster:
    """
    Entire boundaries.tif loaded into RAM as a boolean numpy array.
    All scoring uses array slicing — zero rasterio I/O per candidate.
    This reduces full-village runtime from ~13 min to ~1 min.
    """
    data: np.ndarray          # (H, W) bool, True = edge pixel
    transform: object         # rasterio Affine for EPSG:3857
    crs: str                  # always EPSG:3857
    res_m: float              # pixel size in metres

    @classmethod
    def load(cls, path: Path) -> "BoundaryRaster":
        with rasterio.open(str(path)) as src:
            arr  = src.read(1)
            return cls(
                data      = arr == 255,
                transform = src.transform,
                crs       = str(src.crs),
                res_m     = float(src.res[0]),
            )

    def crop_array(self, bounds_3857: tuple) -> tuple[np.ndarray, object] | None:
        """Return (data_crop, win_transform) for a bounding box in EPSG:3857."""
        from rasterio.transform import from_bounds as tfb
        l, bot, r, t = bounds_3857
        tf = self.transform
        # pixel coordinates
        col0 = int((l   - tf.c) / tf.a)
        col1 = int((r   - tf.c) / tf.a) + 1
        row0 = int((t   - tf.f) / tf.e)
        row1 = int((bot - tf.f) / tf.e) + 1
        H, W = self.data.shape
        col0 = max(col0, 0); col1 = min(col1, W)
        row0 = max(row0, 0); row1 = min(row1, H)
        if col1 <= col0 or row1 <= row0:
            return None
        crop      = self.data[row0:row1, col0:col1]
        win_tf    = tfb(
            tf.c + col0 * tf.a,
            tf.f + row1 * tf.e,
            tf.c + col1 * tf.a,
            tf.f + row0 * tf.e,
            col1 - col0,
            row1 - row0,
        )
        return crop, win_tf


def score_perimeter(
    geom_utm: BaseGeometry,
    braster: BoundaryRaster,
    band_m: float = 3.0,
) -> float:
    """
    Fraction of boundary edge pixels within `band_m` metres of the polygon
    perimeter, computed entirely in RAM via BoundaryRaster.

    High score → polygon edges align with detected field boundaries.
    Low score  → polygon sits in the middle of a field.
    """
    geom_3857 = _reproject(geom_utm, UTM_ZONE, "EPSG:3857")

    outer = geom_3857.buffer(band_m)
    inner = geom_3857.buffer(-band_m)
    band  = outer.difference(inner) if not inner.is_empty else outer

    result = braster.crop_array(band.bounds)
    if result is None:
        return 0.0
    crop, win_tf = result
    h, w = crop.shape
    if h == 0 or w == 0:
        return 0.0

    band_mask = rasterize(
        [band], out_shape=(h, w), transform=win_tf,
        fill=0, default_value=1, dtype=np.uint8,
    )
    edge_in_band = int((crop & (band_mask == 1)).sum())
    band_px      = int(band_mask.sum())
    return float(edge_in_band / band_px) if band_px > 0 else 0.0


# ---------------------------------------------------------------------------
# Local refinement
# ---------------------------------------------------------------------------

def local_refine(
    geom_utm: BaseGeometry,
    braster: BoundaryRaster,
    search_radius_m: float = 16.0,
    step_m: float = 2.0,
    band_m: float = 3.0,
) -> LocalResult:
    """
    Grid search ±search_radius_m (circular mask).

    Key optimisation: rasterize the perimeter band ONCE at the starting
    position. For each candidate offset (tx, ty), shift the crop window
    into the raster array instead of re-rasterizing — pure numpy slicing,
    ~50× faster per candidate.
    """
    geom_3857 = _reproject(geom_utm, UTM_ZONE, "EPSG:3857")
    outer = geom_3857.buffer(band_m)
    inner = geom_3857.buffer(-band_m)
    band  = outer.difference(inner) if not inner.is_empty else outer

    # rasterize band mask ONCE
    pad   = search_radius_m + band_m + braster.res_m * 2
    b     = band.bounds
    l0    = b[0] - pad;  bot0 = b[1] - pad
    r0    = b[2] + pad;  t0   = b[3] + pad

    # clip to raster extent
    tf  = braster.transform
    W   = braster.data.shape[1]
    H   = braster.data.shape[0]
    col_min = max(0, int((l0   - tf.c) / tf.a))
    col_max = min(W, int((r0   - tf.c) / tf.a) + 1)
    row_min = max(0, int((t0   - tf.f) / tf.e))
    row_max = min(H, int((bot0 - tf.f) / tf.e) + 1)

    if col_max <= col_min or row_max <= row_min:
        return LocalResult(0.0, 0.0, 0.0, 0.0, 0.0, search_radius_m, step_m)

    # full padded crop of boundary raster
    edge_crop = braster.data[row_min:row_max, col_min:col_max]
    ch, cw    = edge_crop.shape

    # win_transform for the padded crop
    from rasterio.transform import from_bounds as tfb
    crop_left  = tf.c + col_min * tf.a
    crop_top   = tf.f + row_min * tf.e
    crop_right = tf.c + col_max * tf.a
    crop_bot   = tf.f + row_max * tf.e
    win_tf     = tfb(crop_left, crop_bot, crop_right, crop_top, cw, ch)

    # rasterize the band mask once in the padded crop
    band_mask = rasterize(
        [band], out_shape=(ch, cw), transform=win_tf,
        fill=0, default_value=1, dtype=np.uint8,
    ).astype(bool)

    def _score_shifted(px_dx: int, px_dy: int) -> float:
        """
        Score by shifting the edge_crop window relative to the fixed band_mask.
        Moving polygon east by tx metres = edge raster appears to shift west
        = we sample edge_crop offset by (-px_dx, +px_dy) relative to mask.
        """
        # shift edge_crop by (-px_dx, -px_dy) relative to band_mask
        r0s = max(0,  -px_dy)
        r1s = min(ch, ch - px_dy)
        c0s = max(0,  -px_dx)
        c1s = min(cw, cw - px_dx)
        r0e = max(0,   px_dy)
        r1e = min(ch, ch + px_dy)
        c0e = max(0,   px_dx)
        c1e = min(cw, cw + px_dx)
        if r1s <= r0s or c1s <= c0s or r1e <= r0e or c1e <= c0e:
            return 0.0
        m  = band_mask[r0s:r1s, c0s:c1s]
        ec = edge_crop[r0e:r1e, c0e:c1e]
        if m.shape != ec.shape:
            return 0.0
        bp = int(m.sum())
        return float((m & ec).sum() / bp) if bp > 0 else 0.0

    score_before = _score_shifted(0, 0)

    best_score = score_before
    best_dx    = 0.0
    best_dy    = 0.0

    steps = np.arange(-search_radius_m, search_radius_m + step_m, step_m)
    for tx in steps:
        for ty in steps:
            if math.hypot(tx, ty) > search_radius_m:
                continue
            # tx east  → polygon moves right → equivalent to shifting the
            #            crop window LEFT by px_dx pixels (mask stays fixed)
            # ty north → polygon moves up   → equivalent to shifting the
            #            crop window DOWN by px_dy rows (rows increase downward)
            px_dx = int(round(tx  / braster.res_m))
            px_dy = int(round(-ty / braster.res_m))   # row increases downward
            sc    = _score_shifted(px_dx, px_dy)
            if sc > best_score:
                best_score = sc
                best_dx    = float(tx)
                best_dy    = float(ty)

    return LocalResult(
        extra_dx_m      = best_dx,
        extra_dy_m      = best_dy,
        score_before    = score_before,
        score_after     = best_score,
        score_gap       = best_score - score_before,
        search_radius_m = search_radius_m,
        step_m          = step_m,
    )


# ---------------------------------------------------------------------------
# Single-plot alignment
# ---------------------------------------------------------------------------

def align_plot(
    plot_number: str,
    geom_4326: BaseGeometry,
    global_shift: GlobalShift,
    braster: "BoundaryRaster | None",
    search_radius_m: float = 16.0,
    step_m: float = 2.0,
    band_m: float = 3.0,
) -> AlignmentResult:
    """
    Apply global shift then optionally refine locally using BoundaryRaster.
    All geometry operations in UTM; output back in EPSG:4326.
    """
    tf_to_utm  = _get_tf("EPSG:4326", UTM_ZONE)
    tf_to_4326 = _get_tf(UTM_ZONE, "EPSG:4326")

    geom_utm    = shp_tf(lambda xs, ys, z=None: tf_to_utm.transform(xs, ys), geom_4326)
    shifted_utm = translate(geom_utm, global_shift.dx_m, global_shift.dy_m)

    if braster is not None:
        local = local_refine(shifted_utm, braster,
                             search_radius_m=search_radius_m,
                             step_m=step_m, band_m=band_m)
        final_utm = translate(shifted_utm, local.extra_dx_m, local.extra_dy_m)
    else:
        local     = LocalResult(0.0, 0.0, 0.0, 0.0, 0.0, search_radius_m, step_m)
        final_utm = shifted_utm

    final_4326 = shp_tf(lambda xs, ys, z=None: tf_to_4326.transform(xs, ys), final_utm)
    total_dx   = global_shift.dx_m + local.extra_dx_m
    total_dy   = global_shift.dy_m + local.extra_dy_m

    return AlignmentResult(
        plot_number        = plot_number,
        global_shift       = global_shift,
        local              = local,
        geometry_official  = geom_4326,
        geometry_corrected = final_4326,
        total_dx_m         = total_dx,
        total_dy_m         = total_dy,
        total_dist_m       = math.hypot(total_dx, total_dy),
    )


# ---------------------------------------------------------------------------
# Village-wide pipeline
# ---------------------------------------------------------------------------

def run_alignment(
    village,
    search_radius_m: float = 16.0,
    step_m: float = 2.0,
    band_m: float = 3.0,
) -> tuple[GlobalShift, list[AlignmentResult]]:
    """
    Align every plot in the village.
    Loads BoundaryRaster once into RAM, then processes all plots.
    Returns (global_shift, list[AlignmentResult]).
    """
    global_shift = estimate_global_shift(village)

    braster = (
        BoundaryRaster.load(village.boundaries_path)
        if village.boundaries_path
        else None
    )
    if braster:
        log.info("Loaded boundary raster into RAM: %s px, res=%.2fm",
                 braster.data.size, braster.res_m)

    results: list[AlignmentResult] = []
    plots = village.plots

    for pn in plots.index:
        geom   = plots.loc[pn, "geometry"]
        result = align_plot(
            plot_number     = str(pn),
            geom_4326       = geom,
            global_shift    = global_shift,
            braster         = braster,
            search_radius_m = search_radius_m,
            step_m          = step_m,
            band_m          = band_m,
        )
        results.append(result)
        if len(results) % 200 == 0:
            log.info("Aligned %d / %d plots", len(results), len(plots))

    log.info("Alignment complete: %d plots", len(results))
    return global_shift, results
