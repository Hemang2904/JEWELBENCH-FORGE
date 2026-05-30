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
    * gold_weight_g   — the cast metal (volume * density * casting); this is the
                        single weight you pay gold for and pricing runs off it

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
import time
import urllib.request

# ── Config ───────────────────────────────────────────────────────────────────

WEIGHT_MODEL_PRIMARY = os.environ.get("WEIGHT_MODEL_PRIMARY", "google/gemini-2.5-pro")
WEIGHT_MODEL_SECONDARY = os.environ.get(
    "WEIGHT_MODEL_SECONDARY", "anthropic/claude-sonnet-4.5"
)
# any-llm/vision is the existing path; kept overridable since fal marks it legacy.
VISION_ENDPOINT = os.environ.get("WEIGHT_VISION_ENDPOINT", "fal-ai/any-llm/vision")
# Extra models tried (in order) if the primary two don't BOTH return, so the
# ensemble reliably ends up with two independent estimates.
WEIGHT_FALLBACK_MODELS = [
    m.strip() for m in os.environ.get(
        "WEIGHT_FALLBACK_MODELS", "openai/gpt-5-chat,google/gemini-2.5-flash"
    ).split(",") if m.strip()
]
WEIGHT_CALL_RETRIES = int(os.environ.get("WEIGHT_CALL_RETRIES", "2"))
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

    total_mean = _mean(total_v)
    shank_mean = _mean(shank_v)

    # Single GOLD weight = the cast metal (volume x density x casting).
    gold_weight = volume_to_weight(total_mean, density)

    # Shank value as a min..max RANGE from model spread.
    shank_w = [volume_to_weight(v, density) for v in shank_v]
    shank_lo, shank_hi = _range(shank_w)

    # Gold-weight band, for surfacing confidence.
    gold_w_all = [volume_to_weight(v, density) for v in total_v]
    gold_lo, gold_hi = _range(gold_w_all)

    n_models = len(total_v)
    single_model = n_models < 2
    disagreement = 0.0
    if total_mean > 0 and not single_model:
        disagreement = round((max(total_v) - min(total_v)) / total_mean, 3)

    # A single model can't be cross-checked, so it never gets "high".
    if single_model:
        confidence = "medium"
    elif disagreement > 0.25:
        confidence = "low"
    elif disagreement > 0.10:
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "alloy": alloy,
        "density_g_cm3": density,
        "casting_factor": CASTING_FACTOR,
        "gold_weight_g": gold_weight,
        "gold_weight_range_g": [gold_lo, gold_hi],
        "volume_mm3": round(total_mean, 1),
        "shank_volume_mm3": round(shank_mean, 1),
        "shank_weight_range_g": [shank_lo, shank_hi],
        "models": [e.get("_model") for e in estimates if e],
        "single_model": single_model,
        "model_disagreement": disagreement,
        "confidence": confidence,
        "_per_model": estimates,
    }


def scale_to_target(estimate: dict, target_weight_g: float) -> dict:
    """Scale so GOLD weight hits the user's target. Linear on volume->weight;
    scales volume and the shank range too so dimensions stay consistent."""
    base = estimate.get("gold_weight_g") or 0.0
    if base <= 0 or target_weight_g <= 0:
        return {**estimate, "target_scale": 1.0}
    k = target_weight_g / base
    lo, hi = estimate.get("shank_weight_range_g", [0, 0])
    return {
        **estimate,
        "target_scale": round(k, 4),
        "gold_weight_g": round(base * k, 3),
        "volume_mm3": round((estimate.get("volume_mm3") or 0) * k, 1),
        "shank_volume_mm3": round((estimate.get("shank_volume_mm3") or 0) * k, 1),
        "shank_weight_range_g": [round(lo * k, 3), round(hi * k, 3)],
    }


# ── Vision I/O ───────────────────────────────────────────────────────────────

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)


