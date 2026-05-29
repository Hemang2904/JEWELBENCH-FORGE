"""Live gold rates + editable diamond rate lookup for the BoM card.

Gold rates come from two public, free, no-auth endpoints (gold-api.com for
XAU spot, exchangerate-api.com for USD/INR) and are cached process-wide for
one hour. If either endpoint is unreachable we fall back to a last-known
sane value so the BoM still renders.

Diamond rates are NOT live — there is no free, trusted, public diamond
price API (Rapaport is licensed). diamond_rates.json ships an editable
reference table; the UI labels every diamond cost as "reference rate".
"""

import json
import os
import time
import urllib.request


_GOLD_CACHE = {"ts": 0.0, "data": None}
_CACHE_TTL_SEC = 3600

_GOLD_OZ_TO_G = 31.1034768
_MARKET_SPREAD = 1.05

_FALLBACK_GOLD_USD_PER_OZ = 2650.0
_FALLBACK_PLATINUM_USD_PER_OZ = 950.0
_FALLBACK_USD_INR = 84.5

_ALLOY_PURITY = {
    "24k": 0.999,
    "22k": 22 / 24,
    "18k": 18 / 24,
    "14k": 14 / 24,
    "10k": 10 / 24,
    "pt950": 0.95,
    "ag925": 0.925,
}

# Manufacturing line items in USD. Tune to your shop.
LABOUR_USD_PER_G = float(os.environ.get("LABOUR_USD_PER_G", "4.00"))
CAD_FLAT_USD = float(os.environ.get("CAD_FLAT_USD", "15.00"))
WAX_FLAT_USD = float(os.environ.get("WAX_FLAT_USD", "5.00"))
CASTING_FLAT_USD = float(os.environ.get("CASTING_FLAT_USD", "8.00"))
POLISH_FLAT_USD = float(os.environ.get("POLISH_FLAT_USD", "6.00"))
SETTING_USD_PER_STONE = float(os.environ.get("SETTING_USD_PER_STONE", "0.40"))

# Markup multiplier on metal value (1.0 = at-cost). Used by price_bom.
METAL_MARKUP = float(os.environ.get("METAL_MARKUP", "1.0"))

# Full alloy name -> gold-rate bucket key (shared by compute_costs & price_bom).
ALLOY_TO_RATE_KEY = {
    "24k_yellow_gold": "24k", "22k_yellow_gold": "22k",
    "18k_yellow_gold": "18k", "18k_white_gold": "18k", "18k_rose_gold": "18k",
    "14k_yellow_gold": "14k", "14k_white_gold": "14k", "14k_rose_gold": "14k",
    "10k_yellow_gold": "10k", "platinum_950": "pt950", "silver_925": "ag925",
}


def _fetch_json(url: str, timeout: int = 8) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "JewelBenchMixMatch/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def get_gold_rates() -> dict:
    """Return live gold + FX rates, cached for an hour. Falls back gracefully."""
    now = time.time()
    if _GOLD_CACHE["data"] and now - _GOLD_CACHE["ts"] < _CACHE_TTL_SEC:
        return _GOLD_CACHE["data"]

    usd_per_oz = _FALLBACK_GOLD_USD_PER_OZ
    usd_inr = _FALLBACK_USD_INR
    pt_usd_per_oz = _FALLBACK_PLATINUM_USD_PER_OZ
    live_gold = False
    live_fx = False
    live_pt = False
    source_bits = []

    gold = _fetch_json("https://api.gold-api.com/price/XAU")
    if gold and "price" in gold:
        try:
            usd_per_oz = float(gold["price"])
            live_gold = True
            source_bits.append("gold-api.com")
        except (TypeError, ValueError):
            pass

    pt = _fetch_json("https://api.gold-api.com/price/XPT")
    if pt and "price" in pt:
        try:
            pt_usd_per_oz = float(pt["price"])
            live_pt = True
        except (TypeError, ValueError):
            pass

    fx = _fetch_json("https://api.exchangerate-api.com/v4/latest/USD")
    if fx and isinstance(fx.get("rates"), dict) and "INR" in fx["rates"]:
        try:
            usd_inr = float(fx["rates"]["INR"])
            live_fx = True
            source_bits.append("exchangerate-api.com")
        except (TypeError, ValueError):
            pass

    if not source_bits:
        source = "fallback (network unavailable)"
    else:
        source = " + ".join(source_bits)

    usd_per_g_pure = usd_per_oz / _GOLD_OZ_TO_G * _MARKET_SPREAD
    inr_per_g_pure = usd_per_g_pure * usd_inr
    pt_usd_per_g_pure = pt_usd_per_oz / _GOLD_OZ_TO_G * _MARKET_SPREAD
    pt_inr_per_g_pure = pt_usd_per_g_pure * usd_inr

    per_g = {}
    for alloy, purity in _ALLOY_PURITY.items():
        if alloy == "pt950":
            per_g[f"{alloy}_usd"] = round(pt_usd_per_g_pure * purity, 2)
            per_g[f"{alloy}_inr"] = round(pt_inr_per_g_pure * purity, 2)
        elif alloy == "ag925":
            per_g[f"{alloy}_usd"] = round(0.80 * purity, 2)
            per_g[f"{alloy}_inr"] = round(0.80 * purity * usd_inr, 2)
        else:
            per_g[f"{alloy}_usd"] = round(usd_per_g_pure * purity, 2)
            per_g[f"{alloy}_inr"] = round(inr_per_g_pure * purity, 2)

    out = {
        "usd_per_oz_xau": round(usd_per_oz, 2),
        "usd_per_oz_xpt": round(pt_usd_per_oz, 2),
        "usd_inr": round(usd_inr, 4),
        "ts": int(now),
        "source": source,
        "live_gold": live_gold,
        "live_pt": live_pt,
        "live_fx": live_fx,
        "per_g": per_g,
    }
    _GOLD_CACHE["data"] = out
    _GOLD_CACHE["ts"] = now
    return out


