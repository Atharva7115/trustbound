#!/usr/bin/env python3
"""
Phase 3-5 runner: Global shift + Local alignment + Confidence engine.

Usage:
    uv run phase3_baseline.py
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np

from bhume import load, write_predictions
from bhume.score import score
from src.alignment import BoundaryRaster, estimate_global_shift, run_alignment
from src.confidence import run_confidence
from src.evaluation import compare_against_truths, plot_comparison, print_comparison
from src.flagging import DEFAULT_THRESHOLD, apply_decisions, decisions_to_geodataframe
from src.image_signals import ImageRaster
from src.neighborhood import apply_neighbourhood_to_confidence, build_neighborhood_context
from src.visualization import render_truth_plots

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

VILLAGE_DIR = Path("data/34855_vadnerbhairav_chandavad_nashik")


def build_predictions(alignment_results, confidence_results) -> gpd.GeoDataFrame:
    conf_index = {cb.plot_number: cb for cb in confidence_results}
    records    = []
    for ar in alignment_results:
        cb = conf_index.get(ar.plot_number)
        conf = cb.confidence if cb else 0.5
        note = (
            f"global({ar.global_shift.dx_m:+.1f},{ar.global_shift.dy_m:+.1f})m "
            f"local({ar.local.extra_dx_m:+.0f},{ar.local.extra_dy_m:+.0f})m "
            f"gap={ar.local.score_gap:.3f}"
        )
        if cb:
            note += (f" s1={cb.s1_alignment_gap:.2f} s2={cb.s2_peak_sharpness:.2f}"
                     f" s3={cb.s3_area_consistency:.2f} s4={cb.s4_boundary_visibility:.2f}"
                     f" s5={cb.s5_local_shift_penalty:.2f} s6={cb.s6_global_reliability:.2f}")
        records.append({
            "plot_number": ar.plot_number,
            "status":      "corrected",
            "confidence":  conf,
            "method_note": note,
            "geometry":    ar.geometry_corrected,
        })
    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    gdf = gdf.set_index("plot_number", drop=False)
    return gdf


def main():
    print("\nPhase 3-5: Alignment + Confidence")
    print("=" * 50)

    village = load(str(VILLAGE_DIR))
    print(f"Loaded {village.slug}: {len(village.plots)} plots")

    # load rasters once
    braster = BoundaryRaster.load(village.boundaries_path)
    iraster = ImageRaster.load(village.imagery_path)

    # alignment
    global_shift, results = run_alignment(
        village,
        search_radius_m=16.0, step_m=2.0, band_m=3.0,
        use_image=True, w_boundary=0.6, w_image=0.4,
    )

    # confidence + neighbourhood
    print("\nComputing confidence scores...")
    conf_base    = run_confidence(results, global_shift, village, braster, iraster)
    ctx          = build_neighborhood_context(results, conf_base, global_shift, village)
    conf_results = apply_neighbourhood_to_confidence(results, conf_base, ctx, village)

    # decisions
    decisions    = apply_decisions(results, conf_results, threshold=DEFAULT_THRESHOLD)
    preds        = decisions_to_geodataframe(decisions)

    # score
    print("\n--- bhume scorer ---")
    print(score(preds, village))

    # detailed comparison
    rows = compare_against_truths(village, results, global_shift)
    print_comparison(rows, global_shift)

    # confidence on truth plots
    conf_index = {cb.plot_number: cb for cb in conf_results}
    print("\nConfidence breakdown on truth plots:")
    print(f"  {'plot':<7} {'conf':>6} {'s1':>6} {'s2':>6} {'s3':>6} "
          f"{'s4':>6} {'s5':>6} {'s6':>6}  IoU_final")
    for r in rows:
        cb = conf_index.get(r.plot_number)
        if cb:
            print(f"  {r.plot_number:<7} {cb.confidence:>6.3f} "
                  f"{cb.s1_alignment_gap:>6.3f} {cb.s2_peak_sharpness:>6.3f} "
                  f"{cb.s3_area_consistency:>6.3f} {cb.s4_boundary_visibility:>6.3f} "
                  f"{cb.s5_local_shift_penalty:>6.3f} {cb.s6_global_reliability:>6.3f}"
                  f"  {r.iou_local:.3f}")

    # confidence distribution across all plots
    all_confs = [cb.confidence for cb in conf_results]
    print(f"\nAll-plot confidence:  min={min(all_confs):.3f}  "
          f"median={float(np.median(all_confs)):.3f}  "
          f"max={max(all_confs):.3f}")

    # charts
    plot_comparison(rows, VILLAGE_DIR / "phase3_comparison.png")
    render_truth_plots(
        village.plots, village.example_truths,
        village.imagery_path, village.boundaries_path,
        out_dir=VILLAGE_DIR / "viz_phase3",
        predictions=preds,
    )

    out = write_predictions(VILLAGE_DIR / "predictions.geojson", preds)
    print(f"\nWrote {len(preds)} predictions → {out}")
    print("Done.")


if __name__ == "__main__":
    main()
