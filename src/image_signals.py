"""
Phase 4: Image-based alignment signals.

Provides ImageRaster — an in-memory imagery cache analogous to BoundaryRaster —
and two scoring functions:

  score_image_gradient(geom_utm, iraster)
      Mean Sobel edge magnitude within a thin band around the polygon perimeter.
      High = strong visible field edges coincide with polygon boundary.

  score_combined(geom_utm, braster, iraster, w_boundary, w_image)
      Weighted combination of boundary-hint score and image-gradient score.
      Both are normalised to [0,1] before combining so neither dominates by scale.

Design notes
------------
- Imagery is RGB uint8 at ~1.2 m/px (EPSG:3857).
- We convert to grayscale and apply a Sobel filter.  No Canny — Canny thresholds
  are image-specific; Sobel magnitude is parameter-free and comparably effective
  here because we only need a relative score, not absolute edge detection.
- The same thin-band approach used for boundary hints avoids the interior-density
  trap (maximising interior gradient just centres on the brightest feature, not the
  boundary).
- ImageRaster loads the full image once.  Per-candidate scoring uses numpy slicing
  — zero rasterio I/O per candidate, same pattern as BoundaryRaster.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds as raster_tfb
from scipy.ndimage import sobel
from shapely.geometry.base import BaseGeometry

from src.alignment import BoundaryRaster, UTM_ZONE, _reproject, score_perimeter

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory imagery raster
# ---------------------------------------------------------------------------

@dataclass
class ImageRaster:
    """
    Full satellite image loaded as a float32 Sobel-magnitude array.
    Loaded once; all scoring uses numpy array slicing.
    """
    edge_mag: np.ndarray   # (H, W) float32, Sobel magnitude of grayscale image
    transform: object      # rasterio Affine, EPSG:3857
    crs: str
    res_m: float

    @classmethod
    def load(cls, path: Path) -> "ImageRaster":
        with rasterio.open(str(path)) as src:
            rgb = src.read([1, 2, 3]).astype(np.float32)  # (3, H, W)
            gray = rgb.mean(axis=0)                        # (H, W)
            sx   = sobel(gray, axis=1)
            sy   = sobel(gray, axis=0)
            mag  = np.hypot(sx, sy).astype(np.float32)
            return cls(
                edge_mag  = mag,
                transform = src.transform,
                crs       = str(src.crs),
                res_m     = float(src.res[0]),
            )

    def crop_array(self, bounds_3857: tuple) -> tuple[np.ndarray, object] | None:
        """Return (mag_crop, win_transform) clipped to bounds."""
        l, bot, r, t = bounds_3857
        tf = self.transform
        col0 = int((l   - tf.c) / tf.a)
        col1 = int((r   - tf.c) / tf.a) + 1
        row0 = int((t   - tf.f) / tf.e)
        row1 = int((bot - tf.f) / tf.e) + 1
        H, W = self.edge_mag.shape
        col0 = max(col0, 0); col1 = min(col1, W)
        row0 = max(row0, 0); row1 = min(row1, H)
        if col1 <= col0 or row1 <= row0:
            return None
        crop   = self.edge_mag[row0:row1, col0:col1]
        win_tf = raster_tfb(
            tf.c + col0 * tf.a,
            tf.f + row1 * tf.e,
            tf.c + col1 * tf.a,
            tf.f + row0 * tf.e,
            col1 - col0,
            row1 - row0,
        )
        return crop, win_tf


# ---------------------------------------------------------------------------
# Image gradient perimeter score
# ---------------------------------------------------------------------------

def score_image_gradient(
    geom_utm: BaseGeometry,
    iraster: ImageRaster,
    band_m: float = 4.0,
) -> float:
    """
    Mean Sobel edge magnitude within `band_m` metres of the polygon perimeter,
    normalised by the 95th-percentile magnitude in the patch (so the score
    reflects boundary sharpness relative to the local image contrast).

    Returns a value loosely in [0, 1]; can exceed 1.0 if the boundary is the
    sharpest feature in the crop, but that's fine — it's a relative signal.
    """
    geom_3857 = _reproject(geom_utm, UTM_ZONE, "EPSG:3857")
    outer = geom_3857.buffer(band_m)
    inner = geom_3857.buffer(-band_m)
    band  = outer.difference(inner) if not inner.is_empty else outer

    pad    = band_m + iraster.res_m * 2
    b      = band.bounds
    bounds = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)

    result = iraster.crop_array(bounds)
    if result is None:
        return 0.0
    crop, win_tf = result
    h, w = crop.shape
    if h == 0 or w == 0:
        return 0.0

    band_mask = rasterize(
        [band], out_shape=(h, w), transform=win_tf,
        fill=0, default_value=1, dtype=np.uint8,
    ).astype(bool)

    if band_mask.sum() == 0:
        return 0.0

    mean_band  = float(crop[band_mask].mean())
    p95        = float(np.percentile(crop, 95)) if crop.size > 0 else 1.0
    return mean_band / p95 if p95 > 0 else 0.0


# ---------------------------------------------------------------------------
# Fast shift-based image scorer (rasterize once, shift window per candidate)
# ---------------------------------------------------------------------------

def build_image_scorer(
    geom_utm: BaseGeometry,
    iraster: ImageRaster,
    search_radius_m: float,
    band_m: float = 4.0,
) -> tuple:
    """
    Pre-compute band mask and padded edge-magnitude crop once.
    Returns (score_fn, score_at_origin) where score_fn(px_dx, px_dy) -> float.
    Same pixel-shift trick as BoundaryRaster local_refine.
    """
    geom_3857 = _reproject(geom_utm, UTM_ZONE, "EPSG:3857")
    outer = geom_3857.buffer(band_m)
    inner = geom_3857.buffer(-band_m)
    band  = outer.difference(inner) if not inner.is_empty else outer

    pad    = search_radius_m + band_m + iraster.res_m * 2
    b      = band.bounds
    bounds = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)

    result = iraster.crop_array(bounds)
    if result is None:
        def _zero(px_dx, px_dy): return 0.0
        return _zero, 0.0

    mag_crop, win_tf = result
    ch, cw = mag_crop.shape

    band_mask = rasterize(
        [band], out_shape=(ch, cw), transform=win_tf,
        fill=0, default_value=1, dtype=np.uint8,
    ).astype(bool)

    # normalise by patch 95th percentile
    p95 = float(np.percentile(mag_crop, 95)) if mag_crop.size > 0 else 1.0

    def _score(px_dx: int, px_dy: int) -> float:
        r0s = max(0,  -px_dy); r1s = min(ch, ch - px_dy)
        c0s = max(0,  -px_dx); c1s = min(cw, cw - px_dx)
        r0e = max(0,   px_dy); r1e = min(ch, ch + px_dy)
        c0e = max(0,   px_dx); c1e = min(cw, cw + px_dx)
        if r1s <= r0s or c1s <= c0s or r1e <= r0e or c1e <= c0e:
            return 0.0
        m  = band_mask[r0s:r1s, c0s:c1s]
        ec = mag_crop[r0e:r1e, c0e:c1e]
        if m.shape != ec.shape or m.sum() == 0:
            return 0.0
        return float(ec[m].mean()) / p95 if p95 > 0 else 0.0

    return _score, _score(0, 0)
