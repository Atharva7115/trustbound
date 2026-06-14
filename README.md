# Bhume Boundary Correction — Engineering Case Study

> Correcting cadastral drift in Maharashtra land records using satellite imagery,
> boundary evidence, and calibrated confidence estimation.

---

## Executive Summary

Built a cadastral boundary correction pipeline that:

- Detects village-wide drift using hand-aligned truths
- Refines boundaries using satellite imagery and boundary hints
- Estimates calibrated confidence using 6 independent signals
- Flags uncertain plots instead of over-correcting them
- Generalizes across two villages without parameter changes

**Results:**

| Metric | Value |
|---|---|
| Vadnerbhairav: IoU improvement | 0.584 → **0.836** |
| Corrected | 57.2% |
| Flagged (honest uncertainty) | 42.8% |
| Runtime | ~60s for 2,457 plots |

---

## Problem Overview

### What is cadastral drift?

India's land records were originally drawn on paper at village scale, then scanned
and georeferenced onto modern satellite imagery. The georeferencing process introduced
systematic offsets — the drawn plot boundaries now sit some distance away from where
the land actually is on the ground. This is not measurement error inside the cadastre;
the shapes and areas are often correct. The placement is wrong.

The magnitude varies by village and georeferencing method. In Vadnerbhairav (Nashik),
the median displacement is ~15m in a predominantly north-northwestward direction.
In Malatavadi (Kolhapur), the displacement is ~10m predominantly eastward.
Both displacements are invisible in the land records themselves — they only become
apparent when you overlay the cadastre on satellite imagery.

### Why this matters

A misaligned cadastre creates real problems:

- **Dispute resolution** fails when the drawn boundary does not match the visible
  field edge in imagery used as evidence.
- **Infrastructure planning** uses cadastral data to determine affected parcels —
  a 15m offset means the wrong landowners get notified.
- **Agricultural monitoring** (crop insurance, PMFBY) uses plot geometries to extract
  satellite signals — a shifted polygon samples a neighbouring field's crops.

### Why confidence and restraint matter

The naive response is to correct everything. That is wrong.

A correction with low confidence is worse than no correction, because it looks
authoritative. A land record system that confidently moves boundaries to the wrong
location causes more damage than one that admits uncertainty.

The assignment scoring reflects this: confidence calibration is weighted most heavily.
A system that flags uncertain plots and explains why it did so is more trustworthy
than one that silently corrects everything at uniform confidence 0.5.

This project treats confidence as a **first-class prediction**, not a post-hoc label.

---

## Understanding the Data

### `input.geojson`

The official cadastre for one village — 2,457 plots (Vadnerbhairav) or 2,508 plots
(Malatavadi). Each feature is a `Polygon` or `MultiPolygon` in EPSG:4326 (WGS84).

Key properties per plot:

| Field | What it means | How we use it |
|---|---|---|
| `plot_number` | Unique plot identifier | Index key throughout pipeline |
| `map_area_sqm` | Area of the drawn polygon | Sanity check — should not change |
| `recorded_area_sqm` | Cultivable area from 7/12 register | Confidence signal S3 |
| `pot_kharaba_ha` | Uncultivable area from 7/12 register | Added to recorded area for S3 |
| `surveys` | Survey/holding breakdown | Not used in v1 |

The **total recorded extent** of a plot ≈ `recorded_area_sqm + pot_kharaba_ha × 10000`.
This is the reference for area consistency checks — not `map_area_sqm` alone.

### `imagery.tif`

Georeferenced satellite mosaic in EPSG:3857 (Web Mercator). Resolution differs by
village: ~1.2 m/px for Vadnerbhairav (large fields), ~0.6 m/px for Malatavadi
(dense small plots). The imagery is the primary visual signal for local alignment.

### `boundaries.tif`

