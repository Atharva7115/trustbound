"""
Validate that local_refine finds the same offsets as the trusted probe script.
Probe (_probe_boundary_signal.py) used rasterize-per-candidate (ground truth).
This validates the fast mask-shift approach matches it on 3 truth plots.
"""
import math, statistics
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds as tfb
from shapely.affinity import translate
from shapely.ops import transform as shp_tf
from pyproj import Transformer

from bhume import load
from src.alignment import (
    estimate_global_shift, BoundaryRaster, local_refine,
    _reproject, UTM_ZONE
)

VILLAGE_DIR = "data/34855_vadnerbhairav_chandavad_nashik"
village = load(VILLAGE_DIR)
gs = estimate_global_shift(village)
braster = BoundaryRaster.load(village.boundaries_path)

plots_u  = village.plots.to_crs(UTM_ZONE)
truths_u = village.example_truths.to_crs(UTM_ZONE)

# Ground-truth scorer (rasterize per candidate — slow but correct)
def score_gt(geom_utm, band_m=3.0):
    geom_3857 = _reproject(geom_utm, UTM_ZONE, "EPSG:3857")
    outer = geom_3857.buffer(band_m)
    inner = geom_3857.buffer(-band_m)
    band  = outer.difference(inner) if not inner.is_empty else outer
    import rasterio as rio
    with rio.open(str(village.boundaries_path)) as src:
        from rasterio.windows import from_bounds as rwb
        b = band.bounds
        dl,db,dr,dt = src.bounds
        l=max(b[0],dl); bot=max(b[1],db); r=min(b[2],dr); t=min(b[3],dt)
        if r<=l or t<=bot: return 0.0
        win  = rwb(l,bot,r,t, transform=src.transform)
        data = src.read(1, window=win)
        h,w  = data.shape
        wt   = tfb(l,bot,r,t,w,h)
        mask = rasterize([band], out_shape=(h,w), transform=wt,
                         fill=0, default_value=1, dtype=np.uint8)
        bp = int(mask.sum())
        return float(((data==255)&(mask==1)).sum()/bp) if bp>0 else 0.0

RADIUS, STEP = 16, 2
print(f"Comparing fast vs ground-truth scorer  (radius={RADIUS}m step={STEP}m)")
print(f"{'plot':<7} {'gt_dx':>7} {'gt_dy':>7} {'gt_sc':>8} | {'fast_dx':>8} {'fast_dy':>8} {'fast_sc':>9} {'match':>6}")

for pn in list(village.example_truths.index)[:4]:
    og_utm = plots_u.loc[pn,"geometry"]
    shifted = translate(og_utm, gs.dx_m, gs.dy_m)

    # ground-truth grid search
    best_gt = score_gt(shifted); bdx_gt=0; bdy_gt=0
    for tx in range(-RADIUS, RADIUS+1, STEP):
        for ty in range(-RADIUS, RADIUS+1, STEP):
            if math.hypot(tx,ty) > RADIUS: continue
            sc = score_gt(translate(shifted,tx,ty))
            if sc > best_gt: best_gt=sc; bdx_gt=tx; bdy_gt=ty

    # fast approach
    lr = local_refine(shifted, braster, search_radius_m=RADIUS, step_m=STEP, band_m=3.0)

    match = "OK" if (abs(lr.extra_dx_m-bdx_gt)<=STEP and abs(lr.extra_dy_m-bdy_gt)<=STEP) else "FAIL"
    print(f"{pn:<7} {bdx_gt:>7} {bdy_gt:>7} {best_gt:>8.4f} | "
          f"{lr.extra_dx_m:>8.1f} {lr.extra_dy_m:>8.1f} {lr.score_after:>9.4f} {match:>6}")
