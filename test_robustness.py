"""Robustness test: run head/shank permutations through the live pipeline.

Imports build_combine_prompt from app.py so the test uses the exact prompt
the running Streamlit app sends to Seedream. Mocks streamlit at import time
to avoid running UI code on import.
"""

import os
import sys
import time
import json
import urllib.request
from pathlib import Path

# Load FAL_KEY from a local .env file (gitignored). Falls back to whatever
# is already in the environment so this also works when FAL_KEY is exported
# directly in the shell.
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("FAL_KEY="):
            os.environ["FAL_KEY"] = line.split("=", 1)[1]

import fal_client  # noqa: E402

# Same builder the live Streamlit app uses.
sys.path.insert(0, str(Path(__file__).parent))
from prompts import build_combine_prompt  # noqa: E402
from preprocessing import strip_background_to_white  # noqa: E402


INPUT_DIR = Path("/tmp/jewel_test_inputs")
OUTPUT_DIR = Path("/tmp/jewel_test_outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Catalog of 4 inputs with rich descriptions of head + shank for both swap directions.
RINGS = {
    "A": {
        "path": INPUT_DIR / "A_solitaire_white.png",
        "head_desc": "polished white-gold 4-prong solitaire head with round brilliant diamond",
        "shank_desc": "plain smooth tapered polished white-gold cathedral shank with no stones",
    },
    "B": {
        "path": INPUT_DIR / "B_yellow_3stone.png",
        "head_desc": "yellow-gold 4-prong head with round center diamond and a round side accent stone with ornate gallery",
        "shank_desc": "yellow-gold pavé shank with a continuous row of round white diamonds along the full shoulder",
    },
    "C": {
        "path": INPUT_DIR / "C_cad_pave.jpg",
        "head_desc": "4-prong solitaire head with round center stone (rendered in CAD silver/grey)",
        "shank_desc": "double-row pavé shank with two parallel rows of small round diamonds along the full shoulders (CAD silver/grey)",
    },
    "D": {
        "path": INPUT_DIR / "D_rosegold_emerald.jpg",
        "head_desc": "rose-gold emerald-cut center diamond with a fine round-diamond halo around the emerald cut",
        "shank_desc": "wide rose-gold statement shank fully encrusted with a lattice of marquise and round diamonds across the entire band",
    },
}

# 6 chosen permutations stress different failure modes.
PERMUTATIONS = [
    # name, head_from, shank_from, optional design specs
    ("01_Ahead_Bshank",   "A", "B", {}),  # plain head + ornate shank, cross-color
    ("02_Bhead_Ashank",   "B", "A", {}),  # ornate head + plain shank, cross-color, decoration removal
    ("03_Ahead_Dshank",   "A", "D", {}),  # plain head + maximalist shank, tri-color potential
    ("04_Dhead_Ashank",   "D", "A", {}),  # maximalist head + plain shank
    ("05_Chead_Dshank",   "C", "D", {}),  # CAD head + photoreal shank, cross-medium
    ("06_Bhead_Cshank",   "B", "C", {}),  # cross-color and cross-medium
]


def upload(path: Path) -> str:
    print(f"  bg-stripping + uploading {path.name} ...")
    suffix = path.suffix.lower()
    content_type = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return strip_background_to_white(path.read_bytes(), content_type=content_type)


def make_image_specs(head_id: str, shank_id: str):
    """Mirror what the Streamlit UI builds: list of dicts with file + description.

    The "file" field is only checked for truthiness by the prompt builder, so
    any non-None placeholder works.
    """
    head = RINGS[head_id]
    shank = RINGS[shank_id]
    return [
        {"file": object(), "description": head["head_desc"]},
        {"file": object(), "description": shank["shank_desc"]},
    ]


def run_nano_banana_pro(image_urls, prompt):
    return fal_client.subscribe(
        "fal-ai/nano-banana-pro/edit",
        arguments={
            "image_urls": image_urls,
            "prompt": prompt,
            "num_images": 1,
            "resolution": "2K",
            "aspect_ratio": "auto",
            "output_format": "png",
        },
    )


def download(url: str, dest: Path):
    with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
        f.write(r.read())


def main():
    if not os.environ.get("FAL_KEY"):
        sys.exit("FAL_KEY not set")

    # Upload each input once, cache URLs.
    print("Uploading 4 inputs to fal.ai ...")
    urls = {k: upload(v["path"]) for k, v in RINGS.items()}
    print()

    summary = []
    for name, head_id, shank_id, specs in PERMUTATIONS:
        print(f"--- {name}: head from {head_id}, shank from {shank_id} ---")
        image_specs = make_image_specs(head_id, shank_id)
        prompt = build_combine_prompt(image_specs, specs)
        image_urls = [urls[head_id], urls[shank_id]]

        t0 = time.time()
        try:
            result = run_nano_banana_pro(image_urls, prompt)
            url = (result.get("images") or [{}])[0].get("url")
            if url:
                out_path = OUTPUT_DIR / f"{name}.png"
                download(url, out_path)
                print(f"  ok in {time.time()-t0:.1f}s -> {out_path}")
                summary.append({"name": name, "head": head_id, "shank": shank_id,
                                "url": url, "local": str(out_path), "ok": True,
                                "duration_s": round(time.time() - t0, 1)})
            else:
                print(f"  no image url returned: {result}")
                summary.append({"name": name, "head": head_id, "shank": shank_id,
                                "ok": False, "error": "no url", "raw": str(result)[:300]})
        except Exception as e:
            print(f"  FAILED: {e}")
            summary.append({"name": name, "head": head_id, "shank": shank_id,
                            "ok": False, "error": str(e)})
        print()

    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary written to {summary_path}")


if __name__ == "__main__":
    main()