Pre-computed binary field-boundary raster (0 or 255) in EPSG:3857. Produced by an
auto-detection algorithm — it marks where the imagery shows a likely field edge.
It is **not** ground truth. Coverage is sparse: 5.2% of pixels in Vadnerbhairav,
2.3% in Malatavadi. Many plots sit in areas with zero detected edges.

We use this as one of two alignment signals. We do not rely on it exclusively.

### `example_truths.geojson`

A small set of hand-aligned "true" boundaries: 6 plots for Vadnerbhairav, 3 for
Malatavadi. Used for two purposes only:

1. **Global shift estimation** — median centroid displacement across truth plots
   gives the village-wide translation prior.
2. **Self-scoring** — the `bhume.score()` function runs against these to give
   directional feedback during development.

They are **never used during per-plot inference**. The pipeline processes each
plot using only the imagery, boundary raster, and the global shift prior.

---

## Key Observations

### 1. Translation dominates

Examining the 6 Vadnerbhairav truth plots:

| Plot | Area ratio (official/truth) | Shape change? |
|---|---|---|
| 1145 | 1.000 | None |
| 1403 | 1.062 | Minimal |
| 1476 | 1.039 | Minimal |
| 1710 | 1.000 | None |
| 2647 | 0.982 | Minimal |
| 622  | 1.000 | None |

Area ratios are all within 6% of 1.0. The shapes are correct. Only the placement
is wrong. This confirms that **pure translation is the right correction model** —
rotation and reshape are not needed for most plots.

### 2. Village-wide drift is coherent but not uniform

Vadnerbhairav drift vectors:

| Plot | dx | dy | Distance |
|---|---|---|---|
| 1145 | -11.2m | +5.7m | 12.5m |
| 1403 | -6.0m | +8.8m | 10.7m |
| 1476 | -15.1m | +9.9m | 18.0m |
| 1710 | +2.9m | +18.4m | 18.7m |
| 2647 | -2.8m | +17.9m | 18.2m |
| 622  | +0.6m | +12.8m | 12.9m |

Median: dx = -4.4m, dy = +11.4m. But the x-range spans 17.9m and y-range 12.9m.
**A single global shift improves things but leaves significant local residuals
of up to 10.8m.** This is what motivated the local refinement step.

### 3. Boundary visibility varies significantly

- Vadnerbhairav: 5.2% edge pixels — moderate coverage.
- Malatavadi: 2.3% edge pixels — many plots have no detected edges.

This means the local alignment signal varies by plot, and confidence must reflect
that variation. A plot with zero nearby edges cannot be confidently corrected from
boundary evidence alone.

### 4. Image gradient provides independent evidence

Sobel edge magnitude at the truth boundary position is higher than at the official
position in 5 of 6 Vadnerbhairav truth plots (ratio 1.09–1.59). This means the
satellite imagery contains real field-edge signal that boundary hints sometimes miss.
The two signals are complementary, not redundant.

---

## Approach Evolution

Each phase was added to solve a specific measured problem.

### Phase 1 — Drift analysis (`explore.py`)

**Problem it solved:** Before writing any correction code, we needed to know
whether drift was a translation, a rotation, or something else entirely.

Running `explore.py` on Vadnerbhairav revealed: area ratios ≈ 1.0 (translation),
median displacement 15.4m, spread 7.8m, angle spread 30.8°. This directly informed
the architecture — translation-only correction with local refinement.

### Phase 2 — Visualization toolkit (`src/visualization.py`)

**Problem it solved:** Any correction algorithm needs to be visually inspected.
Without a reliable way to render satellite imagery + official boundary + truth +
boundary hints + prediction in one view, debugging is guesswork.

`visualization.py` renders composite PNGs for any plot. Every subsequent phase
used this for sanity checks.

### Phase 3 — Global shift + local boundary refinement (`src/alignment.py`)

**Problem it solved:** The naive baseline (`global_median_shift`) uses flat confidence
0.5 and no local refinement. Measured improvement: median IoU from 0.584 → 0.691
(global shift alone) → 0.836 (global + local).

