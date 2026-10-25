"""Image encoding runs separately from the scalar writer."""

from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import time
from uuid import uuid4
import warnings

import numpy as np
from PIL import Image

from cloverboard.logger.context import get_context
from cloverboard.storage.paths import resolve_log_path
from cloverboard.storage.protocols import ImageRecord


def _warning(message):
    try:
        warnings.warn(message, RuntimeWarning, stacklevel=3)
    except Warning:
        pass


@dataclass
class _ImageJob:
    image: Image.Image
    name: str
    step: int
    caption: str
    format: str
    wall_time: float


class ImageWorker:
    def __init__(self, run_path, writer):
        self.path = run_path
        self.writer = writer
        self.queue = queue.Queue(maxsize=8)
        self.closed = False
        self.thread = threading.Thread(target=self._run, name="cloverboard-images", daemon=True)
        self.thread.start()

    def put(self, job):
        if self.closed:
            job.image.close()
            return
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            job.image.close()
            _warning("Cloverboard image queue is full; image skipped")

    def close(self):
        self.closed = True
        self.thread.join(timeout=10)

    def _run(self):
        while not self.closed or not self.queue.empty():
            try:
                job = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            temporary = None
            try:
                directory = resolve_log_path(self.path, "media")
                directory.mkdir(exist_ok=True)
                image_id = uuid4().hex
                filename = f"{image_id}.{job.format}"
                path = resolve_log_path(directory, filename)
                temporary = resolve_log_path(directory, filename + ".tmp")
                job.image.save(temporary, format=job.format.upper(), **({"quality": 90} if job.format == "jpeg" else {}))
                temporary.replace(path)
                self.writer.put_image(ImageRecord(image_id, job.name, job.step, job.wall_time, "media/" + filename, job.image.width, job.image.height, job.format, job.caption))
            except Exception:
                _warning("Cloverboard could not save an image")
            finally:
                job.image.close()
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass


def log_image(name: str, image, step: int, *, caption: str = "", format: str = "png") -> None:
    """Record a path, PIL image or HWC NumPy array. Float arrays must be in [0, 1].

    Input is copied now, encoding is queued. Images over 16 million pixels or
    a full queue are skipped. This call has a higher cost than scalar log().
    """
    context = get_context()
    if context is None or context.worker is None:
        return
    prepared = None
    try:
        if not name or not isinstance(step, int) or abs(step) > 2**53 - 1 or format not in {"png", "jpeg"}:
            raise ValueError("Use a name, integer step, and png or jpeg format")
        if isinstance(image, (str, Path)):
            with Image.open(image) as source:
                if source.width * source.height > 16_000_000:
                    raise ValueError("Image exceeds 16 million pixels")
                prepared = source.copy()
        elif isinstance(image, Image.Image):
            if image.width * image.height > 16_000_000:
                raise ValueError("Image exceeds 16 million pixels")
            prepared = image.copy()
        elif isinstance(image, np.ndarray):
            if image.ndim not in {2, 3} or image.shape[0] * image.shape[1] > 16_000_000 or (image.ndim == 3 and image.shape[2] not in {1, 3, 4}):
                raise ValueError("Use an HW or HWC array with 1, 3 or 4 channels")
            values = image
            if np.issubdtype(values.dtype, np.floating):
                if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1:
                    raise ValueError("Float images must contain finite values in [0, 1]")
                values = np.rint(values * 255).astype(np.uint8)
            if values.dtype != np.uint8:
                raise ValueError("Use uint8 or floating point images")
            if values.ndim == 3 and values.shape[2] == 1:
                values = values[:, :, 0]
            prepared = Image.fromarray(values.copy()).copy()
        else:
            raise ValueError("Unsupported image input")
        if prepared.mode not in {"L", "RGB", "RGBA"} or (format == "jpeg" and prepared.mode == "RGBA"):
            converted = prepared.convert("RGB")
            prepared.close()
            prepared = converted
        if context.image_worker is None:
            context.image_worker = ImageWorker(resolve_log_path(context.logdir.resolve(), context.run_id), context.worker)
        context.image_worker.put(_ImageJob(prepared, str(name), step, str(caption), format, time.time()))
    except Exception as error:
        if prepared is not None:
            prepared.close()
        _warning(f"Cloverboard image skipped: {error}")
