"""Geometry-grounded multi-view renderer (fix #3 for "repeating views").

Edit models (nano-banana/edit, Flux Kontext) anchor to the input image and can't
synthesise a genuinely new camera angle — every "view" collapses to the input
~3/4 pose. The only way to get a TRUE top-down / underside / pure-side view of a
novel ring is to reconstruct geometry and re-photograph it.

Pipeline:
    base 2D render  --(fal image-to-3D)-->  GLB mesh  --(orthographic raster)-->
    top / front / side / underside / ... PNGs at exact camera angles.

ONE image-to-3D call produces ALL views, so the angles are mutually consistent
(same physical object) and the cost is a single 3D generation, not N renders.

Rendering is a pure numpy + Pillow software rasteriser (flat-shaded, painter's
algorithm). No OpenGL / EGL / pyrender — so it runs headless on Streamlit Cloud
with only numpy + Pillow + trimesh. The result is a clean CLAY-shaded geometry
view (not textured/photoreal); for additional weight-estimation views that true
angle + accurate silhouette is exactly what matters.
"""

from __future__ import annotations

import io
import math
import os
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# fal model that turns one image into a 3D mesh (GLB). Override via env.
GEN_3D_MODEL = os.environ.get("VIEW_3D_MODEL", "fal-ai/trellis")

# Canvas + look. Clay grey, soft top-left light, white seamless background.
_CANVAS = int(os.environ.get("VIEW_3D_PX", "768"))
_BG = (255, 255, 255)
_BASE_RGB = np.array([196, 178, 150], dtype=float)  # warm metal-ish clay
_LIGHT = np.array([-0.35, 0.55, 0.75])              # from upper-left-front
_LIGHT /= np.linalg.norm(_LIGHT)
_AMBIENT = 0.32

# Per named view: (azimuth°, elevation°, zoom, y_shift) in a Y-up world.
#   azimuth  — rotation around the vertical axis (0 = front, 90 = right side)
#   elevation— +90 looks straight down (top), -90 straight up (underside)
#   zoom     — >1 crops in (macro); y_shift re-centres after a zoom (px, +down)
VIEW_3D_CAMERA = {
    "Top":         (0.0,  90.0, 1.0,  0.0),   # straight down (plan)
    "Front":       (0.0,   0.0, 1.0,  0.0),   # head-on elevation
    "Side":        (90.0,  0.0, 1.0,  0.0),   # true 90° profile
    "Perspective": (45.0, 30.0, 1.0,  0.0),   # 3/4 hero angle
}
# Canonical fallback for any unmapped name.
_DEFAULT_CAM = (45.0, 30.0, 1.0, 0.0)


# ── geometry: load + camera ──────────────────────────────────────────────────

def _as_single_mesh(loaded):
    """trimesh may return a Scene (multi-part GLB) or a Trimesh — unify it."""
    import trimesh
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise ValueError("empty scene")
        return trimesh.util.concatenate(
            [g for g in loaded.dump()] if hasattr(loaded, "dump") else
            list(loaded.geometry.values())
        )
    return loaded


def _load_mesh(glb_path: str, max_faces: int = 40000):
    import trimesh
    mesh = _as_single_mesh(trimesh.load(glb_path, force="scene"))
    if mesh.faces is None or len(mesh.faces) == 0:
        raise ValueError("mesh has no faces")
    # Decimate heavy meshes so the Python rasteriser stays fast.
    if len(mesh.faces) > max_faces:
        try:
            mesh = mesh.simplify_quadric_decimation(max_faces)
        except Exception:
            pass
    # Centre on the origin and normalise scale to a unit-ish box.
    mesh.vertices -= mesh.vertices.mean(axis=0)
    scale = float(np.abs(mesh.vertices).max()) or 1.0
    mesh.vertices /= scale
    return mesh


def _rotation(azim_deg: float, elev_deg: float) -> np.ndarray:
    """View-space rotation: yaw around Y (up), then pitch around X."""
    a, e = math.radians(azim_deg), math.radians(elev_deg)
    ca, sa = math.cos(a), math.sin(a)
    ce, se = math.cos(e), math.sin(e)
    yaw = np.array([[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]])
    pitch = np.array([[1, 0, 0], [0, ce, -se], [0, se, ce]])
    return pitch @ yaw


