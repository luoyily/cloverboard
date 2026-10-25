"""Run metadata, notes/tags, media indexes and bounded asset delivery."""

from dataclasses import replace
import re
import hashlib

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from cloverboard.server.routes import get_run_reader, get_run_id
from cloverboard.storage.protocols import StorageReader, RunNote, NoteConflict, ImagePage, MediaTimeline, MediaNameSummary, AudioPage, RunDetails, TextRecord
from cloverboard.server.models import NoteInput, TagUpdate, TagsResponse, SourceStatusResponse, ShareTokenResponse
from cloverboard.server.thumbnails import thumbnail_cache

router = APIRouter(prefix="/api")
_ASSET_CONTENT = {mime: {"schema": {"type": "string", "format": "binary"}}
                  for mime in ("image/png", "image/jpeg", "audio/wav", "application/zip")}


@router.get("/sources", tags=["Access"], response_model=list[SourceStatusResponse])
def sources(request: Request) -> list[dict]:
    """List configured additional sources; the primary local logdir is implicit, so [] is valid."""
    reader = request.app.state.reader
    return reader.status() if hasattr(reader, "status") else []


@router.get("/images", tags=["Media"], response_model=ImagePage)
def images(
    name: str = Query(min_length=1, max_length=500),
    step: int | None = Query(default=None, ge=-(2**53 - 1), le=2**53 - 1),
    limit: int = Query(default=12, ge=1, le=24),
    offset: int = Query(default=0, ge=0, le=10_000_000),
    run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader),
) -> ImagePage:
    """Page one image name at the greatest recorded Step <= step; omitted step follows latest per run."""
    return reader.image_page(run_id, name, step, limit, offset)


