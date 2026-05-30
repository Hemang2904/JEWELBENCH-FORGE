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


def _tech_drawing_prompt(metal: dict, kd: dict, center: dict | None,
                         ring_size: str) -> str:
    """Build the technical-drawing prompt from the MEASURED values, so the
    callouts reflect the real estimate (not generic proportions)."""
    parts = []
    if kd.get("band_width"):
        parts.append(f"band width {kd['band_width']} mm")
    if kd.get("band_thickness"):
        parts.append(f"band thickness {kd['band_thickness']} mm")
    if center:
        seg = f"center {center.get('shape', 'round')}"
        if center.get("length_mm"):
            seg += f" {center['length_mm']} mm"
        if center.get("carat_each"):
            seg += f" ({center['carat_each']:.2f} ct)"
        parts.append(seg)
    if ring_size:
        parts.append(f"ring size US {ring_size}")
    parts.append(f"finished {_pretty(metal['alloy'])} net weight "
                 f"{metal['net_weight_g']:.2f} g")
    dims = "; ".join(parts)
    return (
        "Re-render this EXACT ring design as a professional jewelry technical "
        "specification drawing in pure side profile view. Add a clean "
        "dimensional callout system — thin dark-grey arrow lines with mm "
        f"labels — using THESE measured values: {dims}. Add a 10 mm scale bar "
        "in the bottom-right corner. The ring stays photorealistic in its "
        "actual metal colors and stones (preserve every design detail); the "
        "dimension lines, mm labels, and scale bar are crisp dark-grey vector "
        "overlays on a pure white RGB(255,255,255) background."
    )


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


def weight_inputs(prefix: str) -> tuple[str, str, float, bool]:
    """Render the alloy / ring-size / target-weight inputs + run button.

    `prefix` keeps widget keys unique across pages. Returns
    (alloy, ring_size, target_weight_g, run_clicked).
    """
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        alloy = st.selectbox("Metal / alloy", _ALLOYS, index=0,
                             format_func=_pretty, key=f"{prefix}_alloy")
    with c2:
        ring_size = st.text_input("Ring size (US) — scale reference",
                                  value="", placeholder="e.g. 6.5",
                                  key=f"{prefix}_ring_size")
    with c3:
        target_w = st.number_input("Target NET weight (g)", min_value=0.0,
                                   value=0.0, step=0.1, key=f"{prefix}_target_w",
                                   help="0 = use the estimated weight as-is")
    run = st.button("⚖️ Estimate weight & price", type="primary",
                    key=f"{prefix}_run", use_container_width=True)
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

    # ── Weight summary ────────────────────────────────────────────────────────
    shank_lo, shank_hi = est.get("shank_weight_range_g", [0, 0])
    m1, m2, m3 = st.columns(3, gap="medium")
    m1.metric("Net weight", f"{est['net_weight_g']:.2f} g")
    m2.metric("Metal weight", f"{est['metal_weight_g']:.2f} g")
    m3.metric("Confidence", est.get("confidence", "—").title(),
              f"Δ models {est.get('model_disagreement', 0):.0%}")

    st.caption(
        f"Shank weight range: **{shank_lo:.2f}–{shank_hi:.2f} g** · "
        f"alloy density {est.get('density_g_cm3')} g/cm³ · "
        f"casting factor {est.get('casting_factor')} · "
        f"models: {', '.join(str(m) for m in est.get('models', []))}"
    )

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
| Net weight | {metal['net_weight_g']:.2f} g |
| Metal weight | {metal['metal_weight_g']:.2f} g |
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

    # ── Technical drawing — generated HERE, with the measured values ──────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 📐 Technical drawing")
    st.caption("Dimensioned spec drawing rendered from the measured values "
               "above (band, center stone, ring size, weight).")
    kd = est.get("key_dimensions_mm") or {}
    center = (max(dia["groups"], key=lambda g: g.get("carat_each", 0))
              if dia["groups"] else None)
    bad_ref = (not design_ref) or str(design_ref).startswith("demo://")
    disabled = is_demo() or bad_ref
    if st.button("Generate technical drawing", key="forge_techdraw",
                 disabled=disabled, use_container_width=True):
        with st.spinner("Rendering dimensioned technical drawing..."):
            try:
                import fal_client
                result = fal_client.subscribe(
                    "fal-ai/nano-banana-pro/edit",
                    arguments={
                        "image_urls": [design_ref],
                        "prompt": _tech_drawing_prompt(metal, kd, center,
                                                       ring_size),
                        "num_images": 1, "resolution": "2K",
                        "aspect_ratio": "auto", "output_format": "png",
                    },
                )
                url = (result.get("images") or [{}])[0].get("url") \
                    or (result.get("image") or {}).get("url")
                if url:
                    st.session_state["forge_techdraw_url"] = url
                else:
                    st.warning("No drawing returned.")
            except Exception as e:
                st.error(f"Technical drawing failed: {e}")
    if disabled:
        st.caption("_(Needs a live design image + FAL_KEY — disabled in demo.)_")
    tdu = st.session_state.get("forge_techdraw_url")
    if tdu:
        st.image(tdu, caption="Technical drawing — measured values",
                 use_container_width=True)


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

    alloy, ring_size, target_w, run = weight_inputs("forge")
    cache_key = f"{results[0]}|{alloy}|{ring_size}"
    if demo:
        st.caption("🎬 **Demo mode** — sample weights/stones, no engine calls. "
                   "Set a real FAL_KEY and unset FORGE_DEMO for live estimates.")
    if run:
        with st.status("Estimating metal weight from references "
                       "(ensemble)...", expanded=True) as s:
            st.write(f"Models: {we.WEIGHT_MODEL_PRIMARY} + "
                     f"{we.WEIGHT_MODEL_SECONDARY}")
            est = run_estimate(image_urls, alloy, ring_size)
            st.session_state["forge_estimate"] = {"key": cache_key, "est": est}
            s.update(label="Weight estimate complete", state="complete"
                     if not est.get("_error") else "error")

    cached = st.session_state.get("forge_estimate")
    if not cached or cached.get("key") != cache_key:
        st.info("Set alloy / ring size / target, then **Estimate weight & "
                "price**.")
        return

    render_priced_bom(cached["est"], target_w, results[0], ring_size)