def _render_view(mesh, azim, elev, zoom, y_shift, px=_CANVAS) -> Image.Image:
    """Orthographic flat-shaded raster of `mesh` from one camera angle."""
    R = _rotation(azim, elev)
    v = mesh.vertices @ R.T                      # (N,3) view space: x right, y up, z toward cam
    f = mesh.faces

    # Orthographic fit: map view-space x/y into the canvas with a margin.
    xy = v[:, :2]
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    span = float((hi - lo).max()) or 1.0
    margin = 0.10
    fit = (px * (1 - 2 * margin)) / span * zoom
    cx, cy = (lo + hi) / 2.0
    sx = (v[:, 0] - cx) * fit + px / 2.0
    sy = px / 2.0 - (v[:, 1] - cy) * fit + y_shift * px   # flip Y for image coords
    screen = np.column_stack([sx, sy])

    # Flat shading from face normals (in view space).
    tri = v[f]                                   # (M,3,3)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    n = n / ln
    diff = np.clip(n @ _LIGHT, 0.0, 1.0)
    shade = _AMBIENT + (1 - _AMBIENT) * diff     # (M,)

    # Painter's algorithm: draw far faces first (smaller z = further from cam).
    depth = v[f, 2].mean(axis=1)
    order = np.argsort(depth)

    img = Image.new("RGB", (px, px), _BG)
    drw = ImageDraw.Draw(img)
    sp = screen[f]                               # (M,3,2)
    for i in order:
        # Backface cull: skip faces pointing away from the camera (+z).
        if n[i, 2] <= 0.02:
            continue
        col = tuple(int(c) for c in np.clip(_BASE_RGB * shade[i], 0, 255))
        p = sp[i]
        drw.polygon([(p[0, 0], p[0, 1]), (p[1, 0], p[1, 1]),
                     (p[2, 0], p[2, 1])], fill=col)
    return img


def render_all_views(glb_path: str, view_names) -> dict:
    """Render each requested named view of one GLB. Returns {name: PNG bytes}."""
    mesh = _load_mesh(glb_path)
    out = {}
    for name in view_names:
        azim, elev, zoom, yshift = VIEW_3D_CAMERA.get(name, _DEFAULT_CAM)
        img = _render_view(mesh, azim, elev, zoom, yshift)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out[name] = buf.getvalue()
    return out


# ── fal image-to-3D ───────────────────────────────────────────────────────────

def image_to_glb(image_url: str) -> str:
    """Call the fal image-to-3D model; return the GLB mesh URL."""
    import fal_client
    result = fal_client.subscribe(GEN_3D_MODEL, arguments={"image_url": image_url})
    mesh = (result or {}).get("model_mesh") or (result or {}).get("model_meshes")
    if isinstance(mesh, list):
        mesh = mesh[0] if mesh else None
    url = (mesh or {}).get("url")
    if not url:
        raise RuntimeError(f"{GEN_3D_MODEL} returned no mesh: keys={list((result or {}).keys())}")
    return url


def _download(url: str) -> str:
    import requests
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".glb")
    with os.fdopen(fd, "wb") as fh:
        fh.write(r.content)
    return path


def render_from_glb_url(glb_url: str, view_names):
    """Download a GLB once and render the requested named views from it."""
    glb_path = _download(glb_url)
    try:
        return render_all_views(glb_path, view_names)
    finally:
        try:
            os.remove(glb_path)
        except OSError:
            pass


def generate_3d_views(image_url: str, view_names):
    """Full pipeline: image -> 3D mesh -> orthographic views.

    Returns (views_dict, glb_url). `views_dict` maps view name -> PNG bytes.
    The glb_url is returned so a single view can later be re-rendered without
    paying for another 3D generation. Raises on failure so the caller can fall
    back to the edit-model path.
    """
    glb_url = image_to_glb(image_url)
    return render_from_glb_url(glb_url, view_names), glb_url


# ── photoreal re-skin: correct POSE from the mesh, photoreal LOOK from an edit ──
# The geometry render fixes the camera pose/orientation (true top/front/side/
# perspective — something edit models can't hold on their own); an edit model then
# re-skins that exact pose to look like the real ring (metal + stones). Two fal
# calls per view + one image-to-3D, but you get correct angles AND a photoreal look.
RESKIN_MODEL = os.environ.get("VIEW_RESKIN_MODEL", "fal-ai/nano-banana-pro/edit")


