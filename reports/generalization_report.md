# Generalization Report

No village-specific hardcoded values. Every parameter is derived from input data.

## How each component generalises

### Global shift
Median centroid displacement across available truth plots (UTM, robust to outliers).  
This village: dx=-4.40m dy=+11.35m from 6 samples.

### Local alignment
Grid search ±16m (1.5× max residual after global shift).  
Perimeter band scoring — no imagery-specific thresholds.

### Image gradient
Sobel magnitude normalised by local 95th-percentile — adapts to any contrast level.

### Confidence
- S3 area: uses recorded area from `input.geojson` — no external reference needed.
- S4 boundary visibility: normalised by village-wide median edge density — auto-calibrates.
- S6 global reliability: derived from drift spread — lower for inconsistent villages.

### Neighbourhood
IDW from 1214 high-confidence anchors. Falls back to global shift when anchors are sparse.

### Flagging threshold
Default 0.5 selected by simulation. Overridable via `--threshold` argument.

## What would need changing for a different village

- UTM zone (hardcoded EPSG:32643 for Maharashtra).
- Search radius if cadastral errors are much larger than 10m.
- Area tolerance (±30%) if record quality is very different.