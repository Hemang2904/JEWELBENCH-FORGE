"""Fidelity gate for the geometry-grounded Additional Views (fix #3).

Run this with a real FAL_KEY against a real ring render BEFORE flipping
VIEW_ENGINE=geometry in production. It runs the full pipeline — image -> fal
image-to-3D -> orthographic clay renders — and writes one PNG per named view so
you can eyeball whether the reconstruction is faithful enough to ship.

    export FAL_KEY=...                      # your fal key
    # optional: pick the 3D model (default fal-ai/trellis)
    # export VIEW_3D_MODEL=fal-ai/hyper3d/rodin
    python tools/verify_3d_views.py <image_url_or_local_path> [out_dir]

Notes
-----
* A local image path is uploaded to fal first (needs fal-client's upload).
* Output is CLAY-shaded geometry from TRUE angles, not a textured photo — that
  is expected and is the point (true top-down / underside an edit model can't do).
* If the rings come back blobby / stones lost, the 3D model isn't faithful enough
  for this design class — keep VIEW_ENGINE=edit (fix #1) or try another
  VIEW_3D_MODEL.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import views3d  # noqa: E402

VIEW_NAMES = list(views3d.VIEW_3D_CAMERA.keys())


def _resolve_image(arg: str) -> str:
    if arg.startswith("http://") or arg.startswith("https://"):
        return arg
    import fal_client
    print(f"Uploading local image {arg} to fal…")
    return fal_client.upload_file(arg)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if not os.environ.get("FAL_KEY"):
        print("ERROR: set FAL_KEY in the environment first.")
        sys.exit(2)

    image = _resolve_image(sys.argv[1])
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "tools/_3d_view_check"
    os.makedirs(out_dir, exist_ok=True)

    print(f"Model: {views3d.GEN_3D_MODEL}")
    print(f"Image: {image}")
    print("Generating 3D mesh + rendering views (this can take 30–90 s)…")
    views, glb_url = views3d.generate_3d_views(image, VIEW_NAMES)

    print(f"\nGLB: {glb_url}\n")
    for name, png in views.items():
        safe = name.split(" ")[0].replace("/", "_").replace("(", "").replace(")", "")
        path = os.path.join(out_dir, f"{safe}.png")
        with open(path, "wb") as fh:
            fh.write(png)
        print(f"  ✓ {name:24s} -> {path}")

    print(f"\nDone. Open {out_dir}/ and judge fidelity before merging.")


if __name__ == "__main__":
    main()