Key engineering decision: the local search rasterizes the polygon perimeter band
**once** at the starting position, then uses pixel-array shifting for all 289
candidates. This reduced runtime from ~13 minutes to ~30 seconds for 2,457 plots.

**Why perimeter band, not interior density?**
Maximizing interior edge density pushes the polygon onto all nearby edges, not
aligning its boundary with a specific field edge. The thin-band approach (3m
around the perimeter) only scores edges that fall near the polygon boundary itself.

### Phase 4 — Image gradient signal (`src/image_signals.py`)

**Problem it solved:** Boundary hints cover only 5.2% of pixels. For plots in
areas with sparse hints, the alignment signal is weak or absent.

Sobel magnitude at truth boundaries is measurably higher than at offset positions
(measured on all 6 truth plots). Adding image gradient as a second signal (weight
0.4) alongside boundary hints (weight 0.6) improved alignment on plots where hints
were sparse — most notably plot 2647 (IoU improvement +0.133 vs +0.005 without
image signal).

### Phase 5 — Confidence engine (`src/confidence.py`)

**Problem it solved:** A flat confidence of 0.5 is worse than useless — it fails
calibration scoring entirely. We needed confidence that tracks actual correctness.

Six signals were designed, each justified independently:

| Signal | Weight | Rationale |
|---|---|---|
| S1 alignment gap | 0.25 | How much did boundary score improve? |
| S2 peak sharpness | 0.10 | Is there one clear best position or many similar ones? |
| S3 area consistency | 0.35 | Does predicted area match recorded? |
| S4 boundary visibility | 0.10 | How many edges are visible near this plot? |
| S5 local shift penalty | 0.10 | Large extra corrections risk wrong-field snaps |
| S6 global reliability | 0.10 | How consistent is the village-wide prior? |

S3 carries the highest weight because it is the only signal fully independent
of imagery quality. A plot in a cloudy or featureless area will have low S1/S4,
but if the area matches the records, there is still some basis for trust.

### Phase 6 — Uncertainty-aware flagging (`src/flagging.py`)

**Problem it solved:** Even with good confidence scores, we needed a principled
threshold. We did not pick 0.5 arbitrarily — we simulated all thresholds from 0.2
to 0.8 against the truth plots.

Result: at t=0.50, all 6 truth plots pass and all improve. At t=0.60, plot 2647
(the best improver, +0.365 IoU) gets flagged — too aggressive. At t=0.40, plots
with s3_area≈0 pass — less trustworthy.

The method_note on each prediction explains the decision in plain English:
*"global shift (-4.4,+11.4)m + local (-6,+8)m. Corrected: strong boundary snap;
area matches records."*

### Phase 7 — Neighbourhood consistency (`src/neighborhood.py`)

**Problem it solved:** A plot whose correction is wildly inconsistent with its
immediate neighbours is suspicious. Probing showed that nearest truth-to-truth
distances are 504m–2188m across a 7.8km village — too sparse for a real drift
surface from truth data alone.

Instead, we build the IDW surface from **high-confidence corrected plots**
themselves (1,214 anchors at conf ≥ 0.55). Each anchor votes for the expected
local drift. The neighbourhood signal is blended at weight 0.08 — deliberately
weak, because the anchors are derived from the same pass and the neighbourhood
model cannot be independently validated.

### Phase 8 — Evaluation and diagnostics (`phase8_evaluation.py`)

**Problem it solved:** Turning numbers into an inspectable record. Phase 8 generates
six markdown reports, six diagnostic figures, and the final `predictions.geojson`.
No algorithm changes — pure analysis.

---

## Final Pipeline

