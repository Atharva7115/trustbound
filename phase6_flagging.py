#!/usr/bin/env python3
"""
Phase 6: Uncertainty-aware flagging.

Full pipeline with decisions, diagnostics, and visualizations.

Usage:
    uv run phase6_flagging.py [--threshold 0.5]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from bhume import load, write_predictions
from bhume.score import score
from src.alignment import BoundaryRaster, run_alignment
from src.confidence import run_confidence
from src.evaluation import compare_against_truths, print_comparison
from src.flagging import (
    DEFAULT_THRESHOLD, apply_decisions, confidence_distribution_summary,
    decisions_to_geodataframe, threshold_report,
)
from src.image_signals import ImageRaster
from src.visualization import render_plots

matplotlib.use("Agg")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

VILLAGE_DIR = Path("data/34855_vadnerbhairav_chandavad_nashik")


# ---------------------------------------------------------------------------
# Diagnostics plots
# ---------------------------------------------------------------------------

def plot_confidence_histogram(
    conf_results, decisions, threshold: float, out_path: Path
) -> None:
    all_confs = np.array([cb.confidence for cb in conf_results])
    corr_confs = np.array([d.confidence for d in decisions
                           if d.status == "corrected" and d.confidence is not None])
    flag_confs = np.array([cb.confidence for cb in conf_results
                           if cb.confidence < threshold])

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("Phase 6: Confidence distribution and flagging decisions", fontsize=12)

    # left: full histogram with threshold line
    ax = axes[0]
    ax.hist(all_confs, bins=40, color="steelblue", alpha=0.8, edgecolor="none",
            label=f"All plots (n={len(all_confs)})")
    ax.axvline(threshold, color="red", lw=2, ls="--",
               label=f"Threshold = {threshold:.2f}")
    ax.fill_betweenx([0, ax.get_ylim()[1] if ax.get_ylim()[1] > 0 else 200],
                     0, threshold, alpha=0.08, color="red", label="Flagged zone")
    ax.set_xlabel("Confidence"); ax.set_ylabel("Count")
    ax.set_title("Confidence distribution — all 2457 plots")
    ax.legend(fontsize=8); ax.set_facecolor("#1a1a2e"); ax.grid(alpha=0.2)

    # right: corrected vs flagged breakdown
    ax2 = axes[1]
    n_corr = len(corr_confs)
    n_flag = len(all_confs) - n_corr
    bars = ax2.bar(["Corrected", "Flagged"], [n_corr, n_flag],
                   color=["#2ecc71", "#e74c3c"], alpha=0.85, width=0.5)
    for bar, val in zip(bars, [n_corr, n_flag]):
        pct = 100 * val / len(all_confs)
        ax2.text(bar.get_x() + bar.get_width() / 2,
                 bar.get_height() + 10,
                 f"{val}\n({pct:.1f}%)", ha="center", va="bottom", fontsize=10)
    ax2.set_title(f"Decisions at threshold={threshold:.2f}")
    ax2.set_ylabel("Number of plots")
    ax2.set_facecolor("#1a1a2e"); ax2.grid(axis="y", alpha=0.2)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path}")


def plot_threshold_sweep(conf_results, truth_rows, out_path: Path) -> None:
    """Coverage vs quality trade-off curve."""
    thresholds = np.arange(0.1, 0.91, 0.02)
    all_confs  = np.array([cb.confidence for cb in conf_results])
    conf_map   = {cb.plot_number: cb.confidence for cb in conf_results}

    coverages, flag_pcts, truth_ious = [], [], []
    for t in thresholds:
        nc = int((all_confs >= t).sum())
        coverages.append(100 * nc / len(all_confs))
        flag_pcts.append(100 * (1 - nc / len(all_confs)))
        corr_truth = [r for r in truth_rows
                      if conf_map.get(r.plot_number, 0) >= t]
        if corr_truth:
            import statistics
            truth_ious.append(statistics.median([r.iou_local for r in corr_truth]))
        else:
            truth_ious.append(float("nan"))

    fig, ax1 = plt.subplots(figsize=(9, 5))
    ax2 = ax1.twinx()

    ax1.plot(thresholds, coverages, color="steelblue", lw=2, label="Coverage %")
    ax1.plot(thresholds, flag_pcts, color="#e74c3c", lw=2, ls="--", label="Flagged %")
    ax2.plot(thresholds, truth_ious, color="#2ecc71", lw=2, marker="o",
             markersize=4, label="Median IoU (truth plots)")

    ax1.axvline(DEFAULT_THRESHOLD, color="orange", lw=1.5, ls=":",
                label=f"Recommended t={DEFAULT_THRESHOLD}")

    ax1.set_xlabel("Confidence threshold")
    ax1.set_ylabel("Plots (%)", color="steelblue")
    ax2.set_ylabel("Median IoU on truth plots", color="#2ecc71")
    ax1.set_title("Coverage vs Quality trade-off across thresholds")
    ax1.set_facecolor("#1a1a2e")
    ax1.grid(alpha=0.2)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="center left")

    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path}")


def plot_signal_breakdown(conf_results, out_path: Path) -> None:
    """Stacked signal contribution across all plots, sorted by confidence."""
    cbs = sorted(conf_results, key=lambda cb: cb.confidence)
    x   = np.arange(len(cbs))

    from src.confidence import (
        W_ALIGNMENT_GAP, W_PEAK_SHARPNESS, W_AREA_CONSISTENCY,
        W_BOUNDARY_VISIBILITY, W_LOCAL_SHIFT_PENALTY, W_GLOBAL_RELIABILITY,
    )
    s1 = np.array([cb.s1_alignment_gap      * W_ALIGNMENT_GAP       for cb in cbs])
    s2 = np.array([cb.s2_peak_sharpness     * W_PEAK_SHARPNESS      for cb in cbs])
    s3 = np.array([cb.s3_area_consistency   * W_AREA_CONSISTENCY    for cb in cbs])
    s4 = np.array([cb.s4_boundary_visibility* W_BOUNDARY_VISIBILITY for cb in cbs])
    s5 = np.array([cb.s5_local_shift_penalty* W_LOCAL_SHIFT_PENALTY for cb in cbs])
    s6 = np.array([cb.s6_global_reliability * W_GLOBAL_RELIABILITY  for cb in cbs])

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.stackplot(x, s1, s2, s3, s4, s5, s6,
                 labels=["S1 alignment gap (0.25)",
                         "S2 peak sharpness (0.10)",
                         "S3 area consistency (0.35)",
                         "S4 boundary visibility (0.10)",
                         "S5 shift penalty (0.10)",
                         "S6 global reliability (0.10)"],
                 colors=["#e74c3c","#f39c12","#2ecc71","#3498db","#9b59b6","#1abc9c"],
                 alpha=0.85)
    ax.axhline(DEFAULT_THRESHOLD, color="white", lw=1.5, ls="--",
               label=f"Threshold={DEFAULT_THRESHOLD}")
    ax.set_xlabel("Plots (sorted by confidence, low→high)")
    ax.set_ylabel("Weighted signal contribution")
    ax.set_title("Confidence signal breakdown — all plots sorted by confidence")
    ax.legend(loc="upper left", fontsize=7, framealpha=0.7)
    ax.set_facecolor("#1a1a2e"); ax.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()
    t    = args.threshold

    print(f"\nPhase 6: Uncertainty-aware flagging  (threshold={t})")
    print("=" * 55)

    village = load(str(VILLAGE_DIR))
    braster = BoundaryRaster.load(village.boundaries_path)
    iraster = ImageRaster.load(village.imagery_path)

    # alignment
    global_shift, results = run_alignment(
        village, search_radius_m=16.0, step_m=2.0, band_m=3.0,
        use_image=True, w_boundary=0.6, w_image=0.4,
    )

    # confidence
    conf_results = run_confidence(results, global_shift, village, braster, iraster)

    # decisions
    decisions = apply_decisions(results, conf_results, threshold=t)
    preds     = decisions_to_geodataframe(decisions)

    # ── Distribution summary ─────────────────────────────────────────────────
    print(confidence_distribution_summary(conf_results))

    # ── Threshold report ─────────────────────────────────────────────────────
    truth_rows = compare_against_truths(village, results, global_shift)
    print(threshold_report(conf_results, truth_rows))

    # ── Truth plot decisions ─────────────────────────────────────────────────
    conf_map = {cb.plot_number: cb for cb in conf_results}
    print(f"\n=== Truth plot decisions at t={t} ===")
    print(f"  {'plot':<7} {'conf':>6} {'status':>10} {'IoU':>7} {'Δ IoU':>8}")
    for r in sorted(truth_rows, key=lambda x: -(conf_map[x.plot_number].confidence
                                                if x.plot_number in conf_map else 0)):
        cb     = conf_map.get(r.plot_number)
        conf   = cb.confidence if cb else 0.0
        status = "CORRECTED" if conf >= t else "FLAGGED"
        print(f"  {r.plot_number:<7} {conf:>6.3f} {status:>10} "
              f"{r.iou_local:>7.3f} {r.iou_local-r.iou_official:>+8.3f}")

    # ── bhume scorer ─────────────────────────────────────────────────────────
    print("\n--- bhume scorer ---")
    print(score(preds, village))

    # ── Diagnostics plots ────────────────────────────────────────────────────
    print("\nGenerating diagnostics...")
    plot_confidence_histogram(conf_results, decisions, t,
                              VILLAGE_DIR / "phase6_confidence_histogram.png")
    plot_threshold_sweep(conf_results, truth_rows,
                         VILLAGE_DIR / "phase6_threshold_sweep.png")
    plot_signal_breakdown(conf_results,
                          VILLAGE_DIR / "phase6_signal_breakdown.png")

    # ── Example corrected plots (top 3 confidence) ───────────────────────────
    corrected = sorted([d for d in decisions if d.status == "corrected"],
                       key=lambda d: -(d.confidence or 0))
    flagged   = sorted([d for d in decisions if d.status == "flagged"],
                       key=lambda d: conf_map.get(d.plot_number,
                           type("x", (), {"confidence": 0})()).confidence)

    print("\nRendering example corrected plots (top 3 confidence)...")
    render_plots(
        [d.plot_number for d in corrected[:3]],
        village.plots, village.imagery_path, village.boundaries_path,
        out_dir=VILLAGE_DIR / "viz_phase6" / "corrected",
        truths=village.example_truths, predictions=preds,
    )

    print("Rendering example flagged plots (bottom 3 confidence)...")
    render_plots(
        [d.plot_number for d in flagged[:3]],
        village.plots, village.imagery_path, village.boundaries_path,
        out_dir=VILLAGE_DIR / "viz_phase6" / "flagged",
        truths=village.example_truths, predictions=preds,
    )

    # ── Write predictions ────────────────────────────────────────────────────
    out = write_predictions(VILLAGE_DIR / "predictions.geojson", preds)
    print(f"\nWrote {len(preds)} predictions → {out}")

    # ── Final summary ────────────────────────────────────────────────────────
    n_corr = sum(1 for d in decisions if d.status == "corrected")
    n_flag = sum(1 for d in decisions if d.status == "flagged")
    print(f"""
=== Phase 6 Summary ===
  Recommended threshold : {t}
  Corrected             : {n_corr} ({100*n_corr/len(decisions):.1f}%)
  Flagged               : {n_flag} ({100*n_flag/len(decisions):.1f}%)
  Truth plots corrected : {sum(1 for r in truth_rows
                               if conf_map.get(r.plot_number,
                                   type('x',(),{'confidence':0})()).confidence >= t)}/6
  Median IoU (truth)    : {__import__('statistics').median([r.iou_local for r in truth_rows]):.3f}
  Low-conf tail (<0.35) : {sum(1 for cb in conf_results if cb.confidence < 0.35)} plots flagged
  High-conf head (>0.75): {sum(1 for cb in conf_results if cb.confidence > 0.75)} plots
  Diagnostics saved to  : {VILLAGE_DIR}/phase6_*.png
""")


if __name__ == "__main__":
    main()
