"""JEWELBENCH-FORGE Bill of Materials — shared weight→price rendering.

Used by two flows:
  * Mix & Match (app.py)            — render() reads the multi-reference design
  * Single Image Estimator (pages/) — calls weight_inputs / run_estimate /
                                      render_priced_bom directly

Pipeline (same for both):
    1. Ensemble weight estimate (Gemini + Sonnet) -> net + metal weight
    2. User picks alloy + ring size + TARGET net weight
    3. Estimate scaled to target
    4. Stones measured in mm -> carat via the FIXED gem chart
    5. Pricing: net-weight metal value (+markup) + carat-based stones
    6. Shank shown as a min..max range; JSON download
"""

from __future__ import annotations

import datetime
import json
import math
import os

import streamlit as st

import sizing
import weight_estimator as we
from pricing import get_gold_rates, load_diamond_rates, price_bom

_ALLOYS = [
    "18k_yellow_gold", "18k_white_gold", "18k_rose_gold",
    "14k_yellow_gold", "14k_white_gold", "14k_rose_gold",
    "22k_yellow_gold", "24k_yellow_gold", "10k_yellow_gold",
    "platinum_950", "silver_925",
]


def is_demo() -> bool:
    return bool(os.environ.get("FORGE_DEMO"))


def _pretty(alloy: str) -> str:
    return alloy.replace("_", " ").title()


_DIM_COMMON = (
    "Preserve every design detail; the ring stays photorealistic in its actual "
    "metal colors and stones. Dimension lines, arrows, mm labels and a 10 mm "
    "scale bar are crisp dark-grey vector overlays on a pure white "
    "RGB(255,255,255) background. CRITICAL: label ONLY the exact measurements "
    "given below — do NOT invent, round, or add any other numbers or "
    "dimensions. Every value on the drawing must match these exactly."
)


def _dim_view_prompts(meas: dict) -> dict:
    """4 dimensioned views built from the authoritative BoM measurements, so the
    callouts match the BoM exactly (no model-invented numbers)."""
    bw, bt = meas.get("band_width"), meas.get("band_thickness")
    hh, hd = meas.get("head_height"), meas.get("head_diameter")
    rs = meas.get("ring_size")
    center = meas.get("center") or {}
    cmm = center.get("length_mm")
    cshape = center.get("shape", "center")

    def v(x):
        return f"{x} mm" if x else "as shown"

    top = ("Re-render this EXACT ring from a perfectly overhead orthographic "
           f"top-down view. Label ONLY: {cshape} stone = {v(cmm)}; band width "
           f"= {v(bw)}. " + _DIM_COMMON)
    side = ("Re-render this EXACT ring from a pure 90-degree side profile. "
            f"Label ONLY: band width = {v(bw)}; band thickness = {v(bt)}; head "
            f"height = {v(hh)}. " + _DIM_COMMON)
    front = ("Re-render this EXACT ring from a head-on front elevation. Label "
             f"ONLY: head diameter = {v(hd)}; band width = {v(bw)}; US ring "
             f"size = {rs}. " + _DIM_COMMON)
    iso = ("Re-render this EXACT ring from a 45-degree three-quarter view. "
           f"Label ONLY: head height = {v(hh)}; band width = {v(bw)}; head "
           f"diameter = {v(hd)}. " + _DIM_COMMON)
    return {"Top — dimensioned": top, "Side — dimensioned": side,
            "Front — dimensioned": front, "Three-Quarter — dimensioned": iso}


def _us_ring_circ_mm(ring_size: str) -> float:
    """US ring size -> inner circumference (mm). 0 if unparseable."""
    try:
        s = float(str(ring_size).strip())
    except (ValueError, TypeError):
        return 0.0
    return 36.537 + 2.5535 * s


