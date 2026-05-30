"""Geometry-based weight estimator for JEWELBENCH-FORGE.

Flow
----
1. The (up to 5) reference images are stitched into one montage so a single
   vision call sees every angle at once ("use all 5 images").
2. An ENSEMBLE of vision models (Gemini 2.5 Pro + Claude Sonnet 4.5 by default)
   each reads the geometry and returns *volumes in mm^3* — not grams. Models
   are good at "how big", bad at "how heavy", so we keep the heavy step in code.
3. Code converts volume -> weight deterministically:
       weight_g = (volume_mm3 / 1000) * alloy_density * casting_factor
   and reconciles the two model estimates: mean = point value, spread = range.

We report only the two numbers the spec asks for:
    * metal_weight_g  — the cast metal body (volume * density * casting)
    * net_weight_g    — metal after stone seats are removed (used for pricing)

The shank (band) is reported as a min..max RANGE, taken from the disagreement
between the two models plus a small uncertainty pad.

Opus is not available on fal, so this ensemble is the high-accuracy path. Swap
either model via WEIGHT_MODEL_PRIMARY / WEIGHT_MODEL_SECONDARY.
"""

from __future__ import annotations

import io
import json
import os
import re
import urllib.request

# ── Config ───────────────────────────────────────────────────────────────────

WEIGHT_MODEL_PRIMARY = os.environ.get("WEIGHT_MODEL_PRIMARY", "google/gemini-2.5-pro")
WEIGHT_MODEL_SECONDARY = os.environ.get(
    "WEIGHT_MODEL_SECONDARY", "anthropic/claude-sonnet-4.5"
)
# any-llm/vision is the existing path; kept overridable since fal marks it legacy.
VISION_ENDPOINT = os.environ.get("WEIGHT_VISION_ENDPOINT", "fal-ai/any-llm/vision")
CASTING_FACTOR = float(os.environ.get("CASTING_FACTOR", "0.97"))
# Extra uncertainty padding applied to the min..max range (fraction).
RANGE_PAD = float(os.environ.get("WEIGHT_RANGE_PAD", "0.05"))

# Alloy densities g/cm^3 (env-overridable). Keyed by the same alloy codes the
# rest of the app uses.
_DENSITY_DEFAULTS = {
    "24k": 19.32, "22k": 17.80, "18k": 15.60, "14k": 13.07,
    "10k": 11.60, "pt950": 20.10, "ag925": 10.36,
}
_DENSITY_ENV = {
    "24k": "DENSITY_24K_GOLD", "22k": "DENSITY_22K_GOLD",
    "18k": "DENSITY_18K_GOLD", "14k": "DENSITY_14K_GOLD",
    "10k": "DENSITY_10K_GOLD", "pt950": "DENSITY_PLATINUM_950",
    "ag925": "DENSITY_SILVER_925",
}

# Map full alloy names (as used in the BoM) -> density code.
_ALLOY_TO_DENSITY_KEY = {
    "24k_yellow_gold": "24k", "22k_yellow_gold": "22k",
    "18k_yellow_gold": "18k", "18k_white_gold": "18k", "18k_rose_gold": "18k",
    "14k_yellow_gold": "14k", "14k_white_gold": "14k", "14k_rose_gold": "14k",
    "10k_yellow_gold": "10k", "platinum_950": "pt950", "silver_925": "ag925",
}


def alloy_density(alloy: str) -> float:
    """g/cm^3 for a full alloy name like '18k_yellow_gold'."""
    key = _ALLOY_TO_DENSITY_KEY.get((alloy or "").lower(), "18k")
    return float(os.environ.get(_DENSITY_ENV[key], _DENSITY_DEFAULTS[key]))


# ── Pure math (unit-testable, no I/O) ────────────────────────────────────────

