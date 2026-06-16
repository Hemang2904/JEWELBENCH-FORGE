"""Extensive PDF report for the robust-aggregation test suite.

Every NUMBER in the report is computed LIVE from weight_estimator at render time
(single source of truth — the PDF can never drift from the code). The verified
PROSE / test catalog / code-change notes are loaded from report_content.json,
which is produced + adversarially verified by the workflow that drives this tool.

Usage:  /Users/hemang/dev/gtvenv/bin/python tools/test_report.py
"""
import datetime
import json
import math
import os
import statistics
import subprocess
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import fitz  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import weight_estimator as we  # noqa: E402

TMP = os.path.join(REPO, "eval_runs"); os.makedirs(TMP, exist_ok=True)
CONTENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_content.json")
C = json.load(open(CONTENT)) if os.path.exists(CONTENT) else {}
ALLOY = "18k_yellow_gold"
DENS = we.alloy_density(ALLOY)

# ── live computation engine ─────────────────────────────────────────────────────
def ests(vols):
    return [{"_model": f"m{i}", "total_metal_volume_mm3": v, "shank_volume_mm3": v * 0.5}
            for i, v in enumerate(vols)]


def new_combine(vols):
    """The shipped robust reconcile."""
    r = we.reconcile(ests(vols), ALLOY)
    return {"vol": r["volume_mm3"], "g": r["gold_weight_g"], "conf": r["confidence"],
            "agg": r["aggregation"], "drop": r["outliers_rejected"],
            "dis": r["model_disagreement"], "n": r["n_models"]}


def old_combine(vols):
    """The previous plain-MEAN reconcile, reconstructed for contrast."""
    vv = [v for v in vols if v and v > 0]
    mean = sum(vv) / len(vv) if vv else 0.0
    g = we.volume_to_weight(mean, DENS)
    n = len(vv); single = n < 2
    dis = round((max(vv) - min(vv)) / mean, 3) if (mean and not single) else 0.0
    conf = ("medium" if single else "low" if dis > 0.25 else "medium" if dis > 0.10 else "high")
    return {"vol": round(mean, 1), "g": g, "conf": conf, "agg": "mean", "drop": 0,
            "dis": dis, "n": n}


HEADLINE = [332, 340, 351, 720]
LOWOUT = [340, 350, 360, 100]
CONFCASE = [335, 340, 345, 1200]
CONTAM_X = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
CONTAM_BASE = 340.0
CONTAM_GOOD = [330.0, 340.0, 350.0]
NSWEEP = [[340], [335, 345], [330, 340, 350], [330, 340, 350, 900],
          [330, 338, 344, 350, 900], [328, 335, 340, 346, 352, 900]]


def contamination():
    xs, me, re_ = [], [], []
    for x in CONTAM_X:
        vols = CONTAM_GOOD + [CONTAM_BASE * x]
        o, n = old_combine(vols), new_combine(vols)
        xs.append(x)
        me.append((o["vol"] - CONTAM_BASE) / CONTAM_BASE * 100)
        re_.append((n["vol"] - CONTAM_BASE) / CONTAM_BASE * 100)
    return xs, me, re_


def run_suite():
    p = subprocess.run([sys.executable, "-m", "unittest",
                        "tests.test_weight_regression", "-v"],
                       cwd=REPO, capture_output=True, text=True)
    out = (p.stderr or "") + (p.stdout or "")
    total = passed = skipped = failed = 0
    rt = 0.0
    for ln in out.splitlines():
        if ln.startswith("Ran "):
            parts = ln.split()
            try:
                total = int(parts[1])
                tok = parts[-1].rstrip("s")          # "0.090s" -> "0.090"
                rt = float(tok) if tok.replace(".", "", 1).isdigit() else 0.0
            except (ValueError, IndexError):
                pass
        if " ... ok" in ln:
            passed += 1
        if " ... skipped" in ln:
            skipped += 1
        if " ... FAIL" in ln or " ... ERROR" in ln:
            failed += 1
    return {"total": total, "passed": passed, "skipped": skipped,
            "failed": failed, "runtime_s": rt, "clean": p.returncode == 0}


SUITE = run_suite()


