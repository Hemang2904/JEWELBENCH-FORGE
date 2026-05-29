"""mm <-> carat sizing for diamonds, derived from the Matrix Gold "Gem Size
List" feature (the screenshots provided as JEWELBENCH-FORGE reference).

Why this module exists
----------------------
A photo gives us *millimetre* stone dimensions, but pricing is keyed on
*carat* weight. Matrix Gold / GIA size charts convert one to the other with
well-known per-shape weight-estimation formulas. We reproduce those formulas
here so carat weight is DETERMINISTIC (auditable, no model guessing) once the
mm dimensions are known.

The classic estimation formula is:

    weight_ct = <average diameter or L*W> * depth_mm * shape_factor * girdle_adj

These factors are the industry-standard diamond weight-estimation constants.
They are validated against the supplied Matrix Gold screenshots:

  * Round  9.40 mm dia, 5.83 depth (62%):  9.40^2 * 5.83 * 0.0061  = 3.14 ct
           -> Matrix Gold shows 3.1 ct                                    OK
  * Oval   10.00 x 8.00 mm, 4.88 depth (61%): 10*8*4.88 * 0.0062   = 2.42 ct
           -> Matrix Gold shows 2.415 ct                                  OK

If depth is unknown we estimate it from a typical depth% for the shape (the
"%"' column in the Matrix Gold list), so a single mm reading still yields a
usable carat weight.
"""

from __future__ import annotations

import json
import os

# Industry-standard weight-estimation factors. Round/oval are pinned to the
# Matrix Gold reference; the rest are the widely published GIA-style constants.
SHAPE_FACTORS: dict[str, float] = {
    "round": 0.0061,
    "oval": 0.0062,
    "pear": 0.0061,
    "marquise": 0.00565,
    "heart": 0.0059,
    "emerald": 0.0080,    # step cut, ~1:1; rises with L:W ratio (see _emerald_adj)
    "radiant": 0.0081,
    "princess": 0.0083,   # square modified brilliant
    "cushion": 0.00815,
    "asscher": 0.0080,
    "trillion": 0.0057,
    "baguette": 0.00915,  # rectangular step
}

# Typical table depth % (depth / average-width) per shape. Matches the Matrix
# Gold "%" column (round ~62, oval ~61). Used only when depth is not measured.
TYPICAL_DEPTH_PCT: dict[str, float] = {
    "round": 0.62,
    "oval": 0.61,
    "pear": 0.60,
    "marquise": 0.60,
    "heart": 0.60,
    "emerald": 0.66,
    "radiant": 0.66,
    "princess": 0.72,
    "cushion": 0.66,
    "asscher": 0.70,
    "trillion": 0.40,
    "baguette": 0.62,
}

# Single-shape shapes are sized off one diameter; the rest off length & width.
_ROUND_LIKE = {"round"}

_DEFAULT_FACTOR = 0.0062
_DEFAULT_DEPTH_PCT = 0.62


def _norm(shape: str | None) -> str:
    return (shape or "round").strip().lower()


def _emerald_adj(factor: float, length_mm: float, width_mm: float) -> float:
    """Step-cut factor rises with the length-to-width ratio. Small correction
    so 2:1 baguettes/emeralds aren't under-weighed."""
    if width_mm <= 0:
        return factor
    ratio = length_mm / width_mm
    if ratio <= 1.05:
        return factor
    # +~0.0003 per 0.5 of ratio above 1.0 (matches published emerald tables)
    return factor + 0.0003 * ((ratio - 1.0) / 0.5)


def estimate_depth_mm(shape: str, length_mm: float, width_mm: float | None) -> float:
    """Estimate depth from typical depth% when it can't be measured.

    Depth% is conventionally relative to the SHORT axis (width). For round the
    width equals the diameter. This matches Matrix Gold: oval 8 mm wide * 61%
    = 4.88 mm depth (exactly the screenshot value).
    """
    shape = _norm(shape)
    if shape in _ROUND_LIKE or width_mm is None:
        base = length_mm
    else:
        base = width_mm
    return round(base * TYPICAL_DEPTH_PCT.get(shape, _DEFAULT_DEPTH_PCT), 3)


def mm_to_carat(
    shape: str,
    length_mm: float,
    width_mm: float | None = None,
    depth_mm: float | None = None,
    girdle_pct: float = 0.0,
) -> float:
    """Convert millimetre dimensions to estimated carat weight.

    shape       diamond cut (round, oval, pear, ...)
    length_mm   long axis; for round this is the diameter
    width_mm    short axis; defaults to length for round/square
    depth_mm    table-to-culet depth; estimated from depth% if omitted
    girdle_pct  girdle allowance added on top (industry default ~1.5%)

    Returns carat weight rounded to 3 dp.
    """
    shape = _norm(shape)
    if length_mm <= 0:
        return 0.0
    if width_mm is None:
        width_mm = length_mm
    if depth_mm is None or depth_mm <= 0:
        depth_mm = estimate_depth_mm(shape, length_mm, width_mm)

    factor = SHAPE_FACTORS.get(shape, _DEFAULT_FACTOR)
    if shape in ("emerald", "radiant", "baguette"):
        factor = _emerald_adj(factor, length_mm, width_mm)

    if shape in _ROUND_LIKE:
        base = length_mm * length_mm  # diameter^2
    else:
        base = length_mm * width_mm

    girdle_adj = 1.0 + max(0.0, girdle_pct) / 100.0
    carat = base * depth_mm * factor * girdle_adj
    return round(carat, 3)


