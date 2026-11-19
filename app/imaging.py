"""Upload validation and image preparation (resize, strip EXIF, base64)."""
import base64
import io
from typing import Tuple

from PIL import Image, ImageOps

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF"}
MODEL_MAX_SIDE = 768    # what the vision model receives (small payload = fast API calls)
MODEL_QUALITY = 82
MODEL_MAX_BYTES = 150 * 1024
THUMB_MAX_SIDE = 480    # what we store for history / reports


class ImageError(ValueError):
    """Raised for unreadable or unsupported uploads (maps to HTTP 400)."""


def _jpeg_bytes(img: Image.Image, max_side: int, quality: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def prepare_image(raw: bytes) -> Tuple[str, str]:
    """Validate `raw` and return (model_base64_jpeg, thumbnail_data_url)."""
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
        img.load()
    except Exception as exc:  # corrupt file, not an image, decompression bomb ...
        raise ImageError("That file could not be read as an image. Please upload a JPG, PNG or WEBP photo.") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageError("Unsupported image format. Please upload a JPG, PNG or WEBP photo.")

    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        img = flat
    else:
        img = img.convert("RGB")

    if min(img.size) < 32:
        raise ImageError("The image is too small to analyse. Please upload a larger photo.")

    model_jpeg = _jpeg_bytes(img, MODEL_MAX_SIDE, MODEL_QUALITY)
    for q in (72, 62, 52):  # rare: very detailed photos - shave quality until the payload is small
        if len(model_jpeg) <= MODEL_MAX_BYTES:
            break
        model_jpeg = _jpeg_bytes(img, MODEL_MAX_SIDE, q)
    model_b64 = base64.b64encode(model_jpeg).decode("ascii")
    thumb = "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes(img, THUMB_MAX_SIDE, 80)).decode("ascii")
    return model_b64, thumb


def shrink_b64(b64: str, max_side: int = 384, quality: int = 70) -> str:
    """Smaller copy of an already-prepared JPEG (the gatekeeper only needs to tell leaf from non-leaf)."""
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        return base64.b64encode(_jpeg_bytes(img.convert("RGB"), max_side, quality)).decode("ascii")
    except Exception:
        return b64
