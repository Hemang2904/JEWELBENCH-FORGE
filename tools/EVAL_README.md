# Weight / dimension evaluation toolkit

Scripts for measuring estimator accuracy against a labeled ground-truth catalog.
All proprietary data (images, weights) and scratch outputs live under the
gitignored `ground_truth/` and `eval_runs/` dirs — only the scripts are tracked.

## Inputs (you provide, kept local)
- `ground_truth/labeled_designs.json` — `[{catalog,id,gold_weight_18k_g,...}]`
- `ground_truth/images/<catalog>/page_NNN.png` — one render per design
- `.env` with `FAL_KEY` (only `run_eval.py` needs it; it calls the live models)

## Scripts
| Script | What it does |
|---|---|
| `run_eval.py` | Runs the **live** estimator on the labeled images and reports MAE / median% / within-10/20%. Staged: `--smoke` (1), `--limit N`, full. Needs `FAL_KEY`. |
| `poc_score.py <estimates.json>` | Scores blind geometry estimates (no live calls) through the deterministic math vs truth. |
| `poc_report.py <estimates.json>` | Detailed weight-validation **PDF** (summary + charts + exact per-design diff), with honest leave-one-out de-biasing. |
| `target_report.py <estimates.json>` | Validates **target-weight mode** — exact weight + cube-root dimension dampening — as a PDF. |
| `dim_calibrate.py <true_dims.json> [estimates.json]` | Grades the model's *dimension* estimates vs the catalog and fits the proportional constants (`head:center`, band `W:T`); writes a PDF scorecard. |

`<estimates.json>` is `{"result": {"estimates": [{page, total_metal_volume_mm3,
key_dimensions_mm, ...}]}}` — the structured output of the blind-estimator pass.
Reports are written to `ground_truth/*.pdf` and `~/Desktop/`.
