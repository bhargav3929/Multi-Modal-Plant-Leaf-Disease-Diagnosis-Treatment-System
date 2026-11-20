"""Bundled 1-click sample specimens.

Three REAL open-licence leaf photographs are fetched once from Wikimedia Commons (httpx + a proper
User-Agent), centre-cropped and resized to 768x768 JPEG. A fourth natural photo
(`non_leaf_control.jpg`, a coffee mug) exercises the Gatekeeper's rejection path.
Credits / licences are written to samples/CREDITS.txt.
"""
import io
import json
import logging
from typing import List, Optional
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps

from . import config

log = logging.getLogger("agri")

SIZE = 768
USER_AGENT = ("PlantLeafDiseaseLangGraph/1.0 (https://github.com/plant-leaf-disease-langgraph; "
              "educational demo) httpx")
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=1280"

SAMPLES = [
    {"id": "healthy-leaf", "file": "healthy_leaf.jpg", "label": "Healthy Leaf", "hint": "Expect: Healthy tomato foliage",
     "commons": "Healthy tomato leaves (7871755330).jpg", "licence": "CC BY 2.0"},
    {"id": "early-blight", "file": "early_blight_leaf.jpg", "label": "Early Blight",
     "hint": "Expect: Fungal, concentric brown lesions (Alternaria solani)",
     "commons": "Early blight on tomato leaves (7871930010).jpg", "licence": "CC BY 2.0"},
    {"id": "powdery-mildew", "file": "powdery_mildew_leaf.jpg", "label": "Powdery Mildew",
     "hint": "Expect: Fungal, white powdery coating",
     "commons": "Powdery mildew 9.jpg", "licence": "CC BY-SA 4.0"},
    {"id": "non-leaf-control", "file": "non_leaf_control.jpg", "label": "Non-Leaf / AI Control",
     "hint": "Expect: Gatekeeper rejects (a coffee mug, not a plant)",
     "commons": "Mug of Coffee on a Wooden Table (26804116318).jpg", "licence": "CC0"},
]


def _square_jpeg(raw: bytes) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    return ImageOps.fit(img, (SIZE, SIZE), method=Image.Resampling.LANCZOS)


def _download(name: str) -> bytes:
    url = COMMONS.format(name=quote(name.replace(" ", "_"), safe="()"))
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def ensure_samples(force: bool = False) -> None:
    """Create any missing sample. Network failures are logged, never fatal."""
    config.SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for s in SAMPLES:
        path = config.SAMPLES_DIR / s["file"]
        if path.exists() and not force:
            continue
        try:
            _square_jpeg(_download(s["commons"])).save(path, format="JPEG", quality=82, optimize=True)
        except Exception as exc:  # offline, blocked, bad image ...
            log.warning("could not create sample %s: %s", s["file"], exc)
    credits = ["Sample photographs from Wikimedia Commons (https://commons.wikimedia.org):", ""]
    credits += [f"- {s['file']}: File:{s['commons']} ({s['licence']})" for s in SAMPLES ]
    (config.SAMPLES_DIR / "CREDITS.txt").write_text("\n".join(credits) + "\n", encoding="utf-8")


def _find(sample_id: str) -> Optional[dict]:
    return next((s for s in SAMPLES if s["id"] == sample_id), None)


def saved_result(sample_id: str) -> Optional[dict]:
    """Result recorded from a real live run (samples/results/<id>.json), or None."""
    path = config.RESULTS_DIR / f"{sample_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _entry(s: dict) -> dict:
    return {"id": s["id"], "title": s["label"], "hint": s["hint"], "input_url": f"samples/{s['file']}",
            "has_saved_result": (config.RESULTS_DIR / f"{s['id']}.json").is_file()}


def sample_manifest() -> dict:
    return {"samples": [_entry(s) for s in SAMPLES if (config.SAMPLES_DIR / s["file"]).exists()]}


def sample_detail(sample_id: str) -> Optional[dict]:
    s = _find(sample_id)
    if not s or not (config.SAMPLES_DIR / s["file"]).exists():
        return None
    return {k: v for k, v in _entry(s).items() if k != "has_saved_result"} | {"result": saved_result(sample_id)}
