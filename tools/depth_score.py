"""Score depth measured from the multi-view profiles vs catalog truth, and
compare to the single-view baseline (head ~33% / thickness ~15%).

Usage:  python tools/depth_score.py <depth_measure_output.json>
"""
import json
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
true = {r["page"]: r for r in json.load(open(os.path.join(REPO, "ground_truth", "c113_true_dims.json")))
        if not r.get("unreadable")}
meas = (json.load(open(sys.argv[1])).get("result", {})).get("measurements", [])


def grade(model_key, true_key, label, baseline):
    pr = []
    for m in meas:
        t = true.get(m.get("page"))
        if not t:
            continue
        mv, tv = m.get(model_key), t.get(true_key)
        if isinstance(mv, (int, float)) and isinstance(tv, (int, float)) and mv > 0 and tv > 0:
            pr.append((mv, tv))
    if not pr:
        print(f"{label}: no pairs"); return
    errs = [(a - b) / b * 100 for a, b in pr]
    a = [abs(x) for x in errs]
    print(f"{label:16}: multi-view median |err| {statistics.median(a):4.1f}%  "
          f"bias {statistics.mean(errs):+5.1f}%  within20 {100*sum(x<=20 for x in a)/len(a):3.0f}%  "
          f"(single-view baseline {baseline})  n={len(pr)}")


print(f"depth measured from the real multi-view profiles ({len(meas)} designs):\n")
grade("head_height_mm", "head_height_mm", "Head height", "32.8% / -30%")
grade("band_thickness_mm", "band_thickness_mm", "Band thickness", "14.7% / -16%")
grade("band_width_mm", "band_width_mm", "Band width", "7.7% / -5%")