# ── live analytics for the deep-dive sections ───────────────────────────────────
def worked_steps():
    v = HEADLINE
    med4 = statistics.median(v)
    tol = we.ROBUST_OUTLIER_TOL
    lo, hi = med4 * (1 - tol), med4 * (1 + tol)
    kept = we._robust_kept(v); pt = we._robust_point(v)
    mean = sum(v) / len(v)
    g_new = we.volume_to_weight(pt, DENS); g_old = we.volume_to_weight(mean, DENS)
    dropped = [x for x in v if x not in kept]
    return [
        f"input volumes (mm3)      = {v}",
        f"median of all {len(v)}        = {med4:g}",
        f"keep band (+/-{tol:.0%})       = [{lo:.2f}, {hi:.2f}]",
        f"dropped (outside band)   = {dropped}        <- {dropped[0]:g} > {hi:.2f}",
        f"kept set                 = {kept}",
        f"point = median(kept)     = {pt:g} mm3        (n_kept={len(kept)} -> median)",
        f"NEW weight = {pt:g}/1000 * {DENS} * {we.CASTING_FACTOR}  = {g_new:.3f} g",
        "",
        f"OLD path: mean(all {len(v)})    = {mean:.2f} mm3",
        f"OLD weight = {mean:.2f}/1000 * {DENS} * {we.CASTING_FACTOR} = {g_old:.3f} g",
        f"correction = {g_old - g_new:+.2f} g  ({(g_old - g_new) / g_new * 100:+.0f}% removed)",
    ]


def degenerate_point():
    v = [100, 200, 900, 1000]
    return v, we._robust_kept(v), we._robust_point(v)


def label_vs_est():
    v = [300, 800, 820]
    r = new_combine(v)
    return v, we._robust_kept(v), we._robust_point(v), r["agg"]


def tol_sweep():
    vectors = [[332, 340, 351, 720], [330, 340, 350, 520]]
    rows = []
    for v in vectors:
        cells = [str(v)]
        for tol in (0.3, 0.5, 0.7):
            we.ROBUST_OUTLIER_TOL = tol
            kept = we._robust_kept(v); pt = we._robust_point(v)
            cells.append(f"{pt:g}  (kept {len(kept)}/{len(v)})")
        we.ROBUST_OUTLIER_TOL = 0.5
        rows.append(cells)
    return rows


NEW_TEST_VECTORS = [
    ("test_robust_point_median_at_n3", [300, 340, 360], "median used at n>=3, not mean"),
    ("test_robust_point_mean_below_n3", [300, 360], "mean fallback below n=3"),
    ("test_high_outlier_rejected", [330, 340, 350, 1000], "huge value dropped"),
    ("test_low_outlier_rejected", [340, 350, 360, 100], "half-volume value dropped"),
    ("test_reconcile_reports_outlier_and_aggregation", [330, 340, 350, 1000], "exposes agg + count"),
    ("test_rejected_outlier_does_not_tank_confidence", [335, 340, 345, 1200], "confidence on kept set"),
    ("test_no_false_rejection_when_all_agree", [330, 340, 350], "no false drop when tight"),
    ("test_two_models_still_mean", [320, 360], "n=2 stays mean (compat)"),
]


def new_test_rows():
    rows = []
    for name, vec, guards in NEW_TEST_VECTORS:
        kept = we._robust_kept(vec); pt = we._robust_point(vec)
        rows.append([name, str(vec), str(kept), f"{pt:g}", guards])
    return rows


COVERAGE = [
    ("volume_to_weight", "TestVolumeToWeight", True),
    ("alloy_density", "TestDensities", True),
    ("_has_valid_total / _sanitize_volumes", "TestValidation", True),
    ("_construction_fill", "TestConstructionFill", True),
    ("us_ring_inner_diameter_mm / calibrate_to_ring_size", "TestRingSizeAndCalibration", True),
    ("refine_shank_volume", "TestRefineShankConstructionWarning", True),
    ("_robust_kept / _robust_point", "TestRobustAggregation [NEW]", True),
    ("reconcile", "TestReconcile + TestRobustAggregation", True),
    ("_finalize_confidence", "TestFinalizeConfidence", True),
    ("_load_image_bytes", "TestLoadImageBytes", True),
    ("scale_to_target", "TestScaleToTarget", True),
    ("annotate_view / render_dimensioned_views", "TestDimensionAnnotation (skips w/o Pillow)", True),
    ("estimate_weight (ensemble orchestration)", "live VLM path — not unit-tested", False),
    ("_call_model / fal network layer", "live network — not unit-tested", False),
]


