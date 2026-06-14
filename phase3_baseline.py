#!/usr/bin/env python3
"""
Phase 3 runner: Global shift + Local boundary refinement.

Runs alignment on all 2457 plots, scores against the 6 example truths,
prints a comparison table (Official / Global / Global+Local), and saves
comparison PNGs.

Usage:
    uv run phase3_baseline.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import geopandas as gpd

from bhume import load, write_predictions
from bhume.score import score
from src.alignment import estimate_global_shift, run_alignment
from src.evaluation import compare_against_truths, plot_comparison, print_comparison
from src.visualization import render_truth_plots

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

VILLAGE_DIR = Path("data/34855_vadnerbhairav_chandavad_nashik")


def build_predictions(alignment_results, plots) -> gpd.GeoDataFrame:
    """Convert alignment results into a contract-valid predictions GeoDataFrame."""
    records = []
    for ar in alignment_results:
        pn = ar.plot_number
        records.append({
            "plot_number" : pn,
            "status"      : "corrected",
            # placeholder confidence — Phase 5 will replace this
            "confidence"  : 0.5,
            "method_note" : (
                f"global dx={ar.global_shift.dx_m:+.1f}m dy={ar.global_shift.dy_m:+.1f}m"
                f" + local ({ar.local.extra_dx_m:+.0f},{ar.local.extra_dy_m:+.0f})m"
                f" boundary_gap={ar.local.score_gap:.3f}"
            ),
            "geometry"    : ar.geometry_corrected,
        })

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    gdf = gdf.set_index("plot_number", drop=False)
    return gdf


def main():
    print("\nPhase 3: Global shift + Local boundary refinement")
    print("=" * 55)

    village = load(str(VILLAGE_DIR))
    print(f"Loaded {village.slug}: {len(village.plots)} plots, "
          f"{len(village.example_truths)} truths")

    # ── Stage 1: estimate global shift from example truths ──────────────────
    global_shift = estimate_global_shift(village)
    print(f"\nGlobal shift: dx={global_shift.dx_m:+.2f}m  "
          f"dy={global_shift.dy_m:+.2f}m  "
          f"spread={global_shift.spread_m:.2f}m  "
          f"n={global_shift.n_samples}")

    # ── Stage 2: run alignment on all plots ─────────────────────────────────
    print(f"\nRunning local refinement on {len(village.plots)} plots "
          f"(search_radius=16m step=2m)...")
    global_shift_out, results = run_alignment(
        village,
        search_radius_m=16.0,
        step_m=2.0,
        band_m=3.0,
    )

    # ── Build predictions GDF ───────────────────────────────────────────────
    preds = build_predictions(results, village.plots)

    # ── Score with bhume scorer ─────────────────────────────────────────────
    print("\n--- bhume scorer output ---")
    print(score(preds, village))

    # ── Detailed comparison table ───────────────────────────────────────────
    rows = compare_against_truths(village, results, global_shift_out)
    print_comparison(rows, global_shift_out)

    # ── Save comparison chart ───────────────────────────────────────────────
    plot_comparison(rows, VILLAGE_DIR / "phase3_comparison.png")

    # ── Visualize corrected boundaries on truth plots ───────────────────────
    render_truth_plots(
        village.plots,
        village.example_truths,
        village.imagery_path,
        village.boundaries_path,
        out_dir=VILLAGE_DIR / "viz_phase3",
        predictions=preds,
    )

    # ── Write predictions.geojson ────────────────────────────────────────────
    out = write_predictions(VILLAGE_DIR / "predictions.geojson", preds)
    print(f"\nWrote {len(preds)} predictions → {out}")
    print("\nPhase 3 complete.")


if __name__ == "__main__":
    main()