def _reskin_view(clay_png: bytes, design_url: str, view_name: str) -> str:
    """Grey clay pose (image 1) + the real design (image 2) -> a PHOTOREAL render of
    the real ring at the clay's exact camera angle. Returns the photoreal image URL."""
    import fal_client
    clay_url = fal_client.upload(clay_png, "image/png")
    prompt = (
        "Photorealistic studio product render of the jewelry piece shown in the SECOND "
        f"image, posed at the EXACT camera angle and orientation of the FIRST image (a grey "
        f"clay preview that defines only the {view_name.lower()} camera pose — ignore its "
        "colour and material entirely). Reproduce the second image's metal colour, gemstones, "
        "prongs, band and every design detail faithfully. Soft studio lighting, gentle "
        "reflections, clean pure-white RGB(255,255,255) background, piece centered and sharp. "
        "Show the piece ALONE as a product shot — never on a hand, finger, ear, neck or mannequin."
    )
    r = fal_client.subscribe(RESKIN_MODEL, arguments={
        "image_urls": [clay_url, design_url], "prompt": prompt, "num_images": 1,
        "resolution": "2K", "aspect_ratio": "auto", "output_format": "png"})
    return (r.get("images") or [{}])[0].get("url") or (r.get("image") or {}).get("url")


def _reskin_clay_set(clay: dict, design_url: str) -> dict:
    """Re-skin a {name: clay PNG bytes} set in parallel -> {name: photoreal url}."""
    import sys
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: dict = {}
    if not clay:
        return out
    with ThreadPoolExecutor(max_workers=len(clay)) as ex:
        futs = {ex.submit(_reskin_view, png, design_url, name): name
                for name, png in clay.items()}
        for fu in as_completed(futs):
            name = futs[fu]
            try:
                u = fu.result()
                if u:
                    out[name] = u
            except Exception as e:  # one view failing shouldn't sink the batch
                print(f"[reskin] {name} failed: {e}", file=sys.stderr)
    return out


def generate_reskin_views(image_url: str, view_names):
    """Full pipeline: image -> 3D mesh -> correct-pose clay per angle -> photoreal
    re-skin. Returns ({name: photoreal_url}, glb_url). glb_url is returned so a
    single view can be re-skinned later without paying for another 3D generation."""
    glb_url = image_to_glb(image_url)
    clay = render_from_glb_url(glb_url, view_names)   # {name: clay PNG bytes}
    return _reskin_clay_set(clay, image_url), glb_url


def reskin_views_from_glb(glb_url: str, design_url: str, view_names):
    """Re-skin specific views from an EXISTING mesh (no new image-to-3D cost)."""
    clay = render_from_glb_url(glb_url, view_names)
    return _reskin_clay_set(clay, design_url)


# ── code-drawn dimension annotations ─────────────────────────────────────────
# The numbers are drawn HERE, deterministically, from the BoM measurement set —
# never by an image model. So they are always crisp vector text, always exactly
# equal to the spec sheet, and always placed correctly. (Image models garble or
# invent digits and mis-place arrows; this can't.)

_ANNOT = (38, 42, 50)        # dark slate for dimension lines + text
_LABEL_BG = (255, 255, 255)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10 scalable default
    except TypeError:                              # very old Pillow
        return ImageFont.load_default()


def _object_bbox(img: "Image.Image", bg_thresh: int = 245):
    """Pixel bbox of the rendered ring (everything not near-white background)."""
    a = np.asarray(img.convert("RGB"))
    mask = (a < bg_thresh).any(axis=2)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        w, h = img.size
        return 0, 0, w, h
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _label(drw, x, y, text, font):
    tb = drw.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    drw.rectangle([x - 3, y - 2, x + tw + 3, y + th + 3], fill=_LABEL_BG)
    drw.text((x, y), text, fill=_ANNOT, font=font)
    return tw, th


