"""
Phase 3 / 8: Evaluation helpers.

Compares Official vs Global vs Global+Local across the example truths.
Also used in Phase 8 for full calibration diagnostics.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.use("Agg")


# ---------------------------------------------------------------------------
# IoU utility
# ---------------------------------------------------------------------------

def iou(a, b) -> float:
    if a is None or b is None or a.is_empty or b.is_empty:
        return 0.0
    u = a.union(b).area
    return float(a.intersection(b).area / u) if u > 0 else 0.0


def centroid_dist_m(a, b, utm_crs: str = "EPSG:32643") -> float:
    import geopandas as gpd
    from shapely.geometry import Point
    pa = gpd.GeoSeries([a], crs="EPSG:4326").to_crs(utm_crs).iloc[0].centroid
    pb = gpd.GeoSeries([b], crs="EPSG:4326").to_crs(utm_crs).iloc[0].centroid
    return float(pa.distance(pb))


# ---------------------------------------------------------------------------
# Per-plot comparison row
# ---------------------------------------------------------------------------

@dataclass
class ComparisonRow:
    plot_number: str
    iou_official: float
    iou_global: float
    iou_local: float
    centroid_err_global_m: float
    centroid_err_local_m: float
    score_gap: float        # boundary perimeter score improvement from local step
    extra_shift_m: float    # magnitude of local refinement


def compare_against_truths(
    village,
    alignment_results,   # list[AlignmentResult]
    global_shift,        # GlobalShift
) -> list[ComparisonRow]:
    """
    For each example truth, compute IoU at three stages:
      official / after global shift / after global+local refinement.
    """
    from src.alignment import UTM_ZONE
    from shapely.affinity import translate
    from shapely.ops import transform as shp_tf
    from pyproj import Transformer

    if village.example_truths is None:
        return []

    plots_u  = village.plots.to_crs(UTM_ZONE)
    truths_u = village.example_truths.to_crs(UTM_ZONE)

    tf_to_utm = Transformer.from_crs("EPSG:4326", UTM_ZONE, always_xy=True)

    # index alignment results by plot_number
    ar_index = {r.plot_number: r for r in alignment_results}

    rows = []
    for pn in village.example_truths.index:
        if pn not in plots_u.index:
            continue

        og_utm = plots_u.loc[pn, "geometry"]
        tg_utm = truths_u.loc[pn, "geometry"]
        sh_utm = translate(og_utm, global_shift.dx_m, global_shift.dy_m)

        iou_off    = iou(og_utm, tg_utm)
        iou_global = iou(sh_utm, tg_utm)

        ar = ar_index.get(pn)
        if ar is not None:
            final_utm = shp_tf(
                lambda xs, ys, z=None: tf_to_utm.transform(xs, ys),
                ar.geometry_corrected,
            )
            iou_loc = iou(final_utm, tg_utm)
            score_gap     = ar.local.score_gap
            extra_shift_m = math.hypot(ar.local.extra_dx_m, ar.local.extra_dy_m)

            # centroid errors in metres
            ce_global = float(sh_utm.centroid.distance(tg_utm.centroid))
            ce_local  = float(final_utm.centroid.distance(tg_utm.centroid))
        else:
            iou_loc = iou_global
            score_gap = extra_shift_m = 0.0
            ce_global = ce_local = float(sh_utm.centroid.distance(tg_utm.centroid))

        rows.append(ComparisonRow(
            plot_number          = pn,
            iou_official         = iou_off,
            iou_global           = iou_global,
            iou_local            = iou_loc,
            centroid_err_global_m= ce_global,
            centroid_err_local_m = ce_local,
            score_gap            = score_gap,
            extra_shift_m        = extra_shift_m,
        ))

    return rows


# ---------------------------------------------------------------------------
# Print comparison table
# ---------------------------------------------------------------------------

def print_comparison(rows: list[ComparisonRow], global_shift) -> None:
    print()
    print("=" * 75)
    print(f"Global shift: dx={global_shift.dx_m:+.2f}m  dy={global_shift.dy_m:+.2f}m  "
          f"spread={global_shift.spread_m:.2f}m  n={global_shift.n_samples}")
    print("=" * 75)
    hdr = f"  {'plot':<7} {'IoU_off':>8} {'IoU_glob':>9} {'IoU_loc':>8} "
    hdr += f"{'delta_g':>8} {'delta_l':>8} {'gap':>7} {'extra_m':>8}"
    print(hdr)
    print("  " + "-" * 73)

    for r in rows:
        dg = r.iou_global - r.iou_official
        dl = r.iou_local  - r.iou_global
        print(f"  {r.plot_number:<7} {r.iou_official:>8.3f} {r.iou_global:>9.3f} "
              f"{r.iou_local:>8.3f} {dg:>+8.3f} {dl:>+8.3f} "
              f"{r.score_gap:>7.4f} {r.extra_shift_m:>8.1f}m")

    print("  " + "-" * 73)
    off   = [r.iou_official for r in rows]
    glob  = [r.iou_global   for r in rows]
    loc   = [r.iou_local    for r in rows]
    print(f"  {'MEDIAN':<7} {statistics.median(off):>8.3f} {statistics.median(glob):>9.3f} "
          f"{statistics.median(loc):>8.3f} "
          f"{statistics.median(glob)-statistics.median(off):>+8.3f} "
          f"{statistics.median(loc)-statistics.median(glob):>+8.3f}")
    print("=" * 75)

    improved_by_local = sum(1 for r in rows if r.iou_local > r.iou_global)
    print(f"\nLocal refinement improved {improved_by_local}/{len(rows)} plots over global shift")
    print(f"Median centroid error: global={statistics.median([r.centroid_err_global_m for r in rows]):.1f}m  "
          f"local={statistics.median([r.centroid_err_local_m for r in rows]):.1f}m")


# ---------------------------------------------------------------------------
# Comparison bar chart
# ---------------------------------------------------------------------------

def plot_comparison(rows: list[ComparisonRow], out_path: Path) -> None:
    pns  = [r.plot_number for r in rows]
    off  = [r.iou_official for r in rows]
    glob = [r.iou_global   for r in rows]
    loc  = [r.iou_local    for r in rows]

    x   = np.arange(len(pns))
    w   = 0.25

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # left: grouped bar per plot
    ax = axes[0]
    ax.bar(x - w, off,  w, label="Official",       color="#e74c3c", alpha=0.85)
    ax.bar(x,     glob, w, label="Global shift",   color="#f39c12", alpha=0.85)
    ax.bar(x + w, loc,  w, label="Global + Local", color="#2ecc71", alpha=0.85)
    ax.axhline(0.5, color="white", lw=0.8, ls="--", alpha=0.5, label="IoU=0.5 threshold")
    ax.set_xticks(x); ax.set_xticklabels(pns, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("IoU"); ax.set_ylim(0, 1.05)
    ax.set_title("IoU per truth plot — three stages")
    ax.legend(fontsize=8); ax.set_facecolor("#1a1a2e"); ax.grid(axis="y", alpha=0.25)

    # right: delta (improvement over official)
    ax2 = axes[1]
    dg = [r.iou_global - r.iou_official for r in rows]
    dl = [r.iou_local  - r.iou_official for r in rows]
    ax2.bar(x - w/2, dg, w, label="Global shift delta",   color="#f39c12", alpha=0.85)
    ax2.bar(x + w/2, dl, w, label="Global+Local delta",   color="#2ecc71", alpha=0.85)
    ax2.axhline(0, color="white", lw=0.8)
    ax2.set_xticks(x); ax2.set_xticklabels(pns, rotation=30, ha="right", fontsize=8)
    ax2.set_ylabel("IoU improvement over official")
    ax2.set_title("IoU improvement over official boundary")
    ax2.legend(fontsize=8); ax2.set_facecolor("#1a1a2e"); ax2.grid(axis="y", alpha=0.25)

    fig.suptitle("Phase 3: Official vs Global vs Global+Local", fontsize=12)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path}")