_DIAMOND_RATES_CACHE: dict = {}


def load_diamond_rates(path: str | None = None) -> dict:
    """Load the editable diamond rate sheet. Cached on first call."""
    global _DIAMOND_RATES_CACHE
    if _DIAMOND_RATES_CACHE:
        return _DIAMOND_RATES_CACHE
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "diamond_rates.json")
    with open(path, "r", encoding="utf-8") as f:
        _DIAMOND_RATES_CACHE = json.load(f)
    return _DIAMOND_RATES_CACHE


def _lookup_round_rate(carat: float, rate_table: list[dict]) -> float:
    for bucket in rate_table:
        if bucket["min_ct"] <= carat < bucket["max_ct"]:
            return float(bucket["rate"])
    return float(rate_table[-1]["rate"])


def price_single_stone(
    carat: float, shape: str, clarity_tier: str, rates: dict
) -> float:
    """USD price for a single stone given carat, shape, and clarity tier."""
    base = _lookup_round_rate(carat, rates["round_rates_usd_per_ct"])
    shape_factor = rates["shape_factor"].get(shape.lower(), 0.80)
    clarity_factor = rates["clarity_color_factor"].get(
        clarity_tier.lower(), 1.0
    )
    return base * shape_factor * clarity_factor * carat


def price_stone_group(group: dict, rates: dict) -> dict:
    """Compute price for one diamond group line (count × carat_each)."""
    count = int(group.get("count", 1))
    carat_each = float(group.get("carat_each", 0))
    shape = (group.get("shape") or "round").lower()
    clarity = (group.get("clarity") or "fine").lower()
    unit_usd = price_single_stone(carat_each, shape, clarity, rates)
    line_total_usd = unit_usd * count
    return {
        **group,
        "shape": shape,
        "clarity": clarity,
        "count": count,
        "carat_each": carat_each,
        "total_carat": round(count * carat_each, 4),
        "unit_usd": round(unit_usd, 2),
        "line_total_usd": round(line_total_usd, 2),
    }


def compute_costs(bom: dict, gold_rates: dict, diamond_rates: dict) -> dict:
    """Roll up metal + stones + manufacturing into a costed BoM."""
    metal = bom.get("metal") or {}
    weight_g = float(metal.get("estimated_weight_grams") or 0.0)
    alloy = (metal.get("alloy") or "18k_yellow_gold").lower()

    alloy_to_rate_key = {
        "24k_yellow_gold": "24k",
        "22k_yellow_gold": "22k",
        "18k_yellow_gold": "18k",
        "18k_white_gold": "18k",
        "18k_rose_gold": "18k",
        "14k_yellow_gold": "14k",
        "14k_white_gold": "14k",
        "14k_rose_gold": "14k",
        "10k_yellow_gold": "10k",
        "platinum_950": "pt950",
        "silver_925": "ag925",
    }
    rate_key = alloy_to_rate_key.get(alloy, "18k")
    metal_usd_per_g = gold_rates["per_g"].get(f"{rate_key}_usd", 0.0)
    metal_inr_per_g = gold_rates["per_g"].get(f"{rate_key}_inr", 0.0)
    metal_cost_usd = round(weight_g * metal_usd_per_g, 2)

    diamond_groups_in = bom.get("diamonds") or []
    diamond_groups = [
        price_stone_group(
            {
                "location": g.get("location", "unspecified"),
                "shape": g.get("shape", "round"),
                "count": g.get("count", 1),
                "carat_each": g.get("carat_each", 0),
                "clarity": g.get("clarity_tier") or g.get("clarity") or "fine",
                "mm_each": g.get("mm_each"),
                "setting": g.get("setting", "prong"),
            },
            diamond_rates,
        )
        for g in diamond_groups_in
    ]
    stone_total_usd = round(sum(g["line_total_usd"] for g in diamond_groups), 2)
    stone_total_count = sum(g["count"] for g in diamond_groups)
    stone_total_carat = round(
        sum(g["total_carat"] for g in diamond_groups), 4
    )

    labour_usd = round(weight_g * LABOUR_USD_PER_G, 2)
    setting_usd = round(stone_total_count * SETTING_USD_PER_STONE, 2)
    manufacturing = {
        "cad_usd": CAD_FLAT_USD,
        "wax_usd": WAX_FLAT_USD,
        "casting_usd": CASTING_FLAT_USD,
        "polishing_usd": POLISH_FLAT_USD,
        "stone_setting_usd": setting_usd,
        "labour_usd": labour_usd,
    }
    manufacturing_total_usd = round(sum(manufacturing.values()), 2)

    total_usd = round(metal_cost_usd + stone_total_usd + manufacturing_total_usd, 2)
    usd_inr = gold_rates["usd_inr"]

    def to_inr(v: float) -> float:
        return round(v * usd_inr, 2)

    return {
        "metal": {
            "alloy": alloy,
            "weight_g": weight_g,
            "rate_usd_per_g": metal_usd_per_g,
            "rate_inr_per_g": metal_inr_per_g,
            "total_usd": metal_cost_usd,
            "total_inr": to_inr(metal_cost_usd),
        },
        "diamonds": {
            "groups": diamond_groups,
            "total_count": stone_total_count,
            "total_carat": stone_total_carat,
            "total_usd": stone_total_usd,
            "total_inr": to_inr(stone_total_usd),
        },
        "manufacturing": {
            **manufacturing,
            "total_usd": manufacturing_total_usd,
            "total_inr": to_inr(manufacturing_total_usd),
        },
        "grand_total": {
            "usd": total_usd,
            "inr": to_inr(total_usd),
        },
    }


