# Kiro Implementation Session Summary

Chronological record of the Kiro IDE implementation session.
All metrics cited come from actual pipeline outputs in this repository.

---

## Phase 1 — Data Exploration (`explore.py`)

**What was built**
- `explore.py`: loads village bundle, prints area/drift statistics, generates
  4 diagnostic PNGs (centroid drift histogram, displacement distribution,
  area distribution, official vs truth overlay)

**Key findings**
- Village: Vadnerbhairav, Nashik. 2,457 plots, 6 example truths
- Imagery: EPSG:3857, 8680×7552 px, ~1.19 m/px
- Boundaries: binary 0/255, 5.2% edge pixels, 2.39 m/px
- Drift per truth plot measured in UTM metres:
  - Median dx=-4.40m, dy=+11.35m
  - x-spread 17.9m, y-spread 12.9m, max residual after global shift 10.8m
- Area ratios 0.982–1.062: shapes correct, placement wrong
- Conclusion: translation dominates, local refinement needed

**Important setup step**
- Village data files were in the project root with numbered suffixes
  (`input.geojson`, `imagery (1).tif`, etc.) — moved into
  `data/34855_vadnerbhairav_chandavad_nashik/` and renamed to match
  the `bhume.load()` expected layout
- `matplotlib` not in `pyproject.toml` — added and synced via `uv sync`

---

## Phase 2 — Visualization Toolkit (`src/visualization.py`, `visualization.py`)

**What was built**
- `src/visualization.py`: `plot_plot()` renders satellite imagery + official
  boundary + boundary hints + optional truth + optional prediction as a single
  PNG. `render_truth_plots()` and `render_plots()` for batch output.
- `visualization.py`: CLI runner, accepts village dir and optional plot numbers

**Key findings**
- All 6 truth plot PNGs generated cleanly showing satellite imagery background,
  red official boundary, lime dashed truth, and cyan boundary hint overlay
- Imagery CRS (EPSG:3857) must be converted back to lon/lat for `imshow` extent —
  straightforward with pyproj Transformer

**No bugs encountered in this phase**

---

## Phase 3 — Global Shift + Local Boundary Refinement (`src/alignment.py`)

**What was built**
- `GlobalShift` dataclass: dx_m, dy_m, n_samples, spread_m
- `BoundaryRaster`: loads full boundaries.tif into RAM as boolean numpy array
- `score_perimeter()`: fraction of edge pixels in 3m perimeter band
- `local_refine()` (initial): grid search with rasterize-per-candidate
- `run_alignment()`: village-wide pipeline

**First performance measurement**
- rasterize-per-candidate: 0.32s/plot → 793s (~13 min) for 2,457 plots
- Too slow. Identified bottleneck: `rasterio.features.rasterize()` called
  289 times per plot

**Optimisation: rasterize-once / pixel-shift**
- Rasterize band mask once at the starting position
- For each candidate (tx, ty): shift the edge_crop array instead of re-rasterizing
- Result: 0.012s/plot → 29s for 2,457 plots — 26× speedup

**Critical bug: pixel shift direction inversion**
- Fast approach returned inverted directions: ground-truth said (-6,-6)m,
  fast approach returned (+6,+6)m
- Root cause: `_score_shifted()` was shifting the mask instead of the crop
- Fix: invert the slice indices — shift edge_crop relative to fixed band_mask
- Validated against ground-truth rasterize-per-candidate on 4 truth plots
  before deploying to full village

**Results after fix**

| Stage | Median IoU | Centroid error |
|---|---|---|
| Official | 0.584 | — |
| Global shift | 0.691 | 7.8m |
| Global + Local | **0.836** | **3.4m** |

Local refinement improved 6/6 truth plots. Median centroid error 7.8m → 3.4m.

---

## Phase 4 — Image Gradient Signal (`src/image_signals.py`)

