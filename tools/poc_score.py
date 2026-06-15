"""Score the Opus-4.8 POC: feed each blind geometry estimate through the real
deterministic math (calibrate -> refine shank -> volume*density*casting) and
compare the predicted 18k weight to the true catalog weight.

Usage:  python tools/poc_score.py <workflow_output.json>
"""
import json
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import weight_estimator as we  # noqa: E402

ALLOY = "18k_yellow_gold"

# true 18k weights from the labeled dataset, keyed by page
truth = {}
for r in json.load(open(os.path.join(REPO, "ground_truth", "labeled_designs.json"))):
    if r.get("catalog") == "c113" and "-" in r["id"]:
        truth[int(r["id"].split("-")[-1])] = r.get("gold_weight_18k_g")

data = json.load(open(sys.argv[1]))
ests = data.get("result", data).get("estimates", [])

rows, pcts, errs = [], [], []
print(f"{'page':>4} {'true_g':>7} {'pred_g':>7} {'err%':>7} {'vol_mm3':>8}  band(WxT)  constr")
print("-" * 70)
for e in sorted(ests, key=lambda x: x.get("page", 0)):
    p = e["page"]
    true_g = truth.get(p)
    est = {  # shape it the way the math functions expect
        "inner_diameter_mm": e["inner_diameter_mm"],
        "total_metal_volume_mm3": e["total_metal_volume_mm3"],
        "shank_volume_mm3": e["shank_volume_mm3"],
        "head_volume_mm3": e.get("head_volume_mm3", 0),
        "key_dimensions_mm": dict(e.get("key_dimensions_mm", {})),
        "band_construction": e.get("band_construction", "solid"),
        "_model": "opus-4.8(max)",
    }
    we.calibrate_to_ring_size(est, "7")   # inner==17.32 -> scale 1.0 (no-op anchor)
    we.refine_shank_volume(est)           # shank from closed-form band dims
    r = we.reconcile([est], ALLOY)
    pred = r["gold_weight_g"]
    kd = est["key_dimensions_mm"]
    err = (pred - true_g) / true_g * 100 if true_g else None
    if true_g:
        pcts.append(abs(err)); errs.append(abs(pred - true_g))
    print(f"{p:>4} {true_g:>7.2f} {pred:>7.2f} {err:>+6.1f}% {r['volume_mm3']:>8.0f}  "
          f"{kd.get('band_width','?')}x{kd.get('band_thickness','?')}  {est['band_construction']}")
    rows.append({"page": p, "true_g": true_g, "pred_g": pred, "err_pct": err})

print("-" * 70)
if pcts:
    print(f"n={len(pcts)}  MAE={statistics.mean(errs):.2f}g  "
          f"median|err|={statistics.median(pcts):.1f}%  mean|err|={statistics.mean(pcts):.1f}%  "
          f"within10%={100*sum(x<=10 for x in pcts)/len(pcts):.0f}%  "
          f"within20%={100*sum(x<=20 for x in pcts)/len(pcts):.0f}%")
json.dump(rows, open(os.path.join(REPO, "ground_truth", "poc_score.json"), "w"), indent=2)
