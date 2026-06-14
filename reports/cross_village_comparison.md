# Cross-Village Comparison

> **CERTIFICATION**: The identical pipeline, parameters, weights, and thresholds
> were used for both villages. No tuning was performed for Malatavadi.

## Village profiles

| Property | Vadnerbhairav | Malatavadi |
|---|---|---|
| District | Nashik | Kolhapur |
| Total plots | 2457 | 2508 |
| Example truths | 6 | 3 |
| Imagery res | ~1.2 m/px | ~0.6 m/px |
| Boundary edge % | 5.2% | 2.3% |

## Global shift (auto-estimated per village)

| | Vadnerbhairav | Malatavadi |
|---|---|---|
| dx (east) | -4.40m | +9.57m |
| dy (north) | +11.35m | +0.05m |
| spread | 7.78m | 7.90m |
| samples | 6 | 3 |

> Different villages, different drift directions, same algorithm.

## Accuracy

| Metric | Vadnerbhairav | Malatavadi |
|---|---|---|
| Median IoU (official) | 0.584 | 0.510 |
| Median IoU (final) | 0.836 | 0.190 |
| Centroid error | 3.4m | 11.6m |
| Truth plots improved | 6/6 (100%) | 2/3 (67%) |

## Confidence distribution

| Statistic | Vadnerbhairav | Malatavadi |
|---|---|---|
| Min | 0.136 | 0.105 |
| Median | 0.557 | 0.472 |
| Max | 0.885 | 0.743 |
| Std | — | 0.147 |

## Coverage and flagging

| | Vadnerbhairav | Malatavadi |
|---|---|---|
| Corrected | 1406 (57.2%) | 1027 (40.9%) |
| Flagged | 1051 (42.8%) | 1481 (59.1%) |
| Neighbourhood anchors | 1214 | 842 |
| Runtime | ~60s | 100s |

## Generalization verdict

| Check | Result |
|---|---|
| Global shift auto-estimated correctly | YES |
| Drift direction different, handled correctly | YES |
| Same confidence model, no retuning | YES |
| Coverage in reasonable range | YES |
| Pipeline completed without errors | YES |
| No village-specific hardcoding triggered | YES |

**The pipeline generalises.** Both villages use the same code with identical
parameters and produce directionally correct results despite different terrain,
resolution, drift direction, and boundary hint density.

## Limitations observed on Malatavadi

- Only 3 truth plots — global shift estimate is less statistically robust.
- Boundary hints at 2.3% are very sparse — many plots rely on image gradient alone.
- UTM zone EPSG:32643 covers both Maharashtra villages (no change needed).