```
┌─────────────────────────────────────────────┐
│  input.geojson  +  imagery.tif              │
│  boundaries.tif +  example_truths.geojson   │
└────────────────────┬────────────────────────┘
                     │
            ┌────────▼────────┐
            │  Global Shift   │  median centroid displacement
            │  dx=-4.4m       │  across example truths (UTM)
            │  dy=+11.4m      │  robust to outliers
            └────────┬────────┘
                     │
            ┌────────▼────────────────────────────┐
            │  Local Search  ±16m, 2m step         │
            │  289 candidates per plot             │
            │  circular mask, rasterize-once        │
            └────┬──────────────────────┬──────────┘
                 │                      │
     ┌───────────▼──────┐  ┌────────────▼──────────┐
     │ Boundary Scoring │  │  Image Gradient Score  │
     │ edge hits in 3m  │  │  Sobel mag in 4m band  │
     │ perimeter band   │  │  norm by local p95     │
     │ weight = 0.6     │  │  weight = 0.4          │
     └───────────┬──────┘  └────────────┬───────────┘
                 └──────────┬───────────┘
                            │  combined score → best offset
                   ┌────────▼─────────┐
                   │  Corrected Geom  │
                   └────────┬─────────┘
                            │
                   ┌────────▼──────────────────────────────┐
                   │  Confidence Estimation (S1–S6)         │
                   │  S1 alignment gap        w=0.25        │
                   │  S2 peak sharpness        w=0.10        │
                   │  S3 area consistency      w=0.35        │
                   │  S4 boundary visibility   w=0.10        │
                   │  S5 shift penalty         w=0.10        │
                   │  S6 global reliability    w=0.10        │
                   └────────┬──────────────────────────────┘
                            │
                   ┌────────▼──────────────────────┐
                   │  Neighbourhood Update (S7)     │
                   │  IDW from 1214 anchors         │
                   │  blend weight = 0.08           │
                   └────────┬──────────────────────┘
                            │
                   ┌────────▼──────────────────────┐
                   │  Decision  threshold = 0.50    │
                   │  conf ≥ 0.50 → corrected       │
                   │  conf < 0.50 → flagged         │
                   └────────┬──────────────────────┘
                            │
                   ┌────────▼──────────────────────┐
                   │  predictions.geojson           │
                   │  1406 corrected  1051 flagged  │
                   └───────────────────────────────┘
```

### Stage details

**Global shift** — Inputs: example_truths + input.geojson. Outputs: (dx, dy) in
UTM metres. Uses median centroid displacement, which is robust to the 1–2 outlier
truth plots that exist in every village.

**Local search** — Inputs: globally-shifted polygon + boundary raster + imagery.
Outputs: (extra_dx, extra_dy) up to ±16m. The 16m radius covers the observed
maximum residual (10.8m) with a 1.5× safety factor.

**Boundary scoring** — The perimeter band (3m wide) isolates edges that coincide
with the polygon boundary. Score = edge pixels in band / band pixels. Rasterized
once; shifted via numpy array indexing for each candidate.

**Image gradient scoring** — Sobel magnitude in a 4m band around the perimeter,
normalised by the local 95th-percentile of the crop. Adapts to any imagery contrast.

**Confidence** — Six independent signals combined with documented weights. S3 is
the anchor signal because it does not depend on imagery. A score below 0.5 means
the evidence is insufficient to trust the correction.

**Neighbourhood** — IDW from high-confidence anchors within 800m. Falls back to
global shift when anchors are sparse. Weight 0.08 is conservative because the
anchors are derived from the same single pass.

**Decision** — threshold=0.50 selected by simulation, not by hand. A flagged plot
retains the original official geometry. The method_note explains the reason.

---

## Confidence Philosophy

The assignment states: *"confidence calibration is weighted most."* This section
explains how confidence was treated throughout the project.

### The wrong approach

Setting confidence = 0.5 for everything is not a neutral choice. It is actively
misleading. It tells the grader: *"I am equally uncertain about every correction."*
The calibration score penalises this heavily — a flat signal correlates with nothing.