def volume_to_weight(volume_mm3: float, density_g_cm3: float,
                     casting: float = CASTING_FACTOR) -> float:
    """mm^3 -> grams.  1 cm^3 = 1000 mm^3."""
    return round(max(0.0, volume_mm3) / 1000.0 * density_g_cm3 * casting, 3)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _range(xs: list[float], pad: float = RANGE_PAD) -> tuple[float, float]:
    """min..max across estimates, widened by `pad` for inherent uncertainty."""
    if not xs:
        return (0.0, 0.0)
    lo, hi = min(xs), max(xs)
    return (round(lo * (1 - pad), 3), round(hi * (1 + pad), 3))


def reconcile(estimates: list[dict], alloy: str) -> dict:
    """Combine per-model volume estimates into final weights + ranges.

    Each estimate dict carries mm^3 volumes:
      total_metal_volume_mm3, shank_volume_mm3, head_volume_mm3,
      stone_seat_volume_mm3
    """
    density = alloy_density(alloy)

    def col(k: str) -> list[float]:
        return [float(e.get(k, 0) or 0) for e in estimates if e]

    total_v = col("total_metal_volume_mm3")
    shank_v = col("shank_volume_mm3")
    seat_v = col("stone_seat_volume_mm3")

    total_mean = _mean(total_v)
    seat_mean = _mean(seat_v)

    metal_weight = volume_to_weight(total_mean, density)
    net_weight = volume_to_weight(max(0.0, total_mean - seat_mean), density)

    # Shank value as a min..max RANGE from model spread.
    shank_w = [volume_to_weight(v, density) for v in shank_v]
    shank_lo, shank_hi = _range(shank_w)

    # Total-weight band, for surfacing confidence.
    metal_w_all = [volume_to_weight(v, density) for v in total_v]
    metal_lo, metal_hi = _range(metal_w_all)

    disagreement = 0.0
    if total_mean > 0 and len(total_v) > 1:
        disagreement = round((max(total_v) - min(total_v)) / total_mean, 3)

    return {
        "alloy": alloy,
        "density_g_cm3": density,
        "casting_factor": CASTING_FACTOR,
        "metal_weight_g": metal_weight,
        "net_weight_g": net_weight,
        "metal_weight_range_g": [metal_lo, metal_hi],
        "shank_weight_range_g": [shank_lo, shank_hi],
        "models": [e.get("_model") for e in estimates if e],
        "model_disagreement": disagreement,
        "confidence": "low" if disagreement > 0.25 else
                      "medium" if disagreement > 0.10 else "high",
        "_per_model": estimates,
    }


def scale_to_target(estimate: dict, target_weight_g: float) -> dict:
    """Scale a reconciled estimate so net weight hits a user target.

    Linear scale on volume -> weight; returns a copy with scaled weights and
    the scale factor so the UI can show derived dimensions accordingly.
    """
    base = estimate.get("net_weight_g") or 0.0
    if base <= 0 or target_weight_g <= 0:
        return {**estimate, "target_scale": 1.0}
    k = target_weight_g / base
    lo, hi = estimate.get("shank_weight_range_g", [0, 0])
    return {
        **estimate,
        "target_scale": round(k, 4),
        "net_weight_g": round(base * k, 3),
        "metal_weight_g": round(estimate.get("metal_weight_g", 0) * k, 3),
        "shank_weight_range_g": [round(lo * k, 3), round(hi * k, 3)],
    }


# ── Vision I/O ───────────────────────────────────────────────────────────────

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = _JSON_BLOCK.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _prompt(alloy: str, ring_size: str | None) -> str:
    scale_hint = (
        f"The piece is a ring in US finger size {ring_size}; use the known "
        f"inner band diameter for that size as your absolute scale reference."
        if ring_size else
        "No scale reference was provided; infer scale from typical ring "
        "proportions and state lower confidence."
    )
    return (
        "You are a jewelry CAD estimator. Study every view in this montage of "
        "the SAME ring and estimate its METAL VOLUME in cubic millimetres. "
        f"{scale_hint}\n"
        "Return ONLY a JSON object, no prose:\n"
        "{\n"
        '  "total_metal_volume_mm3": <number, solid metal body>,\n'
        '  "shank_volume_mm3": <number, band/shank portion>,\n'
        '  "head_volume_mm3": <number, head/setting portion>,\n'
        '  "stone_seat_volume_mm3": <number, metal removed for stone seats>,\n'
        '  "key_dimensions_mm": {"band_width": <n>, "band_thickness": <n>},\n'
        '  "stones": [\n'
        '    {"location": "center|halo|shank|...", "shape": "round|oval|...",\n'
        '     "count": <int>, "length_mm": <n>, "width_mm": <n>}\n'
        "  ],\n"
        '  "confidence": "high|medium|low"\n'
        "}\n"
        "Measure each distinct stone group; give mm dimensions, not carats.\n"
        f"Target alloy is {alloy} (affects nothing in your volume estimate)."
    )