def price_bom(
    weight_result: dict,
    diamond_groups: list[dict],
    gold_rates: dict,
    diamond_rates: dict,
    markup: float | None = None,
) -> dict:
    """JEWELBENCH-FORGE pricing: metal value from NET weight + stones from carat.

    Implements:  (net_metal_weight * spot_per_g * markup) + gemstone_value.
    No flat manufacturing line items (that's compute_costs' job) — FORGE prices
    purely off the two weights the spec asks for, plus carat-based stones.

    weight_result   output of weight_estimator.reconcile/estimate_weight
                    (carries alloy, net_weight_g, metal_weight_g,
                     shank_weight_range_g)
    diamond_groups  groups with carat_each already resolved via the fixed chart
                    (sizing.price_dimensions_to_groups)
    """
    markup = METAL_MARKUP if markup is None else markup
    alloy = (weight_result.get("alloy") or "18k_yellow_gold").lower()
    rate_key = ALLOY_TO_RATE_KEY.get(alloy, "18k")
    rate_usd = gold_rates["per_g"].get(f"{rate_key}_usd", 0.0)
    usd_inr = gold_rates["usd_inr"]

    net_w = float(weight_result.get("net_weight_g") or 0.0)
    metal_w = float(weight_result.get("metal_weight_g") or 0.0)

    # Metal value is priced on NET weight (metal only); markup applied here.
    metal_value_usd = round(net_w * rate_usd * markup, 2)

    # Shank value as a min..max RANGE (band weight range * rate * markup).
    shank_lo_g, shank_hi_g = (weight_result.get("shank_weight_range_g")
                              or [0.0, 0.0])
    shank_value_usd = [
        round(shank_lo_g * rate_usd * markup, 2),
        round(shank_hi_g * rate_usd * markup, 2),
    ]

    # Stones: carat already comes from the fixed gem chart upstream.
    priced_groups = [price_stone_group(g, diamond_rates) for g in diamond_groups]
    stone_total_usd = round(sum(g["line_total_usd"] for g in priced_groups), 2)
    stone_total_carat = round(sum(g["total_carat"] for g in priced_groups), 3)
    stone_total_count = sum(g["count"] for g in priced_groups)

    grand_usd = round(metal_value_usd + stone_total_usd, 2)

    def to_inr(v: float) -> float:
        return round(v * usd_inr, 2)

    return {
        "metal": {
            "alloy": alloy,
            "net_weight_g": net_w,
            "metal_weight_g": metal_w,
            "rate_usd_per_g": rate_usd,
            "markup": markup,
            "value_usd": metal_value_usd,
            "value_inr": to_inr(metal_value_usd),
            "shank_value_usd": shank_value_usd,
            "shank_value_inr": [to_inr(shank_value_usd[0]),
                                to_inr(shank_value_usd[1])],
        },
        "diamonds": {
            "groups": priced_groups,
            "total_count": stone_total_count,
            "total_carat": stone_total_carat,
            "total_usd": stone_total_usd,
            "total_inr": to_inr(stone_total_usd),
        },
        "grand_total": {
            "usd": grand_usd,
            "inr": to_inr(grand_usd),
        },
    }