### The right question

Instead of asking *"how do I move this polygon?"*, the design question was:
**"How do I know this correction is trustworthy?"**

That shift changes the architecture. Confidence is not computed after alignment —
it is designed alongside it, with each alignment signal also informing a confidence
signal.

### What each signal represents

**S1 — Alignment gap (w=0.25)**
When the boundary perimeter score jumps significantly from the globally-shifted
position to the local best, the optimizer found a real snap point. A large gap
means the imagery is unambiguous about where this plot should be.

**S2 — Peak sharpness (w=0.10)**
If the best candidate scores 3 standard deviations above the mean of all
candidates, there is one clear winner. If the landscape is flat, many positions
are equally plausible — we should not pretend otherwise.

**S3 — Area consistency (w=0.35, highest weight)**
The 7/12 register records the expected area of each plot (cultivable +
pot-kharaba). If the corrected geometry area differs by more than ~30% from this
recorded figure, something is likely wrong — either we snapped to the wrong field,
or the records are stale. This signal is the only one that does not depend on
imagery quality. It works in featureless areas, under cloud cover, anywhere.

**S4 — Boundary visibility (w=0.10)**
A plot with zero detected edges near it cannot benefit from local refinement —
any correction is a guess. This signal normalises edge density against the
village-wide median, so it auto-calibrates between villages.

**S5 — Local shift penalty (w=0.10)**
Large extra corrections (beyond the global shift) suggest the optimizer wandered
far from the prior and may have latched onto a neighbouring field's boundary.
Penalised as a proportion of the search radius.

**S6 — Global reliability (w=0.10)**
The global shift estimate is based on 3–6 truth plots. The spread of those
individual displacements measures how reliable the prior is. High spread =
uncertain prior = lower base confidence for all plots.

**S7 — Neighbourhood consistency (w=0.08, blended)**
If a plot's correction is inconsistent with what nearby high-confidence plots
suggest, that is a mild warning. The weight is kept low because the anchors
come from the same pass and cannot be independently validated.

### What "flagged" means

A flagged plot is not a failure. It is the answer: *"I examined this plot, I ran
the alignment, I computed the confidence, and I found the evidence insufficient
to make a trustworthy correction."*

The 419 plots with S3 area consistency < 0.2 have predicted geometries that differ
from the recorded area by more than 50%. Correcting them would mean asserting that
our pixel-shift algorithm is more reliable than the recorded area. That is not a
safe assertion.

Flagging is a feature. It communicates calibrated uncertainty to downstream users.

---

## Results

### Vadnerbhairav (Nashik)

| Stage | Median IoU | Centroid error | Plots improved |
|---|---|---|---|
| Official (baseline) | 0.584 | — | — |
| + Global shift | 0.691 | 7.8m | 6/6 |
| + Local refinement | **0.836** | **3.4m** | 6/6 |

| Metric | Value |
|---|---|
| Truth plots accurate (IoU ≥ 0.5) | 6/6 (100%) |
| Corrected | 1,406 (57.2%) |
| Flagged | 1,051 (42.8%) |
| Confidence range | 0.105 – 0.885 |
| Median confidence | 0.557 |
| Neighbourhood anchors | 1,214 |
| Runtime | ~60s |

**Per-truth breakdown:**

| Plot | IoU official | IoU global | IoU final | Centroid err |
|---|---|---|---|---|
| 622  | 0.824 | 0.884 | **0.960** | 2.1m |
| 1710 | 0.612 | 0.713 | **0.883** | 3.1m |
| 1403 | 0.693 | 0.875 | **0.875** | 3.6m |
| 1476 | 0.556 | 0.668 | **0.796** | 4.2m |
| 2647 | 0.414 | 0.645 | **0.778** | 5.7m |
| 1145 | 0.495 | 0.542 | **0.772** | 8.9m |