def _build_montage(image_urls: list[str], cell: int = 768) -> str:
    """Stitch up to 5 reference images into one montage and upload to fal.

    Returns the montage URL. Requires Pillow + fal_client (imported lazily so
    the pure-math part of this module imports without them).
    """
    from PIL import Image  # lazy
    import fal_client       # lazy

    imgs = []
    for u in image_urls[:5]:
        try:
            with urllib.request.urlopen(u, timeout=15) as r:
                imgs.append(Image.open(io.BytesIO(r.read())).convert("RGB"))
        except Exception:
            continue
    if not imgs:
        raise RuntimeError("no reference images could be loaded for montage")

    cols = min(len(imgs), 3)
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (255, 255, 255))
    for i, im in enumerate(imgs):
        im.thumbnail((cell, cell))
        x = (i % cols) * cell + (cell - im.width) // 2
        y = (i // cols) * cell + (cell - im.height) // 2
        sheet.paste(im, (x, y))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return fal_client.upload(buf.getvalue(), content_type="image/png")


def _call_model(model: str, montage_url: str, alloy: str,
                ring_size: str | None) -> dict | None:
    import fal_client  # lazy
    try:
        result = fal_client.subscribe(
            VISION_ENDPOINT,
            arguments={
                "model": model,
                "prompt": _prompt(alloy, ring_size),
                "image_url": montage_url,
            },
        )
    except Exception as e:
        return {"_model": model, "_error": str(e)}
    parsed = _extract_json(result.get("output") or "")
    if parsed is None:
        return {"_model": model, "_error": "unparseable model output"}
    parsed["_model"] = model
    return parsed


def estimate_weight(image_urls: list[str], alloy: str,
                    ring_size: str | None = None,
                    models: list[str] | None = None) -> dict:
    """Full ensemble estimate from reference image URLs. Live (needs FAL_KEY)."""
    models = models or [WEIGHT_MODEL_PRIMARY, WEIGHT_MODEL_SECONDARY]
    montage_url = _build_montage(image_urls)
    raw = [_call_model(m, montage_url, alloy, ring_size) for m in models]
    good = [e for e in raw if e and "total_metal_volume_mm3" in e]
    if not good:
        return {"_error": "all weight models failed", "_per_model": raw,
                "alloy": alloy}
    out = {**reconcile(good, alloy), "montage_url": montage_url}
    # Stones + key dimensions: take the first model that reported each.
    for e in good:
        if e.get("stones") and "stones" not in out:
            out["stones"] = e["stones"]
        if e.get("key_dimensions_mm") and "key_dimensions_mm" not in out:
            out["key_dimensions_mm"] = e["key_dimensions_mm"]
    out.setdefault("stones", [])
    out.setdefault("key_dimensions_mm", {})
    return out


if __name__ == "__main__":
    # Dry-run the pure math with two mock model estimates (no network).
    mock = [
        {"_model": "google/gemini-2.5-pro", "total_metal_volume_mm3": 320,
         "shank_volume_mm3": 180, "head_volume_mm3": 110,
         "stone_seat_volume_mm3": 30},
        {"_model": "anthropic/claude-sonnet-4.5", "total_metal_volume_mm3": 360,
         "shank_volume_mm3": 200, "head_volume_mm3": 120,
         "stone_seat_volume_mm3": 40},
    ]
    r = reconcile(mock, "18k_yellow_gold")
    print(json.dumps(r, indent=2))
    print("scaled to 5.0g net:",
          json.dumps(scale_to_target(r, 5.0), indent=2))
