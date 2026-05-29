import streamlit as st
import fal_client
import os
from prompts import (
    build_combine_prompt,
    build_target_summary,
    build_correction_addendum,
)
from preprocessing import (
    strip_background_to_white,
    strip_url_to_white,
    enrich_description,
    validate_design,
    extract_bom,
)
from pricing import (
    get_gold_rates,
    load_diamond_rates,
    compute_costs,
)
import hashlib
import datetime
import json as _json

_ENV_VALIDATION_THRESHOLD = int(os.environ.get("VALIDATION_THRESHOLD", "80"))
_ENV_MAX_VALIDATION_TRIES = int(os.environ.get("MAX_VALIDATION_TRIES", "3"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    if hasattr(st, "secrets") and "FAL_KEY" in st.secrets:
        os.environ["FAL_KEY"] = st.secrets["FAL_KEY"]
except Exception:
    pass

if not os.environ.get("FAL_KEY"):
    try:
        fal_key = st.secrets.get("FAL_KEY", "")
        if fal_key:
            os.environ["FAL_KEY"] = fal_key
    except Exception:
        pass

st.set_page_config(
    page_title="JewelBench — Component Composer",
    page_icon="https://jewelbench.ai/wp-content/uploads/2025/05/jewelbench_logo.svg",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&family=Orbitron:wght@500;600;700&display=swap');

:root {
    --cyan: #0EA5E9;
    --cyan-soft: #BAE6FD;
    --cyan-deep: #0369A1;
    --blue: #38BDF8;
    --blue-deep: #0284C7;
    --purple: #6366F1;
    --green: #22C55E;
    --amber: #F59E0B;
    --text-primary: #0F172A;
    --text-secondary: #475569;
    --text-muted: #94A3B8;
    --border: rgba(14, 165, 233, 0.18);
    --border-active: rgba(2, 132, 199, 0.55);
    --glow-cyan: 0 4px 24px rgba(14, 165, 233, 0.22);
    --glow-soft: 0 8px 32px rgba(14, 165, 233, 0.10);
    --radius-card: 20px;
    --radius-input: 12px;
}

/* ════════════════════════════════════════════════════
   Global reset & background
   ════════════════════════════════════════════════════ */
html, body, [data-testid="stAppViewContainer"], .stApp {
    background:
        radial-gradient(ellipse 900px 600px at 10% 0%, rgba(186,230,253,0.45) 0%, transparent 60%),
        radial-gradient(ellipse 700px 500px at 95% 80%, rgba(199,210,254,0.30) 0%, transparent 55%),
        #F8FBFF !important;
    font-family: 'Inter', sans-serif !important;
    color: var(--text-primary) !important;
}
[data-testid="stAppViewContainer"] > section > div {
    padding-top: 0 !important;
}
#MainMenu, footer, header { visibility: hidden !important; }
.stDeployButton { display: none !important; }

/* ── Sidebar (hidden) ── */
[data-testid="stSidebar"] { display: none !important; }


/* ── Section Titles ── */
.section-title {
    font-family: 'Space Grotesk', sans-serif; font-size: 1.4rem;
    margin: 2rem 0 0.3rem; font-weight: 700; letter-spacing: -0.02em; color: var(--text-primary);
}
.section-subtitle { font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 1.2rem; line-height: 1.6; }

/* ── Pill / label badge ── */
.card-badge {
    display: inline-flex; align-items: center; gap: 5px;
    background: linear-gradient(135deg, rgba(14,165,233,0.08), rgba(99,102,241,0.06));
    border: 1px solid rgba(14,165,233,0.22); border-radius: 20px;
    padding: 3px 12px; margin-bottom: 0.7rem;
    font-family: 'Space Grotesk', sans-serif; font-size: 0.68rem;
    color: var(--cyan-deep); font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;
}
.spec-card-label {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.67rem;
    color: var(--cyan-deep); text-transform: uppercase;
    letter-spacing: 2px; font-weight: 700; margin-bottom: 0.5rem; margin-top: 0.8rem;
}

/* ── All containers (border=True) ── */
[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid rgba(14,165,233,0.16) !important;
    border-radius: var(--radius-card) !important;
    background: #FFFFFF !important;
    box-shadow: 0 1px 4px rgba(15,23,42,0.04), 0 4px 16px rgba(14,165,233,0.05) !important;
    transition: box-shadow 0.25s ease, border-color 0.25s ease !important;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: rgba(14,165,233,0.36) !important;
    box-shadow: 0 4px 24px rgba(14,165,233,0.14) !important;
}

/* ── File uploader — force white / light ── */
div[data-testid="stFileUploader"] {
    background: #F0F9FF !important;
    border: 1.5px dashed rgba(14,165,233,0.35) !important;
    border-radius: var(--radius-input) !important;
    transition: all 0.3s ease !important;
}
div[data-testid="stFileUploader"]:hover {
    border-color: var(--cyan) !important;
    background: rgba(186,230,253,0.25) !important;
    box-shadow: var(--glow-cyan) !important;
}
div[data-testid="stFileUploadDropzone"] {
    background: transparent !important;
    border: none !important;
}
div[data-testid="stFileUploader"] section {
    background: transparent !important;
    border: none !important;
}
div[data-testid="stFileUploader"] button {
    background: #FFFFFF !important;
    border: 1px solid rgba(14,165,233,0.30) !important;
    color: var(--cyan-deep) !important;
    border-radius: 8px !important;
}
div[data-testid="stFileUploader"] small,
div[data-testid="stFileUploader"] span,
div[data-testid="stFileUploader"] p {
    color: var(--text-secondary) !important;
}

/* ── Inputs ── */
textarea, input[type="text"], input[type="number"], input[type="password"] {
    background: #FFFFFF !important;
    border: 1px solid rgba(148,163,184,0.35) !important;
    border-radius: var(--radius-input) !important;
    color: var(--text-primary) !important;
    font-family: 'Inter', sans-serif !important;
    box-shadow: 0 1px 3px rgba(15,23,42,0.04) !important;
    transition: border-color 0.2s, box-shadow 0.2s !important;
}
textarea:focus, input:focus {
    border-color: var(--cyan) !important;
    box-shadow: 0 0 0 3px rgba(14,165,233,0.14) !important;
    outline: none !important;
}
[data-testid="stSelectbox"] > div > div,
[data-baseweb="select"] > div {
    background: #FFFFFF !important;
    border: 1px solid rgba(148,163,184,0.35) !important;
    border-radius: var(--radius-input) !important;
    color: var(--text-primary) !important;
    box-shadow: 0 1px 3px rgba(15,23,42,0.04) !important;
}

/* ── Slider ── */
[data-testid="stSlider"] [data-testid="stTickBar"] { display: none; }
.stSlider > div > div > div { background: rgba(14,165,233,0.18) !important; }
.stSlider > div > div > div > div {
    background: linear-gradient(135deg, var(--cyan), var(--blue-deep)) !important;
    box-shadow: 0 0 10px rgba(14,165,233,0.40) !important;
}

/* ── Pipeline Steps ── */
.pipeline-wrap {
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 1px; margin: 1rem 0 1.4rem;
    border-radius: var(--radius-card); overflow: hidden;
    border: 1px solid var(--border); background: var(--border);
    box-shadow: var(--glow-soft);
}
.pipeline-step {
    padding: 1.2rem 0.8rem; background: #FFFFFF; text-align: center;
    transition: background 0.2s ease;
}
.pipeline-step:hover { background: #F0F9FF; }
.pipeline-num {
    width: 28px; height: 28px; border-radius: 50%;
    background: linear-gradient(135deg, var(--blue), var(--blue-deep));
    color: #fff; display: inline-flex; align-items: center; justify-content: center;
    font-family: 'Space Grotesk', sans-serif; font-size: 0.72rem; font-weight: 700;
    margin: 0 auto 0.5rem; box-shadow: 0 2px 8px rgba(14,165,233,0.30);
}
.pipeline-icon { font-size: 1.5rem; display: block; margin-bottom: 0.35rem; }
.pipeline-label { font-family: 'Space Grotesk', sans-serif; font-size: 0.78rem; font-weight: 700; color: var(--text-primary); display: block; margin-bottom: 0.2rem; }
.pipeline-model { font-size: 0.65rem; color: var(--text-muted); font-family: 'Inter', sans-serif; line-height: 1.4; display: block; }

/* ── Pre-flight ── */
.preflight-wrap {
    background: #FFFFFF; border: 1px solid var(--border); border-radius: 16px;
    padding: 1rem 1.4rem; margin-bottom: 1.2rem;
    box-shadow: 0 1px 4px rgba(15,23,42,0.04);
}
.preflight-title {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.68rem; font-weight: 700;
    letter-spacing: 2px; text-transform: uppercase; color: var(--cyan-deep); margin-bottom: 0.7rem;
}
.preflight-row { display: flex; align-items: center; gap: 8px; font-size: 0.82rem; color: var(--text-secondary); padding: 3px 0; }
.preflight-ok { color: #16a34a; font-weight: 700; }
.preflight-no { color: #cbd5e1; }

/* ── Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, #38BDF8 0%, #0284C7 100%) !important;
    color: #FFFFFF !important; border: none !important;
    padding: 0.8rem 2rem !important;
    font-family: 'Space Grotesk', sans-serif !important; font-size: 0.88rem !important;
    font-weight: 700 !important; border-radius: 12px !important;
    letter-spacing: 1px !important; text-transform: uppercase !important;
    transition: all 0.25s ease !important;
    box-shadow: 0 4px 14px rgba(14,165,233,0.28) !important;
}
.stButton > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 24px rgba(14,165,233,0.42) !important;
}
.stButton > button:active { transform: translateY(0) !important; }
button[kind="primary"] { animation: ctaPulse 2.8s ease-in-out infinite; }
@keyframes ctaPulse {
    0%, 100% { box-shadow: 0 4px 14px rgba(14,165,233,0.28); }
    50%       { box-shadow: 0 8px 28px rgba(14,165,233,0.48); }
}

/* ── Score ring ── */
.score-block {
    padding: 1.6rem 2rem; border-radius: 20px;
    background: #FFFFFF; border: 1px solid var(--border);
    display: flex; align-items: center; gap: 2rem; flex-wrap: wrap;
    margin-bottom: 1.2rem; box-shadow: 0 2px 16px rgba(14,165,233,0.08);
}
.score-ring-wrap { position: relative; width: 100px; height: 100px; flex-shrink: 0; }
.score-ring-wrap svg { transform: rotate(-90deg); }
.score-ring-center {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
}
.score-ring-num { font-family: 'Space Grotesk', sans-serif; font-size: 1.7rem; font-weight: 800; line-height: 1; }
.score-ring-num.pass { color: var(--green); }
.score-ring-num.warn { color: var(--amber); }
.score-ring-pct { font-size: 0.62rem; color: var(--text-muted); letter-spacing: 1px; }
.score-label { font-size: 0.68rem; color: var(--text-muted); letter-spacing: 2px; text-transform: uppercase; font-family: 'Space Grotesk', sans-serif; margin-bottom: 4px; }
.score-status-title { font-family: 'Space Grotesk', sans-serif; font-size: 1.1rem; font-weight: 700; color: var(--text-primary); }
.score-status-sub { font-size: 0.82rem; color: var(--text-secondary); margin-top: 4px; }
.score-badge {
    display: inline-block; padding: 4px 14px; border-radius: 20px; margin-top: 10px;
    font-size: 0.7rem; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;
    font-family: 'Space Grotesk', sans-serif;
}
.score-badge.pass { background: rgba(34,197,94,0.10); border: 1px solid rgba(34,197,94,0.35); color: #15803d; }
.score-badge.warn { background: rgba(245,158,11,0.10); border: 1px solid rgba(245,158,11,0.35); color: #92400e; }

/* ── Result cards ── */
.result-card {
    background: #FFFFFF; border: 1px solid var(--border); border-radius: 18px;
    box-shadow: 0 2px 12px rgba(14,165,233,0.07);
    transition: all 0.3s ease; overflow: hidden; position: relative;
}
.result-card:hover {
    border-color: var(--cyan); box-shadow: 0 8px 32px rgba(14,165,233,0.18);
    transform: translateY(-3px);
}
.result-label {
    padding: 0.8rem 1rem; display: flex; justify-content: space-between; align-items: center;
    border-top: 1px solid rgba(14,165,233,0.12); background: #F8FBFF;
}
.result-label span { font-family: 'Space Grotesk', sans-serif; font-size: 0.82rem; color: var(--text-secondary); font-weight: 500; }

/* ── View placeholder ── */
.view-placeholder {
    min-height: 150px; background: #F8FBFF;
    border: 1.5px dashed rgba(14,165,233,0.28); border-radius: 14px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 8px; color: var(--text-muted); font-size: 0.78rem;
    font-family: 'Space Grotesk', sans-serif; transition: all 0.25s ease;
}
.view-placeholder:hover { border-color: var(--cyan); background: rgba(186,230,253,0.15); color: var(--cyan-deep); }
.view-placeholder-icon { font-size: 1.6rem; opacity: 0.45; }

/* ── st.metric ── */
[data-testid="stMetric"] {
    background: #FFFFFF !important; border: 1px solid rgba(14,165,233,0.16) !important;
    border-radius: 16px !important; padding: 1rem 1.2rem !important;
    box-shadow: 0 1px 6px rgba(14,165,233,0.07) !important;
}
[data-testid="stMetricLabel"] p {
    font-family: 'Space Grotesk', sans-serif !important; font-size: 0.68rem !important;
    font-weight: 700 !important; letter-spacing: 1.5px !important;
    text-transform: uppercase !important; color: var(--cyan-deep) !important;
}
[data-testid="stMetricValue"] {
    font-family: 'Space Grotesk', sans-serif !important; font-size: 1.55rem !important;
    font-weight: 800 !important; color: var(--text-primary) !important;
}
[data-testid="stMetricDelta"] { font-size: 0.78rem !important; }

/* ── Expander ── */
[data-testid="stExpander"] summary { font-family: 'Space Grotesk', sans-serif !important; font-weight: 600 !important; }
[data-testid="stExpander"] summary:hover { color: var(--cyan-deep) !important; }

/* ── st.status ── */
[data-testid="stStatusContainer"] {
    border-color: rgba(14,165,233,0.20) !important;
    border-radius: 14px !important; background: #FFFFFF !important;
}

/* ── Markdown ── */
.stMarkdown p, .stMarkdown li { color: var(--text-primary) !important; }

/* ── Refinement section ── */
.refine-sub { font-size: 0.83rem; color: var(--text-secondary); margin-bottom: 0.8rem; }

/* ── Progress ── */
.stProgress > div > div > div {
    background: linear-gradient(90deg, var(--cyan), var(--blue-deep), var(--purple)) !important;
}

@media (max-width: 768px) {
    .jb-hero-title { font-size: 2rem !important; }
    .jb-hero-sub { font-size: 0.9rem !important; }
    .jb-steps { flex-wrap: wrap; border-radius: 16px; }
    .pipeline-wrap { grid-template-columns: 1fr 1fr; }
    .jb-nav-product { display: none; }
    .block-container, [data-testid="block-container"] { padding: 72px 1rem 3rem !important; }
}

/* ── Max-width content container ── */
.block-container,
[data-testid="block-container"],
[data-testid="stAppViewBlockContainer"] {
    max-width: 1140px !important;
    padding: 80px 2.5rem 5rem !important;
    margin: 0 auto !important;
}

/* ── Fixed Navbar ── */
.jb-nav {
    position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
    height: 56px; background: rgba(255,255,255,0.92);
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid rgba(14,165,233,0.14);
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 2rem;
    box-shadow: 0 1px 12px rgba(14,165,233,0.08);
}
.jb-nav-brand { display: flex; align-items: center; gap: 12px; }
.jb-nav-logo { height: 28px; width: auto; }
.jb-nav-product {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.82rem;
    font-weight: 700; color: var(--cyan-deep); letter-spacing: 1px;
    text-transform: uppercase; padding-left: 12px;
    border-left: 1px solid rgba(14,165,233,0.25);
}
.jb-nav-right { display: flex; align-items: center; gap: 12px; }
.jb-nav-pill {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.75rem; font-weight: 600;
    padding: 4px 12px; border-radius: 20px; letter-spacing: 0.5px;
}
.jb-nav-pill.ok { background: rgba(34,197,94,0.10); color: #15803d; border: 1px solid rgba(34,197,94,0.28); }
.jb-nav-pill.err { background: rgba(239,68,68,0.08); color: #b91c1c; border: 1px solid rgba(239,68,68,0.25); }

/* ── Hero ── */
.jb-hero {
    text-align: center; padding: 3.5rem 1rem 2.5rem;
    max-width: 680px; margin: 0 auto;
}
.jb-hero-eyebrow {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.72rem; font-weight: 700;
    letter-spacing: 3px; text-transform: uppercase; color: var(--cyan);
    margin-bottom: 1rem;
}
.jb-hero-title {
    font-family: 'Space Grotesk', sans-serif; font-size: 3rem; font-weight: 800;
    line-height: 1.1; color: var(--text-primary); margin: 0 0 1rem; letter-spacing: -0.03em;
}
.jb-hero-accent { color: var(--cyan-deep); }
.jb-hero-sub {
    font-size: 1rem; color: var(--text-secondary); line-height: 1.7;
    margin: 0 0 1.5rem; max-width: 540px; margin-left: auto; margin-right: auto;
}
.jb-hero-pills { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }
.jb-pill {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.72rem; font-weight: 600;
    padding: 5px 14px; border-radius: 20px; letter-spacing: 0.5px;
    background: #FFFFFF; border: 1px solid rgba(14,165,233,0.22);
    color: var(--cyan-deep);
    box-shadow: 0 1px 4px rgba(14,165,233,0.08);
}

/* ── Steps ── */
.jb-steps {
    display: flex; align-items: center; justify-content: center;
    gap: 0; margin: 0 auto 2.5rem; width: fit-content;
    background: #FFFFFF; border: 1px solid rgba(14,165,233,0.16);
    border-radius: 50px; padding: 5px 6px;
    box-shadow: 0 2px 10px rgba(14,165,233,0.07);
}
.jb-step { display: flex; align-items: center; gap: 7px; padding: 6px 16px; border-radius: 40px; transition: all 0.25s ease; }
.jb-step.active { background: linear-gradient(135deg, var(--blue), var(--blue-deep)); }
.jb-step.done { background: rgba(34,197,94,0.10); }
.jb-step-num {
    width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-family: 'Space Grotesk', sans-serif; font-size: 0.72rem; font-weight: 800;
    background: rgba(14,165,233,0.12); color: var(--text-muted); flex-shrink: 0;
}
.jb-step.active .jb-step-num { background: rgba(255,255,255,0.22); color: #fff; }
.jb-step.done .jb-step-num { background: rgba(34,197,94,0.18); color: #15803d; }
.jb-step-label { font-family: 'Space Grotesk', sans-serif; font-size: 0.78rem; font-weight: 600; color: var(--text-muted); white-space: nowrap; }
.jb-step.active .jb-step-label { color: #fff; }
.jb-step.done .jb-step-label { color: #15803d; }
.jb-step-line { width: 24px; height: 1px; background: rgba(14,165,233,0.18); flex-shrink: 0; }

/* ── Section numbers ── */
.sec-num {
    font-family: 'Space Grotesk', sans-serif; font-size: 0.72rem; font-weight: 800;
    color: var(--cyan); letter-spacing: 1px; margin-right: 10px;
    background: rgba(14,165,233,0.10); border: 1px solid rgba(14,165,233,0.22);
    padding: 2px 8px; border-radius: 6px; vertical-align: middle;
}

/* ── Section divider ── */
.section-divider {
    height: 1px; background: linear-gradient(90deg, transparent, rgba(14,165,233,0.18), transparent);
    margin: 2.5rem 0;
}

/* ── Footer ── */
.jb-footer {
    margin-top: 4rem; padding: 2rem 0;
    border-top: 1px solid rgba(14,165,233,0.12);
    background: #FFFFFF;
}
.jb-footer-inner {
    display: flex; flex-direction: column; align-items: center; gap: 1rem;
}
.jb-footer-logo { height: 28px; opacity: 0.7; }
.jb-footer-links {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: center;
    font-size: 0.78rem; color: var(--text-muted); font-family: 'Space Grotesk', sans-serif;
}
.jb-footer-dot { color: rgba(14,165,233,0.40); }
</style>
""", unsafe_allow_html=True)


# ── HELPERS ──────────────────────────────────────────────────────────────────

def upload_to_fal(uploaded_file):
    img_bytes = uploaded_file.getvalue()
    content_type = uploaded_file.type or "image/png"
    return strip_background_to_white(img_bytes, content_type=content_type)


VIEW_PROMPTS = {
    "Top View": {
        "model": "fal-ai/nano-banana-pro/edit",
        "icon": "⬆️",
        "prompt": (
            "Re-render this EXACT same ring design from a directly overhead "
            "top-down camera angle, looking straight down at the head and "
            "crown with the band visible curving around below. Preserve "
            "every single detail — same head shape, shank profile, metal "
            "colors (rose-gold stays rose-gold, white-gold stays white-gold), "
            "stones, proportions, surface finish, prong count. ONLY the "
            "camera angle changes. Pure white seamless background "
            "RGB(255,255,255), subtle soft shadow, professional jewelry "
            "product photography, ultra-sharp macro detail."
        ),
    },
    "Side Profile": {
        "model": "fal-ai/nano-banana-pro/edit",
        "icon": "↔️",
        "prompt": (
            "Re-render this EXACT same ring design from a pure side profile "
            "view (90-degree side view), showing the full thickness of the "
            "band and the silhouette of the head from the side. Preserve "
            "every single detail. ONLY the camera angle changes. Pure white "
            "seamless background RGB(255,255,255), subtle soft shadow, "
            "professional jewelry product photography, ultra-sharp macro detail."
        ),
    },
    "Front View": {
        "model": "fal-ai/nano-banana-pro/edit",
        "icon": "🔭",
        "prompt": (
            "Re-render this EXACT same ring design from a head-on front "
            "camera angle, looking directly at the face of the center stone "
            "with the band curving away symmetrically on both sides. "
            "Preserve every single detail. ONLY the camera angle changes. "
            "Pure white seamless background RGB(255,255,255), subtle soft "
            "shadow, professional jewelry product photography, ultra-sharp "
            "macro detail."
        ),
    },
    "Macro Detail": {
        "model": "fal-ai/nano-banana-pro/edit",
        "icon": "🔬",
        "prompt": (
            "Re-render this EXACT same ring design as an extreme macro "
            "close-up of the head and setting, filling the frame with the "
            "crown, prongs, center stone facets, and immediate shoulder "
            "area. Preserve every detail. ONLY the camera distance and "
            "focal length change. Pure white seamless background, subtle "
            "soft shadow, professional jewelry product photography, "
            "ultra-sharp macro."
        ),
    },
    "Technical Drawing": {
        "model": "fal-ai/nano-banana-pro/edit",
        "icon": "📐",
        "prompt": (
            "Re-render this EXACT ring design as a professional jewelry "
            "technical specification drawing in pure side profile view. "
            "Add a clean dimensional callout system: thin dark grey arrow "
            "lines pointing to the band width, the head/setting height, "
            "the center stone diameter, and the total ring height, each "
            "labeled in millimeters using standard 1.0-carat round-brilliant "
            "engagement-ring proportions (band ≈ 2.0 mm, head ≈ 7.0 mm "
            "diameter, center stone ≈ 6.5 mm, total height ≈ 10.0 mm). "
            "Add a small 10 mm scale bar in the bottom-right corner for "
            "reference. The ring itself is rendered photorealistically in "
            "its actual metal colors and stones — preserve every design "
            "detail (head shape, prong count, shank profile, decoration). "
            "Pure white seamless background RGB(255,255,255). The dimension "
            "lines, arrows, mm labels, and scale bar are crisp dark-grey "
            "vector overlays drawn ON TOP of the photorealistic ring."
        ),
    },
}


def generate_view(base_url, view_prompt, model="fal-ai/nano-banana-pro/edit"):
    return fal_client.subscribe(
        model,
        arguments={
            "image_urls": [base_url],
            "prompt": view_prompt,
            "num_images": 1,
            "resolution": "2K",
            "aspect_ratio": "auto",
            "output_format": "png",
        },
    )


def extract_image_url(result):
    if not result:
        return None
    if "images" in result:
        for img in result["images"]:
            url = img.get("url")
            if url:
                return url
    if "image" in result:
        return result["image"].get("url")
    return None


# ── ENGINE CHECK & SETTINGS ──────────────────────────────────────────────────

fal_ok = bool(os.environ.get("FAL_KEY"))
VALIDATION_THRESHOLD = _ENV_VALIDATION_THRESHOLD
MAX_VALIDATION_TRIES = _ENV_MAX_VALIDATION_TRIES

# ── NAVBAR ───────────────────────────────────────────────────────────────────

st.markdown(f"""
<nav class="jb-nav">
  <div class="jb-nav-brand">
    <img src="https://jewelbench.ai/wp-content/uploads/2025/05/jewelbench_logo.svg" class="jb-nav-logo" alt="JewelBench"/>
    <span class="jb-nav-product">Component Composer</span>
  </div>
  <div class="jb-nav-right">
    {'<span class="jb-nav-pill ok">● Engine Ready</span>' if fal_ok else '<span class="jb-nav-pill err">● Engine Offline</span>'}
  </div>
</nav>
""", unsafe_allow_html=True)


# ── ENGINE CHECK ───────────────────────────────────────────────────────────

if not fal_ok:
    st.error("JewelBench Engine not configured. Please contact your administrator.")


# ── HERO ────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="jb-hero">
  <div class="jb-hero-eyebrow">JewelBench AI Studio</div>
  <h1 class="jb-hero-title">Mix &amp; Match<br><span class="jb-hero-accent">Jewelry Components</span></h1>
  <p class="jb-hero-sub">Upload reference images, describe the elements you want combined, and let JewelBench Engine generate a production-ready design at 2K resolution.</p>
  <div class="jb-hero-pills">
    <span class="jb-pill">✦ 2K Resolution</span>
    <span class="jb-pill">✦ AI Validated</span>
    <span class="jb-pill">✦ 5-Stage Pipeline</span>
    <span class="jb-pill">✦ Auto Bill of Materials</span>
  </div>
</div>
""", unsafe_allow_html=True)


# ── STEP INDICATORS ─────────────────────────────────────────────────────────

current_step = 1
if any(st.session_state.get(f"img_{i}") for i in range(5)):
    current_step = 2
if st.session_state.get("last_results"):
    current_step = 3

step_labels = ["Upload References", "Specify & Configure", "Generate & Refine"]
step_icons = ["01", "02", "03"]
steps_html = '<div class="jb-steps">'
for i, (num, label) in enumerate(zip(step_icons, step_labels), 1):
    cls = "done" if i < current_step else ("active" if i == current_step else "")
    icon = "✓" if i < current_step else num
    steps_html += f'<div class="jb-step {cls}"><span class="jb-step-num">{icon}</span><span class="jb-step-label">{label}</span></div>'
    if i < 3:
        steps_html += '<div class="jb-step-line"></div>'
steps_html += '</div>'
st.markdown(steps_html, unsafe_allow_html=True)


# ── STEP 1: UPLOAD REFERENCES ────────────────────────────────────────────────

st.markdown("""
<div class="section-title"><span class="sec-num">01</span> Reference Images</div>
<div class="section-subtitle">Upload jewelry images and describe which component to extract from each</div>
""", unsafe_allow_html=True)

_ref_count_col, _ref_hint_col = st.columns([2, 3])
with _ref_count_col:
    num_images = st.slider(
        "References",
        min_value=2, max_value=5, value=2,
        help="How many reference images to combine",
    )
with _ref_hint_col:
    st.markdown(
        f"<div style='padding-top:2rem;color:var(--text-muted);font-size:0.82rem;'>"
        f"Combining <strong style='color:var(--cyan-deep)'>{num_images}</strong> "
        f"reference images &nbsp;·&nbsp; drag the slider to add more</div>",
        unsafe_allow_html=True,
    )

badge_icons = ["◆", "◇", "○", "□", "△"]
image_specs = []
cols = st.columns(num_images, gap="medium")

for i in range(num_images):
    with cols[i]:
        with st.container(border=True):
            st.markdown(
                f'<div class="card-badge"><span>{badge_icons[i]}</span> Reference {i+1}</div>',
                unsafe_allow_html=True,
            )
            uploaded = st.file_uploader(
                f"ref_{i+1}",
                type=["png", "jpg", "jpeg", "webp", "heic"],
                key=f"img_{i}",
                label_visibility="collapsed",
            )
            if uploaded:
                st.image(uploaded, use_container_width=True)

            desc = st.text_area(
                "Component to extract",
                placeholder=(
                    "Be specific — include color, finish, detail.\n"
                    "e.g. 'pavé white-gold shank with rose-gold accents' "
                    "or '6-prong solitaire head, yellow gold'"
                ),
                key=f"desc_{i}",
                height=90,
                label_visibility="collapsed",
            )
        image_specs.append({"file": uploaded, "description": desc})


# ── STEP 2: SPECIFICATIONS ───────────────────────────────────────────────────

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
st.markdown("""
<div class="section-title"><span class="sec-num">02</span> Design Specifications</div>
<div class="section-subtitle">Define metal, stones, dimensions, and special requirements</div>
""", unsafe_allow_html=True)

spec_col1, spec_col2 = st.columns(2, gap="medium")

with spec_col1:
    with st.container(border=True):
        st.markdown('<div class="spec-card-label">METAL TYPE</div>', unsafe_allow_html=True)
        metal = st.selectbox(
            "Metal",
            [
                "— Select —",
                "14K White Gold",
                "14K Yellow Gold",
                "14K Rose Gold",
                "18K White Gold",
                "18K Yellow Gold",
                "18K Rose Gold",
                "Platinum",
                "Two-Tone (White + Rose)",
                "Two-Tone (White + Yellow)",
                "Three-Tone",
                "Sterling Silver",
            ],
            label_visibility="collapsed",
        )
        if metal == "— Select —":
            metal = ""

        st.markdown('<div class="spec-card-label" style="margin-top:1rem;">STONE DETAILS</div>', unsafe_allow_html=True)
        stones = st.text_area(
            "Stones",
            placeholder="5.68mm Round center stone\nChannel-set side diamonds\nNatural HI I1",
            height=100,
            label_visibility="collapsed",
        )

with spec_col2:
    with st.container(border=True):
        st.markdown('<div class="spec-card-label">DIMENSIONS</div>', unsafe_allow_html=True)
        dimensions = st.text_area(
            "Dimensions",
            placeholder="4.5mm shoulder width\n2.2mm shoulder depth\n2mm shank base width",
            height=100,
            label_visibility="collapsed",
        )
        st.markdown('<div class="spec-card-label" style="margin-top:1rem;">ADDITIONAL NOTES</div>', unsafe_allow_html=True)
        notes = st.text_area(
            "Notes",
            placeholder="Cathedral setting, milgrain edges, pave bridge...",
            height=100,
            label_visibility="collapsed",
        )

additional_specs = {"metal": metal, "stones": stones, "dimensions": dimensions, "notes": notes}


# ── AI PIPELINE VISUALIZATION ────────────────────────────────────────────────

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
st.markdown("""
<div class="section-title"><span class="sec-num">03</span> AI Pipeline</div>
<div class="section-subtitle">Five-stage process — from raw uploads to a validated, production-ready design</div>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="pipeline-wrap">
  <div class="pipeline-step">
    <div class="pipeline-num">1</div>
    <span class="pipeline-icon">🧹</span>
    <span class="pipeline-label">Background Strip</span>
    <span class="pipeline-model">Precision background removal</span>
  </div>
  <div class="pipeline-step">
    <div class="pipeline-num">2</div>
    <span class="pipeline-icon">🔍</span>
    <span class="pipeline-label">Vision Enrich</span>
    <span class="pipeline-model">Reads each reference deeply</span>
  </div>
  <div class="pipeline-step">
    <div class="pipeline-num">3</div>
    <span class="pipeline-icon">📝</span>
    <span class="pipeline-label">Prompt Build</span>
    <span class="pipeline-model">Silhouette · finish · color rules</span>
  </div>
  <div class="pipeline-step">
    <div class="pipeline-num">4</div>
    <span class="pipeline-icon">✨</span>
    <span class="pipeline-label">Render @ 2K</span>
    <span class="pipeline-model">JewelBench multi-reference engine</span>
  </div>
  <div class="pipeline-step">
    <div class="pipeline-num">5</div>
    <span class="pipeline-icon">🎯</span>
    <span class="pipeline-label">Validate &amp; Retry</span>
    <span class="pipeline-model">JewelBench AI · threshold {VALIDATION_THRESHOLD}% · up to {MAX_VALIDATION_TRIES} tries</span>
  </div>
</div>
""", unsafe_allow_html=True)


# ── PRE-FLIGHT CHECKLIST ─────────────────────────────────────────────────────

st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
_n_images = sum(1 for s in image_specs if s["file"])
_n_descs = sum(1 for s in image_specs if s["description"].strip())
_check_images = _n_images >= 2
_check_descs = _n_descs >= 2
_check_key = fal_ok
_ready = _check_images and _check_descs and _check_key

def _chk(ok, label):
    icon = '<span class="preflight-ok">✓</span>' if ok else '<span class="preflight-no">○</span>'
    style = "color:var(--text-primary)" if ok else "color:var(--text-muted)"
    return f'<div class="preflight-row">{icon}<span style="{style}">{label}</span></div>'

st.markdown(f"""
<div class="preflight-wrap">
  <div class="preflight-title">Ready to generate?</div>
  {_chk(_check_key, "JewelBench Engine connected")}
  {_chk(_check_images, f"At least 2 reference images ({_n_images} uploaded)")}
  {_chk(_check_descs, f"At least 2 component descriptions ({_n_descs} filled)")}
</div>
""", unsafe_allow_html=True)

_enrich_pending = st.session_state.get("enrichment_pending")

if not _enrich_pending:
    _gen_cols = st.columns([1, 4, 1])
    with _gen_cols[1]:
        generate_clicked = st.button(
            "✨ GENERATE COMBINED DESIGN",
            use_container_width=True,
            type="primary",
            disabled=not _ready,
        )
else:
    generate_clicked = False


# ── PHASE A: ENRICH ──────────────────────────────────────────────────────────

if generate_clicked and _ready:
    active_specs = [s for s in image_specs if s["file"] and s["description"]]

    with st.status("Analyzing references — phases 1 & 2 of 5...", expanded=True) as prep_status:
        st.write("🧹 Phase 1/5 — Cleaning reference backgrounds...")
        try:
            image_urls = [upload_to_fal(s["file"]) for s in active_specs]
        except Exception as e:
            prep_status.update(label="Upload failed", state="error")
            st.error(f"Reference upload failed: {e}")
            st.stop()
        st.write(f"✓ {len(image_urls)} image(s) cleaned and uploaded")

        st.write("🔍 Phase 2/5 — Reading reference images to enrich descriptions...")
        enriched_specs = []
        for spec, url in zip(active_specs, image_urls):
            enriched = enrich_description(url, spec["description"])
            enriched_specs.append({
                "file": spec["file"],
                "description": enriched,
                "_original": spec["description"],
            })
        st.write(f"✓ {len(enriched_specs)} description(s) enriched — review below before rendering")
        prep_status.update(
            label="✓ Analysis complete — review descriptions below",
            state="complete",
        )

    st.session_state["enrichment_pending"] = {
        "image_urls": image_urls,
        "enriched_specs": enriched_specs,
    }
    st.rerun()


# ── ENRICHMENT REVIEW ─────────────────────────────────────────────────────────

confirm_clicked = False
confirmed_image_urls = None
confirmed_enriched_specs = None

if _enrich_pending:
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown("""
<div class="section-title"><span class="sec-num">03b</span> Review AI Descriptions</div>
<div class="section-subtitle">The AI has read your reference images. Edit any description before rendering — precision here is the single biggest lever for output quality.</div>
""", unsafe_allow_html=True)

    _ep_specs = _enrich_pending["enriched_specs"]
    _review_cols = st.columns(len(_ep_specs), gap="medium")
    for i, spec in enumerate(_ep_specs):
        with _review_cols[i]:
            with st.container(border=True):
                st.markdown(
                    f'<div class="card-badge"><span>{badge_icons[i]}</span> Reference {i+1}</div>',
                    unsafe_allow_html=True,
                )
                if spec["file"]:
                    st.image(spec["file"], use_container_width=True)
                changed = spec["description"] != spec["_original"]
                if changed:
                    st.caption(f"Original: *{spec['_original']}*  →  AI enriched")
                else:
                    st.caption("Unchanged from your description")
                st.text_area(
                    f"Description {i+1}",
                    value=spec["description"],
                    key=f"enriched_edit_{i}",
                    height=90,
                    label_visibility="collapsed",
                )

    _confirm_cols = st.columns([1, 3, 1])
    with _confirm_cols[0]:
        if st.button("← Re-upload", use_container_width=True):
            del st.session_state["enrichment_pending"]
            st.rerun()
    with _confirm_cols[1]:
        confirm_clicked = st.button(
            "✅ CONFIRM & RENDER",
            use_container_width=True,
            type="primary",
        )

    if confirm_clicked:
        confirmed_image_urls = _enrich_pending["image_urls"]
        confirmed_enriched_specs = [
            {**spec, "description": st.session_state.get(f"enriched_edit_{i}", spec["description"])}
            for i, spec in enumerate(_ep_specs)
        ]
        del st.session_state["enrichment_pending"]


# ── GENERATION ───────────────────────────────────────────────────────────────

if confirmed_image_urls and confirmed_enriched_specs:
    image_urls = confirmed_image_urls
    enriched_specs = confirmed_enriched_specs

    with st.status("Running AI pipeline...", expanded=True) as gen_status:

        # Phase 3
        st.write("📝 Phase 3/5 — Building master combination prompt...")
        combine_prompt = build_combine_prompt(enriched_specs, additional_specs)
        target_summary = build_target_summary(enriched_specs, additional_specs)
        st.write("✓ Prompt built")

        with st.expander("View descriptions & prompt", expanded=False):
            st.markdown("**Final descriptions used for rendering:**")
            for i, s in enumerate(enriched_specs):
                orig = s.get("_original", "")
                if orig and orig != s["description"]:
                    st.markdown(f"**Image {i+1}** — original: *\"{orig}\"*")
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;final: **{s['description']}**")
                else:
                    st.markdown(f"**Image {i+1}** — **{s['description']}**")
            st.markdown("---")
            st.markdown("**Validator target description:**")
            st.code(target_summary, language="text")
            st.markdown("**Master prompt sent to render engine:**")
            st.code(combine_prompt, language="text")

        # Phase 4+5
        results = []
        strategy_names = []
        attempts_log = []
        best_url = None
        best_score = -1
        best_diagnosis = None
        current_prompt = combine_prompt
        prev_score = -1
        correction_addenda = []

        for attempt in range(1, MAX_VALIDATION_TRIES + 1):
            st.write(f"✨ Phase 4/5 — Rendering attempt {attempt}/{MAX_VALIDATION_TRIES} at 2K...")
            try:
                result = fal_client.subscribe(
                    "fal-ai/nano-banana-pro/edit",
                    arguments={
                        "image_urls": image_urls,
                        "prompt": current_prompt,
                        "num_images": 1,
                        "resolution": "2K",
                        "aspect_ratio": "auto",
                        "output_format": "png",
                    },
                )
            except Exception as e:
                st.warning(f"Attempt {attempt} render failed: {e}")
                attempts_log.append({"attempt": attempt, "error": str(e)})
                continue

            url = extract_image_url(result)
            if not url:
                attempts_log.append({"attempt": attempt, "error": "no image returned"})
                continue

            try:
                url = strip_url_to_white(url)
            except Exception:
                pass

            st.write(f"🎯 Phase 5/5 — Validating attempt {attempt} against target spec...")
            diagnosis = validate_design(url, target_summary)
            score = diagnosis.get("score", 0)
            attempts_log.append({"attempt": attempt, "score": score, "url": url})

            if score > best_score:
                best_url = url
                best_score = score
                best_diagnosis = diagnosis

            if score >= VALIDATION_THRESHOLD:
                st.write(f"✓ Passed validation — score {score}/100")
                break

            if attempt > 1 and score < prev_score:
                attempts_log[-1]["regressed"] = True
                st.write(f"⚠ Score regressed ({score} < {prev_score}), stopping early")
                break

            prev_score = score

            if attempt < MAX_VALIDATION_TRIES:
                st.write(f"↻ Score {score}% — below threshold {VALIDATION_THRESHOLD}%, refining prompt...")
                correction_addenda.append(build_correction_addendum(diagnosis, attempt + 1))
                current_prompt = combine_prompt + "\n\n" + "\n\n".join(correction_addenda)

        if best_url:
            results.append(best_url)
            strategy_names.append("JewelBench AI Render")
            passed = best_score >= VALIDATION_THRESHOLD
            gen_status.update(
                label=f"✓ Generation complete — score {best_score}/100" + (" (passed)" if passed else " (best attempt)"),
                state="complete",
            )
        else:
            gen_status.update(label="Generation failed", state="error")

    if results:
        tries_used = len(attempts_log)
        passed = best_score >= VALIDATION_THRESHOLD
        status_text = (
            f"Passed self-validation in {tries_used} {'try' if tries_used == 1 else 'tries'}"
            if passed
            else f"Best of {tries_used} {'try' if tries_used == 1 else 'tries'} (below {VALIDATION_THRESHOLD}% threshold)"
        )

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.markdown("""
        <div class="section-title"><span class="sec-num">04</span> Generated Design</div>
        """, unsafe_allow_html=True)

        _score_class = "pass" if passed else "warn"
        _ring_color = "#22D67A" if passed else "#FFB547"
        _circumference = 2 * 3.14159 * 40
        _dash = (_circumference * best_score) / 100
        _gap = _circumference - _dash
        st.markdown(f"""
        <div class="score-block">
          <div class="score-ring-wrap">
            <svg width="100" height="100" viewBox="0 0 100 100">
              <circle cx="50" cy="50" r="40" fill="none" stroke="rgba(56,189,248,0.12)" stroke-width="8"/>
              <circle cx="50" cy="50" r="40" fill="none" stroke="{_ring_color}"
                stroke-width="8" stroke-linecap="round"
                stroke-dasharray="{_dash:.1f} {_gap:.1f}"
                style="filter:drop-shadow(0 0 6px {_ring_color}88);transition:stroke-dasharray 1s ease;"/>
            </svg>
            <div class="score-ring-center">
              <span class="score-ring-num {_score_class}">{best_score}</span>
              <span class="score-ring-pct">/ 100</span>
            </div>
          </div>
          <div>
            <div class="score-label">Design Match</div>
            <div class="score-status-title">{'Passed Validation' if passed else 'Best Attempt'}</div>
            <div class="score-status-sub">{status_text}</div>
            <div class="score-badge {_score_class}">{'✓ Production Ready' if passed else '⚠ Below Threshold'}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        result_cols = st.columns(min(len(results), 4), gap="medium")
        for idx, url in enumerate(results):
            label = strategy_names[idx] if idx < len(strategy_names) else f"Var {idx+1}"
            with result_cols[idx % len(result_cols)]:
                st.markdown('<div class="result-card">', unsafe_allow_html=True)
                st.image(url, use_container_width=True)
                st.markdown(
                    f"""<div class="result-label">
                        <span>{label}</span>
                        <a href="{url}" target="_blank"
                           style="color:var(--cyan);text-decoration:none;font-size:0.8rem;font-weight:600;">
                            ↓ Download
                        </a>
                    </div></div>""",
                    unsafe_allow_html=True,
                )

        with st.expander("AI Self-Validation Report", expanded=not passed):
            for entry in attempts_log:
                if "error" in entry:
                    st.markdown(f"**Attempt {entry['attempt']}** — failed: {entry['error']}")
                else:
                    regressed = " — *stopped (score regressed)*" if entry.get("regressed") else ""
                    st.markdown(f"**Attempt {entry['attempt']}** — score **{entry.get('score', 0)}/100**{regressed}")
            if best_diagnosis:
                if best_diagnosis.get("per_component"):
                    st.markdown("**Per-component scores (final output):**")
                    for component, comp_score in best_diagnosis["per_component"].items():
                        bar = "█" * (comp_score // 10) + "░" * (10 - comp_score // 10)
                        st.markdown(f"- `{bar}` **{comp_score}/100** — {component}")
                if best_diagnosis.get("correct"):
                    st.markdown("**Rendered correctly:**")
                    for c in best_diagnosis["correct"]:
                        st.markdown(f"- ✓ {c}")
                if best_diagnosis.get("missing"):
                    st.markdown("**Missing from final output:**")
                    for m in best_diagnosis["missing"]:
                        st.markdown(f"- ✗ {m}")
                if best_diagnosis.get("wrong"):
                    st.markdown("**Rendered incorrectly:**")
                    for w in best_diagnosis["wrong"]:
                        st.markdown(f"- ⚠ {w}")
                if best_diagnosis.get("suggestion"):
                    st.markdown(f"**Validator's suggestion:** {best_diagnosis['suggestion']}")
                if best_diagnosis.get("_error"):
                    st.caption(f"Validator note: {best_diagnosis['_error']}")

        st.session_state["last_results"] = results
        st.session_state["last_prompt"] = combine_prompt
        st.session_state["last_target_summary"] = target_summary
        st.session_state["last_image_urls"] = image_urls
        st.session_state.setdefault("views", {})

        with st.spinner("Computing Bill of Materials..."):
            try:
                bom_raw = extract_bom(results[0], target_summary)
                if bom_raw and not bom_raw.get("_error"):
                    gold = get_gold_rates()
                    diamond_rates = load_diamond_rates()
                    costed = compute_costs(bom_raw, gold, diamond_rates)
                    st.session_state["last_bom"] = bom_raw
                    st.session_state["last_bom_costs"] = costed
                    st.session_state["last_bom_gold"] = gold
                    st.session_state["last_bom_url"] = results[0]
                    st.session_state.pop("last_bom_error", None)
                else:
                    st.session_state["last_bom_error"] = (
                        (bom_raw or {}).get("_error", "BoM extractor returned nothing")
                    )
                    st.session_state.pop("last_bom", None)
                    st.session_state.pop("last_bom_costs", None)
            except Exception as bom_exc:
                st.session_state["last_bom_error"] = f"BoM exception: {bom_exc}"
                st.session_state.pop("last_bom", None)
                st.session_state.pop("last_bom_costs", None)

        st.toast("Design generated successfully!", icon="✨")
    else:
        st.error("Generation failed. Please try again or contact support.")


# ── BILL OF MATERIALS ─────────────────────────────────────────────────────────

if st.session_state.get("last_bom") and st.session_state.get("last_bom_costs"):
    _bom = st.session_state["last_bom"]
    _costs = st.session_state["last_bom_costs"]
    _gold = st.session_state.get("last_bom_gold") or get_gold_rates()
    _fx = _gold["usd_inr"]

    _sku_seed = (
        (st.session_state.get("last_prompt") or "")
        + (st.session_state.get("last_bom_url") or "")
    )
    _sku = "JBR-" + hashlib.sha1(_sku_seed.encode()).hexdigest()[:8].upper()
    _ts_str = datetime.datetime.fromtimestamp(_gold["ts"]).strftime("%H:%M")
    _rate_line = (
        f"Gold ${_gold['usd_per_oz_xau']:,.2f}/oz · "
        f"₹{_gold['per_g'].get('18k_inr', 0):,.0f}/g 18k · "
        f"USD/INR {_gold['usd_inr']:,.2f} · as of {_ts_str}"
    )

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="section-title"><span class="sec-num">05</span> Bill of Materials</div>'
        f'<div class="section-subtitle">{_rate_line}</div>',
        unsafe_allow_html=True,
    )

    _metal = _costs["metal"]
    _dia = _costs["diamonds"]
    _mf = _costs["manufacturing"]
    _gt = _costs["grand_total"]
    _alloy_pretty = _metal["alloy"].replace("_", " ").title()

    # Metric summary row
    _mc1, _mc2, _mc3, _mc4 = st.columns(4, gap="medium")
    _mc1.metric("Metal", f"${_metal['total_usd']:,.2f}", f"₹{_metal['total_inr']:,.0f}")
    _mc2.metric("Diamonds", f"${_dia['total_usd']:,.2f}", f"₹{_dia['total_inr']:,.0f}")
    _mc3.metric("Manufacturing", f"${_mf['total_usd']:,.2f}", f"₹{_mf['total_inr']:,.0f}")
    _mc4.metric("Grand Total", f"${_gt['usd']:,.2f}", f"₹{_gt['inr']:,.0f}")

    st.markdown("<br>", unsafe_allow_html=True)

    with st.expander(f"Metal — {_alloy_pretty}", expanded=True):
        st.markdown(
            f"""
| Alloy | Weight | Rate USD/g | Rate INR/g | Subtotal USD | Subtotal INR |
|---|---|---|---|---|---|
| {_alloy_pretty} | {_metal['weight_g']:.2f} g | ${_metal['rate_usd_per_g']:,.2f} | ₹{_metal['rate_inr_per_g']:,.2f} | ${_metal['total_usd']:,.2f} | ₹{_metal['total_inr']:,.2f} |
"""
        )

    if _dia["groups"]:
        with st.expander(
            f"Diamonds — {_dia['total_count']} stones · {_dia['total_carat']:.3f} ct total",
            expanded=True,
        ):
            st.caption("Reference wholesale rates — edit `diamond_rates.json` to match your supplier")
            _dia_rows = "\n".join(
                f"| {g['location'].replace('_', ' ')} | {g['shape']} | "
                f"{g['count']} | {g['carat_each']:.3f} | "
                f"{(g.get('mm_each') if g.get('mm_each') is not None else '—')} | "
                f"{g['clarity']} | {g['setting']} | "
                f"${g['unit_usd']:,.2f} | ${g['line_total_usd']:,.2f} | "
                f"₹{g['line_total_usd'] * _fx:,.2f} |"
                for g in _dia["groups"]
            )
            st.markdown(
                f"""
| Location | Shape | Count | ct each | mm | Clarity | Setting | $/stone | Subtotal USD | Subtotal INR |
|---|---|---|---|---|---|---|---|---|---|
{_dia_rows}
| **TOTAL** | | **{_dia['total_count']}** | | | | | | **${_dia['total_usd']:,.2f}** | **₹{_dia['total_inr']:,.2f}** |
"""
            )

    with st.expander("Manufacturing", expanded=False):
        st.markdown(
            f"""
| Line | USD | INR |
|---|---|---|
| CAD | ${_mf['cad_usd']:.2f} | ₹{_mf['cad_usd'] * _fx:,.2f} |
| Wax printing | ${_mf['wax_usd']:.2f} | ₹{_mf['wax_usd'] * _fx:,.2f} |
| Casting | ${_mf['casting_usd']:.2f} | ₹{_mf['casting_usd'] * _fx:,.2f} |
| Polishing | ${_mf['polishing_usd']:.2f} | ₹{_mf['polishing_usd'] * _fx:,.2f} |
| Stone setting | ${_mf['stone_setting_usd']:.2f} | ₹{_mf['stone_setting_usd'] * _fx:,.2f} |
| Labour | ${_mf['labour_usd']:.2f} | ₹{_mf['labour_usd'] * _fx:,.2f} |
| **TOTAL** | **${_mf['total_usd']:,.2f}** | **₹{_mf['total_inr']:,.2f}** |
"""
        )

    # Dimensions + ring size
    _dim = _bom.get("dimensions_mm") or {}
    _rs = _bom.get("ring_size") or {}
    _sr = _bom.get("size_range") or {}
    _tol = _bom.get("weight_tolerance_pct")
    _cn = _bom.get("construction_notes") or ""

    with st.expander("Dimensions, Size & Production", expanded=False):
        _spec_cols = st.columns(3, gap="medium")
        with _spec_cols[0]:
            st.markdown("**Dimensions (mm)**")
            st.markdown(
                f"- Inner diameter: **{_dim.get('inner_diameter', '—')}**\n"
                f"- Band width (bottom): **{_dim.get('band_width_at_bottom', '—')}**\n"
                f"- Band width (shoulder): **{_dim.get('band_width_at_shoulder', '—')}**\n"
                f"- Band thickness: **{_dim.get('band_thickness', '—')}**\n"
                f"- Head height: **{_dim.get('head_height_above_finger', '—')}**\n"
                f"- Head diameter: **{_dim.get('head_diameter', '—')}**"
            )
        with _spec_cols[1]:
            st.markdown("**Ring Size**")
            _sr_min = _sr.get("us_min", "—")
            _sr_max = _sr.get("us_max", "—")
            st.markdown(
                f"- US: **{_rs.get('us', '—')}**\n"
                f"- UK: **{_rs.get('uk', '—')}**\n"
                f"- Inner circumference: **{_rs.get('inner_circumference_mm', '—')} mm**\n"
                f"- Producible range: **US {_sr_min} – US {_sr_max}**"
            )
        with _spec_cols[2]:
            st.markdown("**Production**")
            _tol_line = f"- Weight tolerance: **±{_tol}%**\n" if _tol else ""
            st.markdown(
                f"- SKU: **{_sku}**\n"
                f"{_tol_line}"
                f"- Analyzed by: _JewelBench AI_"
            )
            if _cn:
                st.markdown(f"_{_cn}_")

    # Downloads
    _full_bom = {
        "sku": _sku,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_image": st.session_state.get("last_bom_url"),
        "design_summary": st.session_state.get("last_target_summary"),
        "bom": _bom,
        "costs": _costs,
        "gold_rates": _gold,
    }
    _bom_json = _json.dumps(_full_bom, indent=2, default=str)
    _md_lines = [
        f"# Bill of Materials — {_sku}",
        f"_Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        f"_Rates: {_rate_line}_",
        "",
        "## Metal",
        f"- Alloy: **{_alloy_pretty}**",
        (f"- Weight: **{_metal['weight_g']:.2f} g** (tolerance ±{_tol}%)" if _tol else f"- Weight: **{_metal['weight_g']:.2f} g**"),
        f"- Cost: **${_metal['total_usd']:,.2f}** / **₹{_metal['total_inr']:,.2f}**",
        "",
        f"## Diamonds — {_dia['total_count']} stones, {_dia['total_carat']:.3f} ct total",
    ]
    for g in _dia["groups"]:
        _md_lines.append(
            f"- {g['location'].replace('_', ' ')}: {g['count']} × "
            f"{g['shape']} {g['carat_each']:.3f} ct ({g['clarity']}, "
            f"{g['setting']}) — ${g['line_total_usd']:,.2f} / "
            f"₹{g['line_total_usd'] * _fx:,.2f}"
        )
    _md_lines += [
        f"- **Diamond subtotal: ${_dia['total_usd']:,.2f} / ₹{_dia['total_inr']:,.2f}**",
        "",
        "## Manufacturing",
        f"- CAD: ${_mf['cad_usd']:.2f}",
        f"- Wax: ${_mf['wax_usd']:.2f}",
        f"- Casting: ${_mf['casting_usd']:.2f}",
        f"- Polishing: ${_mf['polishing_usd']:.2f}",
        f"- Stone setting: ${_mf['stone_setting_usd']:.2f}",
        f"- Labour: ${_mf['labour_usd']:.2f}",
        f"- **Manufacturing subtotal: ${_mf['total_usd']:.2f} / ₹{_mf['total_inr']:,.2f}**",
        "",
        "## Total Input Cost",
        f"**${_gt['usd']:,.2f} USD / ₹{_gt['inr']:,.2f} INR**",
        "",
        "## Dimensions (mm)",
        f"- Inner diameter: {_dim.get('inner_diameter', '—')}",
        f"- Band width (bottom): {_dim.get('band_width_at_bottom', '—')}",
        f"- Band width (shoulder): {_dim.get('band_width_at_shoulder', '—')}",
        f"- Band thickness: {_dim.get('band_thickness', '—')}",
        f"- Head height: {_dim.get('head_height_above_finger', '—')}",
        f"- Head diameter: {_dim.get('head_diameter', '—')}",
        "",
        "## Ring Size",
        f"- US: {_rs.get('us', '—')}",
        f"- UK: {_rs.get('uk', '—')}",
        f"- Inner circumference: {_rs.get('inner_circumference_mm', '—')} mm",
        f"- Producible range: US {_sr_min} – US {_sr_max}",
        "",
        "## Construction notes",
        _cn or "_(none provided)_",
    ]
    _bom_md = "\n".join(_md_lines)

    _dl_cols = st.columns(2, gap="small")
    with _dl_cols[0]:
        st.download_button(
            "↓ Download BoM (JSON)",
            data=_bom_json,
            file_name=f"{_sku}_bom.json",
            mime="application/json",
            use_container_width=True,
        )
    with _dl_cols[1]:
        st.download_button(
            "↓ Download BoM (Markdown)",
            data=_bom_md,
            file_name=f"{_sku}_bom.md",
            mime="text/markdown",
            use_container_width=True,
        )

elif st.session_state.get("last_bom_error"):
    st.markdown("<br>", unsafe_allow_html=True)
    st.warning(f"Bill of Materials unavailable: {st.session_state['last_bom_error']}")


# ── ADDITIONAL VIEWS ──────────────────────────────────────────────────────────

if st.session_state.get("last_results"):
    base_design_url = st.session_state["last_results"][0]

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="section-title"><span class="sec-num">06</span> Additional Views</div>
    <div class="section-subtitle">Re-render the generated design from different camera angles. Generate all 5 in parallel (~60 s, ~$0.30) or pick individual views to retry.</div>
    """, unsafe_allow_html=True)

    view_names = list(VIEW_PROMPTS.keys())

    # Batch button
    _vbatch_col = st.columns([1, 3, 1])[1]
    with _vbatch_col:
        if st.button("⚡ Generate All Views (parallel)", key="view_btn_all", use_container_width=True, type="primary"):
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with st.status(f"Rendering {len(view_names)} views in parallel...", expanded=True) as _vs:
                _vresults = {}
                _verrors = {}
                with ThreadPoolExecutor(max_workers=len(view_names)) as executor:
                    futures = {
                        executor.submit(
                            generate_view,
                            base_design_url,
                            VIEW_PROMPTS[name]["prompt"],
                            VIEW_PROMPTS[name]["model"],
                        ): name
                        for name in view_names
                    }
                    for future in as_completed(futures):
                        name = futures[future]
                        try:
                            result = future.result()
                            url = extract_image_url(result)
                            if url:
                                _vresults[name] = url
                                st.write(f"✓ {name}")
                            else:
                                _verrors[name] = "no image returned"
                        except Exception as e:
                            _verrors[name] = str(e)
                if _vresults:
                    st.session_state.setdefault("views", {}).update(_vresults)
                for n, msg in _verrors.items():
                    st.warning(f"{n} failed: {msg}")
                _vs.update(
                    label=f"Done — {len(_vresults)}/{len(view_names)} views rendered",
                    state="complete" if not _verrors else "error",
                )
            if _vresults:
                st.toast(f"{len(_vresults)} view(s) rendered!", icon="✨")

    st.markdown("<br>", unsafe_allow_html=True)

    # Always-visible 5-slot grid
    existing_views = st.session_state.get("views", {})
    view_grid_cols = st.columns(5, gap="small")
    for idx, view_name in enumerate(view_names):
        cfg = VIEW_PROMPTS[view_name]
        with view_grid_cols[idx]:
            vurl = existing_views.get(view_name)
            if vurl:
                st.markdown('<div class="result-card">', unsafe_allow_html=True)
                st.image(vurl, use_container_width=True)
                st.markdown(
                    f"""<div class="result-label">
                        <span style="font-size:0.78rem">{view_name}</span>
                        <a href="{vurl}" target="_blank"
                           style="color:var(--cyan);text-decoration:none;font-size:0.75rem;font-weight:600;">
                            ↓
                        </a>
                    </div></div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div class="view-placeholder">'
                    f'<span class="view-placeholder-icon">{cfg["icon"]}</span>'
                    f'<span>{view_name}</span>'
                    f'<span style="font-size:0.7rem;opacity:0.6;">not rendered</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Individual generate button below each slot
            if st.button(
                "Generate" if not vurl else "Re-render",
                key=f"view_btn_{view_name}",
                use_container_width=True,
            ):
                with st.spinner(f"Rendering {view_name}..."):
                    try:
                        result = generate_view(base_design_url, cfg["prompt"], cfg["model"])
                        url = extract_image_url(result)
                        if url:
                            st.session_state.setdefault("views", {})[view_name] = url
                            st.toast(f"{view_name} ready!", icon=cfg["icon"])
                            st.rerun()
                        else:
                            st.warning(f"{view_name} produced no image.")
                    except Exception as e:
                        st.error(f"{view_name} failed: {e}")


# ── REFINEMENT ────────────────────────────────────────────────────────────────

if st.session_state.get("last_results"):
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown(
            '<div class="section-title"><span class="sec-num">07</span> Refine Your Design</div>'
            '<div class="refine-sub">Describe modifications — the original references and specs are preserved unless you override them</div>',
            unsafe_allow_html=True,
        )

        refinement = st.text_input(
            "Modifications",
            placeholder="e.g. 'Thinner prongs, add milgrain detail, make the halo more pronounced'",
            label_visibility="collapsed",
        )

        _ref_btn_cols = st.columns([1, 3, 1])
        with _ref_btn_cols[1]:
            _regen = st.button("↺ REGENERATE WITH CHANGES", use_container_width=True)

    if _regen:
        if not refinement:
            st.warning("Describe what to change before regenerating.")
        else:
            base_prompt = st.session_state["last_prompt"]
            refined_prompt = (
                f"{base_prompt}\n\n"
                f"REFINEMENT (apply on top of the above)\n{refinement.strip()}\n"
                f"Keep the previously specified components, metals, and stones unchanged "
                f"except where the refinement explicitly overrides them."
            )
            base_target = st.session_state.get("last_target_summary", "")
            refinement_target = (
                f"{base_target}\n\nADDITIONAL REFINEMENT REQUESTED: {refinement.strip()}"
            )
            image_urls = st.session_state.get("last_image_urls") or [
                upload_to_fal(s["file"]) for s in image_specs if s["file"]
            ]

            r_best_url = None
            r_best_score = -1
            r_best_diagnosis = None
            r_attempts_log = []
            r_current_prompt = refined_prompt
            r_prev_score = -1
            r_correction_addenda = []

            with st.status("Regenerating with refinements...", expanded=True) as r_status:
                for attempt in range(1, MAX_VALIDATION_TRIES + 1):
                    st.write(f"✨ Rendering attempt {attempt}/{MAX_VALIDATION_TRIES}...")
                    try:
                        result = fal_client.subscribe(
                            "fal-ai/nano-banana-pro/edit",
                            arguments={
                                "image_urls": image_urls,
                                "prompt": r_current_prompt,
                                "num_images": 1,
                                "resolution": "2K",
                                "aspect_ratio": "auto",
                                "output_format": "png",
                            },
                        )
                    except Exception as e:
                        r_attempts_log.append({"attempt": attempt, "error": str(e)})
                        continue

                    url = extract_image_url(result)
                    if not url:
                        r_attempts_log.append({"attempt": attempt, "error": "no image returned"})
                        continue

                    try:
                        url = strip_url_to_white(url)
                    except Exception:
                        pass

                    st.write(f"🎯 Validating attempt {attempt}...")
                    diagnosis = validate_design(url, refinement_target)
                    score = diagnosis.get("score", 0)
                    r_attempts_log.append({"attempt": attempt, "score": score, "url": url})

                    if score > r_best_score:
                        r_best_url = url
                        r_best_score = score
                        r_best_diagnosis = diagnosis

                    if score >= VALIDATION_THRESHOLD:
                        st.write(f"✓ Passed — score {score}/100")
                        break
                    if attempt > 1 and score < r_prev_score:
                        r_attempts_log[-1]["regressed"] = True
                        st.write(f"⚠ Score regressed, stopping early")
                        break
                    r_prev_score = score

                    if attempt < MAX_VALIDATION_TRIES:
                        st.write(f"↻ Score {score}% — refining prompt for next attempt...")
                        r_correction_addenda.append(build_correction_addendum(diagnosis, attempt + 1))
                        r_current_prompt = refined_prompt + "\n\n" + "\n\n".join(r_correction_addenda)

                r_passed = r_best_score >= VALIDATION_THRESHOLD
                r_status.update(
                    label=f"✓ Refinement complete — score {r_best_score}/100",
                    state="complete" if r_best_url else "error",
                )

            if r_best_url:
                r_tries_used = len(r_attempts_log)
                r_status_text = (
                    f"Passed in {r_tries_used} {'try' if r_tries_used == 1 else 'tries'}"
                    if r_passed
                    else f"Best of {r_tries_used} {'try' if r_tries_used == 1 else 'tries'} (below {VALIDATION_THRESHOLD}%)"
                )

                st.markdown('<div class="section-title"><span class="sec-num">✦</span> Refined Design</div>', unsafe_allow_html=True)
                _rscore_class = "pass" if r_passed else "warn"
                _rring_color = "#22D67A" if r_passed else "#FFB547"
                _rcirc = 2 * 3.14159 * 40
                _rdash = (_rcirc * r_best_score) / 100
                _rgap = _rcirc - _rdash
                st.markdown(f"""
                <div class="score-block">
                  <div class="score-ring-wrap">
                    <svg width="100" height="100" viewBox="0 0 100 100">
                      <circle cx="50" cy="50" r="40" fill="none" stroke="rgba(56,189,248,0.12)" stroke-width="8"/>
                      <circle cx="50" cy="50" r="40" fill="none" stroke="{_rring_color}"
                        stroke-width="8" stroke-linecap="round"
                        stroke-dasharray="{_rdash:.1f} {_rgap:.1f}"
                        style="filter:drop-shadow(0 0 6px {_rring_color}88);"/>
                    </svg>
                    <div class="score-ring-center">
                      <span class="score-ring-num {_rscore_class}">{r_best_score}</span>
                      <span class="score-ring-pct">/ 100</span>
                    </div>
                  </div>
                  <div>
                    <div class="score-label">Design Match</div>
                    <div class="score-status-title">{'Passed Validation' if r_passed else 'Best Attempt'}</div>
                    <div class="score-status-sub">{r_status_text}</div>
                    <div class="score-badge {_rscore_class}">{'✓ Production Ready' if r_passed else '⚠ Below Threshold'}</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    st.image(r_best_url, use_container_width=True)
                    st.markdown(
                        f'<div style="text-align:center;margin-top:8px;">'
                        f'<a href="{r_best_url}" target="_blank" '
                        f'style="color:var(--cyan-deep);font-weight:600;text-decoration:none;">'
                        f'↓ Download Full Resolution</a></div>',
                        unsafe_allow_html=True,
                    )

                with st.expander("Refinement Validation Report", expanded=not r_passed):
                    for entry in r_attempts_log:
                        if "error" in entry:
                            st.markdown(f"**Attempt {entry['attempt']}** — failed: {entry['error']}")
                        else:
                            regressed = " — *stopped (score regressed)*" if entry.get("regressed") else ""
                            st.markdown(f"**Attempt {entry['attempt']}** — score **{entry.get('score', 0)}/100**{regressed}")
                    if r_best_diagnosis:
                        if r_best_diagnosis.get("per_component"):
                            st.markdown("**Per-component scores:**")
                            for component, comp_score in r_best_diagnosis["per_component"].items():
                                bar = "█" * (comp_score // 10) + "░" * (10 - comp_score // 10)
                                st.markdown(f"- `{bar}` **{comp_score}/100** — {component}")
                        if r_best_diagnosis.get("correct"):
                            st.markdown("**Rendered correctly:**")
                            for c in r_best_diagnosis["correct"]:
                                st.markdown(f"- ✓ {c}")
                        if r_best_diagnosis.get("missing"):
                            st.markdown("**Missing:**")
                            for m in r_best_diagnosis["missing"]:
                                st.markdown(f"- ✗ {m}")
                        if r_best_diagnosis.get("wrong"):
                            st.markdown("**Rendered incorrectly:**")
                            for w in r_best_diagnosis["wrong"]:
                                st.markdown(f"- ⚠ {w}")
                        if r_best_diagnosis.get("suggestion"):
                            st.markdown(f"**Validator's suggestion:** {r_best_diagnosis['suggestion']}")
                st.toast("Refined design ready!", icon="✨")
            else:
                st.error("Refinement failed. Please try again.")


# ── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="jb-footer">
  <div class="jb-footer-inner">
    <img src="https://jewelbench.ai/wp-content/uploads/2025/05/jewelbench_logo.svg" class="jb-footer-logo" alt="JewelBench"/>
    <div class="jb-footer-links">
      <span>Component Composer</span>
      <span class="jb-footer-dot">·</span>
      <span>Powered by JewelBench Engine</span>
      <span class="jb-footer-dot">·</span>
      <span>© 2026 JewelBench AI</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
