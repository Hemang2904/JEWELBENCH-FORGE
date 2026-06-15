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
import math
import os
import re
import sys
import time
import urllib.request

# ── Config ───────────────────────────────────────────────────────────────────

# gemini-3-pro-preview isn't enabled on fal's any-llm/vision endpoint, so we use
# gemini-2.5-pro (the latest Gemini that works there) + Claude Sonnet 4.5.
WEIGHT_MODEL_PRIMARY = os.environ.get(
    "WEIGHT_MODEL_PRIMARY", "google/gemini-2.5-pro"
)
WEIGHT_MODEL_SECONDARY = os.environ.get(
    "WEIGHT_MODEL_SECONDARY", "anthropic/claude-sonnet-4.5"
)
# any-llm/vision is the existing path; kept overridable since fal marks it legacy.
VISION_ENDPOINT = os.environ.get("WEIGHT_VISION_ENDPOINT", "fal-ai/any-llm/vision")
# Extra models tried (in order) if the primary two don't BOTH return, so the
# ensemble reliably ends up with two independent estimates.
WEIGHT_FALLBACK_MODELS = [
    m.strip() for m in os.environ.get(
        "WEIGHT_FALLBACK_MODELS", "openai/gpt-5-chat,google/gemini-2.5-flash",
    ).split(",") if m.strip()
]
WEIGHT_CALL_RETRIES = int(os.environ.get("WEIGHT_CALL_RETRIES", "2"))
CASTING_FACTOR = float(os.environ.get("CASTING_FACTOR", "0.97"))
# Extra uncertainty padding applied to the min..max range (fraction).
RANGE_PAD = float(os.environ.get("WEIGHT_RANGE_PAD", "0.05"))

# Alloy densities g/cm^3 (env-overridable). Keyed by the same alloy codes the
# rest of the app uses. White and rose/red golds are MEANINGFULLY less dense
# than yellow at the same karat (different alloying metals), so they get their
# own keys — collapsing them to the yellow value made white gold read ~6% heavy.
# Defaults are Stuller published specific gravities (alloy-system dependent —
# e.g. palladium-white runs denser than nickel-white — so all are env-overridable
# and should be calibrated against ground-truth weights where possible).
#   https://www.stuller.com/articles/view/specific-gravity-melting-point-or-various-metals-and-alloys/
_DENSITY_DEFAULTS = {
    "24k": 19.32, "22k": 17.80,
    "18k": 15.60, "18k_white": 14.64, "18k_rose": 15.18,
    "14k": 13.07, "14k_white": 12.61, "14k_rose": 13.26,
    "10k": 11.60, "pt950": 20.10, "ag925": 10.36,
}
_DENSITY_ENV = {
    "24k": "DENSITY_24K_GOLD", "22k": "DENSITY_22K_GOLD",
    "18k": "DENSITY_18K_GOLD",
    "18k_white": "DENSITY_18K_WHITE_GOLD", "18k_rose": "DENSITY_18K_ROSE_GOLD",
    "14k": "DENSITY_14K_GOLD",
    "14k_white": "DENSITY_14K_WHITE_GOLD", "14k_rose": "DENSITY_14K_ROSE_GOLD",
    "10k": "DENSITY_10K_GOLD", "pt950": "DENSITY_PLATINUM_950",
    "ag925": "DENSITY_SILVER_925",
}