def carat_to_round_mm(carat: float, depth_pct: float | None = None) -> float:
    """Inverse for ROUND stones: estimate diameter (mm) from carat weight.

    Useful when the user gives a target stone size in carats and we need to
    render/spec the matching millimetre diameter. Solves
        ct = dia^2 * (dia * depth_pct) * factor * girdle_adj   for dia.
    """
    if carat <= 0:
        return 0.0
    depth_pct = depth_pct or TYPICAL_DEPTH_PCT["round"]
    factor = SHAPE_FACTORS["round"]
    girdle_adj = 1.0
    # ct = dia^3 * depth_pct * factor * girdle_adj
    denom = depth_pct * factor * girdle_adj
    dia_cubed = carat / denom
    return round(dia_cubed ** (1.0 / 3.0), 2)


# ── FIXED CHART LOOKUP ──────────────────────────────────────────────────────
# The gem_size_chart.json file is the authoritative "fixed weight" chart (Matrix
# Gold / GIA style). We look up the nearest stocked size; the formula above is
# only the fallback for sizes the chart doesn't cover.

_CHART_CACHE: dict | None = None


def load_gem_chart(path: str | None = None) -> dict:
    """Load the fixed mm->carat chart. Cached after first read."""
    global _CHART_CACHE
    if _CHART_CACHE is not None:
        return _CHART_CACHE
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "gem_size_chart.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _CHART_CACHE = json.load(f)
    except (OSError, ValueError):
        _CHART_CACHE = {}
    return _CHART_CACHE


def chart_lookup_carat(
    shape: str,
    length_mm: float,
    width_mm: float | None = None,
    chart: dict | None = None,
) -> tuple[float, str]:
    """Return (carat, source) for a stone from the fixed chart.

    source is "chart" for a chart hit (nearest size), or "formula" when the
    shape/size isn't in the chart and we fall back to the estimation formula.
    Round stones are interpolated between the two nearest diameters; fancy
    shapes snap to the nearest (length,width) row.
    """
    shape = _norm(shape)
    chart = chart if chart is not None else load_gem_chart()
    rows = chart.get(shape)
    if not rows or length_mm <= 0:
        return mm_to_carat(shape, length_mm, width_mm), "formula"

    if shape in _ROUND_LIKE:
        pts = sorted((float(r["mm"]), float(r["ct"])) for r in rows)
        # exact / nearest with linear interpolation between neighbours
        for i, (mm, ct) in enumerate(pts):
            if abs(mm - length_mm) < 1e-6:
                return round(ct, 3), "chart"
            if mm > length_mm:
                if i == 0:
                    return round(ct, 3), "chart"
                mm0, ct0 = pts[i - 1]
                frac = (length_mm - mm0) / (mm - mm0)
                return round(ct0 + frac * (ct - ct0), 3), "chart"
        return round(pts[-1][1], 3), "chart"

    # fancy: nearest (length,width) by squared distance
    if width_mm is None:
        width_mm = length_mm
    best = min(
        rows,
        key=lambda r: (float(r["length_mm"]) - length_mm) ** 2
        + (float(r["width_mm"]) - width_mm) ** 2,
    )
    return round(float(best["ct"]), 3), "chart"


def price_dimensions_to_groups(stones: list[dict]) -> list[dict]:
    """Normalise a list of measured stones into priced-ready diamond groups.

    Each input stone may carry mm dimensions and a count; we attach the
    estimated per-stone carat (carat_each) and total carat so the existing
    pricing path (which is carat-based) can consume it unchanged.
    """
    out: list[dict] = []
    for s in stones:
        shape = _norm(s.get("shape"))
        carat_each = s.get("carat_each")
        carat_source = "given"
        if carat_each is None:
            length = float(s.get("length_mm") or s.get("mm_each") or 0)
            width = s.get("width_mm")
            # Fixed chart first; formula only if the chart can't cover it.
            carat_each, carat_source = chart_lookup_carat(shape, length, width)
        count = int(s.get("count", 1))
        out.append({
            **s,
            "shape": shape,
            "count": count,
            "carat_each": round(float(carat_each), 3),
            "carat_source": carat_source,
            "total_carat": round(float(carat_each) * count, 3),
        })
    return out


if __name__ == "__main__":
    # Sanity check against the Matrix Gold screenshots.
    r = mm_to_carat("round", 9.40, depth_mm=5.83, girdle_pct=0)
    o = mm_to_carat("oval", 10.00, 8.00, 4.88, girdle_pct=0)
    print(f"round 9.40/5.83 -> {r} ct (Matrix Gold: 3.1)")
    print(f"oval 10x8/4.88  -> {o} ct (Matrix Gold: 2.415)")
    print(f"carat_to_round_mm(3.1) -> {carat_to_round_mm(3.1)} mm (expect ~9.4)")
