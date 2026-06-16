"""Assemble the jewelry ground-truth TABLE: design -> true gold weight, geometry
volume, dimensions. Pulls three sources and writes one clean CSV:

  1. STL-mesh sets (EZ-NK, EZ-PS, ...): parsed records (design+product+sheet
     gold weight + classified STL part paths) come from the parse workflow, saved
     to tools/parsed_records.json. This script computes each part's mesh volume
     with trimesh (multiprocessing), sums the parts per product, and cross-checks
     the geometry-derived weight against the sheet's stated gold weight.
  2. GR set (mira "Gold Weight reduction results.xlsx"): the sheet already carries
     Volume + Weight per part — used directly (no mesh needed).
  3. C113 catalog: gold_18k_g + dims from ground_truth/c113_true_dims.json.

Usage:  /Users/hemang/dev/gtvenv/bin/python tools/build_truth_table.py
"""
import csv
import json
import math
import os
import statistics
from multiprocessing import Pool

import trimesh

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DENS_18K = 15.6  # g/cm^3 — matches the EZ NK 1001 mesh-vs-sheet check
GR_XLSX = ("/Volumes/Elements/mira files/Customer trials/Customer trials/"
           "Akaar/Aurous/Aurous/Gold Weight reduction results.xlsx")
PARSED = os.path.join(REPO, "tools", "parsed_records.json")
OUT = os.path.join(REPO, "ground_truth", "truth_table.csv")

COLS = ["design_id", "set", "product", "sheet_gold_weight_g", "geom_volume_mm3",
        "geom_weight_g_18k", "match_qty", "geom_over_sheet", "confidence",
        "diamond_ct", "diamond_count", "height_mm", "width_mm", "n_stl_parts",
        "watertight_all", "source", "notes"]


def assign_confidence(r):
    """Separate trustworthy labels from review-needed ones.
      high        : independently clean (C113 weights, GR volume+weight) OR a
                    mesh whose geometry weight matches the sheet within +/-15%.
      review      : has a sheet weight + mesh, but they disagree (matching/qty
                    issue — single-unit STL, missing part, etc.).
      weight-only : usable gold weight, no geometry to cross-check.
      geom-only   : measurable geometry but the sheet has no gold weight
                    (e.g. pendant sheets list only diamonds).
    """
    sw = r.get("sheet_gold_weight_g")
    ratio = r.get("geom_over_sheet")
    if r["set"] in ("C113", "GR"):
        return "high"
    if r["source"] == "stl-mesh":
        if not sw:
            return "geom-only"
        return "high" if (ratio and 0.85 <= ratio <= 1.15) else "review"
    # sheet-only
    return "weight-only" if sw else "no-data"


# ── geometry: STL -> volume ─────────────────────────────────────────────────────
def stl_volume(path):
    try:
        m = trimesh.load(path, force="mesh", process=False)
        wt0 = bool(m.is_watertight)
        v = abs(float(m.volume))
        repaired = False
        if not wt0:
            try:
                m.merge_vertices()
                trimesh.repair.fill_holes(m)
                if m.is_watertight:
                    v = abs(float(m.volume)); repaired = True
            except Exception:
                pass
        return {"path": path, "volume_mm3": v, "watertight": bool(m.is_watertight),
                "faces": int(len(m.faces)), "repaired": repaired, "ok": v > 0}
    except Exception as e:
        return {"path": path, "volume_mm3": 0.0, "watertight": False, "faces": 0,
                "repaired": False, "ok": False, "err": f"{type(e).__name__}: {e}"}


VOLCACHE = os.path.join(REPO, "tools", "_volcache.json")


def compute_volumes(paths, workers=5):
    uniq = sorted({p for p in paths if p})
    if not uniq:
        return {}
    cache = json.load(open(VOLCACHE)) if os.path.exists(VOLCACHE) else {}
    todo = [p for p in uniq if p not in cache]
    if todo:
        print(f"computing mesh volume for {len(todo)} new STL files on {workers} workers "
              f"({len(uniq)-len(todo)} cached) ...")
        with Pool(workers) as pool:
            for r in pool.map(stl_volume, todo):
                cache[r["path"]] = r
        json.dump(cache, open(VOLCACHE, "w"))
    else:
        print(f"all {len(uniq)} STL volumes from cache")
    res = {p: cache[p] for p in uniq}
    bad = [r for r in res.values() if not r["ok"]]
    if bad:
        print(f"  {len(bad)} STL files failed/empty (first few): "
              + "; ".join(os.path.basename(b['path']) + ' ' + b.get('err', 'vol=0') for b in bad[:4]))
    return res


def build_geom_rows(records, volmap):
    rows = []
    for rec in records:
        paths = rec.get("stl_paths") or []
        parts = [volmap[p] for p in paths if volmap.get(p) and volmap[p]["ok"]]
        vol = sum(p["volume_mm3"] for p in parts) if parts else None
        gw = round(vol * DENS_18K / 1000, 3) if vol else None
        sw = rec.get("sheet_gold_weight_g")
        # Earrings are quoted/sold as a PAIR; the STL is a single earring.
        qty = 2 if "EARRING" in rec.get("product", "").upper() else 1
        ratio = round(gw * qty / sw, 3) if (gw and sw) else None
        wt_all = all(p["watertight"] for p in parts) if parts else None
        rows.append({
            "design_id": rec["design_id"], "set": rec["set"], "product": rec.get("product", ""),
            "sheet_gold_weight_g": sw, "geom_volume_mm3": round(vol, 1) if vol else None,
            "geom_weight_g_18k": gw, "match_qty": qty, "geom_over_sheet": ratio,
            "diamond_ct": rec.get("diamond_ct"), "diamond_count": rec.get("diamond_count"),
            "height_mm": rec.get("height_mm"), "width_mm": rec.get("width_mm"),
            "n_stl_parts": len(parts), "watertight_all": wt_all,
            "source": "stl-mesh" if parts else "sheet-only", "notes": rec.get("notes", "")})
    return rows


