"""Single-Image Estimator — a second JewelBench Forge flow.

The user brings ONE image of a piece, optionally edits it with a text prompt
(AI edit), then gets the same ensemble weight estimate + priced Bill of
Materials as the Mix & Match flow.

    upload one image -> (optional) AI edit -> estimate weight -> price BoM
"""

import os

import streamlit as st

# Propagate Streamlit secrets into the environment BEFORE importing modules that
# read config at import time (weight_estimator/pricing via forge_bom).
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

import fal_client
import forge_bom
from preprocessing import strip_background_to_white, strip_url_to_white

st.set_page_config(page_title="JewelBench Forge — Single Image", page_icon="💍",
                   layout="centered")

DEMO = forge_bom.is_demo()
FAL_OK = bool(os.environ.get("FAL_KEY"))

st.title("💍 Single Image Estimator")
st.caption("Bring one image, optionally edit it, then get a weight estimate "
           "and priced Bill of Materials.")

if DEMO:
    st.info("🎬 **Demo mode** — upload/edit are disabled; the estimate uses "
            "sample data. Set a real FAL_KEY and unset FORGE_DEMO for live use.")
elif not FAL_OK:
    st.warning("Engine offline — set FAL_KEY in Secrets to upload, edit, and "
               "estimate.")


# ── 1. Upload ────────────────────────────────────────────────────────────────
st.subheader("1 · Your image")
up = st.file_uploader("Upload a jewelry image", type=["png", "jpg", "jpeg",
                      "webp"], key="si_upload")

if up is not None and not DEMO:
    sig = f"{up.name}:{up.size}"
    if st.session_state.get("si_file_sig") != sig:
        if st.button("⬆️ Use this image", key="si_use", type="primary"):
            with st.spinner("Uploading & cleaning background..."):
                try:
                    url = strip_background_to_white(up.getvalue(),
                                                    up.type or "image/png")
                    st.session_state["si_file_sig"] = sig
                    st.session_state["si_base_url"] = url
                    st.session_state["si_current_url"] = url
                    st.session_state.pop("si_estimate", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Upload failed: {e}")

current = st.session_state.get("si_current_url")
if DEMO and not current:
    current = "demo://uploaded-image"
    st.session_state["si_current_url"] = current

if current:
    if not str(current).startswith("demo://"):
        st.image(current, caption="Current working image",
                 use_container_width=True)
    else:
        st.caption("(demo placeholder image)")


# ── 2. Edit (optional) ───────────────────────────────────────────────────────
st.subheader("2 · Edit (optional)")
edit_prompt = st.text_area(
    "Describe a change to apply",
    placeholder="e.g. change to 18k white gold · widen the band to 2.5 mm · "
                "add a round halo around the center stone",
    key="si_edit_prompt",
)
if st.button("✨ Apply AI edit", key="si_edit_btn",
             disabled=(DEMO or not current or not edit_prompt.strip())):
    with st.status("Applying edit...", expanded=True) as s:
        try:
            result = fal_client.subscribe(
                "fal-ai/nano-banana-pro/edit",
                arguments={
                    "image_urls": [current],
                    "prompt": edit_prompt.strip(),
                    "num_images": 1,
                    "resolution": "2K",
                    "aspect_ratio": "auto",
                    "output_format": "png",
                },
            )
            edited = (result.get("images") or [{}])[0].get("url") \
                or (result.get("image") or {}).get("url")
            if not edited:
                raise RuntimeError("no image returned")
            try:
                edited = strip_url_to_white(edited)
            except Exception:
                pass
            st.session_state["si_current_url"] = edited
            st.session_state.setdefault("si_history", []).append(edited)
            st.session_state.pop("si_estimate", None)
            s.update(label="Edit applied", state="complete")
            st.rerun()
        except Exception as e:
            s.update(label="Edit failed", state="error")
            st.error(f"Edit failed: {e}")

if st.session_state.get("si_base_url") and st.session_state.get("si_current_url") \
        and st.session_state["si_current_url"] != st.session_state["si_base_url"]:
    if st.button("↩︎ Revert to original", key="si_revert"):
        st.session_state["si_current_url"] = st.session_state["si_base_url"]
        st.session_state.pop("si_history", None)
        st.session_state.pop("si_estimate", None)
        st.rerun()


# ── 3. Estimate & price ──────────────────────────────────────────────────────
st.subheader("3 · Weight & price")
if not current:
    st.info("Upload an image first.")
    st.stop()

alloy, ring_size, target_w, run = forge_bom.weight_inputs("single")
cache_key = f"{current}|{alloy}|{ring_size}"

if run:
    with st.status("Estimating metal weight (ensemble)...", expanded=True) as s:
        est = forge_bom.run_estimate([current], alloy, ring_size)
        st.session_state["si_estimate"] = {"key": cache_key, "est": est}
        s.update(label="Weight estimate complete",
                 state="complete" if not est.get("_error") else "error")

cached = st.session_state.get("si_estimate")
if cached and cached.get("key") == cache_key:
    forge_bom.render_priced_bom(cached["est"], target_w, current)
else:
    st.info("Set alloy / ring size / target, then **Estimate weight & price**.")