# ── charts ──────────────────────────────────────────────────────────────────────
plt.rcParams.update({"font.size": 8.5, "figure.dpi": 200, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.spines.top": False, "axes.spines.right": False})
BLUE, RED, GREEN, AMBER = "#2a66b8", "#c0392b", "#1f8a4c", "#c8881a"


def chart_headline():
    o, n = old_combine(HEADLINE), new_combine(HEADLINE)
    fig, ax = plt.subplots(figsize=(3.5, 2.7))
    bars = ax.bar(["old\n(mean)", "new\n(robust)"], [o["g"], n["g"]],
                  color=[RED, GREEN], width=0.55)
    true_g = we.volume_to_weight(statistics.median(HEADLINE[:3]), DENS)
    ax.axhline(true_g, ls="--", lw=1, color="#555")
    ax.text(1.45, true_g, f" honest\n median\n {true_g:.2f} g", va="center", fontsize=7, color="#555")
    for b, v in zip(bars, [o["g"], n["g"]]):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:.2f} g", ha="center", fontsize=8.5, fontweight="bold")
    ax.set_ylabel("estimated gold weight (g)")
    ax.set_title(f"Headline: volumes {HEADLINE}\n(one 720 mm³ outlier among three ~340)", fontsize=8)
    fig.tight_layout(); p = f"{TMP}/rep_headline.png"; fig.savefig(p); plt.close(fig); return p


def chart_contamination():
    xs, me, re_ = contamination()
    fig, ax = plt.subplots(figsize=(3.7, 2.7))
    ax.plot(xs, me, "-o", color=RED, ms=4, label="old mean")
    ax.plot(xs, re_, "-o", color=GREEN, ms=4, label="new robust")
    ax.axhline(0, color="#888", lw=0.8)
    ax.fill_between(xs, -5, 5, color=GREEN, alpha=0.06)
    ax.set_xlabel("outlier magnitude (× the true 340 mm³)")
    ax.set_ylabel("volume error vs truth (%)")
    ax.set_title("One bad model, 3 good: error vs outlier size", fontsize=8)
    ax.legend(frameon=False, fontsize=7.5, loc="upper left")
    fig.tight_layout(); p = f"{TMP}/rep_contam.png"; fig.savefig(p); plt.close(fig); return p


def chart_nsweep():
    ns, vols, cols = [], [], []
    cmap = {"single": AMBER, "mean": BLUE, "median+outlier-reject": GREEN}
    for v in NSWEEP:
        n = new_combine(v); ns.append(n["n"]); vols.append(n["vol"]); cols.append(cmap.get(n["agg"], "#888"))
    fig, ax = plt.subplots(figsize=(3.7, 2.7))
    ax.bar([str(x) for x in ns], vols, color=cols, width=0.6)
    ax.axhline(340, ls="--", lw=1, color="#555")
    ax.set_xlabel("number of models returning (n)")
    ax.set_ylabel("reconciled volume (mm³)")
    ax.set_title("n≥4 rows include a 900 mm³ outlier", fontsize=8)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=AMBER, label="single"), Patch(color=BLUE, label="mean"),
                       Patch(color=GREEN, label="median+reject")], frameon=False, fontsize=7, loc="lower right")
    fig.tight_layout(); p = f"{TMP}/rep_nsweep.png"; fig.savefig(p); plt.close(fig); return p


# ── PDF layout engine ───────────────────────────────────────────────────────────
W, H, M = 595, 842, 48
GRAY = (0.40, 0.40, 0.40); INK = (0.12, 0.12, 0.13)
ACCENT = (0.16, 0.40, 0.72); GREENc = (0.10, 0.50, 0.20); REDc = (0.75, 0.10, 0.10); AMBERc = (0.72, 0.50, 0.10)