@router.get("/image-names", tags=["Media"], response_model=list[MediaNameSummary])
def image_names(run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> list[MediaNameSummary]:
    """List image names with record/Step counts and inclusive first/last Steps."""
    return reader.image_names(run_id)


@router.get("/image-timeline", tags=["Media"], response_model=MediaTimeline)
def image_timeline(name: str = Query(min_length=1, max_length=500), max_steps: int = Query(default=200, ge=2, le=500),
                   run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> MediaTimeline:
    """Return representative recorded Steps, including endpoints; total_steps is the unsampled count."""
    return reader.image_timeline(run_id, name, max_steps)


@router.get("/text-names", tags=["Media"], response_model=list[MediaNameSummary])
def text_names(run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> list[MediaNameSummary]:
    """List plain-text names, counts and Step bounds, without bodies."""
    return reader.text_names(run_id)


@router.get("/text-timeline", tags=["Media"], response_model=MediaTimeline)
def text_timeline(name: str = Query(min_length=1, max_length=500), max_steps: int = Query(default=200, ge=2, le=500),
                  run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> MediaTimeline:
    """Return representative recorded text Steps, including endpoints."""
    return reader.text_timeline(run_id, name, max_steps)


@router.get("/text", tags=["Media"], response_model=TextRecord | None)
def text(name: str = Query(min_length=1, max_length=500),
         step: int | None = Query(default=None, ge=-(2**53 - 1), le=2**53 - 1),
         run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> TextRecord | None:
    """Read the latest plain string at/before Step; unrecorded names/ranges return null."""
    return reader.get_text(run_id, name, step)


@router.get("/run-notes", tags=["Runs"], response_model=RunNote)
def read_note(request: Request, run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> RunNote:
    """Read the note and its optimistic-concurrency revision; updated_at uses Unix seconds."""
    note = reader.get_run_note(run_id)
    return replace(note, writable=note.writable and request.app.state.workspace.writable)


@router.patch("/run-tags", tags=["Runs"], response_model=TagsResponse)
def update_tags(payload: TagUpdate, run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> TagsResponse:
    """Apply tag additions/removals. Peer tags are read-only; removals are applied before additions."""
    try:
        return TagsResponse(tags=reader.update_run_tags(run_id, payload.add, payload.remove))
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/audio-names", tags=["Media"], response_model=list[MediaNameSummary])
def audio_names(run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> list[MediaNameSummary]:
    """List audio names with record/Step counts and inclusive first/last Steps."""
    return reader.audio_names(run_id)


@router.get("/audio-timeline", tags=["Media"], response_model=MediaTimeline)
def audio_timeline(name: str = Query(min_length=1, max_length=500), max_steps: int = Query(default=200, ge=2, le=500), run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> MediaTimeline:
    """Return representative recorded Steps, including endpoints; total_steps is the unsampled count."""
    return reader.audio_timeline(run_id, name, max_steps)


@router.get("/audio", tags=["Media"], response_model=AudioPage)
def audio(name: str = Query(min_length=1, max_length=500), step: int | None = Query(default=None, ge=-(2**53 - 1), le=2**53 - 1), limit: int = Query(default=12, ge=1, le=24), offset: int = Query(default=0, ge=0, le=10_000_000), run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> AudioPage:
    """Page one audio name at the greatest recorded Step <= step; omitted step follows latest per run."""
    return reader.audio_page(run_id, name, step, limit, offset)


@router.put("/run-notes", tags=["Runs"], response_model=RunNote)
def save_note(payload: NoteInput, run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> RunNote:
    """Replace a local note using its loaded revision; stale revisions return 409."""
    try:
        return reader.save_run_note(run_id, payload.body, payload.revision)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except NoteConflict as error:
        raise HTTPException(409, str(error)) from error


@router.get("/share-token", tags=["Access"], response_model=ShareTokenResponse)
def share_token(request: Request, response: Response) -> ShareTokenResponse:
    """Return this server's access token, or null when sharing is disabled; requires normal server access."""
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    return ShareTokenResponse(token=request.app.state.access_token)


@router.get("/run-details", tags=["Runs"], response_model=RunDetails)
def details(run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> RunDetails:
    """Read config, environment and the code-snapshot manifest; an existing run without a snapshot has null."""
    return reader.get_run_details(run_id)


@router.get("/asset", tags=["Media"], response_class=Response, responses={
    200: {"content": _ASSET_CONTENT},
    206: {"description": "Partial asset content for a byte Range request", "content": _ASSET_CONTENT},
    304: {"description": "Cached asset is unchanged"},
    416: {"description": "Requested byte Range is not satisfiable"},
})
async def asset(request: Request, asset_id: str, thumbnail: bool = False, run_id: str = Depends(get_run_id), reader: StorageReader = Depends(get_run_reader)) -> Response:
    """Read an asset by opaque ID, or snapshot for code.zip. Image thumbnails are bounded to 512px."""
    if asset_id != "snapshot" and not re.fullmatch(r"[0-9a-f]{32}", asset_id):
        raise HTTPException(404, "Asset not found")
    item = await run_in_threadpool(reader.get_asset, run_id, asset_id, thumbnail)
    if item is None:
        raise HTTPException(404, "Asset not found")
    if item.path:
        if thumbnail and item.media_type == "audio/wav":
            raise HTTPException(422, "Audio has no image thumbnail")
        if thumbnail and asset_id != "snapshot":
            try:
                preview = await run_in_threadpool(thumbnail_cache.get, item.path)
                content = preview.data
                etag = '"' + hashlib.sha256(content).hexdigest() + '"'
                headers = {"X-Content-Type-Options": "nosniff", "ETag": etag, "Cache-Control": "private, max-age=3600"}
                validators = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
                if etag in validators or "*" in validators:
                    return Response(status_code=304, headers=headers)
                return Response(content, media_type="image/png", headers=headers)
            except (OSError, ValueError) as error:
                raise HTTPException(422, "Unable to preview image") from error
        return FileResponse(item.path, media_type=item.media_type, filename=item.filename, content_disposition_type="attachment" if asset_id == "snapshot" else "inline", headers={"X-Content-Type-Options": "nosniff"})
    client = httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False)
    try:
        forwarded = {key: request.headers[key] for key in ("range", "if-range", "if-none-match", "if-modified-since") if key in request.headers}
        if item.token:
            forwarded["Authorization"] = f"Bearer {item.token}"
        response = await client.send(client.build_request("GET", item.url, headers=forwarded), stream=True)
        if response.status_code == 304:
            headers = {key: response.headers[key] for key in ("etag", "last-modified", "cache-control") if key in response.headers}
            await response.aclose()
            await client.aclose()
            return Response(status_code=304, headers=headers)
        if response.status_code == 416:
            headers = {"Content-Range": response.headers.get("content-range", "bytes */0")}
            await response.aclose()
            await client.aclose()
            return Response(status_code=416, headers=headers)
        if response.status_code not in (200, 206):
            await response.aclose()
            raise HTTPException(404 if response.status_code == 404 else 503, "Source asset unavailable")
        media_type = response.headers.get("content-type", "").split(";")[0]
        if media_type not in {"image/png", "image/jpeg", "application/zip", "audio/wav", "audio/x-wav"}:
            await response.aclose()
            raise HTTPException(502, "Unsupported source asset")
    except Exception as error:
        await client.aclose()
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(503, "Source asset unavailable") from error
    async def close():
        await response.aclose()
        await client.aclose()
    headers = {"X-Content-Type-Options": "nosniff"}
    for key in ("content-range", "accept-ranges", "content-length", "etag", "last-modified", "cache-control"):
        if key in response.headers:
            headers[key] = response.headers[key]
    if asset_id == "snapshot":
        headers["Content-Disposition"] = 'attachment; filename="code.zip"'
    return StreamingResponse(response.aiter_bytes(), status_code=response.status_code, media_type=media_type, headers=headers, background=BackgroundTask(close))
