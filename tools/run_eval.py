"""Weight-accuracy eval harness.

Runs the live weight estimator on the labeled ground-truth catalog images and
compares the predicted 18k weight to the printed true weight. Staged so a key
typo or a bad run doesn't burn cost:

    python tools/run_eval.py --smoke              # 1 design, verify key + pipeline
    python tools/run_eval.py --limit 25           # quick baseline across styles
    python tools/run_eval.py                       # full run (all images)
    python tools/run_eval.py --catalogs c113       # restrict to one catalog

Reads FAL_KEY from the repo .env (never printed). Results saved to
ground_truth/eval_results.json (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

import weight_estimator as we  # noqa: E402

ALLOY = "18k_yellow_gold"  # owner's choice: treat all as 18k, compare on volume/weight
DATASET = os.path.join(REPO, "ground_truth", "labeled_designs.json")
IMG_DIR = os.path.join(REPO, "ground_truth", "images")


def _image_path(rec: dict) -> str | None:
    """Resolve the rendered spec-sheet image for an OCR record (id like 'c113-001')."""
    cat = rec.get("catalog")
    rid = rec.get("id", "")
    if cat == "b900" or "-" not in rid:
        return None  # b900 rings aren't individually cropped yet
    try:
        page = int(rid.split("-")[-1])
    except ValueError:
        return None
    p = os.path.join(IMG_DIR, cat, f"page_{page:03d}.png")
    return p if os.path.exists(p) else None


def _eval_one(rec: dict) -> dict:
    img = _image_path(rec)
    true_g = rec.get("gold_weight_18k_g")
    ring = rec.get("ring_size")
    out = {"id": rec["id"], "catalog": rec["catalog"], "true_g": true_g,
           "ring_size": ring}
    t0 = time.time()
    try:
        est = we.estimate_weight([img], ALLOY, ring_size=str(ring) if ring else None)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
        out["secs"] = round(time.time() - t0, 1)
        return out
    out["secs"] = round(time.time() - t0, 1)
    if est.get("_error"):
        out["error"] = est["_error"]
        return out
    pred = est.get("gold_weight_g")
    out["pred_g"] = pred
    out["volume_mm3"] = est.get("volume_mm3")
    out["confidence"] = est.get("confidence")
    out["uncalibrated"] = est.get("uncalibrated")
    out["models"] = est.get("models")
    if true_g and pred:
        out["abs_err_g"] = round(abs(pred - true_g), 3)
        out["abs_pct"] = round(abs(pred - true_g) / true_g * 100, 1)
        out["signed_pct"] = round((pred - true_g) / true_g * 100, 1)
    return out


def _summary(results: list[dict]) -> dict:
    ok = [r for r in results if r.get("pred_g") and r.get("true_g")]
    pcts = [r["abs_pct"] for r in ok if "abs_pct" in r]
    signed = [r["signed_pct"] for r in ok if "signed_pct" in r]
    errs = [r["abs_err_g"] for r in ok if "abs_err_g" in r]
    s = {"n_total": len(results), "n_ok": len(ok),
         "n_failed": len(results) - len(ok)}
    if pcts:
        s.update({
            "MAE_g": round(statistics.mean(errs), 3),
            "median_abs_pct": round(statistics.median(pcts), 1),
            "mean_abs_pct": round(statistics.mean(pcts), 1),
            "mean_signed_pct_bias": round(statistics.mean(signed), 1),
            "within_10pct": round(100 * sum(p <= 10 for p in pcts) / len(pcts), 0),
            "within_20pct": round(100 * sum(p <= 20 for p in pcts) / len(pcts), 0),
        })
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogs", default="c113,sol113,g80")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--smoke", action="store_true", help="run a single design")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(REPO, "ground_truth", "eval_results.json"))
    args = ap.parse_args()

    key = os.environ.get("FAL_KEY", "")
    if not key or "PASTE" in key:
        sys.exit("FAL_KEY not set in .env — edit line 11 of .env first.")

    cats = set(args.catalogs.split(","))
    data = json.load(open(DATASET))
    pool = [r for r in data if r.get("catalog") in cats
            and r.get("gold_weight_18k_g") and _image_path(r)]
    if args.smoke:
        pool = pool[:1]
    elif args.limit:
        # spread the limit across catalogs for style coverage
        by_cat: dict[str, list] = {}
        for r in pool:
            by_cat.setdefault(r["catalog"], []).append(r)
        pool, i = [], 0
        while len(pool) < args.limit and any(by_cat.values()):
            for c in list(by_cat):
                if by_cat[c]:
                    pool.append(by_cat[c].pop(0))
                if len(pool) >= args.limit:
                    break
            i += 1

    print(f"Running {len(pool)} design(s) | alloy={ALLOY} | workers={args.workers}")
    print(f"Models: {we.WEIGHT_MODEL_PRIMARY} + {we.WEIGHT_MODEL_SECONDARY}\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_eval_one, r): r for r in pool}
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            if r.get("pred_g"):
                flag = " ⚠uncal" if r.get("uncalibrated") else ""
                print(f"  {r['id']:>10}  true {r['true_g']:>5.2f}g  "
                      f"pred {r['pred_g']:>5.2f}g  "
                      f"({r.get('signed_pct', 0):>+5.1f}%)  "
                      f"{r.get('confidence', '—'):<6}{flag}  {r['secs']}s")
            else:
                print(f"  {r['id']:>10}  FAILED: {r.get('error', '?')[:80]}")

    s = _summary(results)
    print("\n" + "=" * 60)
    for k, v in s.items():
        print(f"  {k:>22}: {v}")
    json.dump({"summary": s, "results": results}, open(args.out, "w"), indent=2)
    print(f"\nsaved -> {args.out}")


if __name__ == "__main__":
    main()