class Doc:
    def __init__(self):
        self.doc = fitz.open(); self.pg = None; self.y = 0; self.pageno = 0
        self.newpage()

    def newpage(self):
        self.pg = self.doc.new_page(width=W, height=H); self.pageno += 1; self.y = M + 6
        self.pg.insert_text((M, 30), "JewelBench Forge", fontsize=9, fontname="helv", color=GRAY)
        self.pg.insert_text((W - M - 150, 30), "Robust-Aggregation Test Report", fontsize=8,
                            fontname="helv", color=GRAY)
        self.pg.draw_line(fitz.Point(M, 36), fitz.Point(W - M, 36), color=(0.85, 0.85, 0.85))
        self.pg.insert_text((W / 2 - 8, H - 26), f"{self.pageno}", fontsize=8, fontname="helv", color=GRAY)

    def ensure(self, space):
        if self.y + space > H - 44:
            self.newpage()

    def space(self, dy=8):
        self.y += dy

    def h1(self, t):
        self.ensure(40); self.y += 6
        self.pg.insert_text((M, self.y + 12), t, fontsize=15, fontname="hebo", color=INK); self.y += 18
        self.pg.draw_line(fitz.Point(M, self.y), fitz.Point(W - M, self.y), color=ACCENT, width=1.2); self.y += 14

    def h2(self, t, color=INK):
        self.ensure(28); self.y += 4
        self.pg.insert_text((M, self.y + 10), t, fontsize=11, fontname="hebo", color=color); self.y += 18

    def body(self, t, size=9.2, color=INK, lead=12.5, indent=0, font="helv", width=99):
        for para in t.split("\n"):
            for ln in (textwrap.wrap(para, width) or [""]):
                self.ensure(lead + 2)
                self.pg.insert_text((M + indent, self.y + 8), ln, fontsize=size, fontname=font, color=color)
                self.y += lead

    def bullet(self, t, color=INK):
        lines = textwrap.wrap(t, 94)
        for i, ln in enumerate(lines):
            self.ensure(13)
            if i == 0:
                self.pg.insert_text((M + 4, self.y + 8), "•", fontsize=9.2, fontname="helv", color=ACCENT)
            self.pg.insert_text((M + 16, self.y + 8), ln, fontsize=9.2, fontname="helv", color=color)
            self.y += 12.5

    def callout(self, t, color=ACCENT, bg=(0.95, 0.97, 1.0)):
        lines = textwrap.wrap(t, 96); hgt = 10 + 12 * len(lines)
        self.ensure(hgt + 6)
        self.pg.draw_rect(fitz.Rect(M, self.y, W - M, self.y + hgt), color=None, fill=bg)
        self.pg.draw_line(fitz.Point(M, self.y), fitz.Point(M, self.y + hgt), color=color, width=2.5)
        yy = self.y + 12
        for ln in lines:
            self.pg.insert_text((M + 12, yy), ln, fontsize=9, fontname="helv", color=INK); yy += 12
        self.y += hgt + 8

    def mono_box(self, title, lines, fs=7.6, lead=10.6, bg=(0.972, 0.974, 0.98),
                 border=(0.80, 0.82, 0.86)):
        hgt = (15 if title else 4) + lead * len(lines) + 10
        self.ensure(hgt + 6)
        self.pg.draw_rect(fitz.Rect(M, self.y, W - M, self.y + hgt), color=border, fill=bg, width=0.6)
        yy = self.y + 13
        if title:
            self.pg.insert_text((M + 10, yy), title, fontsize=8, fontname="hebo", color=ACCENT); yy += 14
        for ln in lines:
            self.pg.insert_text((M + 12, yy), ln, fontsize=fs, fontname="cour", color=INK); yy += lead
        self.y += hgt + 8

    def table(self, headers, rows, widths, fs=8, hfs=8, row_colors=None):
        x0 = M; tw = W - 2 * M
        cw = [tw * w for w in widths]
        self.ensure(22)
        # header
        self.pg.draw_rect(fitz.Rect(x0, self.y, x0 + tw, self.y + 16), color=None, fill=(0.93, 0.94, 0.96))
        cx = x0
        for h, w in zip(headers, cw):
            self.pg.insert_text((cx + 4, self.y + 11), str(h), fontsize=hfs, fontname="hebo", color=INK)
            cx += w
        self.y += 16
        for ri, row in enumerate(rows):
            rh = self._rowh(row, cw, fs)
            self.ensure(rh + 2)
            if self.y == M + 6:  # page broke -> redraw header
                pass
            if ri % 2 == 0:
                self.pg.draw_rect(fitz.Rect(x0, self.y, x0 + tw, self.y + rh), color=None, fill=(0.985, 0.985, 0.99))
            col = (row_colors or {}).get(ri, INK)
            cx = x0
            for cell, w in zip(row, cw):
                wrapped = textwrap.wrap(str(cell), max(6, int(w / (fs * 0.52)))) or [""]
                yy = self.y + 10
                for ln in wrapped:
                    self.pg.insert_text((cx + 4, yy), ln, fontsize=fs, fontname="cour" if cx == x0 and False else "helv", color=col)
                    yy += fs + 2.5
                cx += w
            self.pg.draw_line(fitz.Point(x0, self.y + rh), fitz.Point(x0 + tw, self.y + rh), color=(0.9, 0.9, 0.9))
            self.y += rh
        self.y += 6

    def _rowh(self, row, cw, fs):
        m = 1
        for cell, w in zip(row, cw):
            n = len(textwrap.wrap(str(cell), max(6, int(w / (fs * 0.52)))) or [""])
            m = max(m, n)
        return 6 + (fs + 2.5) * m

    def image(self, path, w, h):
        self.ensure(h + 8)
        self.pg.insert_image(fitz.Rect(M, self.y, M + w, self.y + h), filename=path)
        return self.y

    def image_at(self, path, x, w, h):
        self.pg.insert_image(fitz.Rect(x, self.y, x + w, self.y + h), filename=path)

    def save(self, *paths):
        for p in paths:
            self.doc.save(p)
        self.doc.close()


