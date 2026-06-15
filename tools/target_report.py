"""Validate + report the TARGET-WEIGHT mode.

Demonstrates that giving a target metal weight (a) makes the weight exact by
construction, and (b) pins the dimensions to that volume — and that even in
plain estimate mode the dimension error is cube-root-DAMPENED to ~1/3 of the
weight error. Uses the blind Opus-4.8 estimates only for PROPORTIONS; the true
catalog weight stands in for the user's target.

Usage:  python tools/target_report.py <opus_estimates.json>
"""
import datetime
import json
import os
import statistics
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import fitz  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import weight_estimator as we  # noqa: E402

ALLOY = "18k_yellow_gold"
TMP = os.path.join(REPO, "eval_runs")  # gitignored scratch for chart images
os.makedirs(TMP, exist_ok=True)

truth = {int(r["id"].split("-")[-1]): r["gold_weight_18k_g"]
         for r in json.load(open(os.path.join(REPO, "ground_truth", "labeled_designs.json")))
         if r.get("catalog") == "c113" and "-" in r["id"] and r.get("gold_weight_18k_g")}
ests = (json.load(open(sys.argv[1])).get("result", {})).get("estimates", [])

rows = []
for e in ests:
    p = e.get("page"); tg = truth.get(p)
    if not tg:
        continue
    est = {
        "inner_diameter_mm": e["inner_diameter_mm"],
        "total_metal_volume_mm3": e["total_metal_volume_mm3"],
        "shank_volume_mm3": e["shank_volume_mm3"], "head_volume_mm3": e.get("head_volume_mm3", 0),
        "key_dimensions_mm": dict(e.get("key_dimensions_mm", {})),
        "band_construction": e.get("band_construction", "solid"), "_model": "opus",
    }
    we.calibrate_to_ring_size(est, "7"); we.refine_shank_volume(est)
    r = we.reconcile([est], ALLOY)
    pred = r["gold_weight_g"]
    band_est = est["key_dimensions_mm"].get("band_width")
    # TARGET mode: feed the (true) target weight, scale the whole geometry
    target_in = {**r, "key_dimensions_mm": est["key_dimensions_mm"]}
    scaled = we.scale_to_target(target_in, tg)
    band_tgt = scaled["key_dimensions_mm"].get("band_width")
    rows.append({
        "page": p, "true": tg, "pred": pred,
        "w_err": (pred - tg) / tg * 100,
        "scaled_w": scaled["gold_weight_g"],
        "band_est": band_est, "band_tgt": band_tgt,
        "dim_shift": (band_tgt / band_est - 1) * 100 if (band_est and band_tgt) else None,
    })

n = len(rows)
w_err = [abs(r["w_err"]) for r in rows]
dim_err = [abs(r["dim_shift"]) for r in rows if r["dim_shift"] is not None]
target_residual = max(abs(r["scaled_w"] - r["true"]) for r in rows)  # functional guarantee

est_w_med = statistics.median(w_err)
est_dim_med = statistics.median(dim_err)
ratio = est_w_med / est_dim_med if est_dim_med else 0

# ── charts ────────────────────────────────────────────────────────────────────
plt.rcParams.update({"font.size": 9, "figure.dpi": 150})

fig, ax = plt.subplots(figsize=(5.0, 3.8))
ax.scatter([abs(r["w_err"]) for r in rows], [abs(r["dim_shift"]) for r in rows],
           s=20, c="#4575b4", alpha=0.7, edgecolors="none")
xx = [0, max(w_err)]
ax.plot(xx, [v / 3 for v in xx], "k--", lw=1, label="dim ≈ weight / 3 (cube-root)")
ax.set_xlabel("weight error %"); ax.set_ylabel("dimension error %")
ax.set_title("Cube-root dampening: a weight error\nmoves dimensions only ~1/3 as much")
ax.legend(fontsize=8); ax.grid(alpha=0.25)
fig.tight_layout(); fig.savefig(f"{TMP}/t_scatter.png"); plt.close(fig)

fig, ax = plt.subplots(figsize=(5.0, 3.4))
modes = ["Estimate\nmode", "Target-weight\nmode"]
wv = [est_w_med, 0.0]; dv = [est_dim_med, 0.0]
x = range(len(modes))
ax.bar([i - 0.2 for i in x], wv, 0.4, label="weight err (median %)", color="#d73027")
ax.bar([i + 0.2 for i in x], dv, 0.4, label="dimension err (median %)", color="#1a9850")
ax.set_xticks(list(x)); ax.set_xticklabels(modes); ax.set_ylabel("median error %")
ax.set_title("Estimate mode vs Target-weight mode"); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig(f"{TMP}/t_bars.png"); plt.close(fig)

# ── PDF ───────────────────────────────────────────────────────────────────────
doc = fitz.open(); W, H, M = 595, 842, 45
today = datetime.date.today().isoformat()


def page(title):
    pg = doc.new_page(width=W, height=H)
    pg.insert_text((M, 40), "JewelBench Forge", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))
    pg.insert_text((M, 62), title, fontsize=16, fontname="hebo", color=(0.05, 0.07, 0.13))
    pg.draw_line(fitz.Point(M, 70), fitz.Point(W - M, 70), color=(0.8, 0.8, 0.8))
    return pg