# ── GR set: sheet already has Volume + Weight ───────────────────────────────────
def _num(x):
    """Coerce a cell to float — these sheets often store numbers as TEXT."""
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).strip())
    except (ValueError, TypeError):
        return None


def load_gr():
    rows = []
    if not os.path.exists(GR_XLSX):
        return rows
    import openpyxl
    wb = openpyxl.load_workbook(GR_XLSX, data_only=True, read_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            if not row or len(row) < 5:
                continue
            name, vol, dens, wt = row[1], _num(row[2]), row[3], _num(row[4])
            if not (vol and wt and name):
                continue
            rows.append({
                "design_id": str(name).strip(), "set": "GR", "product": "PART",
                "sheet_gold_weight_g": round(float(wt), 3), "geom_volume_mm3": round(float(vol), 1),
                "geom_weight_g_18k": round(float(vol) * DENS_18K / 1000, 3),
                "match_qty": 1, "geom_over_sheet": None,
                "diamond_ct": None, "diamond_count": None, "height_mm": None, "width_mm": None,
                "n_stl_parts": 0, "watertight_all": None,
                "source": "sheet-volume", "notes": f"vol+wt from sheet (density {dens})"})
    return rows


# ── C113 catalog ────────────────────────────────────────────────────────────────
def load_113():
    rows = []
    p = os.path.join(REPO, "ground_truth", "c113_true_dims.json")
    if not os.path.exists(p):
        return rows
    for r in json.load(open(p)):
        if r.get("unreadable"):
            continue
        w = r.get("gold_18k_g")
        if not isinstance(w, (int, float)) or w <= 0:
            continue
        rows.append({
            "design_id": f"C113-{r['page']:03d}", "set": "C113", "product": "RING",
            "sheet_gold_weight_g": float(w), "geom_volume_mm3": None, "geom_weight_g_18k": None,
            "match_qty": 1, "geom_over_sheet": None, "diamond_ct": None, "diamond_count": None,
            "height_mm": r.get("ring_height_mm"), "width_mm": r.get("band_width_mm"),
            "n_stl_parts": 0, "watertight_all": None, "source": "catalog-pdf",
            "notes": f"ring_size {r.get('ring_size_us')}"})
    return rows


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in COLS})


def corr(pairs):
    if len(pairs) < 3:
        return float("nan")
    xs, ys = [a for a, b in pairs], [b for a, b in pairs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in pairs)
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs)); sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return cov / (sx * sy) if sx and sy else float("nan")


def main():
    records = json.load(open(PARSED)) if os.path.exists(PARSED) else []
    paths = [p for r in records for p in (r.get("stl_paths") or [])]
    print(f"parsed records: {len(records)} | STL paths referenced: {len(paths)}")
    volmap = compute_volumes(paths)
    rows = build_geom_rows(records, volmap) + load_gr() + load_113()
    for r in rows:
        r["confidence"] = assign_confidence(r)
    write_csv(rows, OUT)

    # ── summary ──
    from collections import Counter
    print("\n=== TRUTH TABLE SUMMARY ===")
    print(f"total rows: {len(rows)}  ->  {OUT}")
    print("\nby set:")
    by_set = {}
    for r in rows:
        by_set.setdefault(r["set"], []).append(r)
    for s, rs in sorted(by_set.items()):
        print(f"  {s:8} {len(rs):4d} rows  ({sum(1 for r in rs if r['source']=='stl-mesh')} mesh-matched)")
    print("\nby confidence:")
    conf = Counter(r["confidence"] for r in rows)
    for c in ("high", "weight-only", "review", "geom-only", "no-data"):
        if conf.get(c):
            print(f"  {c:12} {conf[c]:4d}")
    # cross-check on the mesh rows that HAVE a sheet weight (after earring x2)
    mesh = [r for r in rows if r["source"] == "stl-mesh" and r["geom_over_sheet"]]
    if mesh:
        ratios = [r["geom_over_sheet"] for r in mesh]
        good = [r for r in mesh if 0.85 <= r["geom_over_sheet"] <= 1.15]
        print(f"\nmesh-vs-sheet cross-check ({len(mesh)} products, earrings x2):")
        print(f"  geom/sheet: median {statistics.median(ratios):.2f}  "
              f"within +/-15%: {len(good)} ({100*len(good)/len(mesh):.0f}%)")
    print(f"\nTRUSTWORTHY now (confidence high or weight-only): "
          f"{sum(1 for r in rows if r['confidence'] in ('high','weight-only'))} rows")
    print(f"geometry measured (any): {sum(1 for r in rows if r['geom_volume_mm3'])} rows")


if __name__ == "__main__":
    main()