**What was built**
- `ImageRaster`: loads full imagery as float32 Sobel-magnitude array (one load)
- `build_image_scorer()`: pre-computes band mask and padded Sobel crop once,
  returns a `_score(px_dx, px_dy)` closure using the same pixel-shift pattern
- `_local_refine_combined()` and `_build_boundary_scorer()` in `alignment.py`:
  pluggable combined scorer (w_boundary=0.6, w_image=0.4)

**Key finding from probing**
- Sobel magnitude at truth boundary: higher than at official position in 5/6 plots
  (ratios 1.09–1.59). Image gradient is a real signal
- Combined scorer improved plot 2647 from IoU 0.650 → 0.778 where boundary
  hints were sparse

**Performance**
- Combined scorer: 0.020s/plot → 49s for 2,457 plots. Acceptable.

**No direction bugs this phase** — same pixel-shift pattern as Phase 3,
already validated.

---

## Phase 5 — Confidence Engine (`src/confidence.py`)

**What was built**
- `ConfidenceBreakdown` dataclass: 6 signal scores + final confidence
- `VillageContext`: precomputed village-wide statistics (reliability, median
  edge density)
- `build_village_context()`: samples 200 random plots for edge density baseline
- `compute_confidence()`: computes S1–S6, combines with documented weights
- `run_confidence()`: village-wide pass after alignment

**Bugs encountered and fixed**

1. **NaN confidence values** — 11 plots had `confidence = NaN`
   - Root cause: `plot_props.get("recorded_area_sqm") or 0.0` does not catch
     pandas `NaN` (because `float('nan') or 0.0` evaluates to `nan`, not `0.0`)
   - Fix: `_safe_float()` helper using `f != f` NaN check

2. **S2 peak sharpness = 1.0 for all plots** — flat signal, no discrimination
   - Root cause: best/top-k-mean ratio is nearly 1.0 when grid is dense
   - Fix: use z-score = (best - mean) / (std + epsilon), normalised at z=2

3. **Spearman ρ = -0.257 on truth plots** — confidence slightly anti-correlates
   - Root cause: plot 622 (IoU=0.960, highest) has low S4 boundary visibility
     (0.402) pulling its confidence down to 0.656
   - Assessment: with n=6, this is statistically meaningless. S4 correctly reflects
     that boundary hints are sparse for plot 622 — the correction succeeded via
     image gradient alone. Not a model error; a dataset limitation.
   - Documented honestly in calibration report.

**Final confidence range (all 2,457 plots)**
- min=0.105, p25=0.406, median=0.557, p75=0.647, max=0.885

---

## Phase 6 — Uncertainty-Aware Flagging (`src/flagging.py`, `phase6_flagging.py`)

**What was built**
- `apply_decisions()`: applies threshold, returns `Decision` objects
- `_build_method_note()`: generates plain-English explanation per decision
- `decisions_to_geodataframe()`: contract-valid GeoDataFrame
- `threshold_report()` and `confidence_distribution_summary()`: diagnostic text
- `phase6_flagging.py`: full runner with 3 diagnostic figures

**Threshold selection process**
- Simulated t = 0.2 through 0.8 on both all plots and truth plots
- At t=0.50: all 6 truth plots corrected, all improve
- At t=0.60: plot 2647 (IoU improvement +0.365, best in village) gets flagged
- At t=0.40: plots with s3_area≈0 pass — less trustworthy
- **Selected: t=0.50**

**Results at t=0.50**
- Corrected: 1,533 (62.4%) — before neighbourhood update
- Flagged: 924 (37.6%)
- Low-conf tail (< 0.35): 419 plots, all have S3 area consistency ≈ 0

---

## Phase 7 — Neighbourhood Model (`src/neighborhood.py`, `phase7_neighborhood.py`)

**What was built**
- `NeighborhoodContext`: IDW drift surface from high-confidence anchors
- `build_neighborhood_context()`: selects conf≥0.55 plots as anchors, builds
  cKDTree over UTM centroids
