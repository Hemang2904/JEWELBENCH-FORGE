"""Which weight-ensemble models actually return on fal (in THIS app's path)?

Run locally with YOUR key (the key never leaves your machine; do not commit it):

    FAL_KEY=your_key_here python tools/test_models.py

It uploads a tiny placeholder image (also verifies upload works), then calls the
exact endpoint + each model id Forge uses, and prints ✅/❌ with the error so you
can see if google/gemini-2.5-pro is working or why it isn't.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weight_estimator as we  # noqa: E402


def main() -> None:
    if not os.environ.get("FAL_KEY"):
        sys.exit("Set FAL_KEY first:  FAL_KEY=your_key python tools/test_models.py")

    import fal_client
    from PIL import Image

    # 1) Upload a placeholder (also confirms upload/cdn works with your key).
    img = Image.new("RGB", (512, 512), (210, 190, 150))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    try:
        url = fal_client.upload(buf.getvalue(), content_type="image/png")
        print(f"✅ upload OK -> {url[:60]}...\n")
    except Exception as e:
        sys.exit(f"❌ upload FAILED ({e}). If this is a 401 cdn-v3 error, the "
                 "fal-client pin didn't take effect.")

    # 2) Hit the exact endpoint + models Forge uses.
    prompt = we._prompt("18k_yellow_gold", "7")
    models = [we.WEIGHT_MODEL_PRIMARY, we.WEIGHT_MODEL_SECONDARY] \
        + we.WEIGHT_FALLBACK_MODELS
    print(f"endpoint: {we.VISION_ENDPOINT}\n")
    for m in models:
        res = we._call_model(m, url, prompt, tries=1)
        if "total_metal_volume_mm3" in res:
            print(f"✅ {m}: OK (returned volume {res['total_metal_volume_mm3']})")
        else:
            print(f"❌ {m}: {res.get('_error')}")


if __name__ == "__main__":
    main()
