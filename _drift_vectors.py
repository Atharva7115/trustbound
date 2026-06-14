"""Visualize drift vectors spatially across the village."""
import math
import statistics

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from shapely.affinity import translate

matplotlib.use("Agg")

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

rows = []
for pn in truths.index:
    o  = plots_u.loc[pn, "geometry"].centroid
    t  = truths_u.loc[pn, "geometry"].centroid
    rows.append(dict(pn=pn, ox=o.x, oy=o.y, dx=t.x-o.x, dy=t.y-o.y,
                     dist=math.hypot(t.x-o.x, t.y-o.y)))

dxs = [r["dx"] for r in rows]
dys = [r["dy"] for r in rows]
mdx, mdy = statistics.median(dxs), statistics.median(dys)

# ── Figure 1: spatial drift vectors ─────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(14, 6))

# all official plots as background
for ax in axes:
    for geom in plots_u.geometry:
        if geom.geom_type == "Polygon":
            polys = [geom]
        else:
            polys = list(geom.geoms)
        for p in polys:
            xs, ys = p.exterior.xy
            ax.plot(xs, ys, color="gray", linewidth=0.3, alpha=0.3)

# left: raw drift vectors
ax = axes[0]
for r in rows:
    ax.annotate("", xy=(r["ox"]+r["dx"], r["oy"]+r["dy"]), xytext=(r["ox"], r["oy"]),
                arrowprops=dict(arrowstyle="->", color="red", lw=2))
    ax.scatter(r["ox"], r["oy"], c="red", s=40, zorder=5)
    ax.text(r["ox"], r["oy"]+15, r["pn"], fontsize=7, ha="center", color="white",
            bbox=dict(boxstyle="round,pad=0.1", fc="black", alpha=0.6))
ax.set_title(f"Raw drift vectors\n(median dx={mdx:+.1f}m, dy={mdy:+.1f}m)", fontsize=10)
ax.set_aspect("equal"); ax.set_facecolor("#1a1a2e")
ax.tick_params(labelsize=7)

# right: residual vectors after global shift
ax = axes[1]
for r in rows:
    rx = r["dx"] - mdx
    ry = r["dy"] - mdy
    res = math.hypot(rx, ry)
    ax.annotate("", xy=(r["ox"]+rx*5, r["oy"]+ry*5), xytext=(r["ox"], r["oy"]),
                arrowprops=dict(arrowstyle="->", color="orange", lw=2))
    ax.scatter(r["ox"], r["oy"], c="orange", s=40, zorder=5)
    ax.text(r["ox"], r["oy"]+15, f"{r['pn']}\n{res:.1f}m", fontsize=6.5,
            ha="center", color="white",
            bbox=dict(boxstyle="round,pad=0.1", fc="black", alpha=0.6))
ax.set_title("Residual vectors after global shift\n(5× magnified — local variation)", fontsize=10)
ax.set_aspect("equal"); ax.set_facecolor("#1a1a2e")
ax.tick_params(labelsize=7)

fig.suptitle("Vadnerbhairav — drift analysis", fontsize=12, color="black")
fig.tight_layout()
out = f"{VILLAGE}/drift_vectors.png"
fig.savefig(out, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved: {out}")

# ── Figure 2: dx/dy scatter coloured by plot location ───────────────────────
fig2, ax2 = plt.subplots(figsize=(6, 6))
colors = plt.cm.plasma(np.linspace(0, 1, len(rows)))
for r, c in zip(rows, colors):
    ax2.scatter(r["dx"], r["dy"], color=c, s=100, zorder=3)
    ax2.annotate(r["pn"], (r["dx"], r["dy"]),
                 textcoords="offset points", xytext=(5, 5), fontsize=8)
ax2.axhline(mdy, color="red", ls="--", lw=1, label=f"median dy={mdy:+.1f}m")
ax2.axvline(mdx, color="blue", ls="--", lw=1, label=f"median dx={mdx:+.1f}m")
# draw circle showing spread
circle = plt.Circle((mdx, mdy), radius=max([math.hypot(r["dx"]-mdx, r["dy"]-mdy) for r in rows]),
                     fill=False, color="gray", ls=":", label="max residual radius")
ax2.add_patch(circle)
ax2.set_xlabel("dx (m, east +)"); ax2.set_ylabel("dy (m, north +)")
ax2.set_title("Drift scatter — each point is one truth plot")
ax2.set_aspect("equal"); ax2.legend(fontsize=8); ax2.grid(alpha=0.3)
fig2.tight_layout()
out2 = f"{VILLAGE}/drift_scatter.png"
fig2.savefig(out2, dpi=150, bbox_inches="tight")
plt.close(fig2)
print(f"Saved: {out2}")
