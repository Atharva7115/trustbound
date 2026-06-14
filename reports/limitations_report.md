# Limitations Report

## 1. Only 6 public truth plots
Global shift is reliable; local drift surface is not feasible.  
Nearest truth-to-truth: 504m–2188m across a 7.8×7.9km village.  
Spearman calibration at n=6 has no statistical power.

## 2. Boundary hints cover only 5.2% of pixels
Many plots have zero detected edges. Local refinement defaults to no extra correction.

## 3. Vegetation, buildings, shadows
Sobel responds to any contrast. Perimeter band approach mitigates but does not eliminate.

## 4. Area record inconsistencies
433 plots (17.6%) have S3 < 0.2.  
May reflect genuine record errors in 7/12 registers, not alignment failure.

## 5. Conservative neighbourhood model
1214 anchors across a large village. S7 weight kept at 0.08 intentionally.

## 6. Translation-only correction
Rotation and local distortion are not modelled.  
x-spread of 17m in truth drifts suggests some rotation — partially handled by local snap.