"""
Phase 2: Visualization and debugging toolkit.

For any plot, renders a composite PNG showing:
  - satellite imagery background
  - official boundary (red)
  - example truth boundary if available (lime dashed)
  - boundary hints overlay (cyan, semi-transparent)
  - predicted boundary if provided (yellow)

Usage:
    uv run visualization.py [village_dir] [plot_number]
    uv run visualization.py                          # shows all 6 truth plots
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import matplotlib
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import from_bounds as raster_window
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

matplotlib.use("Agg")

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level raster helpers
# ---------------------------------------------------------------------------

def _reproject_bounds_to_crs(bounds_4326: tuple, dst_crs: str) -> tuple:
    """Convert (minx, miny, maxx, maxy) from EPSG:4326 to dst_crs."""
    tf = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    left, bottom = tf.transform(bounds_4326[0], bounds_4326[1])
    right, top   = tf.transform(bounds_4326[2], bounds_4326[3])
    return left, bottom, right, top


def _read_raster_crop(
    path: Path,
    bounds_4326: tuple,
    pad_m: float = 0.0,
) -> tuple[np.ndarray, tuple, str] | None:
    """
    Read a raster crop defined by lon/lat bounds (+ optional metre padding).
    Returns (array [bands,H,W], bounds_in_crs, crs_string) or None on failure.
    """
    try:
        with rasterio.open(str(path)) as src:
            l, b, r, t = _reproject_bounds_to_crs(bounds_4326, str(src.crs))
            l -= pad_m; b -= pad_m; r += pad_m; t += pad_m
            # clip to dataset extent
            dl, db, dr, dt = src.bounds
            l, b, r, t = max(l, dl), max(b, db), min(r, dr), min(t, dt)
            if r <= l or t <= b:
                return None
            win = raster_window(l, b, r, t, transform=src.transform)
            bands = list(range(1, src.count + 1))
            arr = src.read(bands, window=win)
            return arr, (l, b, r, t), str(src.crs)
    except Exception as e:
        log.warning("raster read failed (%s): %s", path, e)
        return None


def _geom_to_display_coords(
    geom: BaseGeometry,
    src_crs: str = "EPSG:4326",
) -> list[np.ndarray]:
    """
    Return list of (N,2) arrays of lon/lat coords for each ring in geom,
    reprojecting from src_crs if needed.
    """
    if src_crs != "EPSG:4326":
        from shapely.ops import transform as shp_tf
        tf = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        geom = shp_tf(lambda xs, ys, z=None: tf.transform(xs, ys), geom)

    rings = []
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    else:
        return rings

    for poly in polys:
        rings.append(np.array(poly.exterior.coords))
        for interior in poly.interiors:
            rings.append(np.array(interior.coords))
    return rings


# ---------------------------------------------------------------------------
# Core plot function
# ---------------------------------------------------------------------------

def plot_plot(
    plot_number: str,
    official_geom: BaseGeometry,
    imagery_path: Path,
    boundaries_path: Path | None = None,
    truth_geom: BaseGeometry | None = None,
    predicted_geom: BaseGeometry | None = None,
    predicted_confidence: float | None = None,
    pad_m: float = 40.0,
    title_extra: str = "",
) -> plt.Figure:
    """
    Render a single plot as a matplotlib Figure.

    Layers (bottom to top):
      0. Satellite imagery
      1. Boundary hints (cyan, alpha=0.35)
      2. Official boundary (red, solid)
      3. Example truth (lime, dashed)  — if provided
      4. Predicted boundary (yellow)   — if provided
    """
    bounds = official_geom.bounds  # (minx, miny, maxx, maxy) in lon/lat

    fig, ax = plt.subplots(figsize=(7, 7))

    # --- layer 0: imagery ---
    img_result = _read_raster_crop(imagery_path, bounds, pad_m=pad_m)
    if img_result is not None:
        arr, img_bounds, img_crs = img_result
        # arr is (3, H, W) uint8
        rgb = np.transpose(arr, (1, 2, 0))
        # convert img_bounds (in EPSG:3857) back to lon/lat for imshow extent
        tf_back = Transformer.from_crs(img_crs, "EPSG:4326", always_xy=True)
        img_left_ll, img_bottom_ll = tf_back.transform(img_bounds[0], img_bounds[1])
        img_right_ll, img_top_ll   = tf_back.transform(img_bounds[2], img_bounds[3])
        ax.imshow(
            rgb,
            extent=[img_left_ll, img_right_ll, img_bottom_ll, img_top_ll],
            origin="upper",
            aspect="auto",
            zorder=0,
        )
    else:
        ax.set_facecolor("#1a1a2e")

    # --- layer 1: boundary hints ---
    if boundaries_path is not None and boundaries_path.exists():
        bnd_result = _read_raster_crop(boundaries_path, bounds, pad_m=pad_m)
        if bnd_result is not None:
            bnd_arr, bnd_bounds, bnd_crs = bnd_result
            bnd_mask = (bnd_arr[0] == 255).astype(np.float32)
            # build RGBA: cyan where edge, transparent elsewhere
            rgba = np.zeros((*bnd_mask.shape, 4), dtype=np.float32)
            rgba[..., 0] = 0.0   # R
            rgba[..., 1] = 1.0   # G
            rgba[..., 2] = 1.0   # B
            rgba[..., 3] = bnd_mask * 0.45
            tf_back = Transformer.from_crs(bnd_crs, "EPSG:4326", always_xy=True)
            bl, bb = tf_back.transform(bnd_bounds[0], bnd_bounds[1])
            br, bt = tf_back.transform(bnd_bounds[2], bnd_bounds[3])
            ax.imshow(
                rgba,
                extent=[bl, br, bb, bt],
                origin="upper",
                aspect="auto",
                zorder=1,
            )

    # --- helper to draw a geometry ---
    def draw_geom(geom, color, lw, ls, zorder, label=None):
        rings = _geom_to_display_coords(geom)
        for i, ring in enumerate(rings):
            ax.plot(
                ring[:, 0], ring[:, 1],
                color=color, linewidth=lw, linestyle=ls,
                zorder=zorder,
                label=label if i == 0 else None,
            )

    # --- layer 2: official ---
    draw_geom(official_geom, color="red", lw=1.8, ls="-", zorder=2, label="Official")

    # --- layer 3: truth ---
    if truth_geom is not None:
        draw_geom(truth_geom, color="lime", lw=1.8, ls="--", zorder=3, label="Truth")

    # --- layer 4: prediction ---
    if predicted_geom is not None:
        conf_str = f" (conf={predicted_confidence:.2f})" if predicted_confidence is not None else ""
        draw_geom(predicted_geom, color="yellow", lw=2.0, ls="-", zorder=4,
                  label=f"Predicted{conf_str}")

    # --- axes / legend ---
    pad_deg = pad_m / 111_320
    ax.set_xlim(bounds[0] - pad_deg, bounds[2] + pad_deg)
    ax.set_ylim(bounds[1] - pad_deg, bounds[3] + pad_deg)
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")

    conf_title = (f"  conf={predicted_confidence:.2f}" if predicted_confidence is not None else "")
    ax.set_title(f"Plot {plot_number}{conf_title}  {title_extra}", fontsize=10)

    legend_handles = [
        mpatches.Patch(edgecolor="red",    facecolor="none", label="Official"),
        mpatches.Patch(edgecolor="cyan",   facecolor="cyan", alpha=0.4, label="Boundary hints"),
    ]
    if truth_geom is not None:
        legend_handles.append(
            mpatches.Patch(edgecolor="lime", facecolor="none",
                           linestyle="--", label="Truth")
        )
    if predicted_geom is not None:
        lbl = f"Predicted" + (f" ({predicted_confidence:.2f})" if predicted_confidence is not None else "")
        legend_handles.append(
            mpatches.Patch(edgecolor="yellow", facecolor="none", label=lbl)
        )
    ax.legend(handles=legend_handles, loc="upper right", fontsize=7,
              framealpha=0.7, facecolor="#111")

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def save_plot(fig: plt.Figure, out_path: Path) -> Path:
    """Save figure and close it. Returns the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_truth_plots(
    plots,           # GeoDataFrame indexed by plot_number
    truths,          # GeoDataFrame or None
    imagery_path: Path,
    boundaries_path: Path | None,
    out_dir: Path,
    predictions=None,  # GeoDataFrame or None
) -> list[Path]:
    """Render one PNG per example truth plot. Returns list of saved paths."""
    if truths is None:
        log.warning("No example truths — nothing to render.")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    for pn in truths.index:
        if pn not in plots.index:
            log.warning("Truth plot %s not in official plots — skipping.", pn)
            continue

        official_geom = plots.loc[pn, "geometry"]
        truth_geom    = truths.loc[pn, "geometry"]

        pred_geom = None
        pred_conf = None
        if predictions is not None and pn in predictions.index:
            pred_row = predictions.loc[pn]
            if str(pred_row.get("status")) == "corrected":
                pred_geom = pred_row["geometry"]
                pred_conf = pred_row.get("confidence")

        fig = plot_plot(
            plot_number=pn,
            official_geom=official_geom,
            imagery_path=imagery_path,
            boundaries_path=boundaries_path,
            truth_geom=truth_geom,
            predicted_geom=pred_geom,
            predicted_confidence=float(pred_conf) if pred_conf is not None else None,
            title_extra="[truth available]",
        )
        out = save_plot(fig, out_dir / f"plot_{pn}.png")
        saved.append(out)
        print(f"  Saved: {out}")

    return saved


def render_plots(
    plot_numbers: Sequence[str],
    plots,
    imagery_path: Path,
    boundaries_path: Path | None,
    out_dir: Path,
    truths=None,
    predictions=None,
) -> list[Path]:
    """Render arbitrary plot numbers."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for pn in plot_numbers:
        if pn not in plots.index:
            log.warning("Plot %s not found.", pn)
            continue
        official_geom = plots.loc[pn, "geometry"]
        truth_geom    = truths.loc[pn, "geometry"] if (truths is not None and pn in truths.index) else None
        pred_geom     = None
        pred_conf     = None
        if predictions is not None and pn in predictions.index:
            r = predictions.loc[pn]
            if str(r.get("status")) == "corrected":
                pred_geom = r["geometry"]
                pred_conf = r.get("confidence")
        fig = plot_plot(
            plot_number=pn,
            official_geom=official_geom,
            imagery_path=imagery_path,
            boundaries_path=boundaries_path,
            truth_geom=truth_geom,
            predicted_geom=pred_geom,
            predicted_confidence=float(pred_conf) if pred_conf is not None else None,
        )
        out = save_plot(fig, out_dir / f"plot_{pn}.png")
        saved.append(out)
        print(f"  Saved: {out}")
    return saved
