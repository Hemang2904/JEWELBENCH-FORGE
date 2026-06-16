"""Extensive dimension-accuracy check: every dimension the app outputs vs the
catalog's printed truth, across all designs. Reports per-dimension error, bias,
within-10/20%, and correlation (does the model TRACK real variation, or just
output a near-constant that happens to look accurate?). Writes a PDF scorecard.

Usage:  python tools/dim_validate.py [true_dims.json] [estimates.json]
"""
import datetime
import json
import math
import os
import statistics
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import fitz  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "ground_truth", "c113_true_dims.json")
ESTS = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "eval_runs", "estimates.json")
TMP = os.path.join(REPO, "eval_runs"); os.makedirs(TMP, exist_ok=True)


def inner_d(rs):
    try:
        s = float(str(rs).strip())
    except (TypeError, ValueError):
        return None
    return (36.537 + 2.5535 * s) / math.pi


true = {r["page"]: r for r in json.load(open(TRUE)) if not r.get("unreadable")}
ests = {e["page"]: e for e in (json.load(open(ESTS)).get("result", {})).get("estimates", [])}
pages = sorted(set(true) & set(ests))

rows = []
for p in pages:
    e, t = ests[p], true[p]
    kd = e.get("key_dimensions_mm", {})
    bt_m, hh_m = kd.get("band_thickness"), kd.get("head_height")
    idia = inner_d(t.get("ring_size_us") or "7")
    rh_m = (idia + 2 * bt_m + hh_m) if (idia and bt_m and hh_m) else None
    rows.append({
        "page": p,
        "Band width": (kd.get("band_width"), t.get("band_width_mm")),
        "Band thickness": (bt_m, t.get("band_thickness_mm")),
        "Head height": (hh_m, t.get("head_height_mm")),
        "Ring height (derived)": (rh_m, t.get("ring_height_mm")),
        "shape_m": (e.get("center_stone_shape") or "").lower().strip(),
        "shape_t": (t.get("center_stone_shape") or "").lower().strip(),
    })


def score(dim):
    pr = [r[dim] for r in rows if isinstance(r[dim][0], (int, float)) and isinstance(r[dim][1], (int, float))
          and r[dim][0] > 0 and r[dim][1] > 0]
    if len(pr) < 3:
        return None
    M, T = [m for m, t in pr], [t for m, t in pr]
    errs = [(m - t) / t * 100 for m, t in pr]
    a = [abs(x) for x in errs]
    mm, mt = statistics.mean(M), statistics.mean(T)
    cov = sum((m - mm) * (t - mt) for m, t in pr)
    sm = math.sqrt(sum((m - mm) ** 2 for m, _ in pr)); st = math.sqrt(sum((t - mt) ** 2 for _, t in pr))
    corr = cov / (sm * st) if sm and st else 0.0
    return {"n": len(pr), "true_mean": mt, "true_sd": statistics.pstdev(T), "model_mean": mm,
            "median": statistics.median(a), "mean": statistics.mean(a), "bias": statistics.mean(errs),
            "w10": 100 * sum(x <= 10 for x in a) / len(a), "w20": 100 * sum(x <= 20 for x in a) / len(a),
            "corr": corr, "M": M, "T": T}


DIMS = ["Band width", "Band thickness", "Head height", "Ring height (derived)"]
S = {d: score(d) for d in DIMS}


def shape_ok(a, b):
    if not a or not b:
        return None
    a, b = a.replace("square", "").strip(), b.replace("square", "").strip()
    return a[:4] == b[:4] or a in b or b in a
shape_pairs = [shape_ok(r["shape_m"], r["shape_t"]) for r in rows]
shape_pairs = [x for x in shape_pairs if x is not None]
shape_acc = 100 * sum(shape_pairs) / len(shape_pairs) if shape_pairs else None


def verdict(s):
    if not s:
        return "—"
    if s["median"] <= 10:
        return "GOOD — publish"
    if s["median"] <= 20:
        return "FAIR — flag as estimate"
    return "POOR — needs multi-view"


print(f"paired designs: {len(pages)}")
for d in DIMS:
    s = S[d]
    if s:
        print(f"  {d:24}: median |err| {s['median']:5.1f}%  bias {s['bias']:+6.1f}%  "
              f"w10 {s['w10']:3.0f}%  corr {s['corr']:+.2f}  true {s['true_mean']:.2f}±{s['true_sd']:.2f}  "
              f"-> {verdict(s)}")
if shape_acc is not None:
    print(f"  {'Center stone shape':24}: {shape_acc:.0f}% match (n={len(shape_pairs)})")