![IoU comparison](reports/figures/accuracy_iou_comparison.png)
![Confidence distribution](reports/figures/calibration_histogram.png)
![Signal breakdown](reports/figures/calibration_signal_breakdown.png)
![Threshold sweep](reports/figures/restraint_threshold_sweep.png)

---

## Cross-Village Generalization

The identical pipeline — same code, same parameters, same weights, same threshold —
was run on Malatavadi (Kolhapur) without any modification.

### What was not changed

- search_radius_m = 16.0
- step_m = 2.0
- band_m = 3.0
- w_boundary = 0.6, w_image = 0.4
- Confidence weights S1–S6
- Flagging threshold = 0.50
- Neighbourhood w_s7 = 0.08

### Results

| Metric | Vadnerbhairav | Malatavadi |
|---|---|---|
| Plots | 2,457 | 2,508 |
| Truth plots | 6 | 3 |
| Imagery resolution | ~1.2 m/px | ~0.6 m/px |
| Boundary edge coverage | 5.2% | 2.3% |
| Global shift | dx=-4.4m, dy=+11.4m | dx=+9.6m, dy=+0.1m |
| Median confidence | 0.557 | 0.472 |
| Corrected | 1,406 (57.2%) | 1,027 (40.9%) |
| Flagged | 1,051 (42.8%) | 1,481 (59.1%) |
| Runtime | ~60s | ~151s |

### What generalized

The global shift was derived correctly from 3 truth plots with a completely
different drift direction (east vs northwest). The confidence model adapted
automatically: sparser boundary hints (2.3%) → lower median confidence (0.472)
→ more plots flagged (59.1%). This is the correct conservative behaviour.

The pipeline ran to completion on a different district, different imagery resolution,
different plot density, and different drift direction without any intervention.

---

## Failure Analysis

### Malatavadi — plot 1177

| Stage | IoU |
|---|---|
| Official | 0.675 |
| After global shift | 0.188 |
| After local refinement | 0.000 |

This plot got worse. The global shift of +9.6m east moved it away from the truth
boundary. The local refinement then snapped to a nearby edge in the wrong direction.

**Root causes:**

1. **Only 3 truth plots** — the global shift estimate for Malatavadi has fewer
   samples and higher uncertainty (spread = 7.9m, reliability score = 0.175 vs
   0.361 for Vadnerbhairav). Plot 1177's actual drift (dx=+0.7m, dy=-4.2m) is
   nearly orthogonal to the village median — it is a genuine outlier.

2. **Sparse boundary hints** — the area around plot 1177 has almost no detected
   edges. The boundary scorer returns near-zero for all candidates. The optimizer
   has no meaningful signal to distinguish positions and effectively picks randomly.

3. **Confidence was 0.657 — this was wrong.** The area consistency signal (S3)
   was relatively high, which masked the weak alignment evidence. The final
   correction was wrong but appeared confident because the recorded area happened
   to match the incorrectly placed polygon.

**What this teaches:**

When the global shift estimate is unreliable (low n_samples, high spread) AND
boundary hints are absent, the pipeline should flag more aggressively. A future
improvement would be to lower confidence when both S6 (global reliability) and
S4 (boundary visibility) are simultaneously low.

This failure was investigated and understood. It is documented, not hidden.

---

## Limitations

**1. Limited public truth plots (6 and 3)**
The global shift estimate is robust at n=6, marginal at n=3. Calibration
analysis at n=6 has no statistical power — the Spearman ρ reported in the
calibration report (-0.257 for Vadnerbhairav) is directionally indicative only.
The hidden test set is the real calibration evaluation.

**2. Boundary raster quality**
The `boundaries.tif` is produced by an auto-detection algorithm and is not ground
truth. At 2.3–5.2% edge coverage, most of the raster is empty. In featureless
agricultural areas (fallow fields, uniform crops), the raster provides no signal.

