"""Dimension calibration + scorecard from the catalog's labeled true dims.

Grades the model's *dimension* estimates (band width/thickness, head height)
against the catalog's printed truth, and fits the proportional constants the
derivation falls back on (head:center ratio, band width:thickness ratio).

Usage:  python tools/dim_calibrate.py <dim_extract_output.json>
"""
import datetime
import json
import os
import statistics
import sys

import fitz  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def acc(est, true):
    """median |%err| and signed bias% for paired estimates vs truth."""
    pairs = [(e, t) for e, t in zip(est, true) if e and t]
    if not pairs:
        return None
    errs = [(e - t) / t * 100 for e, t in pairs]
    return {"n": len(pairs), "median_abs": statistics.median([abs(x) for x in errs]),
            "bias": statistics.mean(errs)}


# true dims (extraction) keyed by page
ext = (json.load(open(sys.argv[1])).get("result", {})).get("records", [])
true = {r["page"]: r for r in ext if not r.get("unreadable")}

# model proportions (estimates from the blind-estimator workflow); pass as argv[2]
# or drop them at eval_runs/estimates.json
poc_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "eval_runs", "estimates.json")
poc = (json.load(open(poc_path)).get("result", {})).get("estimates", [])
est = {e["page"]: e for e in poc}

# my labeled-weight dataset, to sanity-check the extraction
wtruth = {int(r["id"].split("-")[-1]): r["gold_weight_18k_g"]
          for r in json.load(open(os.path.join(REPO, "ground_truth", "labeled_designs.json")))
          if r.get("catalog") == "c113" and "-" in r["id"]}

pages = sorted(set(true) & set(est))
bw_e = [est[p].get("key_dimensions_mm", {}).get("band_width") for p in pages]
bw_t = [true[p].get("band_width_mm") for p in pages]
bt_e = [est[p].get("key_dimensions_mm", {}).get("band_thickness") for p in pages]
bt_t = [true[p].get("band_thickness_mm") for p in pages]
hh_e = [est[p].get("key_dimensions_mm", {}).get("head_height") for p in pages]
hh_t = [true[p].get("head_height_mm") for p in pages]

# fitted constants from TRUE data
head_center_ratio = med([true[p]["head_height_mm"] / true[p]["center_stone_length_mm"]
                         for p in pages
                         if true[p].get("head_height_mm") and true[p].get("center_stone_length_mm")])
band_wt_ratio = med([true[p]["band_width_mm"] / true[p]["band_thickness_mm"]
                     for p in pages
                     if true[p].get("band_width_mm") and true[p].get("band_thickness_mm")])
# extraction sanity: do the extracted weights match my dataset?
wmatch = [abs(true[p]["gold_18k_g"] - wtruth.get(p)) for p in pages
          if true[p].get("gold_18k_g") and wtruth.get(p)]

A = {"band_width": acc(bw_e, bw_t), "band_thickness": acc(bt_e, bt_t), "head_height": acc(hh_e, hh_t)}

print(f"paired designs: {len(pages)}")
for k, v in A.items():
    if v:
        print(f"  {k:14}: model median |err| {v['median_abs']:.1f}%  (bias {v['bias']:+.1f}%, n={v['n']})")
print(f"fitted head:center ratio = {head_center_ratio:.2f}  (code constant 1.10)" if head_center_ratio else "")
print(f"fitted band W:T ratio    = {band_wt_ratio:.2f}  (code constant 1.30)" if band_wt_ratio else "")
if wmatch:
    print(f"extraction sanity: weight match median {statistics.median(wmatch):.2f} g over n={len(wmatch)}")

# ── PDF ───────────────────────────────────────────────────────────────────────
doc = fitz.open(); W, H, M = 595, 842, 45
pg = doc.new_page(width=W, height=H)
pg.insert_text((M, 40), "JewelBench Forge", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))
pg.insert_text((M, 62), "Dimension Calibration & Scorecard", fontsize=16, fontname="hebo")
pg.draw_line(fitz.Point(M, 70), fitz.Point(W - M, 70), color=(0.8, 0.8, 0.8))
y = 96
pg.insert_text((M, y), f"Date: {datetime.date.today().isoformat()}   "
               f"Paired designs: {len(pages)}   Source: c113 catalog printed dims vs model estimates",
               fontsize=9, fontname="helv"); y += 24
pg.insert_text((M, y), "MODEL DIMENSION ACCURACY (estimate vs printed true)", fontsize=11, fontname="hebo"); y += 20
pg.insert_text((M, y), f"{'dimension':<16}{'median |err|':>14}{'bias':>10}{'n':>6}", fontsize=9, fontname="cobo"); y += 14
for k, v in A.items():
    if v:
        pg.insert_text((M, y), f"{k:<16}{v['median_abs']:>13.1f}%{v['bias']:>+9.1f}%{v['n']:>6}",
                       fontsize=9, fontname="cour"); y += 13
y += 14
pg.insert_text((M, y), "FITTED PROPORTIONAL CONSTANTS (from true catalog dims)", fontsize=11, fontname="hebo"); y += 20
for label, fit, cur in [("head_height : center_stone", head_center_ratio, 1.10),
                        ("band width : thickness", band_wt_ratio, 1.30)]:
    if fit:
        pg.insert_text((M, y), f"{label:<30} fitted x{fit:.2f}   (current code x{cur:.2f})",
                       fontsize=9, fontname="cour"); y += 14
y += 16
pg.insert_text((M, y), "READ", fontsize=11, fontname="hebo"); y += 16
note = ("The model supplies band/head dimensions DIRECTLY, so these constants are only fallbacks; the "
        "scorecard above shows how trustworthy each published dimension is. Update the code constants to "
        "the fitted values, and treat any dimension whose median error is high as 'estimate' on the spec "
        "sheet. In target-weight mode the absolute sizes are pinned to the weight, so only these "
        "proportions (not absolute scale) drive dimension error.")
import textwrap
for ln in textwrap.wrap(note, 96):
    pg.insert_text((M, y), ln, fontsize=9, fontname="helv"); y += 13
out = os.path.join(REPO, "ground_truth", "Dimension_Calibration.pdf")
desk = os.path.expanduser("~/Desktop/JewelBench_Dimension_Calibration.pdf")
doc.save(out); doc.save(desk); doc.close()
print(f"PDF -> {desk}")