# ── compose ─────────────────────────────────────────────────────────────────────
d = Doc()
nar = C.get("narrative", {})
cat = C.get("testCatalog", {})
code = C.get("codeChanges", {})
vE = C.get("vEvidence", {})
vC = C.get("vCatalog", {})
comp = C.get("completeness", {})

# COVER ----------------------------------------------------------------------------
d.pg.insert_text((M, d.y + 26), "Robust Ensemble Aggregation", fontsize=23, fontname="hebo", color=INK)
d.pg.insert_text((M, d.y + 48), "Test & Verification Report", fontsize=15, fontname="helv", color=ACCENT)
d.y += 70
d.body(f"Date {datetime.date.today().isoformat()}   ·   module weight_estimator.py   ·   "
       f"branch fix/weight-accuracy-view-consistency", size=9, color=GRAY)
d.space(6)
sm = SUITE
badge = (f"{sm['total']} tests · {sm['passed']} passed · {sm['skipped']} skipped · "
         f"{sm['failed']} failed · {sm['runtime_s']:.2f}s")
d.callout("SUITE STATUS  —  " + badge + ("   — all green" if sm["clean"] else "   — FAILURES present"),
          color=GREENc if sm["clean"] else REDc, bg=(0.93, 0.98, 0.94) if sm["clean"] else (1.0, 0.95, 0.95))
allpass = vE.get("all_pass")
if allpass is not None:
    d.callout("INDEPENDENT VERIFICATION  —  every headline number in this report was re-derived "
              "from scratch by a separate adversarial agent. " + (vE.get("summary") or ""),
              color=GREENc if allpass else AMBERc, bg=(0.93, 0.98, 0.94) if allpass else (1.0, 0.98, 0.92))
d.space(2)
d.h2("Executive summary")
d.body(nar.get("exec_summary", "(summary pending)"))
d.space(4)
d.h2("The problem")
d.body(nar.get("problem", ""))

# THE FIX --------------------------------------------------------------------------
d.h1("What changed")
for ch in code.get("changes", []):
    d.h2(ch.get("title", ""), color=ACCENT)
    d.body(f"{ch.get('location','')}", size=8.4, color=GRAY, font="cour")
    d.body(ch.get("detail", ""))
    d.space(2)
if code.get("knobs"):
    d.h2("Configuration knobs")
    d.table(["env var", "default", "purpose"],
            [[k.get("name", ""), k.get("default", ""), k.get("purpose", "")] for k in code["knobs"]],
            [0.27, 0.16, 0.57])
if code.get("degradation"):
    d.h2("Graceful degradation")
    d.table(["models (n)", "method", "behaviour"],
            [[g.get("n_models", ""), g.get("method", ""), g.get("behavior", "")] for g in code["degradation"]],
            [0.16, 0.26, 0.58])

# ALGORITHM ------------------------------------------------------------------------
d.h1("Algorithm")
d.body("The robust central estimate, exactly as implemented (weight_estimator.py: _robust_kept / "
       "_robust_point) — auditable from this page alone:", size=8.8, color=GRAY)
d.mono_box("_robust_kept(xs)  ->  _robust_point(xs)", [
    "xs = [x for x in xs if x is a positive finite number]",
    "if len(xs) < 3:                      # n = 1 or 2",
    "    return xs                        #   keep all; point = mean(xs)",
    "med    = median(xs)",
    f"lo, hi = med*(1-TOL), med*(1+TOL)    # TOL = {we.ROBUST_OUTLIER_TOL:g}  (+/-{we.ROBUST_OUTLIER_TOL:.0%} of the median)",
    "kept   = [x for x in xs if lo <= x <= hi]  or  xs     # 'or xs' = all-rejected fallback",
    "point  = median(kept) if len(kept) >= 3 else mean(kept)",
])
d.body("reconcile() applies this to the per-model TOTAL-volume column, converts the point to grams "
       "(point/1000 x density x casting factor), measures disagreement on the KEPT set, and reports "
       "aggregation / outliers_rejected / n_models / confidence.", size=8.8)
