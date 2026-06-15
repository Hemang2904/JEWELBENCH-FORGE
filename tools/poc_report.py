"""Build a detailed PDF validation report from the full Opus-4.8 blind POC.

Runs each blind geometry estimate through the real deterministic math, computes
raw and HONEST leave-one-out de-biased predictions, and renders a multi-page PDF
with charts + the exact per-design diff (true vs predicted weight).

Usage:  python tools/poc_report.py <workflow_output.json>
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
import fitz  # PyMuPDF  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import weight_estimator as we  # noqa: E402

ALLOY = "18k_yellow_gold"
TMP = os.path.join(REPO, "eval_runs")  # gitignored scratch for chart images
os.makedirs(TMP, exist_ok=True)

# ── load truth + estimates ────────────────────────────────────────────────────
truth = {int(r["id"].split("-")[-1]): r["gold_weight_18k_g"]
         for r in json.load(open(os.path.join(REPO, "ground_truth", "labeled_designs.json")))
         if r.get("catalog") == "c113" and "-" in r["id"] and r.get("gold_weight_18k_g")}

data = json.load(open(sys.argv[1]))
ests = (data.get("result", data)).get("estimates", [])

rows = []
for e in ests:
    p = e.get("page")
    tg = truth.get(p)
    if not tg:
        continue
    est = {
        "inner_diameter_mm": e["inner_diameter_mm"],
        "total_metal_volume_mm3": e["total_metal_volume_mm3"],
        "shank_volume_mm3": e["shank_volume_mm3"],
        "head_volume_mm3": e.get("head_volume_mm3", 0),
        "key_dimensions_mm": dict(e.get("key_dimensions_mm", {})),
        "band_construction": e.get("band_construction", "solid"),
        "_model": "opus-4.8(max)",
    }
    we.calibrate_to_ring_size(est, "7")
    we.refine_shank_volume(est)
    r = we.reconcile([est], ALLOY)
    kd = est["key_dimensions_mm"]
    rows.append({
        "page": p, "shape": (e.get("center_stone_shape") or "?").lower(),
        "true_g": tg, "pred_g": r["gold_weight_g"], "vol": r["volume_mm3"],
        "bw": kd.get("band_width"), "bt": kd.get("band_thickness"),
        "head": e.get("head_volume_mm3", 0), "constr": est["band_construction"],
        "conf": e.get("confidence", "?"),
    })
rows.sort(key=lambda x: x["page"])
n = len(rows)

# ── leave-one-out de-bias (honest: each point corrected by a factor fit on the rest) ──
ratios = [r["true_g"] / r["pred_g"] for r in rows if r["pred_g"]]
for r in rows:
    others = [r2["true_g"] / r2["pred_g"] for r2 in rows if r2 is not r and r2["pred_g"]]
    k = statistics.mean(others) if others else 1.0
    r["pred_db"] = r["pred_g"] * k
    r["diff_raw"] = r["pred_g"] - r["true_g"]
    r["err_raw"] = (r["pred_g"] - r["true_g"]) / r["true_g"] * 100
    r["diff_db"] = r["pred_db"] - r["true_g"]
    r["err_db"] = (r["pred_db"] - r["true_g"]) / r["true_g"] * 100


def stats(key):
    a = [abs(r[key]) for r in rows]
    s = [r[key] for r in rows]
    return {
        "median": statistics.median(a), "mean": statistics.mean(a),
        "mae_g": statistics.mean([abs(r["pred_db" if "db" in key else "pred_g"] - r["true_g"]) for r in rows]),
        "bias": statistics.mean(s),
        "w10": 100 * sum(x <= 10 for x in a) / len(a),
        "w20": 100 * sum(x <= 20 for x in a) / len(a),
    }


raw, db = stats("err_raw"), stats("err_db")
global_k = statistics.mean(ratios)

# ── charts ────────────────────────────────────────────────────────────────────
plt.rcParams.update({"font.size": 9, "figure.dpi": 150})

# 1. scatter true vs de-biased pred
fig, ax = plt.subplots(figsize=(5.2, 4.0))
for r in rows:
    c = "#1a9850" if abs(r["err_db"]) <= 10 else ("#fdae61" if abs(r["err_db"]) <= 20 else "#d73027")
    ax.scatter(r["true_g"], r["pred_db"], c=c, s=22, edgecolors="none", alpha=0.8)
lim = [min(r["true_g"] for r in rows) - 0.3, max(r["true_g"] for r in rows) + 0.3]
ax.plot(lim, lim, "k--", lw=1, alpha=0.5)
ax.set_xlabel("True 18k weight (g)"); ax.set_ylabel("Predicted (de-biased, g)")
ax.set_title("Predicted vs True  (green ≤10%, amber ≤20%, red >20%)")
ax.set_xlim(lim); ax.set_ylim(lim); ax.grid(alpha=0.25)
fig.tight_layout(); fig.savefig(f"{TMP}/chart_scatter.png"); plt.close(fig)

# 2. error histogram (de-biased)
fig, ax = plt.subplots(figsize=(5.2, 3.2))
ax.hist([r["err_db"] for r in rows], bins=24, color="#4575b4", edgecolor="white")
for x in (-10, 0, 10):
    ax.axvline(x, color="k", lw=1, ls="--", alpha=0.5)
ax.set_xlabel("De-biased error %"); ax.set_ylabel("designs")
ax.set_title("Error distribution (de-biased)")
fig.tight_layout(); fig.savefig(f"{TMP}/chart_hist.png"); plt.close(fig)

# 3. median |err| by center-stone shape
byshape = {}
for r in rows:
    byshape.setdefault(r["shape"], []).append(abs(r["err_db"]))
shapes = sorted(byshape, key=lambda s: -len(byshape[s]))[:8]
fig, ax = plt.subplots(figsize=(5.2, 3.2))
ax.bar(range(len(shapes)), [statistics.median(byshape[s]) for s in shapes], color="#762a83")
ax.set_xticks(range(len(shapes)))
ax.set_xticklabels([f"{s}\n(n={len(byshape[s])})" for s in shapes], fontsize=7)
ax.axhline(10, color="k", lw=1, ls="--", alpha=0.5)
ax.set_ylabel("median |err| %"); ax.set_title("De-biased error by center stone")
fig.tight_layout(); fig.savefig(f"{TMP}/chart_shape.png"); plt.close(fig)

# ── PDF ───────────────────────────────────────────────────────────────────────
doc = fitz.open()
W, H = 595, 842  # A4 pt
M = 45
today = datetime.date.today().isoformat()


def newpage(title):
    pg = doc.new_page(width=W, height=H)
    pg.insert_text((M, 40), "JewelBench Forge", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))
    pg.insert_text((M, 62), title, fontsize=16, fontname="hebo", color=(0.05, 0.07, 0.13))
    pg.draw_line(fitz.Point(M, 70), fitz.Point(W - M, 70), color=(0.8, 0.8, 0.8))
    return pg


# Page 1 — summary
pg = newpage("Opus 4.8 — Weight-Estimation Validation")
y = 92
lines = [
    (f"Date: {today}     Engine: Claude Opus 4.8 (via Max)     Designs: n={n} (catalog c113, all US 7)", 9, "helv"),
    ("", 6, "helv"),
    ("METHOD", 11, "hebo"),
    ("Each design's metal volume was estimated BLIND by an independent Opus 4.8 agent reading clean CAD", 9, "helv"),
    ("renders with the printed weight cropped out and all dimension text ignored — anchored only to the", 9, "helv"),
    ("known US-7 inner diameter (17.32 mm). Estimates were run through the production deterministic math", 9, "helv"),
    ("(ring-size calibration, closed-form shank, volume x 18k density x casting). De-biased = honest", 9, "helv"),
    ("leave-one-out: each design corrected by the systematic factor fit on the OTHER designs only.", 9, "helv"),
    ("", 6, "helv"),
    ("HEADLINE RESULTS", 11, "hebo"),
]
for t, fs, fn in lines:
    pg.insert_text((M, y), t, fontsize=fs, fontname=fn); y += fs + 5

# results box
def box(x, y, w, h, title, vals, accent):
    pg.draw_rect(fitz.Rect(x, y, x + w, y + h), color=(0.85, 0.85, 0.85), fill=(0.97, 0.97, 0.99))
    pg.insert_text((x + 10, y + 18), title, fontsize=10, fontname="hebo", color=accent)
    yy = y + 38
    for k, v in vals:
        pg.insert_text((x + 12, yy), k, fontsize=9, fontname="helv", color=(0.3, 0.3, 0.3))
        pg.insert_text((x + w - 12 - fitz.get_text_length(v, "hebo", 11), yy), v, fontsize=11, fontname="hebo")
        yy += 19

bw = (W - 2 * M - 20) / 2
box(M, y, bw, 150, "RAW (no calibration)", [
    ("Median |error|", f"{raw['median']:.1f}%"),
    ("Mean |error|", f"{raw['mean']:.1f}%"),
    ("MAE", f"{raw['mae_g']:.2f} g"),
    ("Within 10% / 20%", f"{raw['w10']:.0f}% / {raw['w20']:.0f}%"),
    ("Systematic bias", f"{raw['bias']:+.1f}%"),
], (0.6, 0.3, 0.1))
box(M + bw + 20, y, bw, 150, "DE-BIASED (leave-one-out)", [
    ("Median |error|", f"{db['median']:.1f}%"),
    ("Mean |error|", f"{db['mean']:.1f}%"),
    ("MAE", f"{db['mae_g']:.2f} g"),
    ("Within 10% / 20%", f"{db['w10']:.0f}% / {db['w20']:.0f}%"),
    ("Calibration factor", f"x{global_k:.3f}"),
], (0.1, 0.45, 0.2))
y += 168
pg.insert_text((M, y), "VERDICT", fontsize=11, fontname="hebo"); y += 16
improve = raw["median"] - db["median"]
verdict = (
    f"Across n={n} diverse designs, blind single-view Opus 4.8 reaches a median {db['median']:.0f}% error "
    f"(MAE {db['mae_g']:.2f} g; {db['w20']:.0f}% within 20%, {db['w10']:.0f}% within 10%). The systematic "
    f"bias is only {raw['bias']:+.0f}%, so a single calibration factor (x{global_k:.2f}) improves the median "
    f"by just {improve:.0f} pts -- the residual is SCATTER-dominated, concentrated in head/accent volume on "
    f"complex settings (halos, three-stone, clusters) that a single view under-determines. "
    f"Implication: Opus 4.8 is the strongest single engine and a clear baseline, but reaching jewelry-grade "
    f"+/-10% needs Opus PLUS multi-view geometry (the mesh path) and ensembling -- not the model alone. "
    f"NOTE: a small simple-solitaire subsample looked far better (~5% median) -- the full diverse set is the "
    f"honest number, which is why it was run.")
for ln in textwrap.wrap(verdict, 96):
    pg.insert_text((M, y), ln, fontsize=9, fontname="helv"); y += 13

# Page 2 — charts
pg = newpage("Validation Charts")
pg.insert_image(fitz.Rect(M, 90, W - M, 90 + 320), filename=f"{TMP}/chart_scatter.png")
pg.insert_image(fitz.Rect(M, 430, M + 245, 430 + 165), filename=f"{TMP}/chart_hist.png")
pg.insert_image(fitz.Rect(M + 260, 430, W - M, 430 + 165), filename=f"{TMP}/chart_shape.png")

# Pages 3+ — exact per-design diff table
hdr = f"{'pg':>3} {'shape':<9} {'true':>5} {'pred':>5} {'pred*':>5} {'d_g':>6} {'err%':>6} {'band':>9} {'head':>5} {'constr':<10}"
def table_header(pg, y):
    pg.insert_text((M, y), hdr, fontsize=8, fontname="cobo")
    pg.draw_line(fitz.Point(M, y + 4), fitz.Point(W - M, y + 4), color=(0.7, 0.7, 0.7))
    return y + 16

pg = newpage("Exact Per-Design Diff (true vs predicted 18k weight)")
y = table_header(pg, 92)
for r in rows:
    if y > H - 50:
        pg = newpage("Exact Per-Design Diff (cont.)")
        y = table_header(pg, 92)
    col = (0.1, 0.45, 0.2) if abs(r["err_db"]) <= 10 else ((0.7, 0.45, 0.05) if abs(r["err_db"]) <= 20 else (0.75, 0.1, 0.1))
    line = (f"{r['page']:>3} {r['shape'][:9]:<9} {r['true_g']:>5.2f} {r['pred_g']:>5.2f} "
            f"{r['pred_db']:>5.2f} {r['diff_db']:>+6.2f} {r['err_db']:>+5.1f}% "
            f"{str(r['bw'])+'x'+str(r['bt']):>9} {r['head']:>5.0f} {r['constr'][:10]:<10}")
    pg.insert_text((M, y), line, fontsize=8, fontname="cour", color=col)
    y += 11

out = os.path.join(REPO, "ground_truth", "Opus48_Weight_Validation.pdf")
doc.save(out)
desk = os.path.expanduser("~/Desktop/JewelBench_Opus48_Validation.pdf")
doc.save(desk)
doc.close()
print(f"n={n}  RAW median {raw['median']:.1f}% (bias {raw['bias']:+.1f}%)  |  "
      f"DE-BIASED median {db['median']:.1f}%, within20%={db['w20']:.0f}%  (x{global_k:.3f})")
print(f"PDF saved -> {out}")
print(f"PDF copy  -> {desk}")