# ── scatter charts ────────────────────────────────────────────────────────────
plt.rcParams.update({"font.size": 8, "figure.dpi": 150})
fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.4))
for ax, d in zip(axes.ravel(), DIMS):
    s = S[d]
    if not s:
        ax.set_visible(False); continue
    ax.scatter(s["T"], s["M"], s=14, c="#4575b4", alpha=0.6, edgecolors="none")
    lim = [min(s["T"] + s["M"]) * 0.9, max(s["T"] + s["M"]) * 1.1]
    ax.plot(lim, lim, "k--", lw=1, alpha=0.5)
    ax.set_title(f"{d}\nmed {s['median']:.0f}% · bias {s['bias']:+.0f}% · r={s['corr']:.2f}", fontsize=8)
    ax.set_xlabel("true mm"); ax.set_ylabel("model mm"); ax.grid(alpha=0.25)
fig.tight_layout(); fig.savefig(f"{TMP}/dimval_scatter.png"); plt.close(fig)

# ── PDF ───────────────────────────────────────────────────────────────────────
doc = fitz.open(); W, H, M = 595, 842, 45
pg = doc.new_page(width=W, height=H)
pg.insert_text((M, 40), "JewelBench Forge", fontsize=10, fontname="helv", color=(0.4, 0.4, 0.4))
pg.insert_text((M, 62), "Dimension Accuracy — Extensive Check", fontsize=16, fontname="hebo")
pg.draw_line(fitz.Point(M, 70), fitz.Point(W - M, 70), color=(0.8, 0.8, 0.8))
y = 92
pg.insert_text((M, y), f"Date {datetime.date.today().isoformat()}   ·   {len(pages)} designs   ·   "
               "model dimension estimates vs catalog printed truth", fontsize=9, fontname="helv"); y += 22
hdr = f"{'dimension':<22}{'n':>4}{'med|err|':>9}{'bias':>8}{'w10':>6}{'w20':>6}{'corr':>7}  verdict"
pg.insert_text((M, y), hdr, fontsize=8, fontname="cobo"); y += 5
pg.draw_line(fitz.Point(M, y), fitz.Point(W - M, y), color=(0.7, 0.7, 0.7)); y += 14
for d in DIMS:
    s = S[d]
    if not s:
        continue
    col = (0.1, 0.45, 0.2) if s["median"] <= 10 else ((0.7, 0.45, 0.05) if s["median"] <= 20 else (0.75, 0.1, 0.1))
    line = (f"{d:<22}{s['n']:>4}{s['median']:>8.1f}%{s['bias']:>+7.1f}%{s['w10']:>5.0f}%{s['w20']:>5.0f}%"
            f"{s['corr']:>+7.2f}  {verdict(s)}")
    pg.insert_text((M, y), line, fontsize=8, fontname="cour", color=col); y += 13
if shape_acc is not None:
    pg.insert_text((M, y), f"{'Center stone shape':<22}{len(shape_pairs):>4}{shape_acc:>8.0f}%   (categorical match)",
                   fontsize=8, fontname="cour"); y += 16
pg.insert_image(fitz.Rect(M, y + 6, W - M, y + 6 + 360), filename=f"{TMP}/dimval_scatter.png")
y2 = y + 380
pg.insert_text((M, y2), "READ", fontsize=11, fontname="hebo"); y2 += 16
bw, bt, hh = S["Band width"], S["Band thickness"], S["Head height"]
note = (
    f"Band width is the most trustworthy dimension (median {bw['median']:.0f}% err, corr {bw['corr']:+.2f}). "
    f"Band thickness reads ~{abs(bt['bias']):.0f}% light and head height ~{abs(hh['bias']):.0f}% light — both "
    f"depth-axis quantities a single view under-determines (low correlation = the model is guessing a typical "
    f"value rather than reading this ring). 'corr' is the key tell: high = tracks real variation; near zero = "
    f"a near-constant guess. In target-weight mode the absolute sizes are pinned to the weight, so these are "
    f"PROPORTIONAL errors; the fix for the depth dimensions (band thickness, head height) is the multi-view / "
    f"mesh path. Publish band width + plan; flag head height + band thickness as estimates until multi-view.")
import textwrap
for ln in textwrap.wrap(note, 96):
    pg.insert_text((M, y2), ln, fontsize=9, fontname="helv"); y2 += 13
out = os.path.join(REPO, "ground_truth", "Dimension_Accuracy.pdf")
desk = os.path.expanduser("~/Desktop/JewelBench_Dimension_Accuracy.pdf")
doc.save(out); doc.save(desk); doc.close()
print(f"PDF -> {desk}")
