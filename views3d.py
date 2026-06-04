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
from PIL import Image, ImageDraw

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
    "Top-Down (plan)":       (0.0,  90.0, 1.0,  0.0),
    "Side Profile (90°)":    (90.0,  0.0, 1.0,  0.0),
    "Front Elevation":       (0.0,   0.0, 1.0,  0.0),
    "Three-Quarter (45°)":   (45.0, 30.0, 1.0,  0.0),
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
