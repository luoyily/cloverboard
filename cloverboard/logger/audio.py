"""Bounded WAV recording; encoding and file writes run outside the training loop."""

from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import time
from uuid import uuid4
import wave

import numpy as np

from cloverboard.logger.context import get_context
from cloverboard.logger.images import _warning
from cloverboard.storage.paths import resolve_log_path
from cloverboard.storage.protocols import AudioRecord

MAX_AUDIO_BYTES = 32 * 1024 * 1024


@dataclass
class AudioJob:
    samples: np.ndarray
    sample_rate: int
    name: str
    step: int
    caption: str
    wall_time: float


class AudioWorker:
    def __init__(self, run_path, writer):
        self.path, self.writer = run_path, writer
        self.queue = queue.Queue(maxsize=4)
        self.closed = False
        self.thread = threading.Thread(target=self._run, name="cloverboard-audio", daemon=True)
        self.thread.start()

    def put(self, job):
        if self.closed:
            return
        try:
            self.queue.put_nowait(job)
        except queue.Full:
            _warning("Cloverboard audio queue is full; audio skipped")

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
                audio_id = uuid4().hex
                path = resolve_log_path(directory, audio_id + ".wav")
                temporary = resolve_log_path(directory, audio_id + ".wav.tmp")
                samples = job.samples
                with wave.open(str(temporary), "wb") as output:
                    output.setnchannels(samples.shape[1])
                    output.setsampwidth(2)
                    output.setframerate(job.sample_rate)
                    output.writeframes(samples.astype("<i2", copy=False).tobytes())
                temporary.replace(path)
                self.writer.put_audio(AudioRecord(audio_id, job.name, job.step, job.wall_time, "media/" + path.name, job.sample_rate, samples.shape[1], samples.shape[0], job.caption))
            except Exception:
                _warning("Cloverboard could not save audio")
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass


def log_audio(name: str, audio, step: int, *, sample_rate: int | None = None, caption: str = "") -> None:
    """Record PCM16 WAV or a float [-1, 1]/int16 NumPy array (samples, channels).

    Arrays require sample_rate; WAV files use their declared rate. Up to two
    channels and 32 MiB decoded PCM per record. Failures warn and skip logging.
    """
    context = get_context()
    if context is None or context.worker is None:
        return
    try:
        if not isinstance(name, str) or not name.strip() or len(name) > 500 or not isinstance(step, int) or isinstance(step, bool) or abs(step) > 2**53 - 1:
            raise ValueError("Use a name and safe integer step")
        if isinstance(audio, (str, Path)):
            with wave.open(str(audio), "rb") as source:
                channels, frames, rate = source.getnchannels(), source.getnframes(), source.getframerate()
                if source.getsampwidth() != 2 or channels not in (1, 2) or frames * channels * 2 > MAX_AUDIO_BYTES:
                    raise ValueError("Use mono/stereo PCM16 WAV up to 32 MiB")
                if sample_rate is not None and sample_rate != rate:
                    raise ValueError("sample_rate must match the WAV header; no resampling is performed")
                samples = np.frombuffer(source.readframes(frames), dtype="<i2").reshape(-1, channels).copy()
                sample_rate = rate
        elif isinstance(audio, np.ndarray):
            values = audio[:, None] if audio.ndim == 1 else audio
            if values.ndim != 2 or values.shape[1] not in (1, 2) or values.size * 2 > MAX_AUDIO_BYTES:
                raise ValueError("Use mono or (samples, channels) arrays up to 32 MiB PCM")
            if np.issubdtype(values.dtype, np.floating):
                if not np.isfinite(values).all() or np.any(values < -1) or np.any(values > 1):
                    raise ValueError("Float audio must be finite and within [-1, 1]")
                samples = np.rint(values * 32767).astype("<i2")
            elif values.dtype == np.int16:
                samples = values.copy()
            else:
                raise ValueError("Use float or int16 audio")
        else:
            raise ValueError("Use a WAV path or NumPy array")
        if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or not 1000 <= sample_rate <= 384000 or not len(samples):
            raise ValueError("Use nonempty audio and an integer sample_rate between 1000 and 384000")
        if context.audio_worker is None:
            context.audio_worker = AudioWorker(resolve_log_path(context.logdir.resolve(), context.run_id), context.worker)
        context.audio_worker.put(AudioJob(samples, sample_rate, name, step, str(caption), time.time()))
    except Exception as error:
        _warning(f"Cloverboard audio skipped: {error}")