d.space(4)
d.h2("Where it sits in the pipeline")
d.mono_box("estimate_weight()  ->  reconcile()", [
    "montage(images)",
    "  -> run first target = max(2, MIN_MODELS) models in parallel   (each retries x3)",
    "  -> walk fallback pool until `target` good estimates returned   (gemini-flash, gpt-5)",
    "  -> per estimate: calibrate_to_ring_size + refine_shank_volume  (anchor scale to ring size)",
    "  -> reconcile(): ROBUST median + outlier rejection            <=== THIS CHANGE",
    "  -> dims / stones taken from the model CLOSEST to the reconciled volume",
    "  -> _finalize_confidence(): cap when uncalibrated / clamped / no ring size",
])

# EVIDENCE -------------------------------------------------------------------------
d.h1("Empirical robustness evidence")
d.body("Every figure below is recomputed live from weight_estimator.reconcile at render time.", size=8.6, color=GRAY)
d.space(4)
o, n = old_combine(HEADLINE), new_combine(HEADLINE)
ytop = d.y
d.image_at(chart_headline(), M, 232, 180)
# side table
d.pg.insert_text((M + 250, ytop + 14), "Headline case", fontsize=11, fontname="hebo", color=INK)
rows = [("metric", "old mean", "new robust"),
        ("volume mm3", f"{o['vol']:.0f}", f"{n['vol']:.0f}"),
        ("gold weight", f"{o['g']:.2f} g", f"{n['g']:.2f} g"),
        ("confidence", o["conf"], n["conf"]),
        ("outliers cut", "0", str(n["drop"]))]
yy = ytop + 30
for i, (a, b, c) in enumerate(rows):
    fn = "hebo" if i == 0 else "helv"
    col = INK if i == 0 else GREENc if i in (2,) else INK
    d.pg.insert_text((M + 250, yy), a, fontsize=8.6, fontname=fn, color=INK)
    d.pg.insert_text((M + 370, yy), b, fontsize=8.6, fontname=fn, color=REDc if i else INK)
    d.pg.insert_text((M + 450, yy), c, fontsize=8.6, fontname=fn, color=GREENc if i else INK)
    yy += 15
err_old = (o["vol"] - 340) / 340 * 100
d.pg.insert_text((M + 250, yy + 4), f"old mean is {err_old:+.0f}% vs the honest median;",
                 fontsize=8, fontname="helv", color=GRAY)
d.pg.insert_text((M + 250, yy + 16), "robust lands on it and stays 'high'.", fontsize=8, fontname="helv", color=GRAY)
d.y = max(d.y + 188, yy + 30)
d.space(6)

d.h2("Worked example — the headline case, step by step", color=ACCENT)
d.mono_box(None, worked_steps())
d.space(2)

# demonstrations from verified content (numbers re-rendered live where possible)
for dem in C.get("evidence", {}).get("demonstrations", []):
    d.h2(dem.get("name", ""), color=ACCENT)
    if dem.get("description"):
        d.body(dem["description"], size=8.8)
    hdrs = dem.get("table_headers", []); rws = dem.get("table_rows", [])
    if hdrs and rws:
        wdt = [1.0 / len(hdrs)] * len(hdrs)
        d.table(hdrs, rws, wdt)
    if dem.get("takeaway"):
        d.body("-> " + dem["takeaway"], size=8.8, color=GREENc)
    d.space(3)

# charts page
d.h1("Behaviour under contamination")
yt = d.y
d.image_at(chart_contamination(), M, 250, 182)
d.image_at(chart_nsweep(), M + 262, 250, 182)
d.y = yt + 190
xs, me, re_ = contamination()
d.body(f"Left: with three honest models clustered at 340 mm3, the mean's error climbs to "
       f"{me[-1]:+.0f}% as a single outlier grows to 3x, while the robust estimate holds at "
       f"{re_[-1]:+.0f}% -- the median's ~50% breakdown point in action. Right: the aggregation "
       f"label moves single -> mean -> median+reject as n grows; from n=3 a 900 mm3 outlier is "
       f"simply dropped and the volume stays pinned at the true 340 mm3.", size=8.8)

