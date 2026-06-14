#!/usr/bin/env python3
"""
Phase 7: Neighborhood-aware drift estimation.

Adds S7 (neighbourhood consistency) to the confidence model.
Compares results before/after neighbourhood update.

Usage:
    uv run phase7_neighborhood.py
"""
from __future__ import annotations

import logging
import statistics
from pathlib import Path

import geopandas as gpd
import numpy as np

from bhume import load, write_predictions
from bhume.score import score
from src.alignment import BoundaryRaster, UTM_ZONE, _get_tf, run_alignment
from src.confidence import run_confidence
from src.evaluation import compare_against_truths, print_comparison
from src.flagging import (
    DEFAULT_THRESHOLD, apply_decisions, decisions_to_geodataframe,
    threshold_report, confidence_distribution_summary,
)
from src.image_signals import ImageRaster
from src.neighborhood import build_neighborhood_context, apply_neighbourhood_to_confidence
from src.visualization import render_truth_plots

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

VILLAGE_DIR = Path("data/34855_vadnerbhairav_chandavad_nashik")


def _conf_stats(conf_results, label: str):
    confs = [cb.confidence for cb in conf_results]
    print(f"  {label}: min={min(confs):.3f}  "
          f"median={statistics.median(confs):.3f}  max={max(confs):.3f}  "
          f"std={statistics.stdev(confs):.3f}")


def main():
    print("\nPhase 7: Neighbourhood-aware drift estimation")
    print("=" * 55)

    village = load(str(VILLAGE_DIR))
    braster = BoundaryRaster.load(village.boundaries_path)
    iraster = ImageRaster.load(village.imagery_path)

    # ── Stage 1: alignment ───────────────────────────────────────────────────
    global_shift, results = run_alignment(
        village, search_radius_m=16.0, step_m=2.0, band_m=3.0,
        use_image=True, w_boundary=0.6, w_image=0.4,
    )

    # ── Stage 2: base confidence ─────────────────────────────────────────────
    conf_base = run_confidence(results, global_shift, village, braster, iraster)
    print("\nBase confidence (before neighbourhood):")
    _conf_stats(conf_base, "base")

    # ── Stage 3: neighbourhood context ──────────────────────────────────────
    ctx = build_neighborhood_context(
        results, conf_base, global_shift, village, anchor_threshold=0.55,
    )
    print(f"\nNeighbourhood anchors: {ctx.n_anchors} plots")
    print(f"  IDW k={8}  power={2.0}  max_influence={800}m")

    # ── Stage 4: apply neighbourhood to confidence ──────────────────────────
    conf_updated = apply_neighbourhood_to_confidence(
        results, conf_base, ctx, village, w_s7=0.08,
    )
    print("\nUpdated confidence (after neighbourhood):")
    _conf_stats(conf_updated, "updated")

    # ── Compare before/after on truth plots ─────────────────────────────────
    truth_rows  = compare_against_truths(village, results, global_shift)
    conf_base_m = {cb.plot_number: cb for cb in conf_base}
    conf_upd_m  = {cb.plot_number: cb for cb in conf_updated}

    print("\n=== Neighbourhood effect on truth plots ===")
    print(f"  {'plot':<7} {'conf_before':>12} {'conf_after':>11} "
          f"{'delta':>7} {'IoU':>7}")
    for r in sorted(truth_rows, key=lambda x: -(conf_upd_m.get(
            x.plot_number, type('x',(),{'confidence':0})()).confidence)):
        cb_b = conf_base_m.get(r.plot_number)
        cb_u = conf_upd_m.get(r.plot_number)
        if cb_b and cb_u:
            d = cb_u.confidence - cb_b.confidence
            print(f"  {r.plot_number:<7} {cb_b.confidence:>12.3f} "
                  f"{cb_u.confidence:>11.3f} {d:>+7.3f} {r.iou_local:>7.3f}")

    # ── Decisions + score ────────────────────────────────────────────────────
    decisions = apply_decisions(results, conf_updated, threshold=DEFAULT_THRESHOLD)
    preds     = decisions_to_geodataframe(decisions)

    print("\n--- bhume scorer ---")
    print(score(preds, village))

    # ── Coverage stats ───────────────────────────────────────────────────────
    n_corr = sum(1 for d in decisions if d.status == "corrected")
    n_flag = sum(1 for d in decisions if d.status == "flagged")
    print(f"\nCoverage at t={DEFAULT_THRESHOLD}:")
    print(f"  Corrected : {n_corr} ({100*n_corr/len(decisions):.1f}%)")
    print(f"  Flagged   : {n_flag} ({100*n_flag/len(decisions):.1f}%)")

    # ── Threshold report ─────────────────────────────────────────────────────
    print(threshold_report(conf_updated, truth_rows))

    # ── Visualize truth plots with neighbourhood-updated predictions ─────────
    render_truth_plots(
        village.plots, village.example_truths,
        village.imagery_path, village.boundaries_path,
        out_dir=VILLAGE_DIR / "viz_phase7",
        predictions=preds,
    )

    # ── Write predictions ────────────────────────────────────────────────────
    out = write_predictions(VILLAGE_DIR / "predictions.geojson", preds)
    print(f"\nWrote {len(preds)} predictions → {out}")
    print("\nPhase 7 complete.")


if __name__ == "__main__":
    main()