# Map full alloy names (as used in the BoM) -> density code.
_ALLOY_TO_DENSITY_KEY = {
    "24k_yellow_gold": "24k", "22k_yellow_gold": "22k",
    "18k_yellow_gold": "18k",
    "18k_white_gold": "18k_white", "18k_rose_gold": "18k_rose",
    "14k_yellow_gold": "14k",
    "14k_white_gold": "14k_white", "14k_rose_gold": "14k_rose",
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


# ── Estimate validation (reject non-finite / impossible model output) ─────────

_VOLUME_KEYS = (
    "total_metal_volume_mm3", "shank_volume_mm3", "head_volume_mm3",
    "accent_metal_volume_mm3", "stone_seat_volume_mm3",
)


def _has_valid_total(d: dict) -> bool:
    """True only if total_metal_volume_mm3 is a FINITE positive number. Models
    occasionally emit NaN/Infinity (json.loads parses both) or a string — those
    must never enter the ensemble, where the mean would propagate inf straight
    to grams."""
    try:
        v = float(d.get("total_metal_volume_mm3"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0.0


def _sanitize_volumes(d: dict) -> None:
    """Coerce any non-finite/garbage component volume to 0.0 in place, so a bad
    head/shank/accent reading can't poison reconcile()."""
    for k in _VOLUME_KEYS:
        if k in d:
            try:
                v = float(d[k])
                d[k] = v if math.isfinite(v) else 0.0
            except (TypeError, ValueError):
                d[k] = 0.0


# Scale clamp: a wildly wrong model inner-diameter shouldn't blow up the solve.
_SCALE_MIN, _SCALE_MAX = float(os.environ.get("WEIGHT_SCALE_MIN", "0.4")), \
    float(os.environ.get("WEIGHT_SCALE_MAX", "2.5"))


def us_ring_inner_diameter_mm(ring_size: str | float | None) -> float:
    """US ring size -> KNOWN inside (finger-hole) diameter in mm. 0 if blank.
    This is the one exact measurement we anchor the whole solve to."""
    try:
        s = float(str(ring_size).strip())
    except (ValueError, TypeError):
        return 0.0
    return (36.537 + 2.5535 * s) / math.pi  # circumference / pi


def _apply_scale(est: dict, sc: float) -> None:
    """Rescale one estimate in place: volumes by sc^3, lengths by sc."""
    for k in ("total_metal_volume_mm3", "shank_volume_mm3",
              "head_volume_mm3", "stone_seat_volume_mm3"):
        if est.get(k):
            est[k] = float(est[k]) * sc ** 3
    kd = est.get("key_dimensions_mm") or {}
    for k, val in list(kd.items()):
        if isinstance(val, (int, float)):
            kd[k] = round(val * sc, 2)
    for stone in est.get("stones") or []:
        for k in ("length_mm", "width_mm"):
            if stone.get(k):
                stone[k] = round(float(stone[k]) * sc, 2)


_BAND_W_RANGE = (1.0, 12.0)   # mm, physical sanity clamps
_BAND_T_RANGE = (0.8, 6.0)
_SHANK_FILL = float(os.environ.get("SHANK_FILL_FACTOR", "0.85"))  # D-/comfort-fit
# Solid vs hollow makes a big weight difference (research: ~30-50% less).
_FILL_BY_CONSTRUCTION = {
    "solid": 0.85, "partially-hollow": 0.68, "partially_hollow": 0.68,
    "hollow": 0.50, "open-back": 0.55, "open_back": 0.55,
}
# Normalised lookup so hyphens / underscores / spaces all collapse to one key.
_FILL_NORM = {k.replace("-", "_"): v for k, v in _FILL_BY_CONSTRUCTION.items()}


def _construction_fill(raw) -> tuple[float, str | None]:
    """Resolve a band_construction string to a fill factor.

    Returns (fill, unrecognized): unrecognized is the original string when it
    matched no known construction, so the caller can WARN rather than the old
    silent fall-through to solid 0.85 — which quietly made any typo
    ('open back', 'partially hollow', '') a ~40% heavier shank."""
    key = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in _FILL_NORM:
        return _FILL_NORM[key], None
    return _SHANK_FILL, (str(raw) if raw not in (None, "") else "(missing)")


def parametric_shank_volume(inner_d_mm: float, band_w: float, band_t: float,
                            fill: float = _SHANK_FILL) -> float:
    """Robust closed-form band volume: cross-section area x centerline
    circumference x a fill factor (cross-sections aren't perfect rectangles)."""
    centerline = math.pi * (inner_d_mm + band_t)
    return band_w * band_t * centerline * fill


def refine_shank_volume(est: dict) -> None:
    """Replace the model's shank-volume GUESS with the closed-form value from
    the calibrated band dimensions + known inner diameter, and fix the total to
    match. Fill factor reflects solid/hollow construction. No-op if band dims or
    inner diameter are missing."""
    kd = est.get("key_dimensions_mm") or {}
    bw, bt = kd.get("band_width"), kd.get("band_thickness")
    inner = est.get("inner_diameter_mm")
    if not (bw and bt and inner):
        return
    bw = min(max(float(bw), _BAND_W_RANGE[0]), _BAND_W_RANGE[1])
    bt = min(max(float(bt), _BAND_T_RANGE[0]), _BAND_T_RANGE[1])
    kd["band_width"], kd["band_thickness"] = round(bw, 2), round(bt, 2)
    fill, unrecognized = _construction_fill(est.get("band_construction"))
    if unrecognized is not None:
        est["_construction_warning"] = unrecognized
    new_shank = parametric_shank_volume(float(inner), bw, bt, fill)
    old_shank = float(est.get("shank_volume_mm3") or 0)
    total = float(est.get("total_metal_volume_mm3") or 0)
    est["shank_volume_mm3"] = round(new_shank, 1)
    est["total_metal_volume_mm3"] = round(max(0.0, total - old_shank + new_shank), 1)


def calibrate_to_ring_size(estimate: dict, ring_size) -> dict:
    """Anchor a model estimate to the KNOWN ring-size diameter.

    scale = known_inner_diameter / model_inner_diameter, clamped to a sane
    band. All geometry is rescaled so the band hole matches the real ring —
    this is what stops absurd carats/weights from a bad absolute-mm guess.
    Mutates and returns the estimate; no-op without ring size or a model
    inner-diameter reading.
    """
    known = us_ring_inner_diameter_mm(ring_size)
    model_id = float(estimate.get("inner_diameter_mm") or 0)
    if known <= 0 or model_id <= 0:
        estimate["_scale_applied"] = 1.0
        estimate["_scale_clamped"] = False
        return estimate
    raw_sc = known / model_id
    sc = max(_SCALE_MIN, min(_SCALE_MAX, raw_sc))
    _apply_scale(estimate, sc)
    estimate["_scale_applied"] = round(sc, 3)
    # Flag when the model's absolute-mm guess was so far off that the scale had
    # to be clamped — the geometry is then only loosely anchored, so downstream
    # confidence should be downgraded rather than read as calibrated.
    estimate["_scale_clamped"] = not (_SCALE_MIN <= raw_sc <= _SCALE_MAX)
    estimate["inner_diameter_mm"] = round(known, 2)
    return estimate


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


def _finalize_confidence(out: dict, typed_ring: bool, clamped: bool) -> dict:
    """Honesty pass on the reconciled output.

    A typed ring size that produced NO actual scale calibration means the
    geometry was never anchored to the known size, so the gram number must not
    be surfaced as confident (the dangerous false-confidence case: two models
    that agree but are both uncalibrated). Also downgrades when the scale clamp
    was hit or no ring size was given at all. Pure; mutates and returns `out`."""
    uncalibrated = bool(typed_ring and not out.get("scale_calibrated"))
    out["uncalibrated"] = uncalibrated
    out["scale_clamped"] = bool(clamped)
    if (not typed_ring) or uncalibrated or clamped:
        out["confidence"] = "low"
        out["confidence_reason"] = (
            "no ring size given — weight is not scale-calibrated"
            if not typed_ring else
            "ring size given but the model geometry could not be scale-anchored"
            if uncalibrated else
            "scale calibration hit its safety clamp — dimensions may be off")
    return out


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
        f"SCALE ANCHOR: this ring is US finger size {ring_size}. The inside band "
        f"diameter for that size is a KNOWN fixed number — measure inner_diameter_mm "
        f"to match it, and judge every other dimension relative to it."
        if ring_size else
        "No ring size given; infer scale from typical ring proportions and lower "
        "your confidence."
    )
    return (
        "You are a master jeweler and CAD estimator. Goal: estimate the METAL "
        "VOLUME of this ONE ring accurately enough for a weight estimate within "
        "about ±10%.\n"
        "The montage is a jewelry SPEC SHEET of the SAME ring: orthographic TOP, "
        "FRONT and SIDE (no perspective — MEASURE proportions from these), a 3/4 "
        "perspective (overall mass), a band cross-section and an underside view "
        "(these tell you band thickness and whether it is SOLID or HOLLOW), and a "
        "head macro (setting volume). Cross-check every dimension across views.\n"
        f"{scale_hint}\n"
        "METHOD (geometric decomposition): the band is a hollow ring "
        "(V ≈ π·(inner_diameter+thickness)·width·thickness); the head/setting and "
        "any halo/shoulder framework are separate solids. Report mm dimensions and "
        "per-component mm³ volumes; we convert to grams with metal density.\n"
        "Return ONLY a JSON object, no prose:\n"
        "{\n"
        '  "inner_diameter_mm": <inside finger-hole diameter — the scale anchor>,\n'
        '  "total_metal_volume_mm3": <solid metal body = shank + head + accents>,\n'
        '  "shank_volume_mm3": <band/shank metal only>,\n'
        '  "head_volume_mm3": <head/setting/gallery metal only>,\n'
        '  "accent_metal_volume_mm3": <halo/shoulder/pavé framework metal, '
        'EXCLUDING the stones>,\n'
        '  "stone_seat_volume_mm3": <metal removed to seat stones>,\n'
        '  "key_dimensions_mm": {"band_width": <n>, "band_thickness": <n>,\n'
        '     "head_height": <n>, "head_diameter": <n>},\n'
        '  "band_construction": "solid|partially-hollow|hollow",\n'
        '  "stones": [\n'
        '    {"location": "center|halo|hidden_halo|three_stone|shank|pave|'
        'shoulder|gallery", "shape": "round|oval|pear|marquise|emerald|'
        'princess|cushion|...", "count": <int>, "length_mm": <n>,\n'
        '     "width_mm": <n>}\n'
        "  ],\n"
        '  "confidence": "high|medium|low"\n'
        "}\n"
        "RULES:\n"
        "- Measure off the ORTHOGRAPHIC views; the band cross-section + underside "
        "tell you construction — a HOLLOW or open-back band has ~30-50% less metal "
        "than a solid one, so set band_construction honestly.\n"
        "- Enumerate EVERY distinct stone group (center, halo, hidden halo, "
        "three-stone, shank/pavé/channel, shoulder, gallery) with the correct "
        "SHAPE and BOTH length_mm and width_mm (round: length=width=diameter).\n"
        "- Everything in mm and mm³ — never carats or grams.\n"
        f"Target alloy is {alloy} (does not affect your volume estimate)."
    )


def _load_image_bytes(u) -> bytes | None:
    """Return raw image bytes from any reference the pipeline produces:
    a bytes object (geometry-mode views are PNG BYTES), a data: URL, an
    http(s) URL, or a local file path. None if it can't be loaded.

    The old montage loader only did urllib.urlopen(), which RAISED on bytes and
    was silently swallowed — so geometry-mode views (the consistent ones) never
    reached the weight estimate and it ran on the base image alone."""
    if isinstance(u, (bytes, bytearray)):
        return bytes(u)
    if not isinstance(u, str):
        return None
    if u.startswith("data:"):
        import base64
        try:
            return base64.b64decode(u.split(",", 1)[1])
        except Exception:
            return None
    if u.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(u, timeout=15) as r:
                return r.read()
        except Exception:
            return None
    try:  # local filesystem path
        with open(u, "rb") as fh:
            return fh.read()
    except Exception:
        return None


def _build_montage(image_urls: list, cell: int = 768) -> str:
    """Stitch up to 5 reference images into one montage and upload to fal.

    Accepts URLs, local paths, data: URLs, OR raw PNG bytes (geometry views).
    Returns the montage URL. Requires Pillow + fal_client (imported lazily so
    the pure-math part of this module imports without them).
    """
    from PIL import Image  # lazy
    import fal_client       # lazy

    items = list(image_urls or [])[:8]
    imgs = []
    for u in items:
        raw = _load_image_bytes(u)
        if raw is None:
            continue
        try:
            imgs.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        except Exception:
            continue
    dropped = len(items) - len(imgs)
    if dropped:
        # Surface silent drops — a montage running on fewer views than the UI
        # claims ("reads 7 views") is a real accuracy bug, not a no-op.
        print(f"[weight] montage: dropped {dropped} of {len(items)} reference "
              f"image(s); kept {len(imgs)}", file=sys.stderr)
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


def _is_anthropic_direct(model: str) -> bool:
    """Opus (and any 'anthropic-direct/...' id) isn't on fal — call Anthropic's
    API directly. Detected by 'opus' or an explicit 'anthropic-direct/' prefix."""
    m = model.lower()
    return m.startswith("anthropic-direct/") or "opus" in m


def _anthropic_model_id(model: str) -> str:
    for pre in ("anthropic-direct/", "anthropic/", "openrouter/"):
        if model.startswith(pre):
            return model[len(pre):]
    return model


def _call_anthropic_vision(model: str, montage_url: str, prompt: str,
                           tries: int) -> dict:
    """Direct Anthropic vision call (for Opus, which fal doesn't host).
    Needs ANTHROPIC_API_KEY + the `anthropic` package."""
    import base64
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"_model": model, "_error": "ANTHROPIC_API_KEY not set"}
    try:
        import anthropic
    except ImportError:
        return {"_model": model, "_error": "anthropic package not installed"}
    try:
        with urllib.request.urlopen(montage_url, timeout=20) as r:
            b64 = base64.standard_b64encode(r.read()).decode()
    except Exception as e:
        return {"_model": model, "_error": f"montage fetch failed: {e}"}

    client = anthropic.Anthropic()
    real = _anthropic_model_id(model)
    last_err = "unknown error"
    for attempt in range(1, tries + 1):
        try:
            msg = client.messages.create(
                model=real, max_tokens=2000,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            text = "".join(b.text for b in msg.content if b.type == "text")
            parsed = _extract_json(text)
            if parsed is not None and _has_valid_total(parsed):
                _sanitize_volumes(parsed)
                parsed["_model"] = model
                return parsed
            last_err = "unparseable / missing or non-finite total volume"
        except Exception as e:
            last_err = str(e)[:200]
        if attempt < tries:
            time.sleep(0.6 * attempt)
    return {"_model": model, "_error": last_err}


def _call_model(model: str, montage_url: str, prompt: str,
                tries: int | None = None) -> dict:
    """One model, with retries + tolerant parsing. Routes Opus/anthropic-direct
    ids to the Anthropic API; everything else goes through fal."""
    tries = WEIGHT_CALL_RETRIES if tries is None else tries
    if _is_anthropic_direct(model):
        return _call_anthropic_vision(model, montage_url, prompt, tries)
    import fal_client  # lazy
    last_err = "unknown error"
    for attempt in range(1, tries + 1):
        try:
            result = fal_client.subscribe(
                VISION_ENDPOINT,
                arguments={"model": model, "prompt": prompt,
                           "image_url": montage_url},
            )
            parsed = _extract_json(result.get("output") or "")
            if parsed is not None and _has_valid_total(parsed):
                _sanitize_volumes(parsed)
                parsed["_model"] = model
                return parsed
            last_err = "unparseable / missing or non-finite total volume"
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
        if _has_valid_total(res):
            good.append(res)
        else:
            errors.append({"model": res.get("_model"),
                           "error": res.get("_error") or "invalid/non-finite volume"})

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

    # MATH SOLVER: anchor each estimate's geometry to the known ring-size
    # diameter before reconciling, so imperfect absolute-mm guesses are
    # calibrated to the real size (volume ∝ scale^3, lengths ∝ scale).
    scales = []
    for e in good:
        calibrate_to_ring_size(e, ring_size)   # anchor geometry to ring size
        refine_shank_volume(e)                 # shank from closed-form formula
        if e.get("_scale_applied"):
            scales.append(e["_scale_applied"])

    out = {**reconcile(good, alloy), "montage_url": montage_url,
           "errors": errors,
           "scale_calibrated": bool(scales and any(s != 1.0 for s in scales)),
           "scale_applied": round(sum(scales) / len(scales), 3) if scales else 1.0,
           "inner_diameter_mm": round(us_ring_inner_diameter_mm(ring_size), 2)}
    # Stones + key dimensions: take the first model that reported each.
    for e in good:
        if e.get("stones") and "stones" not in out:
            out["stones"] = e["stones"]
        if e.get("key_dimensions_mm") and "key_dimensions_mm" not in out:
            out["key_dimensions_mm"] = e["key_dimensions_mm"]
    out.setdefault("stones", [])
    out.setdefault("key_dimensions_mm", {})

    # Honesty pass: downgrade confidence + flag when a typed ring size produced
    # no real calibration, or the scale clamp was hit, or no ring size at all.
    typed_ring = us_ring_inner_diameter_mm(ring_size) > 0
    clamped = any(e.get("_scale_clamped") for e in good)
    _finalize_confidence(out, typed_ring, clamped)
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
