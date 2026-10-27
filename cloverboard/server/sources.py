"""One-hop source routing. Public IDs never become filesystem paths directly."""

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import re
from threading import Lock
import tomllib
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from cloverboard.server.models import RunSummaryResponse
from cloverboard.storage.paths import UnsafeLogPathError, validate_run_id
from cloverboard.storage.protocols import FileAsset, ImageRecord, AudioRecord, AudioPage, TextRecord, MediaNameSummary, MediaTimeline, ImagePage, RunNote, RunNotFound, RunDetails, RunSummary, ScalarBounds, ScalarPoint, ScalarSummary, StorageReader
from cloverboard.storage.sqlite_backend import SqliteStorageReader

_SOURCE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class SourceUnavailable(RuntimeError):
    pass


class SourceRequestError(RuntimeError):
    """A peer rejected the query; preserve its public HTTP error semantics."""

    def __init__(self, status_code: int, detail: str | list[dict]):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass
class Source:
    key: str
    label: str
    reader: StorageReader | None = None
    url: str | None = None
    token: str | None = None


def qualify(key: str, run_id: str) -> str:
    return key + ":" + base64.urlsafe_b64encode(run_id.encode()).decode().rstrip("=")


def split_run_id(run_id: str) -> tuple[str, str]:
    if ":" not in run_id:
        return "local", validate_run_id(run_id)
    key, encoded = run_id.split(":", 1)
    if not _SOURCE_ID.fullmatch(key) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", encoded):
        raise UnsafeLogPathError("Invalid source identity")
    try:
        original = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode()
    except (ValueError, UnicodeError) as error:
        raise UnsafeLogPathError("Invalid source identity") from error
    if not original or qualify(key, original) != run_id:
        raise UnsafeLogPathError("Invalid source identity")
    return key, original


def _parse_source_url(value: str) -> tuple[str, str | None]:
    url = value.strip()
    if "://" not in url:
        url = "http://" + url
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise ValueError
        parsed.port  # Validate the port before a peer is used.
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise ValueError("Use an HTTP(S) source URL with an optional ?token=TOKEN and no embedded credentials or fragment") from None
    if len(query) > 1 or (query and (query[0][0] != "token" or not query[0][1])):
        raise ValueError("Source URLs accept only one non-empty token query parameter")
    token = query[0][1] if query else None
    if token and any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise ValueError("Source tokens cannot contain control characters")
    return parsed._replace(path=parsed.path.rstrip("/"), query="").geturl(), token


def load_sources(peers: str = "", local_sources=(), config=None) -> list[Source]:
    items = []
    base = Path.cwd()
    if config:
        config_path = Path(config).resolve()
        base = config_path.parent
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        items.extend((key, value) for key, value in data.get("sources", {}).items())
    for item in local_sources:
        key, separator, path = item.partition("=")
        if not separator:
            raise ValueError("Use --source NAME=PATH")
        items.append((key, {"logdir": str(Path(path).resolve())}))
    for item in filter(None, (value.strip() for value in peers.split(","))):
        name, separator, address = item.partition("=")
        named = bool(separator and _SOURCE_ID.fullmatch(name))
        key, url = (name, address) if named else (None, item)
        items.append((key, {"url": url}))
    sources = []
    seen = {"local"}
    for key, item in items:
        if ("url" in item) == ("logdir" in item):
            raise ValueError("Each source needs exactly one url or logdir")
        if "url" in item:
            url, url_token = _parse_source_url(str(item["url"]))
            if key is None:
                key = "peer-" + hashlib.sha256(url.encode()).hexdigest()[:10]
        if not _SOURCE_ID.fullmatch(key) or key in seen:
            raise ValueError("Source names must be unique letters, digits, underscores or hyphens; local is reserved")
        seen.add(key)
        label = str(item.get("label", key))
        if "logdir" in item:
            sources.append(Source(key, label, reader=SqliteStorageReader(base / item["logdir"])))
            continue
        if url_token is not None and ("token" in item or "token_env" in item):
            raise ValueError(f"Specify the token only once for source {key}")
        token = url_token if url_token is not None else item.get("token")
        if item.get("token_env"):
            token = os.environ.get(item["token_env"])
            if not token:
                raise ValueError(f"Token environment variable is missing for source {key}")
        sources.append(Source(key, label, url=url, token=token))
    return sources


