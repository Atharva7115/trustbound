#!/usr/bin/env python3
"""
Phase 2: Visualization runner.

Renders composite PNGs for example truth plots (or any plot numbers you specify).

Usage:
    uv run visualization.py                            # all 6 truth plots
    uv run visualization.py data/<village> 622 1145    # specific plots
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd

from src.visualization import render_truth_plots, render_plots

VILLAGE_DIR = Path("data/34855_vadnerbhairav_chandavad_nashik")


def _load(village_dir: Path):
    plots = gpd.read_file(str(village_dir / "input.geojson"))
    plots["plot_number"] = plots["plot_number"].astype(str)
    plots = plots.set_index("plot_number", drop=False)

    truths = None
    for name in ("example_truths.geojson", "example_truths (2).geojson"):
        tp = village_dir / name
        if tp.exists():
            truths = gpd.read_file(str(tp))
            truths["plot_number"] = truths["plot_number"].astype(str)
            truths = truths.set_index("plot_number", drop=False)
            break

    imagery_path    = village_dir / "imagery.tif"
    boundaries_path = village_dir / "boundaries.tif"
    return plots, truths, imagery_path, boundaries_path if boundaries_path.exists() else None


def main():
    args = sys.argv[1:]

    # first arg can be a village dir
    if args and Path(args[0]).is_dir():
        village_dir = Path(args.pop(0))
    else:
        village_dir = VILLAGE_DIR

    plots, truths, imagery_path, boundaries_path = _load(village_dir)
    out_dir = village_dir / "viz"
    print(f"Saving PNGs to: {out_dir}")

    if args:
        # specific plot numbers given on CLI
        render_plots(
            args, plots, imagery_path, boundaries_path,
            out_dir=out_dir, truths=truths,
        )
    else:
        # default: all example truth plots
        render_truth_plots(
            plots, truths, imagery_path, boundaries_path, out_dir=out_dir,
        )

    print("\nPhase 2 complete.")


if __name__ == "__main__":
    main()
