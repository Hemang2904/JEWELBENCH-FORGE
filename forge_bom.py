"""JEWELBENCH-FORGE end-of-flow Bill of Materials.

Rendered LAST (after the design + additional views) so AI/token spend accrues
in order and the BoM reflects the final design. Flow:

    1. Ensemble weight estimate from all reference images (Gemini + Sonnet)
    2. User picks alloy + ring size + TARGET net weight
    3. Estimate is scaled to the target; dimensions derived for display
    4. Stones measured in mm -> carat via the FIXED gem chart
    5. Pricing: net-weight metal value (+markup) + carat-based stones
    6. Shank shown as a min..max range; JSON/MD download

Encapsulated as render(st_session) so the large app.py only needs a one-line
call at the very end.
"""

from __future__ import annotations

import datetime
import json

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


def _pretty(alloy: str) -> str:
    return alloy.replace("_", " ").title()


def render() -> None:
    """Draw the FORGE Bill of Materials section. No-op until a design exists."""
    results = st.session_state.get("last_results")
    image_urls = st.session_state.get("last_image_urls") or []
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

    # ── Inputs ────────────────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        alloy = st.selectbox("Metal / alloy", _ALLOYS, index=0,
                             format_func=_pretty, key="forge_alloy")
    with c2:
        ring_size = st.text_input("Ring size (US) — scale reference",
                                  value="", placeholder="e.g. 6.5",
                                  key="forge_ring_size")
    with c3:
        target_w = st.number_input("Target NET weight (g)", min_value=0.0,
                                   value=0.0, step=0.1, key="forge_target_w",
                                   help="0 = use the estimated weight as-is")

    run = st.button("⚖️ Estimate weight & price", type="primary",
                    key="forge_run", use_container_width=True)

    # Cache the (expensive) ensemble estimate per design+alloy+ring-size.
    cache_key = f"{results[0]}|{alloy}|{ring_size}"
    if run:
        with st.status("Estimating metal weight from references "
                       "(ensemble)...", expanded=True) as s:
            st.write(f"Models: {we.WEIGHT_MODEL_PRIMARY} + "
                     f"{we.WEIGHT_MODEL_SECONDARY}")
            est = we.estimate_weight(image_urls, alloy, ring_size or None)
            st.session_state["forge_estimate"] = {"key": cache_key, "est": est}
            s.update(label="Weight estimate complete", state="complete"
                     if not est.get("_error") else "error")

    cached = st.session_state.get("forge_estimate")
    if not cached or cached.get("key") != cache_key:
        st.info("Set alloy / ring size / target, then **Estimate weight & "
                "price**.")
        return

    est = cached["est"]
    if est.get("_error"):
        st.error(f"Weight estimate failed: {est['_error']}")
        return

    # Scale to target net weight if provided.
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

    # ── Stones via FIXED chart ───────────────────────────────────────────────
    stone_groups = sizing.price_dimensions_to_groups(est.get("stones", []))

    # ── Price ─────────────────────────────────────────────────────────────────
    gold = get_gold_rates()
    diamond_rates = load_diamond_rates()
    bom = price_bom(est, stone_groups, gold, diamond_rates)

    metal = bom["metal"]
    dia = bom["diamonds"]
    gt = bom["grand_total"]
    fx = gold["usd_inr"]

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

    # ── Download ──────────────────────────────────────────────────────────────
    sku = "JBF-" + datetime.datetime.now().strftime("%y%m%d%H%M")
    payload = {
        "sku": sku,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "design_image": results[0],
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