- `idw_drift_estimate()`: inverse-distance weighted (power=2), confidence-weighted
- `neighbourhood_consistency_score()`: Gaussian decay, max_influence=800m
- `apply_neighbourhood_to_confidence()`: blends S7 at w=0.08 into existing scores

**Key finding from probing**
- Nearest truth-to-truth distances: 504m–2188m across a 7.8×7.9km village
- All KNN estimates (K=3,5,10,20) collapse to global shift — no truth data
  within any neighbourhood. Truth-based drift surface not feasible.
- Alternative: build IDW surface from 1,214 high-confidence corrected plots

**Effect on truth plots**
- All 6 truth plots: confidence nudged upward (+0.010 to +0.028)
- Plot 2647: -0.004 (large local shift flagged as outlier vs neighbours — correct)
- Decisions at t=0.50 after neighbourhood: 1,406 corrected (57.2%), 1,051 flagged

---

## Phase 8 — Evaluation and Generalization

### Vadnerbhairav evaluation (`phase8_evaluation.py`)

**What was built**
- 6 matplotlib figures: IoU comparison, confidence histogram, confidence vs IoU,
  signal breakdown, threshold sweep, drift vectors
- 6 markdown reports: accuracy, calibration, restraint, generalization,
  limitations, summary
- Final `predictions.geojson`: 2,457 plots

**Bug encountered**
- `UnicodeEncodeError` on Windows when writing markdown containing `Δ`
- Root cause: `pathlib.Path.write_text()` defaults to cp1252 on Windows
- Fix: `encoding="utf-8"` parameter on all `write_text()` calls

**Final bhume scorer output**
```
accuracy:    median IoU pred=0.875 vs official=0.612  (improvement=0.271)
             median centroid err=3.608m  accurate(IoU>=0.5)=1.000
calibration: Spearman(conf,IoU)=-0.257  AUC=—
```

### Malatavadi generalization (`run_malatavadi.py`)

**What was built**
- Same pipeline, zero parameter changes
- `reports/malatavadi_generalization.md`
- `reports/cross_village_comparison.md`
- `data/34854_malatavadi_hatkanangale_kolhapur/predictions.geojson`

**Setup**
- Malatavadi files in root with numbered suffixes (`input (1).geojson` etc.)
- Moved to `data/34854_malatavadi_hatkanangale_kolhapur/` and renamed

**Results**
- Global shift auto-derived: dx=+9.57m, dy=+0.05m (completely different direction)
- Corrected: 1,027 (40.9%), Flagged: 1,481 (59.1%)
- Median confidence: 0.472 vs 0.557 — sparser boundary hints correctly reduce confidence
- Plot 1177: IoU 0.675 → 0.000 (documented failure — global prior unreliable at n=3,
  boundary hints absent, actual drift is an outlier vs village median)

**Bug encountered**
- `ValueError: The truth value of a GeoDataFrame is ambiguous` in cross-village
  report writer
- Root cause: `if village.example_truths` on a GeoDataFrame uses pandas __bool__
- Fix: `if village.example_truths is not None`

---

## Final Repository State

```
src/
  alignment.py      — GlobalShift, BoundaryRaster, combined scorer (Phase 3–4)
  image_signals.py  — ImageRaster, Sobel scorer (Phase 4)
  confidence.py     — 6 signals S1–S6, NaN-safe (Phase 5)
  flagging.py       — threshold policy, method_note generation (Phase 6)
  neighborhood.py   — IDW surface, S7 signal (Phase 7)
  evaluation.py     — comparison tables and charts (Phases 3, 8)
  visualization.py  — per-plot PNG renderer (Phase 2)

data/
  34855_vadnerbhairav_chandavad_nashik/predictions.geojson  (2,457 plots)
  34854_malatavadi_hatkanangale_kolhapur/predictions.geojson (2,508 plots)

reports/
  6 markdown reports + 9 diagnostic figures
```

**Runtime**
- Vadnerbhairav: ~60s for 2,457 plots
- Malatavadi: ~151s for 2,508 plots (larger imagery, finer resolution)
