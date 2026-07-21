"""Tech Sheet — a JewelBench Forge flow.

Turn ONE design into a production tech sheet: dimensioned multi-views + full
dimensions + metal weight + a priced Bill of Materials, computed from ring size,
metal, and an OPTIONAL target weight.

    upload / pick a design  ->  specs (metal · ring size · optional target)
                            ->  dimensioned views + dimensions + weight + priced BoM

Reuses the same ensemble + pricing as the other flows (weight_estimator now runs
Opus 4.8 + Fable 5 for the vision reasoning, targeting <=10% deviation) plus the
BoM-matched dimensioned views (forge_bom._ai_dim_views).
"""

import os

import streamlit as st

# Propagate Streamlit secrets into the environment BEFORE importing modules that
# read config at import time (weight_estimator / pricing via forge_bom).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
try:
    if hasattr(st, "secrets"):
        for _k, _v in st.secrets.items():
            if isinstance(_v, (str, int, float, bool)):
                os.environ[str(_k)] = str(_v)
except Exception:
    pass

import forge_bom
import weight_estimator as we
from preprocessing import strip_background_to_white

st.set_page_config(page_title="JewelBench Forge — Tech Sheet", page_icon="📐",
                   layout="wide")

DEMO = forge_bom.is_demo()
FAL_OK = bool(os.environ.get("FAL_KEY"))

# Active vision model(s) — mirror the estimator's candidate selection: dedupe, then
# take the first WEIGHT_MIN_MODELS (what actually runs), so the label is honest for a
# single model (Opus only) OR an ensemble (not "median of 4" when it runs 3).
_seen: set = set()
_CANDIDATES = [m for m in (we.WEIGHT_MODEL_PRIMARY, we.WEIGHT_MODEL_SECONDARY,
                           *we.WEIGHT_FALLBACK_MODELS)
               if m and not (m in _seen or _seen.add(m))]
_MODELS = _CANDIDATES[:max(1, min(we.WEIGHT_MIN_MODELS, len(_CANDIDATES)))] if _CANDIDATES else []
_MODELS_LABEL = " + ".join(_MODELS) if _MODELS else "(none configured)"
_ENSEMBLE_NOTE = f"ensemble median of {len(_MODELS)}" if len(_MODELS) > 1 else "single model"

st.title("📐 Tech Sheet")
st.caption("Dimensioned multi-views, metal weight and a priced Bill of Materials — "
           "computed from ring size, metal, and an optional target weight. "
           f"Vision reasoning: **{_MODELS_LABEL}** ({_ENSEMBLE_NOTE}, targeting ≤10% deviation).")

if DEMO:
    st.info("🎬 **Demo mode** — dimensioned views need the live engine; the sheet uses "
            "sample weight/BoM. Set a real FAL_KEY (+ ANTHROPIC_API_KEY) and unset "
            "FORGE_DEMO for a live tech sheet.")
elif not FAL_OK:
    st.warning("Engine offline — set FAL_KEY in Secrets to upload and generate a tech sheet.")


# ── 1 · Your design ──────────────────────────────────────────────────────────
st.subheader("1 · Your design")
up = st.file_uploader("Upload a jewelry image", type=["png", "jpg", "jpeg", "webp"],
                      key="ts_upload")