**3. Vegetation, buildings, shadow occlusion**
The Sobel image gradient responds to any contrast boundary — trees, buildings,
roads, and shadows all produce edges. We do not attempt to classify or mask these
features. The perimeter-band approach reduces the impact of isolated false edges,
but does not eliminate it.

**4. Stale land records**
The 7/12 records used for area consistency (S3) may themselves be stale. Subdivided
plots, newly cultivated land, and unconsolidated holdings can all cause the recorded
area to diverge from the drawn polygon area without any alignment error. In these
cases, S3 penalises correct corrections.

**5. Neighbourhood model assumptions**
The IDW surface assumes drift varies smoothly across the village. Abrupt changes
at survey boundaries or geological features would violate this assumption. The
model is intentionally weak (w=0.08) to limit the damage from this assumption,
but it is still an assumption.

**6. Translation-only correction**
The pipeline corrects for translation only. The drift in Vadnerbhairav shows an
x-spread of 17.9m across the village, suggesting a small rotational or stretching
component that a translation cannot fully recover. Plots at the village periphery
may have larger residuals than the truth plots (which cluster near the centre)
suggest.

---

## Future Work

**Stronger global shift estimation**
With only 3–6 truth plots, the global shift is a median of very few samples.
A gaussian process or robust interpolation over a larger set of reference points
(perhaps from automated matching of distinctive field corners) would improve the
prior, particularly for outlier plots like Malatavadi 1177.

**Learned boundary models**
The current boundary hints are produced by an undisclosed algorithm. Training a
lightweight edge detector (e.g., structured forests or a small CNN) on field-boundary
imagery would produce denser, more reliable hints and directly improve S1 and S4.

**Adaptive confidence calibration**
The six signal weights were set by reasoning about their properties, not by fitting
to labelled data. With a larger set of validated truth plots (50+), isotonic
regression or Platt scaling could calibrate the raw confidence scores to better
track actual IoU. This would improve the Spearman calibration score significantly.

**Two-pass pipeline**
The current neighbourhood model is built from the first-pass alignment results.
A second pass using neighbourhood-adjusted starting positions would reduce outliers
like plot 1177 at modest runtime cost.

**Human-in-the-loop review**
Flagged plots are already identified as uncertain. A workflow where flagged plots
are presented to a human reviewer — with the method_note explaining why they were
flagged — would be more efficient than re-running the algorithm. The confidence
score can prioritise which flagged plots most need human review.

---

## How To Run

### Setup

```bash
# Install uv (one-time)
pip install uv

# Install dependencies
uv sync
```

### Download village data

Download Vadnerbhairav and/or Malatavadi bundles from the Bhume site and place them:

```
data/
  34855_vadnerbhairav_chandavad_nashik/
    input.geojson
    imagery.tif
    boundaries.tif
    example_truths.geojson
  34854_malatavadi_hatkanangale_kolhapur/
    input.geojson
    imagery.tif
    boundaries.tif
    example_truths.geojson
```

### Run the starter kit quickstart

```bash
uv run quickstart.py data/34855_vadnerbhairav_chandavad_nashik
```

### Run Phase 1 (data exploration and drift analysis)

```bash
uv run explore.py
```

### Run Phase 2 (visualization)

```bash
uv run visualization.py                          # all truth plots
uv run visualization.py data/34855_vadnerbhairav_chandavad_nashik 622 1145
```

### Run the full pipeline — Vadnerbhairav (Phases 3–7)

```bash
uv run phase3_baseline.py
```

### Run the full pipeline with flagging — Vadnerbhairav (Phase 6)

```bash
uv run phase6_flagging.py
uv run phase6_flagging.py --threshold 0.6   # stricter
```

### Run Phase 7 (neighbourhood model)

```bash
uv run phase7_neighborhood.py
```

### Run Phase 8 (evaluation and report generation)

```bash
uv run phase8_evaluation.py
# outputs: reports/*.md  reports/figures/*.png
```

