"""Bounded, memory-only image previews; safe for read-only workspaces."""

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, Lock

from PIL import Image, ImageOps

_preview_workers = BoundedSemaphore(2)


@dataclass(frozen=True)
class ImagePreview:
    data: bytes
    original_size: tuple[int, int]
    returned_size: tuple[int, int]


class InvalidCrop(ValueError):
    pass


def encode_preview(source, max_side: int = 512, crop: tuple[int, int, int, int] | None = None) -> ImagePreview:
    """Normalize orientation, crop original pixels, then shrink and encode lossless PNG."""
    if not 128 <= max_side <= 1024:
        raise ValueError("max_side must be between 128 and 1024")
    with _preview_workers:
        try:
            original = Image.open(source)
        except Image.DecompressionBombError as error:
            raise ValueError("Image exceeds the preview pixel limit") from error
        with original:
            if original.format not in {"PNG", "JPEG"}:
                raise ValueError("Only PNG and JPEG images are supported")
            if original.width * original.height > 16_000_000:
                raise ValueError("Image exceeds the preview pixel limit")
            preview = ImageOps.exif_transpose(original)
            try:
                original_size = preview.size
                if crop is not None:
                    x, y, width, height = crop
                    if x < 0 or y < 0 or width < 1 or height < 1 or x + width > preview.width or y + height > preview.height:
                        raise InvalidCrop("Crop must stay within the original image")
                    region = preview.crop((x, y, x + width, y + height))
                    preview.close()
                    preview = region
                preview.thumbnail((max_side, max_side))
                if preview.mode not in ("RGB", "RGBA", "L", "LA"):
                    converted = preview.convert("RGBA")
                    preview.close()
                    preview = converted
                output = BytesIO()
                preview.save(output, format="PNG")
                return ImagePreview(output.getvalue(), original_size, preview.size)
            finally:
                preview.close()


class ThumbnailCache:
    def __init__(self, max_bytes: int = 64 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._bytes = 0
        self._items: OrderedDict[tuple, ImagePreview] = OrderedDict()
        self._pending: dict[tuple, Future[ImagePreview]] = {}
        self._lock = Lock()

    def get(self, path: Path, max_side: int = 512, crop: tuple[int, int, int, int] | None = None) -> ImagePreview:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, max_side, crop)
        with self._lock:
            cached = self._items.get(key)
            if cached is not None:
                self._items.move_to_end(key)
                return cached
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = Future()
                self._pending[key] = pending
        if not owner:
            return pending.result()
        try:
            content = self._encode(path, max_side, crop)
            with self._lock:
                self._items[key] = content
                self._bytes += len(content.data)
                while self._items and self._bytes > self.max_bytes:
                    _, removed = self._items.popitem(last=False)
                    self._bytes -= len(removed.data)
            pending.set_result(content)
            return content
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def _encode(self, path: Path, max_side: int, crop: tuple[int, int, int, int] | None) -> ImagePreview:
        return encode_preview(path, max_side, crop)


thumbnail_cache = ThumbnailCache()