class SourceReader:
    def __init__(self, primary: StorageReader | None, sources: list[Source]):
        self.primary = primary
        self.sources = {source.key: source for source in sources}
        self._runs: dict[str, list[RunSummary]] = {}
        self._online = {source.key: True for source in sources}
        self._lock = Lock()
        self._client_lock = Lock()
        self._client = None

    def open(self):
        """Share the thread-safe connection pool; credentials stay per request."""
        with self._client_lock:
            if self._client is None and any(source.url for source in self.sources.values()):
                self._client = httpx.Client(timeout=3, follow_redirects=False, trust_env=False)
            return self._client

    def close(self):
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _json(self, source: Source, route: str, params=None):
        query = {key: value for key, value in (params or {}).items() if value is not None}
        query["local_only"] = "true"
        try:
            response = self.open().get(source.url + route, params=query, headers={"Authorization": f"Bearer {source.token}"} if source.token else {})
            if response.status_code in (404, 409, 422):
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                detail = payload.get("detail", "Source rejected the query") if isinstance(payload, dict) else "Source rejected the query"
                if not isinstance(detail, (str, list)):
                    detail = "Source rejected the query"
                raise SourceRequestError(response.status_code, detail)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise SourceUnavailable(f"Source {source.label} is unavailable") from error

    def _list_source(self, source):
        try:
            if source.reader is not None:
                runs = source.reader.list_runs()
            else:
                runs = []
                for item in self._json(source, "/api/runs"):
                    value = RunSummaryResponse.model_validate(item)
                    split_run_id(value.run_id)
                    runs.append(RunSummary(value.run_id, value.name, value.started_at, value.config,
                                           time_origin=value.time_origin, time_origin_kind=value.time_origin_kind, source_label=value.source_label,
                                           has_notes=value.has_notes, note_excerpt=value.note_excerpt, tags=value.tags, tags_writable=False,
                                           has_scalars=value.has_scalars, last_recorded_at=value.last_recorded_at))
            translated = [replace(run, run_id=qualify(source.key, run.run_id), source=source.key,
                                  source_label=source.label + (f" / {run.source_label}" if run.source_label else ""), source_online=True) for run in runs]
            with self._lock:
                self._runs[source.key] = translated
                self._online[source.key] = True
            return translated
        except Exception:
            with self._lock:
                self._online[source.key] = False
                return [replace(run, source_online=False) for run in self._runs.get(source.key, [])]

    def list_runs(self):
        runs = self.primary.list_runs() if self.primary else []
        if self.sources:
            with ThreadPoolExecutor(max_workers=min(8, len(self.sources))) as pool:
                for group in pool.map(self._list_source, self.sources.values()):
                    runs.extend(group)
        return runs

    def status(self):
        with self._lock:
            return [{"source_id": key, "label": source.label, "online": self._online[key], "kind": "local" if source.reader else "peer"} for key, source in self.sources.items()]

    def _resolve(self, run_id):
        key, original = split_run_id(run_id)
        if key == "local":
            if self.primary is None:
                raise RunNotFound("Run not found")
            return self.primary, None, validate_run_id(original)
        source = self.sources.get(key)
        if source is None:
            raise SourceUnavailable("Source is not configured here")
        if source.reader:
            return source.reader, None, validate_run_id(original)
        return None, source, original

    def list_metrics(self, run_id):
        reader, source, original = self._resolve(run_id)
        if source:
            return self._json(source, "/api/metrics", {"run_id": original})["metrics"]
        return reader.list_metrics(original)

    def get_scalar_series(self, run_id, metric, max_points=800, step_min=None, step_max=None, smoothing=0):
        reader, source, original = self._resolve(run_id)
        if source:
            data = self._json(source, "/api/scalars", {"run_id": original, "metric": metric, "max_points": max_points, "step_min": step_min, "step_max": step_max, "smoothing": smoothing})
            return [ScalarPoint(p["step"], float("nan") if p["value"] is None else p["value"], p.get("smoothed"), p.get("wall_time")) for p in data["points"]]
        return reader.get_scalar_series(original, metric, max_points, step_min, step_max, smoothing)

    def get_scalar_summary(self, run_id, metric, target=None, direction="gte"):
        reader, source, original = self._resolve(run_id)
        if not source:
            return reader.get_scalar_summary(original, metric, target, direction)
        data = self._json(source, "/api/scalar-summary", {"run_id": original, "metric": metric, "target": target, "direction": direction})
        def point(value):
            return ScalarPoint(value["step"], float("nan") if value["value"] is None else value["value"], wall_time=value.get("wall_time")) if value else None
        return ScalarSummary(point(data["latest"]), data["minimum"], data["maximum"], point(data["first_hit"]))

    def get_scalar_series_batch(self, run_id, metrics, max_points=800, step_min=None, step_max=None, smoothing=0):
        reader, source, original = self._resolve(run_id)
        if not source:
            return reader.get_scalar_series_batch(original, metrics, max_points, step_min, step_max, smoothing)
        data = self._json(source, "/api/scalars-batch", {"run_id": original, "metrics": metrics, "max_points": max_points, "step_min": step_min, "step_max": step_max, "smoothing": smoothing})
        return {item["metric"]: [ScalarPoint(point["step"], float("nan") if point["value"] is None else point["value"], point.get("smoothed"), point.get("wall_time")) for point in item["points"]] for item in data}

    def get_scalar_bounds(self, run_id, metric, step_min=None, step_max=None):
        reader, source, original = self._resolve(run_id)
        if source:
            return ScalarBounds(**self._json(source, "/api/scalar-bounds", {"run_id": original, "metric": metric, "step_min": step_min, "step_max": step_max}))
        return reader.get_scalar_bounds(original, metric, step_min, step_max)

    def image_names(self, run_id):
        reader, source, original = self._resolve(run_id)
        if source:
            return [MediaNameSummary(**item) for item in self._json(source, "/api/image-names", {"run_id": original})]
        return reader.image_names(original)

    def text_names(self, run_id):
        reader, source, original = self._resolve(run_id)
        if source:
            return [MediaNameSummary(**item) for item in self._json(source, "/api/text-names", {"run_id": original})]
        return reader.text_names(original)

    def text_timeline(self, run_id, name, max_steps=200):
        reader, source, original = self._resolve(run_id)
        if source:
            return MediaTimeline(**self._json(source, "/api/text-timeline", {"run_id": original, "name": name, "max_steps": max_steps}))
        return reader.text_timeline(original, name, max_steps)

    def get_text(self, run_id, name, step=None):
        reader, source, original = self._resolve(run_id)
        if source:
            data = self._json(source, "/api/text", {"run_id": original, "name": name, "step": step})
            return TextRecord(**data) if data is not None else None
        return reader.get_text(original, name, step)

    def image_timeline(self, run_id, name, max_steps=200):
        reader, source, original = self._resolve(run_id)
        if source:
            return MediaTimeline(**self._json(source, "/api/image-timeline", {"run_id": original, "name": name, "max_steps": max_steps}))
        return reader.image_timeline(original, name, max_steps)

    def image_page(self, run_id, name, step=None, limit=12, offset=0):
        reader, source, original = self._resolve(run_id)
        if source:
            data = self._json(source, "/api/images", {"run_id": original, "name": name, "step": step, "limit": limit, "offset": offset})
            data["images"] = [ImageRecord(**item) for item in data["images"]]
            return ImagePage(**data)
        return reader.image_page(original, name, step, limit, offset)

    def get_run_note(self, run_id):
        reader, source, original = self._resolve(run_id)
        if source:
            return replace(RunNote(**self._json(source, "/api/run-notes", {"run_id": original})), writable=False)
        return reader.get_run_note(original)

    def save_run_note(self, run_id, body, revision):
        reader, source, original = self._resolve(run_id)
        if source:
            raise PermissionError("Peer notes are read-only. Edit the note on its source instance.")
        return reader.save_run_note(original, body, revision)

    def get_run_details(self, run_id):
        reader, source, original = self._resolve(run_id)
        if source:
            return RunDetails(**self._json(source, "/api/run-details", {"run_id": original}))
        return reader.get_run_details(original)

    def get_asset(self, run_id, asset_id, thumbnail=False):
        reader, source, original = self._resolve(run_id)
        if source:
            query = urlencode({"run_id": original, "asset_id": asset_id, "local_only": "true", "thumbnail": str(thumbnail).lower()})
            return FileAsset("application/octet-stream", "code.zip" if asset_id == "snapshot" else asset_id, url=source.url + "/api/asset?" + query, token=source.token)
        return reader.get_asset(original, asset_id, thumbnail)

    def update_run_tags(self, run_id, add, remove):
        reader, source, original = self._resolve(run_id)
        if source:
            raise PermissionError("Peer tags are read-only. Edit tags on the source instance.")
        return reader.update_run_tags(original, add, remove)

    def audio_names(self, run_id):
        reader, source, original = self._resolve(run_id)
        if source:
            return [MediaNameSummary(**item) for item in self._json(source, "/api/audio-names", {"run_id": original})]
        return reader.audio_names(original)

    def audio_timeline(self, run_id, name, max_steps=200):
        reader, source, original = self._resolve(run_id)
        if source:
            return MediaTimeline(**self._json(source, "/api/audio-timeline", {"run_id": original, "name": name, "max_steps": max_steps}))
        return reader.audio_timeline(original, name, max_steps)

    def audio_page(self, run_id, name, step=None, limit=12, offset=0):
        reader, source, original = self._resolve(run_id)
        if source:
            data = self._json(source, "/api/audio", {"run_id": original, "name": name, "step": step, "limit": limit, "offset": offset})
            data["audio"] = [AudioRecord(**item) for item in data["audio"]]
            return AudioPage(**data)
        return reader.audio_page(original, name, step, limit, offset)

    def scalar_count(self, run_id, metric, step_min=None, step_max=None):
        reader, source, original = self._resolve(run_id)
        if source:
            return self._json(source, "/api/scalar-count", {"run_id": original, "metric": metric, "step_min": step_min, "step_max": step_max})["count"]
        return reader.scalar_count(original, metric, step_min, step_max)

    def scalar_at_step(self, run_id, metric, step):
        reader, source, original = self._resolve(run_id)
        if source:
            data = self._json(source, "/api/scalar-at-step", {"run_id": original, "metric": metric, "step": step})
            return ScalarPoint(data["step"], float("nan") if data["value"] is None else data["value"], wall_time=data.get("wall_time")) if data else None
        return reader.scalar_at_step(original, metric, step)