def _dim_line(drw, p0, p1, text, font, horizontal=True):
    """Double-headed dimension line p0->p1 with a centred, halo-boxed label."""
    drw.line([p0, p1], fill=_ANNOT, width=2)
    a = 5
    if horizontal:
        for px in (p0, p1):
            s = 1 if px is p0 else -1
            drw.line([px, (px[0] + s * a, px[1] - a)], fill=_ANNOT, width=2)
            drw.line([px, (px[0] + s * a, px[1] + a)], fill=_ANNOT, width=2)
    else:
        for px in (p0, p1):
            s = 1 if px is p0 else -1
            drw.line([px, (px[0] - a, px[1] + s * a)], fill=_ANNOT, width=2)
            drw.line([px, (px[0] + a, px[1] + s * a)], fill=_ANNOT, width=2)
    mx, my = (p0[0] + p1[0]) // 2, (p0[1] + p1[1]) // 2
    tb = drw.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    if horizontal:
        _label(drw, mx - tw // 2, my - th - 7, text, font)
    else:
        _label(drw, mx + 7, my - th // 2, text, font)


def _overall_height_mm(meas: dict):
    """Approx finger-hole-bottom to head-top height (mm), from ring size + band +
    head. Used ONLY as a rough scale-bar reference on side-on views."""
    try:
        s = float(str(meas.get("ring_size")).strip())
    except (TypeError, ValueError):
        return None
    inner_d = (36.537 + 2.5535 * s) / math.pi
    return inner_d + 2 * float(meas.get("band_thickness") or 0) + float(meas.get("head_height") or 0)


def annotate_view(png_bytes: bytes, view_name: str, meas: dict) -> bytes:
    """Overlay crisp, EXACT dimension annotations onto a clay-rendered view.

    `meas` is forge_bom's measurement set; we draw only the numbers relevant to
    each angle, plus a foolproof bottom strip with the full spec. Returns PNG
    bytes. Pure-deterministic — unit-testable without any network."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    W, H = img.size
    drw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = _object_bbox(img)
    f = _font(max(15, W // 40))
    ftitle = _font(max(18, W // 30))
    pad = 18

    def g(k):
        v = meas.get(k)
        return v if isinstance(v, (int, float)) and v > 0 else None
    bw, bt, hh, hd = g("band_width"), g("band_thickness"), g("head_height"), g("head_diameter")
    rs, gw = meas.get("ring_size"), g("gold_weight_g")
    name = view_name.split(" (")[0].split(" —")[0]

    # ~10 mm scale bar — an honest visual reference on side-on views, where the
    # silhouette's vertical extent ~ the ring's overall height (set by ring size).
    # Skipped on the top-down view, where that assumption doesn't hold.
    overall_mm = _overall_height_mm(meas)
    if overall_mm and y1 > y0 and "Top" not in view_name:
        bar_px = int(round(10.0 / (overall_mm / (y1 - y0))))
        if 8 < bar_px < W - 40:
            by = H - 60
            drw.line([(20, by), (20 + bar_px, by)], fill=_ANNOT, width=3)
            for bx in (20, 20 + bar_px):
                drw.line([(bx, by - 5), (bx, by + 5)], fill=_ANNOT, width=2)
            _label(drw, 20, by - 22, "~10 mm", f)

    # title
    _label(drw, 12, 10, name, ftitle)

    # foolproof bottom spec strip — always present, always exact
    bits = []
    if bw and bt:
        bits.append(f"band {bw:g}x{bt:g} mm")
    elif bw:
        bits.append(f"band {bw:g} mm")
    if hd and hh:
        bits.append(f"head {hd:g}x{hh:g} mm")
    elif hd:
        bits.append(f"head dia {hd:g} mm")
    if rs:
        bits.append(f"US {rs}")
    if gw:
        bits.append(f"{gw:.2f} g")
    strip = "   ·   ".join(bits)
    tb = drw.textbbox((0, 0), strip, font=f)
    sh = tb[3] - tb[1]
    drw.rectangle([0, H - sh - 14, W, H], fill=(247, 247, 249))
    drw.line([(0, H - sh - 14), (W, H - sh - 14)], fill=(208, 208, 212), width=1)
    drw.text((12, H - sh - 8), strip, fill=_ANNOT, font=f)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def render_dimensioned_views(image_url: str, meas: dict, view_names=None):
    """image -> ONE mesh -> consistent ortho views -> code-drawn dimensions.

    The views are mutually consistent (same mesh) and every printed dimension is
    crisp + exactly the BoM value (drawn in code, not by an image model).
    Returns ({view_name: annotated PNG bytes}, glb_url)."""
    names = list(view_names) if view_names else list(VIEW_3D_CAMERA.keys())
    raw, glb_url = generate_3d_views(image_url, names)
    annotated = {n: annotate_view(b, n, meas) for n, b in raw.items()}
    return annotated, glb_url
