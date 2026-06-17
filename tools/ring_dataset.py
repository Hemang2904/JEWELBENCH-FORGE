"""Measure every ring STL on the drive into a real ring weight-distribution
dataset. Volume from trimesh (multiprocessing + a resumable cache), weight at
18k & 14k. Writes ground_truth/ring_weight_dataset.csv and prints the
distribution + a data-derived sanity band.

This is the ground truth for image -> measurement: the true weight distribution
of thousands of real ring designs (delicate solitaires through cocktail rings),
far wider than the 112-design catalog. Next step after this is extracting band
dims from each mesh to build image->dimension labels.

Usage:  /Users/hemang/dev/gtvenv/bin/python tools/ring_dataset.py
"""
import csv
import json
import os
import re
import statistics
import sys
from multiprocessing import Pool

import trimesh

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import weight_estimator as we

CACHE = os.path.join(REPO, "tools", "_ring_volcache.json")
PATHS_FILE = "/tmp/cad_paths.txt"
OUT = os.path.join(REPO, "ground_truth", "ring_weight_dataset.csv")
DENS18 = we.alloy_density("18k_yellow_gold")
DENS14 = we.alloy_density("14k_yellow_gold")

POS = re.compile(r"solit|soliratire|wedding|eternity|count 113|cocktel|cocktail|couple band|\bring\b", re.I)
NEG = re.compile(r"earring|necklace|pendant|bracelet|bangle|nose|chain", re.I)
# a real wearable ring's metal volume sits roughly in this window; outside it the
# STL is a sub-part (shank only), a non-ring, or a unit error.
RING_VOL_MIN, RING_VOL_MAX = 30.0, 4000.0


def ring_stls():
    out = []
    if not os.path.exists(PATHS_FILE):
        return out
    for line in open(PATHS_FILE):
        p = line.strip()
        if p.lower().endswith(".stl") and POS.search(p) and not NEG.search(p):
            fp = "/Volumes/Elements/" + p[2:] if p.startswith("./") else p
            out.append(fp)
    return sorted(set(out))


def measure(path):
    try:
        m = trimesh.load(path, force="mesh", process=False)
        v = abs(float(m.volume))
        return {"path": path, "vol": v, "faces": int(len(m.faces)),
                "wt": bool(m.is_watertight), "ok": v > 0}
    except Exception as e:
        return {"path": path, "vol": 0.0, "faces": 0, "wt": False, "ok": False,
                "err": f"{type(e).__name__}"}


def compute(paths, workers=5):
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [p for p in paths if p not in cache]
    print(f"to measure: {len(todo)} ({len(paths) - len(todo)} cached)", flush=True)
    if todo:
        with Pool(workers) as pool:
            for i, r in enumerate(pool.imap_unordered(measure, todo, chunksize=4), 1):
                cache[r["path"]] = r
                if i % 100 == 0:
                    json.dump(cache, open(CACHE, "w"))
                    print(f"  measured {i}/{len(todo)} ...", flush=True)
        json.dump(cache, open(CACHE, "w"))
    return {p: cache[p] for p in paths}


def pct(xs, p):
    return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))]


def main():
    paths = ring_stls()
    print(f"ring STLs matched: {len(paths)}", flush=True)
    res = compute(paths)

    rows = []
    for p, r in res.items():
        if not r["ok"]:
            continue
        v = r["vol"]
        rows.append({
            "path": p, "folder": os.path.basename(os.path.dirname(p)),
            "volume_mm3": round(v, 1),
            "weight_18k_g": round(we.volume_to_weight(v, DENS18), 2),
            "weight_14k_g": round(we.volume_to_weight(v, DENS14), 2),
            "faces": r["faces"], "watertight": r["wt"],
            "plausible_ring": RING_VOL_MIN <= v <= RING_VOL_MAX,
        })
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cols = ["path", "folder", "volume_mm3", "weight_18k_g", "weight_14k_g",
            "faces", "watertight", "plausible_ring"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["ok"]] if rows and "ok" in rows[0] else rows
    plaus = sorted(r["weight_18k_g"] for r in rows if r["plausible_ring"])
    nbad = len(rows) - len(plaus)
    print(f"\n=== RING WEIGHT DATASET ({len(rows)} measured, {OUT}) ===")
    print(f"plausible rings (30-4000 mm3): {len(plaus)}  |  filtered out-of-range: {nbad}")
    if plaus:
        print(f"\n18k gold weight (g) over {len(plaus)} real rings:")
        print(f"  min {plaus[0]:.1f}  p05 {pct(plaus,.05):.1f}  p25 {pct(plaus,.25):.1f}  "
              f"median {statistics.median(plaus):.1f}  mean {statistics.mean(plaus):.1f}  "
              f"p75 {pct(plaus,.75):.1f}  p95 {pct(plaus,.95):.1f}  p99 {pct(plaus,.99):.1f}  max {plaus[-1]:.1f}")
        from collections import Counter
        hist = Counter(min(20, int(x)) for x in plaus)
        print("  weight histogram (g bucket -> count):")
        for g in sorted(hist):
            bar = "#" * max(1, hist[g] * 40 // max(hist.values()))
            label = f"{g}" if g < 20 else "20+"
            print(f"   {label:>3} | {bar} {hist[g]}")
        print(f"\n  -> data-derived ring sanity band (18k):")
        print(f"     typical (p05-p95): {pct(plaus,.05):.1f} - {pct(plaus,.95):.1f} g")
        print(f"     hard (p01-p99 padded): {max(0.5, pct(plaus,.01)*0.8):.1f} - {pct(plaus,.99)*1.25:.1f} g")


if __name__ == "__main__":
    main()
