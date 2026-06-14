"""Temporary drift analysis script — delete after Phase 3 design."""
import math
import statistics

import geopandas as gpd
from shapely.affinity import translate

VILLAGE = "data/34855_vadnerbhairav_chandavad_nashik"
UTM = "EPSG:32643"

plots  = gpd.read_file(f"{VILLAGE}/input.geojson")
truths = gpd.read_file(f"{VILLAGE}/example_truths.geojson")
plots["plot_number"]  = plots["plot_number"].astype(str)
truths["plot_number"] = truths["plot_number"].astype(str)
plots  = plots.set_index("plot_number", drop=False)
truths = truths.set_index("plot_number", drop=False)

plots_u  = plots.to_crs(UTM)
truths_u = truths.to_crs(UTM)

print("=== Per-plot drift (UTM metres) ===")
dxs, dys, rows = [], [], []
for pn in truths.index:
    o  = plots_u.loc[pn, "geometry"].centroid
    t  = truths_u.loc[pn, "geometry"].centroid
    dx = t.x - o.x
    dy = t.y - o.y
    dist  = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(dy, dx))
    og = plots_u.loc[pn, "geometry"]
    tg = truths_u.loc[pn, "geometry"]
    union = og.union(tg).area
    iou   = og.intersection(tg).area / union if union > 0 else 0.0
    area_ratio = og.area / tg.area
    dxs.append(dx); dys.append(dy)
    rows.append(dict(pn=pn, dx=dx, dy=dy, dist=dist, angle=angle, iou=iou, area_ratio=area_ratio))
    print(f"  {pn:<6}  dx={dx:+7.2f}m  dy={dy:+7.2f}m  dist={dist:5.1f}m"
          f"  angle={angle:+6.1f}deg  IoU={iou:.3f}  area_ratio={area_ratio:.3f}")

print()
print("=== Aggregate stats ===")
print(f"  dx   median={statistics.median(dxs):+.2f}  mean={sum(dxs)/len(dxs):+.2f}"
      f"  std={statistics.stdev(dxs):.2f}  range=[{min(dxs):+.1f}, {max(dxs):+.1f}]")
print(f"  dy   median={statistics.median(dys):+.2f}  mean={sum(dys)/len(dys):+.2f}"
      f"  std={statistics.stdev(dys):.2f}  range=[{min(dys):+.1f}, {max(dys):+.1f}]")
dists  = [r["dist"]  for r in rows]
angles = [r["angle"] for r in rows]
print(f"  dist   median={statistics.median(dists):.1f}  max={max(dists):.1f}  min={min(dists):.1f}")
print(f"  angle  median={statistics.median(angles):+.1f}deg  std={statistics.stdev(angles):.1f}deg")

# ── Residuals after global median shift ──────────────────────────────────────
print()
mdx, mdy = statistics.median(dxs), statistics.median(dys)
print(f"=== Residuals after global median shift  dx={mdx:+.2f}  dy={mdy:+.2f} ===")
residuals = []
for r in rows:
    rx = r["dx"] - mdx
    ry = r["dy"] - mdy
    res_dist = math.hypot(rx, ry)
    residuals.append(res_dist)
    og = plots_u.loc[r["pn"], "geometry"]
    tg = truths_u.loc[r["pn"], "geometry"]
    shifted = translate(og, mdx, mdy)
    u2 = shifted.union(tg).area
    iou_after = shifted.intersection(tg).area / u2 if u2 > 0 else 0.0
    print(f"  {r['pn']:<6}  residual={res_dist:5.2f}m  IoU_after={iou_after:.3f}  (was {r['iou']:.3f})")
print(f"  median residual after shift : {statistics.median(residuals):.2f}m")
print(f"  max    residual after shift : {max(residuals):.2f}m")

# ── Shape preservation check ─────────────────────────────────────────────────
print()
print("=== Shape preservation (area ratio official/truth) ===")
ratios = [r["area_ratio"] for r in rows]
print(f"  median={statistics.median(ratios):.3f}  std={statistics.stdev(ratios):.3f}"
      f"  range=[{min(ratios):.3f}, {max(ratios):.3f}]")
print("  (1.0 = same area = pure translation; deviation = scale/shape change)")

# ── Spatial spread of truth centroids ────────────────────────────────────────
print()
print("=== Spatial spread of truth plot centroids ===")
xs = [truths_u.loc[pn, "geometry"].centroid.x for pn in truths.index]
ys = [truths_u.loc[pn, "geometry"].centroid.y for pn in truths.index]
print(f"  x range: {max(xs)-min(xs):.0f}m  y range: {max(ys)-min(ys):.0f}m")
print("  (wider spread = truths sample more of the village = better global estimate)")

# ── Search radius recommendation ─────────────────────────────────────────────
print()
print("=== Search radius recommendation ===")
max_residual = max(residuals)
safety = 1.5
search_r = max_residual * safety
print(f"  max residual after global shift : {max_residual:.1f}m")
print(f"  recommended local search radius : {search_r:.0f}m  (max_residual x {safety})")