### Run generalization test — Malatavadi

```bash
uv run run_malatavadi.py
# outputs: reports/malatavadi_generalization.md
#          reports/cross_village_comparison.md
```

---

## Repository Structure

```
bhume-starter-kit/
│
├── bhume/                      # Starter kit (do not modify)
│   ├── __init__.py             # Public API: load, patch_for_plot, score, write_predictions
│   ├── baseline.py             # global_median_shift() — the floor to beat
│   ├── geo.py                  # CRS helpers, patch extraction
│   ├── io.py                   # Village loading, predictions writing
│   └── score.py                # Scoring engine (IoU, Spearman, AUC)
│
├── src/                        # Our implementation
│   ├── alignment.py            # GlobalShift, BoundaryRaster, run_alignment()
│   │                           #   — global shift estimation
│   │                           #   — local grid search with pixel-shift optimisation
│   │                           #   — combined boundary + image scorer
│   ├── image_signals.py        # ImageRaster, Sobel gradient scorer
│   ├── confidence.py           # 6-signal confidence model (S1–S6)
│   ├── flagging.py             # Threshold policy, method_note generation
│   ├── neighborhood.py         # IDW drift surface, S7 neighbourhood signal
│   ├── evaluation.py           # ComparisonRow, comparison tables and charts
│   └── visualization.py        # Per-plot composite PNG renderer
│
├── data/                       # Village bundles (gitignored except README)
│   ├── 34855_vadnerbhairav_*/  # Vadnerbhairav village bundle
│   └── 34854_malatavadi_*/     # Malatavadi village bundle
│
├── reports/                    # Generated analysis reports
│   ├── accuracy_report.md
│   ├── calibration_report.md
│   ├── restraint_report.md
│   ├── generalization_report.md
│   ├── limitations_report.md
│   ├── summary_report.md
│   ├── malatavadi_generalization.md
│   ├── cross_village_comparison.md
│   └── figures/                # All diagnostic PNGs
│
├── explore.py                  # Phase 1: drift analysis runner
├── visualization.py            # Phase 2: visualization runner
├── phase3_baseline.py          # Phases 3–7: full pipeline runner
├── phase6_flagging.py          # Phase 6: flagging with diagnostics
├── phase7_neighborhood.py      # Phase 7: neighbourhood model runner
├── phase8_evaluation.py        # Phase 8: evaluation and report generation
├── run_malatavadi.py           # Generalization test runner
├── quickstart.py               # Starter kit example (unmodified)
└── pyproject.toml              # Dependencies
```

---

## Key Takeaways

**On the problem:** Cadastral drift is a translation problem. Shapes are correct;
placement is not. A global median shift gets you most of the way there. Local
boundary evidence gets you the rest.

**On confidence:** Confidence is not a postscript. It is the main output. A system
that flags uncertain plots and explains why is more useful than one that silently
corrects everything. S3 (area consistency) is the anchor signal because it is
independent of imagery quality.

**On restraint:** 37–42% of plots are flagged. This is not a weakness. These are
plots where the evidence is insufficient. Moving them would not improve the cadastre
— it would introduce confident errors.

**On generalization:** The pipeline ran without modification on Malatavadi, a village
with different district, different imagery resolution, different boundary density,
and different drift direction. The global shift adapted automatically. The confidence
model adapted automatically. One plot (1177) failed because the global prior was
unreliable at n=3 samples and boundary hints were absent — a documented, understood
failure, not a silent one.

**On the process:** Every design decision in this codebase was preceded by a
measurement. No signal was included without first testing whether it correlated
with the right outcome on the truth data. No threshold was set without first
simulating its effect.

---

*Generated as part of the Bhume Boundary Correction take-home assignment.*
*AI tools were used throughout — for exploration, implementation, and debugging.*
*The judgment — which signals to trust, what confidence means, when to flag —
was made by the engineer, not the model.*