if up is not None and not DEMO:
    sig = f"{up.name}:{up.size}"
    if st.session_state.get("ts_file_sig") != sig:
        if st.button("⬆️ Use this design", key="ts_use", type="primary"):
            with st.spinner("Uploading & cleaning background..."):
                try:
                    url = strip_background_to_white(up.getvalue(), up.type or "image/png")
                    st.session_state["ts_file_sig"] = sig
                    st.session_state["ts_current_url"] = url
                    st.session_state.pop("ts_sheet", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Upload failed: {e}")

current = st.session_state.get("ts_current_url")
if DEMO and not current:
    current = "demo://uploaded-design"
    st.session_state["ts_current_url"] = current

if current:
    if not str(current).startswith("demo://"):
        st.image(current, caption="Working design", width=320)
    else:
        st.caption("(demo placeholder design)")


# ── 2 · Specifications ───────────────────────────────────────────────────────
st.subheader("2 · Specifications")
if not current:
    st.info("Upload a design first.")
    st.stop()

# Metal · ring size · OPTIONAL target weight (leave 0 to estimate from the image).
alloy, ring_size, target_w, run = forge_bom.weight_inputs("tech")
cache_key = f"{current}|{alloy}|{ring_size}|{target_w}"


def _meas_from_est(est: dict, ring_size: str) -> dict:
    """Build the measurement dict _dim_view_prompts expects, from the estimate,
    so the dimensioned callouts match the BoM exactly (no model-invented numbers)."""
    kd = est.get("key_dimensions_mm") or {}
    center = next((s for s in (est.get("stones") or []) if s.get("location") == "center"), {})
    return {
        "band_width": kd.get("band_width"),
        "band_thickness": kd.get("band_thickness"),
        "head_height": kd.get("head_height"),
        "head_diameter": kd.get("head_diameter"),
        "ring_size": ring_size,
        "center": {"length_mm": center.get("length_mm"),
                   "shape": center.get("shape", "center")},
    }


if run:
    with st.status("Building the tech sheet…", expanded=True) as s:
        st.write(f"Vision: **{_MODELS_LABEL}** ({_ENSEMBLE_NOTE})")
        est = forge_bom.run_estimate([current], alloy, ring_size)
        views = {}
        if not est.get("_error") and not DEMO:
            st.write("Rendering dimensioned views (top · side · front · three-quarter)…")
            try:
                # Views must carry the SAME numbers as the priced BoM: if designing
                # to a target, dimension the scaled estimate, not the raw one.
                est_for_views = (we.scale_to_target(est, target_w)
                                 if (target_w and target_w > 0) else est)
                views = forge_bom._ai_dim_views(current, _meas_from_est(est_for_views, ring_size))
            except Exception as e:  # views are a bonus — never fail the whole sheet
                st.write(f"(dimensioned views unavailable: {e})")
        st.session_state["ts_sheet"] = {"key": cache_key, "est": est, "views": views}
        s.update(label="Tech sheet ready" if not est.get("_error") else "Estimate failed",
                 state="complete" if not est.get("_error") else "error")

cached = st.session_state.get("ts_sheet")
if not cached or cached.get("key") != cache_key:
    st.info("Set metal / ring size / (optional) target, then **Estimate weight & price**.")
    st.stop()

est = cached["est"]
views = cached.get("views") or {}
if est.get("_error"):
    st.error(f"Estimate failed: {est['_error']}")
    st.stop()


# ── 3 · Dimensioned views ────────────────────────────────────────────────────
if views:
    st.subheader("3 · Dimensioned views")
    cols = st.columns(len(views))
    for (name, url), col in zip(views.items(), cols):
        with col:
            st.image(url, caption=name, use_container_width=True)
elif not DEMO:
    st.subheader("3 · Dimensioned views")
    st.caption("Dimensioned views were not produced for this run.")


# ── 4 · Weight & dimensions ──────────────────────────────────────────────────
# When designing to a target, show the SCALED estimate so weight, dimensions and
# views all agree with the priced BoM below.
targeted = bool(target_w and target_w > 0)
est_display = we.scale_to_target(est, target_w) if targeted else est

st.subheader("4 · Weight & dimensions")
w_g = est_display.get("gold_weight_g")
lo_hi = est_display.get("gold_weight_range_g") or []
dev = None
if not targeted and len(lo_hi) == 2 and w_g:  # deviation is only meaningful for an ESTIMATE
    lo, hi = lo_hi
    dev = round((hi - lo) / 2 / w_g * 100, 1)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Metal weight" + (" · target" if targeted else ""), f"{w_g:g} g" if w_g is not None else "—")
kd = est_display.get("key_dimensions_mm") or {}
m2.metric("Band width", f"{kd.get('band_width')} mm" if kd.get("band_width") else "—")
m3.metric("Band thickness", f"{kd.get('band_thickness')} mm" if kd.get("band_thickness") else "—")
m4.metric("Head diameter", f"{kd.get('head_diameter')} mm" if kd.get("head_diameter") else "—")

if targeted:
    st.markdown(
        '<span style="display:inline-block;padding:4px 12px;border-radius:100px;'
        'background:rgba(34,197,94,.12);color:#22C55E;font-weight:600;font-size:13px">'
        '🎯 designed to target — weight is exact, dimensions derived to match</span>',
        unsafe_allow_html=True)
elif dev is not None:
    ok = dev <= 10
    color = "#22C55E" if ok else "#F59E0B"
    bg = "rgba(34,197,94,.12)" if ok else "rgba(245,158,11,.14)"
    st.markdown(
        f'<span style="display:inline-block;padding:4px 12px;border-radius:100px;'
        f'background:{bg};color:{color};font-weight:600;font-size:13px">'
        f'±{dev}% ensemble spread{" · within ≤10% target" if ok else " · above target — add views / ring size"}'
        f'</span>',
        unsafe_allow_html=True)
if not targeted and est_display.get("confidence"):
    st.caption(f"Confidence: **{est_display['confidence']}** · aggregation: {est_display.get('aggregation', 'median')}. "
               "Deviation is the model-ensemble spread; the vision reasoning targets ≤10%.")


# ── 5 · Priced Bill of Materials ─────────────────────────────────────────────
# Pass the UNSCALED est + target — render_priced_bom scales internally, so the
# BoM matches est_display above without double-scaling.
st.subheader("5 · Priced Bill of Materials")
forge_bom.render_priced_bom(est, target_w, current, ring_size)
