"""
Probe what boundary signal actually correlates with IoU improvement.
Tests three scoring approaches:
  A) edge density inside polygon (naive — wrong, maximized by moving onto edges)
  B) edge hit rate along polygon perimeter (correct signal)
  C) combined: perimeter edge hits - interior penalty
"""
import math
import statistics

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_bounds as tfb
from rasterio.windows import from_bounds as rwb
from shapely.affinity import translate
from shapely.ops import transform as shp_tf

VILLAGE = "data/34855_vadnerbhairav_chandavad_nashik"
UTM     = "EPSG:32643"
tf_u_to_3857 = Transformer.from_crs(UTM, "EPSG:3857", always_xy=True)

plots  = gpd.read_file(f"{VILLAGE}/input.geojson")
truths = gpd.read_file(f"{VILLAGE}/example_truths.geojson")
plots["plot_number"]  = plots["plot_number"].astype(str)
truths["plot_number"] = truths["plot_number"].astype(str)
plots  = plots.set_index("plot_number", drop=False)
truths = truths.set_index("plot_number", drop=False)
plots_u  = plots.to_crs(UTM)
truths_u = truths.to_crs(UTM)

dxs, dys = [], []
for pn in truths.index:
    o = plots_u.loc[pn, "geometry"].centroid
    t = truths_u.loc[pn, "geometry"].centroid
    dxs.append(t.x - o.x); dys.append(t.y - o.y)
mdx, mdy = statistics.median(dxs), statistics.median(dys)


def to_3857(geom_utm):
    return shp_tf(lambda xs, ys, z=None: tf_u_to_3857.transform(xs, ys), geom_utm)


def score_perimeter(geom_utm, bsrc, buf_m: float = 3.0):
    """
    Fraction of boundary-raster edge pixels that fall within buf_m of the
    polygon perimeter. High = polygon edges align with detected field edges.
    """
    geom_3857 = to_3857(geom_utm)
    ring_3857 = to_3857(geom_utm.exterior if geom_utm.geom_type == "Polygon"
                        else geom_utm.convex_hull)  # fallback for MultiPolygon

    b = geom_3857.buffer(buf_m + 5).bounds
    l, bot, r, t = b
    dl, db, dr, dt = bsrc.bounds
    l, bot, r, t = max(l, dl), max(bot, db), min(r, dr), min(t, dt)
    if r <= l or t <= bot:
        return 0.0

    win = rwb(l, bot, r, t, transform=bsrc.transform)
    data = bsrc.read(1, window=win)
    h, w = data.shape
    if h == 0 or w == 0:
        return 0.0
    win_tf = tfb(l, bot, r, t, w, h)

    # rasterize buffered perimeter (ring buffer)
    ring_buf = ring_3857.buffer(buf_m) if ring_3857.geom_type != "Polygon" else \
               to_3857(geom_utm.exterior.buffer(buf_m) if hasattr(geom_utm, "exterior") else geom_utm.buffer(buf_m))
    # build a thin band around the perimeter
    outer = geom_3857.buffer(buf_m)
    inner = geom_3857.buffer(-buf_m)
    band  = outer.difference(inner) if not inner.is_empty else outer

    band_mask = rasterize([band], out_shape=(h, w), transform=win_tf,
                          fill=0, default_value=1, dtype=np.uint8)
    edge_in_band = ((data == 255) & (band_mask == 1)).sum()
    band_px      = band_mask.sum()
    return float(edge_in_band / band_px) if band_px > 0 else 0.0


def iou(a, b):
    u = a.union(b).area
    return a.intersection(b).area / u if u > 0 else 0.0


STEP   = 2   # metres grid step
RADIUS = 16  # metres search radius

print(f"Search grid: ±{RADIUS}m step={STEP}m  ({(2*RADIUS//STEP+1)**2} candidates)")
print()

with rasterio.open(f"{VILLAGE}/boundaries.tif") as bsrc:
    for pn in truths.index:
        og_utm = plots_u.loc[pn, "geometry"]
        tg_utm = truths_u.loc[pn, "geometry"]
        shifted = translate(og_utm, mdx, mdy)

        iou_official = iou(og_utm, tg_utm)
        iou_global   = iou(shifted, tg_utm)
        sc_shifted   = score_perimeter(shifted, bsrc)

        best_sc, best_dx, best_dy = sc_shifted, 0, 0
        offsets = range(-RADIUS, RADIUS + 1, STEP)
        for tx in offsets:
            for ty in offsets:
                if math.hypot(tx, ty) > RADIUS:
                    continue
                cand = translate(shifted, tx, ty)
                sc   = score_perimeter(cand, bsrc)
                if sc > best_sc:
                    best_sc, best_dx, best_dy = sc, tx, ty

        best_geom    = translate(shifted, best_dx, best_dy)
        iou_refined  = iou(best_geom, tg_utm)

        gap = best_sc - sc_shifted   # how much better is the best candidate

        print(f"plot {pn}:")
        print(f"  IoU  official={iou_official:.3f}  global={iou_global:.3f}  "
              f"global+local={iou_refined:.3f}  delta={iou_refined-iou_global:+.3f}")
        print(f"  perimeter score  at_global={sc_shifted:.4f}  best={best_sc:.4f}  "
              f"gap={gap:.4f}  extra_shift=({best_dx:+d},{best_dy:+d})m")
        print()

print("Key question: does positive gap correlate with positive IoU delta?")
