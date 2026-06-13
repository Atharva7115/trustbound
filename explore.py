#!/usr/bin/env python3
"""
Phase 1: Data exploration and drift analysis.

Loads the village bundle, prints summary statistics, and generates plots:
  - centroid_drift_histogram.png  : dx/dy distributions across example truths
  - displacement_distribution.png : total displacement magnitude
  - area_distribution.png         : official map area vs recorded area
  - official_vs_truth.png         : spatial overlay of official and truth polygons

Run:
    uv run explore.py [village_dir]
    uv run explore.py .          # if data is in root
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.plot import show as rshow
from shapely.geometry import shape

matplotlib.use("Agg")  # no display needed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _centroid_lonlat(geom):
    return shape(geom).centroid


def _approx_meters(dlon: float, dlat: float, lat: float) -> tuple[float, float]:
    """Convert lon/lat delta to approximate metres."""
    dx = dlon * 111_320 * math.cos(math.radians(lat))
    dy = dlat * 111_320
    return dx, dy


def _utm_for(lon: float) -> str:
    return f"EPSG:{32600 + int((lon + 180) // 6) + 1}"


# ---------------------------------------------------------------------------
# Loading (handles root-level data layout used by this repo)
# ---------------------------------------------------------------------------

def load_village(village_dir: str | Path):
    """Load raw GeoJSON and raster paths; also try bhume.load if possible."""
    d = Path(village_dir)

    input_path = d / "input.geojson"
    imagery_path = d / "imagery.tif"
    boundaries_path = d / "boundaries.tif"

    if not input_path.exists():
        raise FileNotFoundError(f"input.geojson not found in {d}")

    plots = gpd.read_file(str(input_path))
    plots["plot_number"] = plots["plot_number"].astype(str)
    plots = plots.set_index("plot_number", drop=False)

    # example truths: accept either name variant
    truths = None
    for name in ("example_truths.geojson", "example_truths (2).geojson"):
        tp = d / name
        if tp.exists():
            truths = gpd.read_file(str(tp))
            truths["plot_number"] = truths["plot_number"].astype(str)
            truths = truths.set_index("plot_number", drop=False)
            break

    return plots, truths, imagery_path, boundaries_path if boundaries_path.exists() else None


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def print_summary(plots: gpd.GeoDataFrame, truths, village_dir: Path):
    n = len(plots)
    village_name = plots["village"].iloc[0] if "village" in plots.columns else "unknown"
    n_truths = 0 if truths is None else len(truths)

    map_areas = plots["map_area_sqm"].dropna().tolist()
    rec_areas = plots["recorded_area_sqm"].dropna().tolist()

    print(f"\n{'=' * 60}")
    print(f"Village : {village_name}")
    print(f"Dir     : {village_dir}")
    print(f"Plots   : {n}")
    print(f"Truths  : {n_truths}")
    print()
    print("Map area (sqm):")
    print(f"  min={min(map_areas):.0f}  median={statistics.median(map_areas):.0f}"
          f"  mean={sum(map_areas)/len(map_areas):.0f}  max={max(map_areas):.0f}")
    if rec_areas:
        print("Recorded area (sqm):")
        print(f"  min={min(rec_areas):.0f}  median={statistics.median(rec_areas):.0f}"
              f"  mean={sum(rec_areas)/len(rec_areas):.0f}  max={max(rec_areas):.0f}")

    # pot_kharaba
    pks = plots["pot_kharaba_ha"].dropna().tolist()
    if pks:
        print(f"Pot-kharaba (ha): n={len(pks)}  median={statistics.median(pks):.3f}"
              f"  max={max(pks):.2f}")

    # null recorded areas
    n_null = plots["recorded_area_sqm"].isna().sum()
    print(f"Null recorded_area : {n_null} / {n} ({100*n_null/n:.1f}%)")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Drift analysis
# ---------------------------------------------------------------------------

def compute_drift(plots: gpd.GeoDataFrame, truths: gpd.GeoDataFrame):
    """Return list of drift dicts for each plot with an example truth."""
    drifts = []
    utm = None
    for pn in truths.index:
        if pn not in plots.index:
            continue
        o_geom = plots.loc[pn, "geometry"]
        t_geom = truths.loc[pn, "geometry"]
        oc = o_geom.centroid
        tc = t_geom.centroid

        if utm is None:
            utm = _utm_for(oc.x)

        # lon/lat delta
        dlon = tc.x - oc.x
        dlat = tc.y - oc.y
        dx_m, dy_m = _approx_meters(dlon, dlat, oc.y)
        dist_m = math.hypot(dx_m, dy_m)

        # UTM for precise area
        o_utm = plots.loc[[pn]].to_crs(utm).geometry.iloc[0]
        t_utm = truths.loc[[pn]].to_crs(utm).geometry.iloc[0]
        iou_official = _iou(o_utm, t_utm)

        drifts.append({
            "plot_number": pn,
            "dlon": dlon,
            "dlat": dlat,
            "dx_m": dx_m,
            "dy_m": dy_m,
            "dist_m": dist_m,
            "iou_official": iou_official,
        })

    return drifts


def _iou(a, b) -> float:
    if a is None or b is None or a.is_empty or b.is_empty:
        return 0.0
    union = a.union(b).area
    return float(a.intersection(b).area / union) if union > 0 else 0.0


def print_drift(drifts: list[dict]):
    print("Drift per example truth (approx metres):")
    print(f"  {'plot':<8}  {'dx_m':>8}  {'dy_m':>8}  {'dist_m':>8}  {'IoU_official':>14}")
    for d in drifts:
        print(f"  {d['plot_number']:<8}  {d['dx_m']:>8.1f}  {d['dy_m']:>8.1f}"
              f"  {d['dist_m']:>8.1f}  {d['iou_official']:>14.3f}")
    dxs = [d["dx_m"] for d in drifts]
    dys = [d["dy_m"] for d in drifts]
    dists = [d["dist_m"] for d in drifts]
    print()
    print("Drift summary:")
    print(f"  dx_m: median={statistics.median(dxs):.1f}  std={std(dxs):.1f}"
          f"  range=[{min(dxs):.1f}, {max(dxs):.1f}]")
    print(f"  dy_m: median={statistics.median(dys):.1f}  std={std(dys):.1f}"
          f"  range=[{min(dys):.1f}, {max(dys):.1f}]")
    print(f"  dist: median={statistics.median(dists):.1f}  max={max(dists):.1f}")
    print()

    # Characterise drift type
    dx_spread = max(dxs) - min(dxs)
    dy_spread = max(dys) - min(dys)
    median_dist = statistics.median(dists)
    print("Drift character:")
    if median_dist > 5:
        print(f"  - Significant systematic offset (~{median_dist:.0f}m)")
    if dx_spread > 10 or dy_spread > 10:
        print(f"  - Local variation present (x-spread={dx_spread:.1f}m, y-spread={dy_spread:.1f}m)")
        print("  - Mix of global translation + local distortion — neighbourhood model will help")
    else:
        print("  - Tight spread — pure translation may suffice")


def std(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_drift_histograms(drifts: list[dict], out_dir: Path):
    dxs = [d["dx_m"] for d in drifts]
    dys = [d["dy_m"] for d in drifts]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    fig.suptitle("Centroid drift: official → truth (metres)", fontsize=12)

    axes[0].bar(range(len(dxs)), dxs, color="steelblue")
    axes[0].axhline(statistics.median(dxs), color="red", linestyle="--",
                    label=f"median={statistics.median(dxs):.1f}m")
    axes[0].set_title("East-West drift (dx)")
    axes[0].set_xlabel("Plot index")
    axes[0].set_ylabel("metres (+ = east)")
    axes[0].legend()

    axes[1].bar(range(len(dys)), dys, color="darkorange")
    axes[1].axhline(statistics.median(dys), color="red", linestyle="--",
                    label=f"median={statistics.median(dys):.1f}m")
    axes[1].set_title("North-South drift (dy)")
    axes[1].set_xlabel("Plot index")
    axes[1].set_ylabel("metres (+ = north)")
    axes[1].legend()

    fig.tight_layout()
    out = out_dir / "centroid_drift_histogram.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  Saved: {out}")


def plot_displacement_distribution(drifts: list[dict], out_dir: Path):
    dxs = [d["dx_m"] for d in drifts]
    dys = [d["dy_m"] for d in drifts]

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(dxs, dys, s=80, color="steelblue", zorder=3)
    for d in drifts:
        ax.annotate(d["plot_number"], (d["dx_m"], d["dy_m"]),
                    textcoords="offset points", xytext=(4, 4), fontsize=8)
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.axvline(0, color="gray", linewidth=0.5)
    ax.set_xlabel("dx (m, east +)")
    ax.set_ylabel("dy (m, north +)")
    ax.set_title("Drift scatter: official centroid → truth centroid")
    ax.set_aspect("equal")
    fig.tight_layout()
    out = out_dir / "displacement_distribution.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  Saved: {out}")


def plot_area_distribution(plots: gpd.GeoDataFrame, out_dir: Path):
    map_areas = plots["map_area_sqm"].dropna()
    rec_areas = plots["recorded_area_sqm"].dropna()

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    fig.suptitle("Plot area distributions", fontsize=12)

    axes[0].hist(map_areas / 10_000, bins=60, color="steelblue", edgecolor="none")
    axes[0].set_xlabel("Map area (ha)")
    axes[0].set_ylabel("count")
    axes[0].set_title("Official (map) area")
    axes[0].set_yscale("log")

    axes[1].hist(rec_areas / 10_000, bins=60, color="darkorange", edgecolor="none")
    axes[1].set_xlabel("Recorded area (ha)")
    axes[1].set_title("Recorded (7/12) area")
    axes[1].set_yscale("log")

    fig.tight_layout()
    out = out_dir / "area_distribution.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  Saved: {out}")


def plot_official_vs_truth(plots: gpd.GeoDataFrame, truths: gpd.GeoDataFrame,
                           imagery_path: Path, out_dir: Path):
    """Overlay official and truth polygons for all example truths."""
    # get extent of truth plots with a buffer
    truth_union = truths.geometry.union_all()
    buf = truth_union.buffer(0.002)
    bx = buf.bounds  # (minx, miny, maxx, maxy)

    fig, ax = plt.subplots(figsize=(10, 10))

    # clip and show imagery
    try:
        with rasterio.open(str(imagery_path)) as src:
            from pyproj import Transformer
            from rasterio.windows import from_bounds as fb
            tf = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            left, bottom = tf.transform(bx[0], bx[1])
            right, top = tf.transform(bx[2], bx[3])
            dl, db, dr, dt = src.bounds
            left = max(left, dl); bottom = max(bottom, db)
            right = min(right, dr); top = min(top, dt)
            win = fb(left, bottom, right, top, transform=src.transform)
            rgb = src.read([1, 2, 3], window=win)
            img_arr = np.transpose(rgb, (1, 2, 0))
            extent_4326_for_imshow = [bx[0], bx[2], bx[1], bx[3]]
            ax.imshow(img_arr, extent=extent_4326_for_imshow, origin="upper",
                      aspect="auto", zorder=0)
    except Exception as e:
        print(f"  Warning: could not render imagery background: {e}")

    # official polygons (all in area, light)
    clip_plots = plots.cx[bx[0]:bx[2], bx[1]:bx[3]]
    clip_plots.plot(ax=ax, facecolor="none", edgecolor="yellow",
                    linewidth=0.8, alpha=0.6, zorder=1)

    # highlight official for truth plots
    truth_official = plots.loc[[pn for pn in truths.index if pn in plots.index]]
    truth_official.plot(ax=ax, facecolor="none", edgecolor="red",
                        linewidth=1.5, zorder=2)

    # truth polygons
    truths.plot(ax=ax, facecolor="none", edgecolor="lime",
                linewidth=1.5, linestyle="--", zorder=3)

    # labels
    for pn in truths.index:
        if pn in plots.index:
            c = truths.loc[pn, "geometry"].centroid
            ax.text(c.x, c.y, pn, fontsize=7, color="white",
                    ha="center", va="center", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.1", fc="black", alpha=0.5))

    patches = [
        mpatches.Patch(edgecolor="yellow", facecolor="none", label="Official (all)"),
        mpatches.Patch(edgecolor="red", facecolor="none", label="Official (truth plots)"),
        mpatches.Patch(edgecolor="lime", facecolor="none", linestyle="--", label="Example truth"),
    ]
    ax.legend(handles=patches, loc="upper right", fontsize=8)
    ax.set_title("Official vs Example Truth boundaries")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")

    fig.tight_layout()
    out = out_dir / "official_vs_truth.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"  Saved: {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(village_dir: str = "data/34855_vadnerbhairav_chandavad_nashik"):
    d = Path(village_dir)

    print("Loading village bundle...")
    plots, truths, imagery_path, boundaries_path = load_village(d)
    print_summary(plots, truths, d)

    if truths is None:
        print("No example truths found — skipping drift analysis.")
        return

    drifts = compute_drift(plots, truths)
    print_drift(drifts)

    print("Generating plots...")
    plot_drift_histograms(drifts, d)
    plot_displacement_distribution(drifts, d)
    plot_area_distribution(plots, d)
    if imagery_path.exists():
        plot_official_vs_truth(plots, truths, imagery_path, d)

    print("\nPhase 1 complete.")
    print("Key insight: understand drift character above before proceeding to Phase 2.")


DEFAULT_VILLAGE = "data/34855_vadnerbhairav_chandavad_nashik"

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VILLAGE)