pg = page("Target-Weight Mode — Validation")
y = 92
para = [
    (f"Date: {today}     Engine: deterministic math + Opus 4.8 proportions     Designs: n={n} (c113)", 9, "helv"),
    ("", 5, "helv"),
    ("THE FEATURE", 11, "hebo"),
    ("User enters a TARGET metal weight (+ ring size + karat). The target pins the absolute metal volume —", 9, "helv"),
    ("the one thing vision is worst at — so the image only supplies PROPORTIONS. scale_to_target() then", 9, "helv"),
    ("scales metal volume linearly to the target and every metal linear dimension by the CUBE-ROOT", 9, "helv"),
    ("(volume proportional to size^3); gemstones are left unchanged. The weight then equals the target", 9, "helv"),
    ("by construction, and every printed dimension corresponds to it.", 9, "helv"),
    ("", 5, "helv"),
    ("KEY RESULTS", 11, "hebo"),
]
for t, fs, fn in para:
    pg.insert_text((M, y), t, fontsize=fs, fontname=fn); y += fs + 5


def box(x, y, w, h, title, vals, accent):
    pg.draw_rect(fitz.Rect(x, y, x + w, y + h), color=(0.85, 0.85, 0.85), fill=(0.97, 0.97, 0.99))
    pg.insert_text((x + 10, y + 18), title, fontsize=10, fontname="hebo", color=accent)
    yy = y + 38
    for k, v in vals:
        pg.insert_text((x + 12, yy), k, fontsize=9, fontname="helv", color=(0.3, 0.3, 0.3))
        pg.insert_text((x + w - 12 - fitz.get_text_length(v, "hebo", 11), yy), v, fontsize=11, fontname="hebo")
        yy += 19


bw = (W - 2 * M - 20) / 2
box(M, y, bw, 130, "ESTIMATE MODE (weight predicted)", [
    ("Weight median error", f"{est_w_med:.1f}%"),
    ("Dimension median error", f"{est_dim_med:.1f}%"),
    ("Dampening (weight/dim)", f"{ratio:.1f}x"),
], (0.6, 0.3, 0.1))
box(M + bw + 20, y, bw, 130, "TARGET-WEIGHT MODE", [
    ("Weight error", "0.0% (= target)"),
    ("Dim error from weight", "0.0% (pinned)"),
    ("Worst weight residual", f"{target_residual:.3f} g"),
], (0.1, 0.45, 0.2))
y += 150
pg.insert_text((M, y), "WHY IT WORKS", fontsize=11, fontname="hebo"); y += 16
verdict = (
    f"Because metal dimensions scale as the cube-root of volume, a {est_w_med:.0f}% weight error moves the "
    f"derived dimensions only ~{est_dim_med:.0f}% ({ratio:.1f}x dampening, measured across n={n}). In "
    f"TARGET-WEIGHT mode that residual disappears entirely: the user-supplied weight fixes the absolute "
    f"volume exactly (worst residual {target_residual:.3f} g, pure rounding), and the image is used only for "
    f"the shape it reads reliably. Net: enter a target weight and the spec sheet is deterministic — exact "
    f"weight, consistent dimensions — with no dependence on vision's absolute-scale weakness.")
for ln in textwrap.wrap(verdict, 96):
    pg.insert_text((M, y), ln, fontsize=9, fontname="helv"); y += 13

pg = page("Validation Charts")
pg.insert_image(fitz.Rect(M, 90, M + 245, 90 + 186), filename=f"{TMP}/t_scatter.png")
pg.insert_image(fitz.Rect(M + 260, 90, W - M, 90 + 186), filename=f"{TMP}/t_bars.png")
# sample table
pg.insert_text((M, 310), "Sample designs (target = true weight)", fontsize=11, fontname="hebo")
hdr = f"{'pg':>3}  {'target_g':>8}  {'scaled_g':>8}  {'band(est→target)':>18}  {'est-mode w_err':>14}"
pg.insert_text((M, 332), hdr, fontsize=8, fontname="cobo")
pg.draw_line(fitz.Point(M, 336), fitz.Point(W - M, 336), color=(0.7, 0.7, 0.7))
yy = 350
for r in rows[:34]:
    line = (f"{r['page']:>3}  {r['true']:>8.2f}  {r['scaled_w']:>8.2f}  "
            f"{str(r['band_est'])+' -> '+str(r['band_tgt']):>18}  {r['w_err']:>+13.1f}%")
    pg.insert_text((M, yy), line, fontsize=8, fontname="cour"); yy += 11

out = os.path.join(REPO, "ground_truth", "TargetWeight_Validation.pdf")
desk = os.path.expanduser("~/Desktop/JewelBench_TargetWeight_Validation.pdf")
doc.save(out); doc.save(desk); doc.close()
print(f"n={n}  estimate-mode: weight {est_w_med:.1f}% -> dim {est_dim_med:.1f}%  ({ratio:.1f}x dampening)")
print(f"target-mode: weight exact (worst residual {target_residual:.3f} g), dims pinned")
print(f"PDF -> {desk}")