# BREAKDOWN + EDGE CASES -----------------------------------------------------------
d.h1("Breakdown point & edge cases")
d.body("The median tolerates corruption of up to ~50% of inputs (its breakdown point); the mean's is "
       "0% -- one bad value moves it without bound. For the small ensembles here:", size=8.8)
d.table(["models (n)", "outliers tolerated", "behaviour"],
        [["2", "0", "no rejection -- plain mean (median == mean)"],
         ["3", "1", "one bad value dropped; median of the kept 2-3"],
         ["4", "1 cleanly", "two same-direction outliers can split the median"],
         ["5", "2", "robust"],
         ["6", "2", "robust"]],
        [0.16, 0.24, 0.60])
dv, dk, dp = degenerate_point()
d.h2("Documented failure mode -- a 50/50 split", color=AMBERc)
d.mono_box(None, [
    f"input {dv}    two equal clusters, no majority",
    f"median = {statistics.median(dv):g}    +/-50% band rejects BOTH clusters  ->  kept = []",
    f"'or xs' fallback keeps all  ->  point = median(all) = {dp:g}   (a value no model produced)",
])
d.body("With no majority to trust, the estimator returns the midpoint and the wide spread shows as low "
       "confidence. This is inherent to any robust aggregator, not a regression -- it is exactly why "
       ">=3 genuinely independent models matter.", size=8.6, color=GRAY)
lv, lk, lp, lagg = label_vs_est()
d.h2("Label vs estimator -- an honest caveat", color=AMBERc)
d.mono_box(None, [
    f"input {lv}  ->  median {statistics.median(lv):g}, drop {[x for x in lv if x not in lk]}  ->  kept {lk}",
    f"len(kept) = 2 < 3   =>   point = MEAN(kept) = {lp:g}     yet aggregation label = '{lagg}'",
])
d.body("The aggregation field reflects n_models and that rejection ran -- not a guarantee that a median "
       "(rather than a small-kept-set mean) produced the number. outliers_rejected tells the true story.",
       size=8.6, color=GRAY)
d.h2("Parameter sensitivity -- ROBUST_OUTLIER_TOL")
d.body(f"Default {we.ROBUST_OUTLIER_TOL:g} (+/-{we.ROBUST_OUTLIER_TOL:.0%} of the median), env-overridable. "
       "Lower = stricter (drops more); higher = more permissive. Point estimate at three tolerances:", size=8.6)
d.table(["input volumes", "tol 0.3", "tol 0.5 (default)", "tol 0.7"],
        tol_sweep(), [0.34, 0.22, 0.22, 0.22], fs=7.8)

# TEST CATALOG ---------------------------------------------------------------------
d.h1("Complete test catalog")
d.body(f"All {sm['total']} tests in tests/test_weight_regression.py. The class added by this change "
       "(TestRobustAggregation) is marked NEW.", size=8.8, color=GRAY)
d.space(4)
d.h2("New robust-aggregation tests -- input -> kept -> point estimate", color=GREENc)
d.table(["test", "input", "kept", "pt", "guards"], new_test_rows(),
        [0.30, 0.20, 0.18, 0.08, 0.24], fs=7.2)
d.space(4)
for cl in cat.get("classes", []):
    tag = "  [NEW]" if cl.get("is_new") else ""
    d.h2(cl.get("name", "") + tag, color=GREENc if cl.get("is_new") else INK)
    if cl.get("purpose"):
        d.body(cl["purpose"], size=8.6, color=GRAY)
    rows = [[t.get("name", ""), t.get("guards", ""), t.get("status", "")] for t in cl.get("tests", [])]
    rc = {i: GREENc if "ok" in (r[2] or "").lower() else AMBERc for i, r in enumerate(rows)}
    d.table(["test", "guards", "status"], rows, [0.34, 0.54, 0.12], fs=7.8, row_colors=rc)

# RATIONALE + LIMITS ---------------------------------------------------------------
d.h1("Why this is the right statistic")
d.body(nar.get("statistical_rationale", ""))
d.space(4)
d.h2("When it helps")
d.body(nar.get("when_it_helps", ""))
d.h2("When it does NOT help", color=AMBERc)
d.body(nar.get("when_it_does_not", ""))
d.space(4)
d.h2("Limitations & honest caveats", color=AMBERc)
for lim in nar.get("limitations", []):
    d.bullet(lim)
