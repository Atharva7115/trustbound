# ChatGPT Session Summary

**Link:** https://chatgpt.com/share/6a2fb703-72b8-83ee-b12b-94eb5f9bfd58

Summarised record of web discussions used to frame the problem and design the
approach. This is a structured summary of topics covered and decisions made —
not a verbatim transcript. All metrics and findings cited here come from the
actual repository outputs.

---

## 1. Understanding the Assignment

**Topics covered**
- What the Bhume grading rubric actually weights (confidence calibration > accuracy)
- Why "metrics shortlist, method review decides" changes the design target
- What a 5-minute walkthrough needs to demonstrate

**Key decisions influenced**
- Treat confidence as a first-class prediction, not a post-hoc label
- Prioritise explainability and honest flagging over maximising IoU
- Each pipeline stage should be independently justifiable without reference to
  the truth labels it was not trained on

---

## 2. Understanding Cadastral Drift

**Topics covered**
- How Maharashtra village maps were drawn at 1:4000–1:8000 scale on paper,
  then scanned and batch-georeferenced onto satellite imagery
- Why batch georeferencing introduces a village-wide rigid offset with small
  local residuals — not random noise
- Why translation dominates: the shapes are correct, only the placement is wrong

**Key decisions influenced**
- Translation-only correction is the right model (area ratios confirmed near 1.0)
- Global shift captures the dominant component; local refinement handles residuals
- Rotation and reshape are not justified by the evidence

---

## 3. Dataset Analysis

**Topics covered**
- CRS handling: `input.geojson` is EPSG:4326; rasters are EPSG:3857; scoring
  must use UTM (EPSG:32643) for accurate metre distances and areas
- The `pot_kharaba_ha` field: uncultivable area held separately from cultivable.
  Total expected plot extent = `recorded_area_sqm + pot_kharaba_ha × 10000`
- `boundaries.tif` reliability: auto-detected edges, not ground truth.
  Confirmed at 5.2% coverage (Vadnerbhairav), 2.3% (Malatavadi)
- Example truths: for evaluation only. The only legitimate inference use is
  computing the global shift estimate (once, offline)

**Key decisions influenced**
- Use `recorded_area_sqm + pot_kharaba_ha × 10000` as the area reference for
  signal S3, not `map_area_sqm`
- Do all geometry scoring in UTM, not lon/lat
- Example truths never used during per-plot inference

---

## 4. Global Shift Reasoning

**Topics covered**
- Median vs mean for estimating the shift from 6 truth plots
- How to measure the reliability of the estimate (spread of residuals)
- Whether to weight truth plots differently (e.g., by area or confidence)

**Key decisions influenced**
- Use median centroid displacement — robust to 1–2 outlier truth plots in a set of 6
- `GlobalShift.spread_m` = median absolute deviation of residuals after applying
  the median shift. For Vadnerbhairav: 7.78m. Used as confidence signal S6
- No weighting of truth plot contributions — not justified with n=6

---

## 5. Local Alignment Strategy

**Topics covered**
- Interior edge density vs perimeter band scoring — which avoids the alignment trap
- How interior density maximisation pushes the polygon onto all edges, not its own
- What search radius is appropriate (1.5× max observed residual = 16m)
- How to make 289 candidates per plot fast enough for 2,457 plots

**Key decisions influenced**
- Perimeter band score (3m width): edges that fall within the polygon boundary zone,
  not edges inside the polygon
- Search radius 16m = 1.5 × 10.8m (max residual after global shift)
- Rasterize band mask once per plot; shift via numpy array indexing for each
  candidate. Reduces runtime from ~13 min to ~30s for 2,457 plots
- Validated fast implementation against ground-truth rasterize-per-candidate
  on 4 truth plots before trusting it on the full village

---

## 6. Image-Based Signals

**Topics covered**
- Whether satellite imagery adds information where boundary hints are absent
- Canny vs Sobel for edge detection without per-image threshold tuning
- How to normalise across plots with different imagery contrast levels

**Key decisions influenced**
- Sobel magnitude: parameter-free, gives relative scores for ranking positions
- Normalise by local 95th-percentile of the crop — adapts to each plot's contrast
- Boundary weight 0.6, image weight 0.4: hints more precise when present;
  image provides fallback when hints absent
- Confirmed on truth plots: Sobel magnitude at truth position is higher than
  official position in 5 of 6 plots (ratio 1.09–1.59)

---

## 7. Confidence Calibration

**Topics covered**
- Which signals actually correlate with IoU given only 6 truth plots
- Why S3 (area consistency) should carry the highest weight
- How to handle NaN values in pandas property fields
- Why peak sharpness (S2) needs z-score rather than best/top-k ratio

**Key decisions influenced**
- S3 weight = 0.35 (highest): the only signal independent of imagery quality
- S1 alignment gap weight = 0.25: measures optimizer confidence
- Remaining signals (S2, S4, S5, S6) = 0.10 each
- NaN-safe float conversion: `f != f` is True only for NaN — catches pandas NaN
  that passes through Python's `or 0.0` undetected
- S2 uses z-score = (best - mean) / (std + epsilon), normalised at z=2.0
- Spearman ρ = -0.257 on 6 truth plots is not interpretable; hidden test set
  is the real calibration evaluation

---

## 8. Flagging and Restraint

**Topics covered**
- How to select a threshold without overfitting to 6 truth plots
- What the method_note should communicate to a human reviewer
- Whether 37–42% flagged rate is a problem or a feature

**Key decisions influenced**
- Threshold = 0.50, derived by simulating t=0.2 through t=0.8 on truth plots:
  at t=0.50 all 6 truth plots pass and all improve; at t=0.60 plot 2647
  (best improver, +0.365 IoU) gets flagged — too aggressive
- method_note format: plain English reason, not algorithm description.
  Example: *"global shift (-4.4,+11.4)m + local (-6,+8)m. Corrected: strong
  boundary snap; area matches records. conf=0.88"*
- 419 plots with S3 area consistency < 0.2 are correctly flagged — their recorded
  area is incompatible with the predicted geometry
- 37–42% flagged is the honest answer for this dataset given boundary hint sparsity

---

## 9. Cross-Village Evaluation

**Topics covered**
- What would need to change to run on Malatavadi without tuning
- How to interpret the failure on Malatavadi plot 1177
- What "generalises" actually means for a land records correction system

**Key decisions influenced**
- Global shift auto-estimated from village's own truth plots — no hardcoded values
- Malatavadi run with zero parameter changes. Drift direction completely different
  (dx=+9.6m east vs -4.4m west for Vadnerbhairav) — derived correctly from 3 plots
- Plot 1177 failure documented and explained: actual drift (+0.7m east) is an outlier
  vs village median (+9.6m east); n=3 truth plots insufficient to detect it;
  boundary hints absent in that area. Confidence was incorrectly high (0.657)
  because S3 matched by coincidence
- Failure is a limitation of n=3, not a design flaw. Documented in reports/

---

## 10. README and Submission Preparation

**Topics covered**
- How to structure a README that serves both a 5-minute walkthrough and a
  technical reviewer reading the code
- What failure analysis and limitations sections demonstrate to reviewers
- What the transcripts folder should contain per the contract

**Key decisions influenced**
- README structure: Executive Summary first, then Problem → Data → Observations
  → Approach Evolution → Pipeline → Confidence Philosophy → Results →
  Generalization → Failure Analysis → Limitations → Future Work → How To Run
- Failure analysis written proactively — reviewers will find failures anyway;
  documenting them demonstrates understanding
- Transcripts folder explains how AI was directed, not just that it was used
