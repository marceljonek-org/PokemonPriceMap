"""Stiahne obrázky balení, zmenší ich a uloží do docs/img ako WebP.

Stránka tak nikdy nehotlinkuje na eshop: obrázok nezmizne, keď eshop
produkt stiahne, a nezaťažuje cudzí server.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
IMG_DIR = DOCS / "img"
DATA = ROOT / "data"

MAX_SIDE = 400
QUALITY = 82
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
}


def local_name(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12] + ".webp"


def download(client: httpx.Client, url: str) -> Image.Image | None:
    try:
        resp = client.get(url, timeout=20)
        resp.raise_for_status()
        image = Image.open(io.BytesIO(resp.content))
        image.load()
        return image
    except Exception as exc:                              # noqa: BLE001
        print(f"  obrázok zlyhal: {url[:80]} ({type(exc).__name__})")
        return None


def convert(image: Image.Image) -> Image.Image:
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.mode else "RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    if image.mode == "RGBA":
        canvas = Image.new("RGB", image.size, (255, 255, 255))
        canvas.paste(image, mask=image.split()[-1])
        image = canvas
    return image


def main() -> int:
    payload_path = DOCS / "latest.json"
    if not payload_path.exists():
        print("latest.json chýba — najprv spusti scrape.py", file=sys.stderr)
        return 1

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    fetched = kept = 0

    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        for product in payload["products"]:
            target = IMG_DIR / local_name(product["key"])
            if target.exists():
                product["image"] = f"img/{target.name}"
                kept += 1
                continue
            source = product.get("image") or ""
            if not source.startswith("http"):
                product["image"] = ""
                continue
            image = download(client, source)
            if image is None:
                product["image"] = ""
                continue
            convert(image).save(target, "WEBP", quality=QUALITY, method=5)
            product["image"] = f"img/{target.name}"
            fetched += 1

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload_path.write_text(body, encoding="utf-8")
    (DATA / "latest.json").write_text(body, encoding="utf-8")
    print(f"Obrázky: {fetched} nových, {kept} už uložených")
    return 0


if __name__ == "__main__":
    sys.exit(main())