for extra in [
    "At n<=2 the median equals the mean, so the change is a no-op -- it only bites with >=3 models.",
    "A systematic bias shared by ALL models (e.g. every VLM under-reads a hollow shank) is NOT "
    "corrected; the median of biased inputs is still biased.",
    "If every input is rejected (a true 50/50 split) the 'or xs' fallback averages them all -- see "
    "the documented failure mode above.",
    "aggregation / outliers_rejected describe n_models and the rejection, not which estimator "
    "(median vs a 2-point mean) produced the value.",
    "Disagreement, confidence and rejection are computed on the TOTAL-volume column; the shank "
    "volume uses the same robust point but is not separately gated.",
    "estimate_weight()'s live ensemble depends on fal returning >=3 models; if only 2 return it "
    "degrades to the mean (still correct, just without the median).",
]:
    d.bullet(extra, color=GRAY)

# COVERAGE -------------------------------------------------------------------------
d.h1("Test-coverage matrix")
cov_rows = [[fn, cls, "covered" if ok else "NOT unit-tested"] for fn, cls, ok in COVERAGE]
cov_rc = {i: GREENc if r[2] == "covered" else AMBERc for i, r in enumerate(cov_rows)}
d.table(["function", "covering tests", "status"], cov_rows, [0.40, 0.42, 0.18], fs=7.8, row_colors=cov_rc)
d.callout("Scope: these tests exercise the deterministic estimator math. The live VLM / network path "
          "(estimate_weight, _call_model) is integration-level and intentionally not unit-tested -- it "
          "needs FAL_KEY and real model calls. End-to-end WEIGHT accuracy on the 113-design ground-truth "
          "catalog is measured separately (tools/dim_validate.py, run_eval.py), not in this report; the "
          "demos here are synthetic stress cases that isolate the aggregator.",
          color=AMBERc, bg=(1.0, 0.985, 0.93))

# VERIFICATION ---------------------------------------------------------------------
d.h1("Adversarial verification")
d.body("A separate agent re-ran the suite and independently recomputed every headline number, "
       "defaulting to 'mismatch' unless its own run confirmed the claim.", size=8.8, color=GRAY)
d.space(3)
if vC:
    d.h2("Suite re-run")
    d.body(f"runs clean: {vC.get('suite_runs_clean')}   ·   counts match: {vC.get('counts_match')}   "
           f"·   {vC.get('note','')}", size=8.8)
    for dd in vC.get("discrepancies", []) or []:
        d.bullet("discrepancy: " + dd, color=REDc)
if vE.get("verdicts"):
    d.h2("Numeric claims re-derived")
    rows = [[v.get("claim", ""), v.get("recomputed", ""), "yes" if v.get("matches") else "NO"]
            for v in vE["verdicts"]]
    rc = {i: GREENc if r[2] == "yes" else REDc for i, r in enumerate(rows)}
    d.table(["claim", "independently recomputed", "match?"], rows, [0.42, 0.46, 0.12], fs=7.8, row_colors=rc)

# APPENDIX -------------------------------------------------------------------------
d.h1("Appendix — reproduce")
d.body("Run the suite:", size=9)
d.body("    python3 -m unittest tests.test_weight_regression -v", font="cour", size=8.4, color=ACCENT)
d.space(2)
d.body("Reproduce the headline figure:", size=9)
d.body("    python3 -c \"import weight_estimator as we; "
       "print(we.reconcile([{'_model':'m%d'%i,'total_metal_volume_mm3':v,'shank_volume_mm3':v*.5} "
       "for i,v in enumerate([332,340,351,720])],'18k_yellow_gold'))\"", font="cour", size=7.6, color=ACCENT)
d.space(2)
d.body("Regenerate this PDF:", size=9)
d.body("    /Users/hemang/dev/gtvenv/bin/python tools/test_report.py", font="cour", size=8.4, color=ACCENT)
if comp.get("missing") or comp.get("suggested_additions"):
    d.space(6)
    d.h2("Completeness review (build-time critic)", color=GRAY)
    for s in (comp.get("suggested_additions") or [])[:6]:
        d.bullet(s, color=GRAY)

OUT = os.path.join(REPO, "ground_truth", "RobustAggregation_Test_Report.pdf")
DESK = os.path.expanduser("~/Desktop/JewelBench_RobustAggregation_Test_Report.pdf")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
d.save(OUT, DESK)
print(f"suite: {SUITE}")
print(f"PDF -> {DESK}")
print(f"PDF -> {OUT}")