def _extract_json(text: str) -> dict | None:
    """Tolerant JSON pull — handles ```json fences, prose around the object,
    and trailing commas (all common in Gemini/GPT output)."""
    if not text:
        return None
    t = _FENCE.sub("", text).replace("```", "").strip()
    m = _JSON_BLOCK.search(t)
    if not m:
        return None
    blob = re.sub(r",(\s*[}\]])", r"\1", m.group(0))  # drop trailing commas
    try:
        return json.loads(blob)
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
        "You are a jewelry CAD estimator. The montage shows MULTIPLE views of "
        "the SAME ring (orthographic top/side/front, three-quarter, a band "
        "cross-section, a head macro, and an underside view). Use them together "
        "— the cross-section and underside reveal band thickness and hollowing; "
        "the top/macro reveal every stone. Estimate METAL VOLUME in cubic "
        f"millimetres. {scale_hint}\n"
        "Return ONLY a JSON object, no prose:\n"
        "{\n"
        '  "total_metal_volume_mm3": <number, solid metal body>,\n'
        '  "shank_volume_mm3": <number, band/shank portion>,\n'
        '  "head_volume_mm3": <number, head/setting portion>,\n'
        '  "stone_seat_volume_mm3": <number, metal removed for stone seats>,\n'
        '  "key_dimensions_mm": {"band_width": <n>, "band_thickness": <n>,\n'
        '     "head_height": <n>, "head_diameter": <n>},\n'
        '  "stones": [\n'
        '    {"location": "center|halo|hidden_halo|three_stone|shank|pave|'
        'shoulder|gallery", "shape": "round|oval|pear|marquise|emerald|'
        'princess|cushion|...", "count": <int>, "length_mm": <n>,\n'
        '     "width_mm": <n>}\n'
        "  ],\n"
        '  "confidence": "high|medium|low"\n'
        "}\n"
        "CRITICAL for stones: enumerate EVERY distinct diamond/gemstone group "
        "you can see across ALL views — center, halo, hidden halo, side/"
        "three-stone, shank/pavé/channel, shoulder accents, gallery/peek-a-boo. "
        "Miss none, do not merge different groups. Identify each SHAPE "
        "correctly. Give BOTH length_mm and width_mm (for round, set "
        "length=width=diameter; for oval/pear/marquise/emerald give the true "
        "long and short axes). Report mm dimensions, never carats.\n"
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
    for u in image_urls[:8]:
        try:
            with urllib.request.urlopen(u, timeout=15) as r:
                imgs.append(Image.open(io.BytesIO(r.read())).convert("RGB"))
        except Exception:
            continue
    if not imgs:
        raise RuntimeError("no reference images could be loaded for montage")

    cols = min(len(imgs), 4)
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


def _call_model(model: str, montage_url: str, prompt: str,
                tries: int | None = None) -> dict:
    """One model, with retries + tolerant parsing. Returns a parsed estimate
    (with _model) or {"_model", "_error"} after exhausting retries."""
    import fal_client  # lazy
    tries = WEIGHT_CALL_RETRIES if tries is None else tries
    last_err = "unknown error"
    for attempt in range(1, tries + 1):
        try:
            result = fal_client.subscribe(
                VISION_ENDPOINT,
                arguments={"model": model, "prompt": prompt,
                           "image_url": montage_url},
            )
            parsed = _extract_json(result.get("output") or "")
            if parsed is not None and "total_metal_volume_mm3" in parsed:
                parsed["_model"] = model
                return parsed
            last_err = "unparseable output / missing volume"
        except Exception as e:
            last_err = str(e)[:200]
        if attempt < tries:
            time.sleep(0.6 * attempt)  # brief backoff for transient failures
    return {"_model": model, "_error": last_err}


def estimate_weight(image_urls: list[str], alloy: str,
                    ring_size: str | None = None,
                    models: list[str] | None = None) -> dict:
    """Ensemble estimate. Tries the primary two in parallel, then walks the
    fallback models until TWO independent estimates succeed — so a single
    flaky model no longer collapses the ensemble. Live (needs FAL_KEY)."""
    pool = models or [WEIGHT_MODEL_PRIMARY, WEIGHT_MODEL_SECONDARY]
    seen: set[str] = set()
    ordered: list[str] = []
    for m in pool + WEIGHT_FALLBACK_MODELS:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)

    montage_url = _build_montage(image_urls)
    prompt = _prompt(alloy, ring_size)
    good: list[dict] = []
    errors: list[dict] = []

    def _record(res: dict) -> None:
        if "total_metal_volume_mm3" in res:
            good.append(res)
        else:
            errors.append({"model": res.get("_model"),
                           "error": res.get("_error")})

    # Phase 1: the primary two, in parallel (each already retries internally).
    from concurrent.futures import ThreadPoolExecutor
    first = ordered[:2]
    with ThreadPoolExecutor(max_workers=max(1, len(first))) as ex:
        for res in ex.map(lambda m: _call_model(m, montage_url, prompt), first):
            _record(res)

    # Phase 2: still short of two? walk the fallbacks one at a time.
    for m in ordered[2:]:
        if len(good) >= 2:
            break
        _record(_call_model(m, montage_url, prompt))

    if not good:
        return {"_error": "all weight models failed", "errors": errors,
                "_per_model": errors, "alloy": alloy}

    out = {**reconcile(good, alloy), "montage_url": montage_url,
           "errors": errors}
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
