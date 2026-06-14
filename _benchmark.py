"""Benchmark single-plot alignment to estimate full village runtime."""
import time
import geopandas as gpd
import rasterio
from src.alignment import estimate_global_shift, align_plot, BoundaryRaster, UTM_ZONE
from bhume import load

VILLAGE_DIR = "data/34855_vadnerbhairav_chandavad_nashik"
village = load(VILLAGE_DIR)

global_shift = estimate_global_shift(village)
braster = BoundaryRaster.load(village.boundaries_path)
print(f"Boundary raster loaded: {braster.data.shape}  res={braster.res_m:.2f}m")

# time 20 plots
sample_pns = list(village.plots.index[:20])

t0 = time.time()
for pn in sample_pns:
    geom = village.plots.loc[pn, "geometry"]
    align_plot(pn, geom, global_shift, braster,
               search_radius_m=16.0, step_m=2.0, band_m=3.0)
elapsed = time.time() - t0

per_plot = elapsed / len(sample_pns)
total_est = per_plot * len(village.plots)
print(f"20 plots in {elapsed:.2f}s → {per_plot:.3f}s/plot")
print(f"Estimated full village ({len(village.plots)} plots): {total_est:.0f}s ({total_est/60:.1f} min)")