def _dim_volume_check(est: dict, ring_size: str) -> str:
    """Cross-check: a solid band of the measured dimensions, wrapped at the
    ring circumference, vs the model's shank volume. '' if data missing."""
    kd = est.get("key_dimensions_mm") or {}
    bw, bt = kd.get("band_width"), kd.get("band_thickness")
    inner_circ = _us_ring_circ_mm(ring_size)
    shank_vol_est = est.get("shank_volume_mm3") or 0
    if not (bw and bt and inner_circ and shank_vol_est):
        return ""
    centerline = inner_circ + math.pi * float(bt)
    shank_vol_dim = float(bw) * float(bt) * centerline
    diff = abs(shank_vol_dim - shank_vol_est) / shank_vol_est
    flag = "✅ consistent" if diff <= 0.30 else "⚠️ mismatch — recheck band/size"
    return (f"Dimensional cross-check (shank): {bw}×{bt} mm band at US "
            f"{ring_size} → ~{shank_vol_dim:,.0f} mm³ vs model "
            f"{shank_vol_est:,.0f} mm³ ({diff:.0%} diff) — {flag}")


def _derive_band_dims(est: dict, ring_size: str):
    """Band width/thickness — the model's values if present, otherwise solved
    from the shank volume + ring circumference so they're CONSISTENT with the
    weight (no invented numbers)."""
    kd = est.get("key_dimensions_mm") or {}
    bw, bt = kd.get("band_width"), kd.get("band_thickness")
    inner = _us_ring_circ_mm(ring_size)
    shank_vol = est.get("shank_volume_mm3") or 0
    if bw and bt:
        return round(float(bw), 2), round(float(bt), 2)
    if not (inner and shank_vol):
        return (round(float(bw), 2) if bw else None,
                round(float(bt), 2) if bt else None)
    ratio = 1.3  # typical width:thickness if neither is known
    bt_v = float(bt) if bt else 1.8
    for _ in range(8):  # iterate: area depends on thickness via centerline
        area = shank_vol / (inner + math.pi * bt_v)
        bt_v = float(bt) if bt else (area / ratio) ** 0.5
    area = shank_vol / (inner + math.pi * bt_v)
    bw_v = float(bw) if bw else area / bt_v
    return round(bw_v, 2), round(bt_v, 2)


def _measurements(est: dict, metal: dict, dia: dict, ring_size: str) -> dict:
    """Single authoritative measurement set the spec chart AND the tech report
    both read, so every number is identical to the BoM."""
    center = (max(dia["groups"], key=lambda g: g.get("carat_each", 0))
              if dia["groups"] else None)
    bw, bt = _derive_band_dims(est, ring_size)
    kd = est.get("key_dimensions_mm") or {}
    cmm = center.get("length_mm") if center else None
    hd = kd.get("head_diameter") or (round(float(cmm) * 1.4, 1) if cmm else None)
    hh = kd.get("head_height") or (round(float(cmm) * 1.1, 1) if cmm else None)
    return {
        "gold_weight_g": metal["gold_weight_g"],
        "ring_size": ring_size or "—",
        "band_width": bw, "band_thickness": bt,
        "head_height": hh, "head_diameter": hd,
        "center": center, "stones": dia["groups"],
        "volume_mm3": est.get("volume_mm3"),
    }


