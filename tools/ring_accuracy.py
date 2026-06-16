"""LOCAL accuracy test: run forge's deterministic ring weight math on the 112
real rings (true gold_18k_g + dims) and measure error. Then calibrate a head/
setting allowance against ground truth so a dimensions-only estimate reproduces
the true weight. No API / no vision — pure geometry vs catalog truth.

Usage:  /Users/hemang/dev/gtvenv/bin/python tools/ring_accuracy.py
"""
import json
import math
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import weight_estimator as we  # forge's real weight math

D18 = we.alloy_density("18k_yellow_gold")  # 15.6
rings = [r for r in json.load(open(os.path.join(REPO, "ground_truth", "c113_true_dims.json")))
         if not r.get("unreadable") and isinstance(r.get("gold_18k_g"), (int, float)) and r["gold_18k_g"] > 0]


def num(x):
    return float(x) if isinstance(x, (int, float)) and x > 0 else None


def feats(r):
    bw, bt = num(r.get("band_width_mm")), num(r.get("band_thickness_mm"))
    inner = we.us_ring_inner_diameter_mm(r.get("ring_size_us") or "7")
    sl, sw = num(r.get("center_stone_length_mm")), num(r.get("center_stone_width_mm"))
    hh = num(r.get("head_height_mm"))
    return bw, bt, inner, sl, sw, hh, r["gold_18k_g"]


def shank_g(bw, bt, inner, fill):
    return we.volume_to_weight(we.parametric_shank_volume(inner, bw, bt, fill), D18)


def score(name, pred_fn):
    errs, paired = [], []
    for r in rings:
        bw, bt, inner, sl, sw, hh, true = feats(r)
        if not (bw and bt and inner):
            continue
        p = pred_fn(bw, bt, inner, sl, sw, hh)
        if p is None or p <= 0:
            continue
        errs.append((p - true) / true * 100)
        paired.append((p, true))
    a = [abs(e) for e in errs]
    print(f"  {name:34} n={len(a):3d}  median|err| {statistics.median(a):5.1f}%  "
          f"bias {statistics.mean(errs):+6.1f}%  within20 {100*sum(x<=20 for x in a)/len(a):3.0f}%  "
          f"within10 {100*sum(x<=10 for x in a)/len(a):3.0f}%")
    return paired


print(f"=== forge ring-weight accuracy vs {len(rings)} real rings (true gold_18k_g) ===\n")
print("baseline models:")
score("shank only, fill 1.00 (rect)", lambda bw, bt, inner, sl, sw, hh: shank_g(bw, bt, inner, 1.0))
score("shank only, fill 0.85 (solid)", lambda bw, bt, inner, sl, sw, hh: shank_g(bw, bt, inner, 0.85))

# Calibrate a HEAD allowance: total = shank(0.85) + head, head metal modelled as
# k_head * stone_footprint_volume (length x width x head_height). Fit k_head to the
# residual the shank leaves, by least-squares over the rings that have stone dims.
res_x, res_y = [], []
for r in rings:
    bw, bt, inner, sl, sw, hh, true = feats(r)
    if not (bw and bt and inner and sl and sw and hh):
        continue
    shank = shank_g(bw, bt, inner, 0.85)
    foot_vol = sl * sw * hh            # mm^3 envelope of the setting
    res_x.append(foot_vol)
    res_y.append(true - shank)         # grams the head must supply
# least-squares slope through origin: k = sum(xy)/sum(xx), in grams per mm^3 envelope
k_raw = sum(x * y for x, y in zip(res_x, res_y)) / sum(x * x for x in res_x)
# express as an effective FILL of the stone envelope (k_raw vs density)
head_fill = k_raw / (D18 / 1000)
print(f"\ncalibrated head allowance: head_g = {k_raw:.5f} * (stoneL*stoneW*headH)  "
      f"(= {head_fill:.3f} fill of the stone envelope)\n")


def total_pred(bw, bt, inner, sl, sw, hh):
    g = shank_g(bw, bt, inner, 0.85)
    if sl and sw and hh:
        g += k_raw * (sl * sw * hh)
    return g


print("calibrated model:")
paired = score("shank 0.85 + calibrated head", total_pred)

# correlation of prediction vs truth
if len(paired) >= 3:
    P, T = [p for p, t in paired], [t for p, t in paired]
    mp, mt = statistics.mean(P), statistics.mean(T)
    cov = sum((p - mp) * (t - mt) for p, t in paired)
    sp = math.sqrt(sum((p - mp) ** 2 for p in P)); st = math.sqrt(sum((t - mt) ** 2 for t in T))
    print(f"\n  corr(predicted, true) = {cov/(sp*st):+.3f}")

# the data-derived sanity band (for the validator we'll wire into forge)
W = sorted(r["gold_18k_g"] for r in rings)
print(f"\nring sanity band (18k solitaire): "
      f"p05 {W[len(W)//20]:.1f} g  median {statistics.median(W):.1f} g  p95 {W[len(W)*19//20]:.1f} g  "
      f"(full {min(W):.1f}-{max(W):.1f} g)")
print(f"suggested SANITY: warn outside ~{W[len(W)//20]:.1f}-{W[len(W)*19//20]:.1f} g for a solitaire ring")
