"""Generate gem_size_chart.json — a FIXED mm -> carat lookup chart per shape,
mirroring the Matrix Gold "Gem Size List" feature.

We seed the chart with values computed from the validated weight-estimation
formulas in sizing.py (which match the Matrix Gold screenshots to ~1%), at the
standard millimetre steps a jeweller actually stocks. Replace any row with the
exact figures from a published online chart (GIA / supplier) when available —
the app reads whatever is in gem_size_chart.json, it does not recompute.

Run from repo root:  python tools/build_gem_chart.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sizing import mm_to_carat  # noqa: E402

# Standard round-brilliant diameters carried by most suppliers (mm).
ROUND_MM = [
    0.8, 0.9, 1.0, 1.1, 1.2, 1.25, 1.3, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75,
    3.0, 3.25, 3.5, 3.75, 4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 5.75,
    6.0, 6.25, 6.5, 6.75, 7.0, 7.25, 7.5, 7.75, 8.0, 8.25, 8.5, 8.75,
    9.0, 9.2, 9.4, 9.5, 9.6, 9.8, 10.0, 10.5, 11.0, 11.5, 12.0,
]

# Standard length x width pairs (mm) for fancy shapes.
FANCY_MM = {
    "oval":     [(4,3),(5,3),(6,4),(7,5),(8,6),(9,7),(10,8),(11,9),(12,10),(14,10)],
    "pear":     [(4,3),(5,3),(6,4),(7,5),(8,5),(9,6),(10,7),(11,7),(12,8),(14,9)],
    "marquise": [(4,2),(5,2.5),(6,3),(7,3.5),(8,4),(9,4.5),(10,5),(12,6),(14,7)],
    "emerald":  [(4,3),(5,3),(6,4),(7,5),(8,6),(9,7),(10,8),(11,9),(12,10)],
    "radiant":  [(4,3),(5,4),(6,4),(7,5),(8,6),(9,7),(10,8),(11,9)],
    "cushion":  [(3,3),(4,4),(5,5),(6,6),(7,7),(8,8),(9,9),(10,10),(11,11)],
    "princess": [(2,2),(3,3),(3.5,3.5),(4,4),(4.5,4.5),(5,5),(5.5,5.5),(6,6),(6.5,6.5),(7,7),(8,8)],
    "asscher":  [(3,3),(4,4),(5,5),(6,6),(7,7),(8,8),(9,9),(10,10)],
    "heart":    [(4,4),(5,5),(6,6),(7,7),(8,8),(9,9),(10,10),(11,11)],
    "trillion": [(3,3),(4,4),(5,5),(6,6),(7,7),(8,8),(9,9)],
    "baguette": [(2,1),(3,1.5),(3,2),(4,2),(5,2.5),(5,3),(6,3),(7,3)],
}


def build() -> dict:
    chart: dict = {
        "_comment": (
            "FIXED mm->carat gem size chart (Matrix Gold / GIA style). "
            "Seeded from sizing.py formulas; edit rows to match a published "
            "chart. App looks up nearest size, it does not recompute."
        ),
        "_source": "seed: sizing.py weight-estimation formulas (Matrix Gold validated)",
        "round": [],
    }
    for d in ROUND_MM:
        chart["round"].append({"mm": d, "ct": mm_to_carat("round", d, girdle_pct=0.0)})
    for shape, pairs in FANCY_MM.items():
        rows = []
        for length, width in pairs:
            rows.append({
                "length_mm": length,
                "width_mm": width,
                "ct": mm_to_carat(shape, length, width, girdle_pct=0.0),
            })
        chart[shape] = rows
    return chart


if __name__ == "__main__":
    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "gem_size_chart.json",
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(build(), f, indent=2)
    print(f"wrote {out_path}")