def _spec_chart_md(sku: str, metal: dict, dia: dict, est: dict,
                   ring_size: str, gold: dict, meas: dict) -> str:
    """Standard jewelry spec chart (all values) as Markdown."""
    sv = metal["shank_value_usd"]
    lines = [
        f"### JewelBench Forge — Spec Sheet `{sku}`", "",
        "**Metal**", "", "| Field | Value |", "|---|---|",
        f"| Alloy | {_pretty(metal['alloy'])} |",
        f"| Gold weight | {metal['gold_weight_g']:.2f} g |",
        f"| Density | {est.get('density_g_cm3')} g/cm³ |",
        f"| Casting factor | {est.get('casting_factor')} |",
        f"| Rate | ${metal['rate_usd_per_g']:,.2f} /g |",
        f"| Markup | ×{metal['markup']:.2f} |",
        f"| Metal value | ${metal['value_usd']:,.2f} / ₹{metal['value_inr']:,.0f} |",
        f"| Shank value range | ${sv[0]:,.2f} – ${sv[1]:,.2f} |",
        f"| Ring size (US) | {ring_size or '—'} |", "",
        "**Dimensions (mm, estimated)**", "", "| Field | mm |", "|---|---|",
        f"| Band width | {meas.get('band_width', '—')} |",
        f"| Band thickness | {meas.get('band_thickness', '—')} |",
        f"| Head height | {meas.get('head_height', '—')} |",
        f"| Head diameter | {meas.get('head_diameter', '—')} |", "",
        f"**Stones — {dia['total_count']} total, {dia['total_carat']:.3f} ct**",
        "",
        "| Location | Shape | Count | mm | ct each | ct src | $/stone | Subtotal |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for g in dia["groups"]:
        lines.append(
            f"| {g.get('location', '—')} | {g['shape']} | {g['count']} | "
            f"{g.get('length_mm', '—')} | {g['carat_each']:.3f} | "
            f"{g.get('carat_source', '—')} | ${g['unit_usd']:,.2f} | "
            f"${g['line_total_usd']:,.2f} |"
        )
    lines += [
        "", "**Totals**", "", "| Field | Value |", "|---|---|",
        f"| Metal value | ${metal['value_usd']:,.2f} |",
        f"| Diamonds | ${dia['total_usd']:,.2f} |",
        f"| Gold basis | ${gold['usd_per_oz_xau']:,.0f}/oz · USD/INR {gold['usd_inr']:.2f} |",
    ]
    return "\n".join(lines)


def _demo_estimate(alloy: str) -> dict:
    """Offline sample estimate (no fal calls) for FORGE_DEMO=1 previews."""
    mock = [
        {"_model": "google/gemini-2.5-pro (demo)", "total_metal_volume_mm3": 330,
         "shank_volume_mm3": 185, "head_volume_mm3": 115,
         "stone_seat_volume_mm3": 32},
        {"_model": "anthropic/claude-sonnet-4.5 (demo)",
         "total_metal_volume_mm3": 365, "shank_volume_mm3": 205,
         "head_volume_mm3": 122, "stone_seat_volume_mm3": 41},
    ]
    out = we.reconcile(mock, alloy)
    out["stones"] = [
        {"location": "center", "shape": "round", "count": 1, "length_mm": 6.5},
        {"location": "halo", "shape": "round", "count": 12, "length_mm": 1.3},
        {"location": "shank", "shape": "round", "count": 20, "length_mm": 1.0},
    ]
    out["key_dimensions_mm"] = {"band_width": 2.2, "band_thickness": 1.8}
    return out


def _valid_ring_size(rs: str) -> bool:
    """A parseable US ring size in a sane range — required to calibrate scale."""
    try:
        v = float(str(rs).strip())
    except (ValueError, TypeError):
        return False
    return 0.5 <= v <= 18.0


def weight_inputs(prefix: str) -> tuple[str, str, float, bool]:
    """Render the alloy / ring-size / target-weight inputs + run button.

    Ring size is REQUIRED (it's the scale anchor): the estimate button stays
    disabled until a valid US size is entered. Returns
    (alloy, ring_size, target_weight_g, run_clicked).
    """
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        alloy = st.selectbox("Metal / alloy", _ALLOYS, index=0,
                             format_func=_pretty, key=f"{prefix}_alloy")
    with c2:
        ring_size = st.text_input("Ring size (US) — required",
                                  value="", placeholder="e.g. 6.5",
                                  key=f"{prefix}_ring_size",
                                  help="The scale anchor — the whole estimate "
                                       "is calibrated to it, so it's required.")
    with c3:
        target_w = st.number_input("Target NET weight (g)", min_value=0.0,
                                   value=0.0, step=0.1, key=f"{prefix}_target_w",
                                   help="0 = use the estimated weight as-is")

    rs_ok = is_demo() or _valid_ring_size(ring_size)
    if not rs_ok:
        st.caption("⚠️ Enter a valid **US ring size** (e.g. 6.5) to enable the "
                   "estimate — it's the scale reference the math depends on.")
    run = st.button("⚖️ Estimate weight & price", type="primary",
                    key=f"{prefix}_run", use_container_width=True,
                    disabled=not rs_ok)
    return alloy, ring_size, target_w, run


def run_estimate(image_urls: list[str], alloy: str, ring_size: str) -> dict:
    """Run the ensemble estimate (or the demo estimate in FORGE_DEMO mode)."""
    if is_demo():
        return _demo_estimate(alloy)
    return we.estimate_weight(image_urls, alloy, ring_size or None)


def render_priced_bom(est: dict, target_w: float = 0.0,
                      design_ref: str = "", ring_size: str = "") -> None:
    """Scale to target, price, and draw the full BoM. Shared by both flows."""
    if est.get("_error"):
        st.error(f"Weight estimate failed: {est['_error']}")
        return

    if target_w and target_w > 0:
        est = we.scale_to_target(est, float(target_w))

    # ── Weight summary (single GOLD weight) ──────────────────────────────────
    shank_lo, shank_hi = est.get("shank_weight_range_g", [0, 0])
    m1, m2, m3 = st.columns(3, gap="medium")
    m1.metric("Gold weight", f"{est.get('gold_weight_g', 0):.2f} g")
    m2.metric("Volume", f"{est.get('volume_mm3', 0):,.0f} mm³")
    m3.metric("Confidence", est.get("confidence", "—").title(),
              f"Δ models {est.get('model_disagreement', 0):.0%}")

    st.caption(
        f"Shank weight range: **{shank_lo:.2f}–{shank_hi:.2f} g** · "
        f"alloy density {est.get('density_g_cm3')} g/cm³ · "
        f"casting factor {est.get('casting_factor')} · "
        f"models: {', '.join(str(m) for m in est.get('models', []))}"
    )

    # Scale calibration status — the math is anchored to the known ring size.
    if est.get("uncalibrated"):
        st.error("⚠️ **Uncalibrated weight** — a ring size was given but the "
                 "model's geometry couldn't be anchored to it, so this weight is "
                 "**not scale-calibrated**. Treat as low-confidence; re-run with "
                 "clearer orthographic / additional views.")
    elif est.get("scale_calibrated"):
        msg = (f"📐 Scale-calibrated to ring size US {ring_size} "
               f"(inner Ø {est.get('inner_diameter_mm')} mm, "
               f"×{est.get('scale_applied')}) — geometry anchored to the "
               "known size, shank volume solved from band dimensions.")
        if est.get("scale_clamped"):
            st.warning("⚠️ " + msg + "  NOTE: the scale had to be **clamped** — "
                       "the model's absolute-size guess was far off, so "
                       "dimensions may be approximate.")
        else:
            st.caption(msg)
    elif not str(ring_size or '').strip():
        st.warning("⚠️ No ring size — the solver can't calibrate scale, so "
                   "values may be off. Enter the ring size for accurate math.")

    if est.get("confidence_reason"):
        st.caption(f"ℹ️ Confidence note: {est['confidence_reason']}.")

    # Surface any unrecognized band-construction strings (silently defaulted to
    # solid before, which over-weighed hollow/open-back pieces).
    _cw = [e.get("_construction_warning") for e in (est.get("_per_model") or [])
           if e.get("_construction_warning")]
    if _cw:
        st.caption("⚠️ Unrecognized band construction "
                   f"({', '.join(map(str, _cw))}) — treated as solid; verify "
                   "solid vs hollow for an accurate shank weight.")

    # Dimension ↔ volume cross-check.
    _xc = _dim_volume_check(est, ring_size)
    if _xc:
        st.caption(_xc)

    # Reference-weight sanity (typical rings ~1.5-15 g; research band).
    _gw = est.get("gold_weight_g") or 0
    if _gw and (_gw < 0.8 or _gw > 50):
        st.caption(f"⚠️ {_gw:.1f} g is outside the typical ring range "
                   "(~1.5–15 g) — re-check dimensions, ring size and "
                   "solid/hollow construction.")
    if est.get("single_model"):
        st.warning("⚠️ Only one model returned — the ensemble cross-check "
                   "didn't run, so treat this as a single-model estimate "
                   "(confidence capped at medium).")
    errs = est.get("errors") or []
    if errs:
        with st.expander(f"Model call diagnostics ({len(errs)} failed)"):
            for e in errs:
                st.caption(f"✗ `{e.get('model')}` — {e.get('error')}")

    stone_groups = sizing.price_dimensions_to_groups(est.get("stones", []))

    gold = get_gold_rates()
    diamond_rates = load_diamond_rates()
    bom = price_bom(est, stone_groups, gold, diamond_rates)

    metal = bom["metal"]
    dia = bom["diamonds"]
    gt = bom["grand_total"]

    st.markdown("<br>", unsafe_allow_html=True)
    g1, g2, g3 = st.columns(3, gap="medium")
    g1.metric("Metal value", f"${metal['value_usd']:,.2f}",
              f"₹{metal['value_inr']:,.0f}")
    g2.metric("Diamonds", f"${dia['total_usd']:,.2f}",
              f"₹{dia['total_inr']:,.0f}")
    g3.metric("Grand total", f"${gt['usd']:,.2f}", f"₹{gt['inr']:,.0f}")

    with st.expander(f"Metal — {_pretty(metal['alloy'])}", expanded=True):
        sv = metal["shank_value_usd"]
        st.markdown(
            f"""
| Item | Value |
|---|---|
| Gold weight | {metal['gold_weight_g']:.2f} g |
| Rate | ${metal['rate_usd_per_g']:,.2f} /g |
| Markup | ×{metal['markup']:.2f} |
| **Metal value** | **${metal['value_usd']:,.2f} / ₹{metal['value_inr']:,.2f}** |
| **Shank value (range)** | **${sv[0]:,.2f} – ${sv[1]:,.2f}** |
"""
        )

    if dia["groups"]:
        with st.expander(
            f"Diamonds — {dia['total_count']} stones · "
            f"{dia['total_carat']:.3f} ct (carat from fixed gem chart)",
            expanded=True,
        ):
            rows = "\n".join(
                f"| {g.get('location', '—')} | {g['shape']} | {g['count']} | "
                f"{g.get('length_mm', '—')} | {g['carat_each']:.3f} | "
                f"{g.get('carat_source', '—')} | ${g['unit_usd']:,.2f} | "
                f"${g['line_total_usd']:,.2f} |"
                for g in dia["groups"]
            )
            st.markdown(
                f"""
| Location | Shape | Count | mm | ct each | ct src | $/stone | Subtotal |
|---|---|---|---|---|---|---|---|
{rows}
| **TOTAL** | | **{dia['total_count']}** | | **{dia['total_carat']:.3f}** | | | **${dia['total_usd']:,.2f}** |
"""
            )

    sku = "JBF-" + datetime.datetime.now().strftime("%y%m%d%H%M")
    payload = {
        "sku": sku,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "design_image": design_ref,
        "weight_estimate": est,
        "bom": bom,
        "gold_rates": gold,
    }
    st.download_button(
        "↓ Download BoM (JSON)",
        data=json.dumps(payload, indent=2, default=str),
        file_name=f"{sku}_bom.json",
        mime="application/json",
        use_container_width=True,
    )

    # ── Production spec sheet: image + standard chart + dimensioned views ─────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 📐 Production spec sheet")
    # One authoritative measurement set — the chart AND the tech report read
    # these, so every number matches the BoM (no model-invented values).
    meas = _measurements(est, metal, dia, ring_size)
    bad_ref = (not design_ref) or str(design_ref).startswith("demo://")
    disabled = is_demo() or bad_ref
    spec_md = _spec_chart_md(sku, metal, dia, est, ring_size, gold, meas)

    col_img, col_chart = st.columns([1, 1], gap="medium")
    with col_img:
        st.markdown("**Design**")
        if not bad_ref:
            st.image(design_ref, use_container_width=True)
        else:
            st.caption("_(demo placeholder — no live design image)_")
    with col_chart:
        st.markdown(spec_md)

    st.download_button("↓ Download spec chart (Markdown)", data=spec_md,
                       file_name=f"{sku}_spec.md", mime="text/markdown",
                       use_container_width=True)

    st.markdown("##### 📑 Technical report — dimensioned views")
    st.caption("Multiple views (top, side, front, three-quarter) labelled with "
               "the **exact** measurements below — same values as the BoM.")
    # Authoritative legend: these are the only numbers the drawings may show.
    _c = meas.get("center") or {}
    st.markdown(
        f"""
| Measurement | Value |
|---|---|
| Gold weight | {meas['gold_weight_g']:.2f} g |
| Ring size (US) | {meas['ring_size']} |
| Band width × thickness | {meas.get('band_width', '—')} × {meas.get('band_thickness', '—')} mm |
| Head diameter × height | {meas.get('head_diameter', '—')} × {meas.get('head_height', '—')} mm |
| Center stone | {_c.get('shape', '—')} {_c.get('length_mm', '—')} mm ({_c.get('carat_each', 0):.2f} ct) |
"""
    )
    if st.button("Generate technical report (dimensioned views)",
                 key="forge_dimviews", disabled=disabled,
                 use_container_width=True):
        prompts = _dim_view_prompts(meas)
        import fal_client
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _one(prompt: str):
            r = fal_client.subscribe(
                "fal-ai/nano-banana-pro/edit",
                arguments={"image_urls": [design_ref], "prompt": prompt,
                           "num_images": 1, "resolution": "2K",
                           "aspect_ratio": "auto", "output_format": "png"})
            return (r.get("images") or [{}])[0].get("url") \
                or (r.get("image") or {}).get("url")

        out: dict = {}
        with st.status(f"Rendering {len(prompts)} dimensioned views...",
                       expanded=True) as s:
            with ThreadPoolExecutor(max_workers=len(prompts)) as ex:
                futs = {ex.submit(_one, p): n for n, p in prompts.items()}
                for f in as_completed(futs):
                    name = futs[f]
                    try:
                        u = f.result()
                        if u:
                            out[name] = u
                            st.write(f"✓ {name}")
                        else:
                            st.write(f"✗ {name}: no image")
                    except Exception as e:
                        st.write(f"✗ {name}: {e}")
            st.session_state["forge_dim_views"] = out
            s.update(label=f"{len(out)} dimensioned view(s) rendered",
                     state="complete" if out else "error")
    if disabled:
        st.caption("_(Needs a live design image + FAL_KEY — disabled in demo.)_")
    dv = st.session_state.get("forge_dim_views") or {}
    if dv:
        cols = st.columns(len(dv), gap="small")
        for i, (name, url) in enumerate(dv.items()):
            with cols[i]:
                st.image(url, caption=name, use_container_width=True)


def render() -> None:
    """Mix & Match end-of-flow BoM. No-op until a design exists."""
    demo = is_demo()
    results = st.session_state.get("last_results")
    image_urls = st.session_state.get("last_image_urls") or []
    if not results and demo:
        results = ["demo://sample-design"]
    if not results:
        return

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title"><span class="sec-num">07</span> '
        'Bill of Materials</div>'
        '<div class="section-subtitle">Weight estimated from all reference '
        'images, then priced to your target.</div>',
        unsafe_allow_html=True,
    )

    # The estimate reads the 7 generated views (purpose-built for weight) plus
    # the base design; falls back to the reference photos if no views yet.
    view_urls = list((st.session_state.get("views") or {}).values())
    est_images = ([results[0]] + view_urls) if view_urls else \
        (image_urls or [results[0]])

    alloy, ring_size, target_w, run = weight_inputs("forge")

    if not demo and len(view_urls) < 3:
        st.warning("For the most accurate weight, generate the **Additional "
                   "Views** above first — the estimate reads those 7 views "
                   "(cross-section, underside, top, side, front…).")
    if not demo and not ring_size.strip():
        st.info("Tip: enter the **ring size** — it's the scale reference; "
                "weights are noticeably more accurate with it.")

    cache_key = f"{results[0]}|{alloy}|{ring_size}|v{len(view_urls)}"
    if demo:
        st.caption("🎬 **Demo mode** — sample weights/stones, no engine calls. "
                   "Set a real FAL_KEY and unset FORGE_DEMO for live estimates.")
    if run:
        with st.status("Estimating metal weight from the views "
                       "(ensemble)...", expanded=True) as s:
            st.write(f"Models: {we.WEIGHT_MODEL_PRIMARY} + "
                     f"{we.WEIGHT_MODEL_SECONDARY}")
            st.write(f"Reading {len(est_images)} image(s) "
                     f"({len(view_urls)} generated views + base design)")
            est = run_estimate(est_images, alloy, ring_size)
            st.session_state["forge_estimate"] = {"key": cache_key, "est": est}
            s.update(label="Weight estimate complete", state="complete"
                     if not est.get("_error") else "error")

    cached = st.session_state.get("forge_estimate")
    if not cached or cached.get("key") != cache_key:
        st.info("Set alloy / ring size / target, then **Estimate weight & "
                "price**.")
        return

    render_priced_bom(cached["est"], target_w, results[0], ring_size)
