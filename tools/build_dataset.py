"""Build a weight-estimation dataset by running jewelry images through the
ensemble (Gemini 2.5 Pro primary) and recording the extracted features + the
estimate alongside a GROUND-TRUTH weight column.

Run locally with your key:
    FAL_KEY=your_key python tools/build_dataset.py manifest.csv --out dataset.csv

manifest.csv columns (header required):
    piece_id, ring_size, metal, actual_weight_g, images
      - ring_size       : US size (required — the scale anchor)
      - metal           : e.g. 18k_yellow_gold (defaults to 18k_yellow_gold)
      - actual_weight_g : the TRUE weighed/CAD weight (leave blank if unknown)
      - images          : one or more local paths OR urls, separated by ';'

IMPORTANT — about labels:
  Gemini 2.5 Pro produces the INPUT FEATURES (dimensions, stones, construction)
  and a BASELINE estimate. It is NOT a source of training labels for weight —
  a model can't learn to beat its teacher. For training a better model, fill
  `actual_weight_g` from CAD (exact) or a scale. With ground truth present we
  auto-compute error_pct so you can measure how good the formulas/ensemble are.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sizing  # noqa: E402
import weight_estimator as we  # noqa: E402

FIELDS = [
    "piece_id", "ring_size", "metal", "actual_weight_g",
    "gold_weight_est_g", "error_pct",
    "inner_diameter_mm", "band_width_mm", "band_thickness_mm",
    "band_construction", "head_diameter_mm", "head_height_mm",
    "shank_volume_mm3", "total_volume_mm3",
    "n_stone_groups", "total_stone_carat",
    "confidence", "model_disagreement", "models", "errors",
    "estimate_json",
]


def _to_urls(images: list[str]) -> list[str]:
    """Local paths -> fal uploads; URLs passed through."""
    import fal_client
    urls = []
    for img in images:
        img = img.strip()
        if not img:
            continue
        if img.startswith(("http://", "https://")):
            urls.append(img)
        else:
            with open(img, "rb") as f:
                urls.append(fal_client.upload(f.read(), content_type="image/png"))
    return urls


def _row(rec: dict) -> dict:
    images = [s for s in (rec.get("images") or "").split(";") if s.strip()]
    metal = (rec.get("metal") or "18k_yellow_gold").strip()
    ring_size = (rec.get("ring_size") or "").strip()
    actual = (rec.get("actual_weight_g") or "").strip()

    urls = _to_urls(images)
    est = we.estimate_weight(urls, metal, ring_size or None)

    kd = est.get("key_dimensions_mm") or {}
    groups = sizing.price_dimensions_to_groups(est.get("stones") or [])
    total_ct = round(sum(g["total_carat"] for g in groups), 3)
    gw = est.get("gold_weight_g")

    err = ""
    if actual and gw:
        try:
            err = round(100 * (float(gw) - float(actual)) / float(actual), 1)
        except (ValueError, ZeroDivisionError):
            err = ""

    return {
        "piece_id": rec.get("piece_id", ""),
        "ring_size": ring_size,
        "metal": metal,
        "actual_weight_g": actual,
        "gold_weight_est_g": gw,
        "error_pct": err,
        "inner_diameter_mm": est.get("inner_diameter_mm"),
        "band_width_mm": kd.get("band_width"),
        "band_thickness_mm": kd.get("band_thickness"),
        "band_construction": est.get("band_construction"),
        "head_diameter_mm": kd.get("head_diameter"),
        "head_height_mm": kd.get("head_height"),
        "shank_volume_mm3": est.get("shank_volume_mm3"),
        "total_volume_mm3": est.get("volume_mm3"),
        "n_stone_groups": len(groups),
        "total_stone_carat": total_ct,
        "confidence": est.get("confidence"),
        "model_disagreement": est.get("model_disagreement"),
        "models": ";".join(str(m) for m in est.get("models", [])),
        "errors": json.dumps(est.get("errors") or []),
        "estimate_json": json.dumps(est, default=str),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a weight dataset via the ensemble.")
    ap.add_argument("manifest", help="CSV with piece_id,ring_size,metal,actual_weight_g,images")
    ap.add_argument("--out", default="dataset.csv")
    args = ap.parse_args()

    if not os.environ.get("FAL_KEY"):
        sys.exit("Set FAL_KEY first:  FAL_KEY=your_key python tools/build_dataset.py ...")

    with open(args.manifest, newline="") as f:
        records = list(csv.DictReader(f))
    if not records:
        sys.exit("manifest is empty")

    rows, errors = [], []
    for i, rec in enumerate(records, 1):
        pid = rec.get("piece_id", f"row{i}")
        try:
            row = _row(rec)
            rows.append(row)
            msg = f"[{i}/{len(records)}] {pid}: est {row['gold_weight_est_g']} g"
            if row["error_pct"] != "":
                msg += f" vs actual {row['actual_weight_g']} ({row['error_pct']:+}% )"
            msg += f"  · models: {row['models'] or 'NONE'}"
            print(msg)
        except Exception as e:
            print(f"[{i}/{len(records)}] {pid}: FAILED — {e}")
            errors.append((pid, str(e)))

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    # quick accuracy summary if any ground truth present
    errs = [abs(r["error_pct"]) for r in rows if r["error_pct"] != ""]
    print(f"\nWrote {len(rows)} rows -> {args.out}  ({len(errors)} failed)")
    if errs:
        errs.sort()
        mae = sum(errs) / len(errs)
        med = errs[len(errs) // 2]
        within10 = 100 * sum(1 for e in errs if e <= 10) / len(errs)
        print(f"Accuracy vs ground truth (n={len(errs)}): "
              f"mean abs err {mae:.1f}% · median {med:.1f}% · within ±10%: {within10:.0f}%")


if __name__ == "__main__":
    main()
