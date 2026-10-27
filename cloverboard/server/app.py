"""FastAPI application factory."""

import secrets
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from cloverboard.server.routes import router
from cloverboard.server.compression import JsonGzipMiddleware
from cloverboard.server.security import McpBodyGuard, SecurityMiddleware, host_allowlist
from cloverboard.server.workspace import WorkspaceStore, router as workspace_router
from cloverboard.storage.paths import UnsafeLogPathError
from cloverboard.storage.protocols import RunNotFound, StorageReader
from cloverboard.server.models import ErrorResponse
from cloverboard.server.sources import Source, SourceReader, SourceUnavailable, SourceRequestError
from cloverboard.server.assets import router as assets_router
from cloverboard.server.mcp import create_mcp

_PACKAGE_STATIC_DIR = Path(__file__).resolve().parents[1] / "_static"
_DEFAULT_STATIC_DIR = _PACKAGE_STATIC_DIR if _PACKAGE_STATIC_DIR.is_dir() else Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(
    reader: StorageReader | None = None,
    static_dir: str | Path | None = None,
    logdir: str | Path | None = None,
    read_only: bool = False,
    share: bool = False,
    sources: list[Source] | None = None,
    host: str = "127.0.0.1",
) -> FastAPI:
    """Build the web app.

    reader: read-side storage backend; when None the catalog is empty and
        requests for individual runs return 404.
    static_dir: frontend build output; defaults to bundled package assets or
        web/dist in a source checkout, mounted at / when the directory exists.
    logdir: workspace state and views live in logdir/.workspace; None uses
        an isolated in-memory workspace for fixtures and tests.
    read_only: reject HTTP writes while allowing browser-local viewing state.
    share: require a fresh access token and force read-only mode.
    """
    combined_reader = SourceReader(reader, sources) if sources else reader
    mcp = create_mcp(combined_reader, host, authenticated=share)
    mcp_http = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        try:
            if isinstance(combined_reader, SourceReader):
                combined_reader.open()
            async with mcp.session_manager.run():
                yield
        finally:
            if isinstance(combined_reader, SourceReader):
                combined_reader.close()

    app = FastAPI(
        title="CloverBoard", lifespan=lifespan,
        description=(
            "Local experiment data and Web viewing state. Run IDs are opaque. "
            "Recording timestamps use Unix seconds; workspace saved_at uses client milliseconds. "
            "Step ranges are inclusive. Latest means the record with the greatest Step. "
            "Missing runs return 404; unrecorded metrics/media in existing runs return empty results. "
            "Data endpoints support local_only for one-hop source forwarding."
        ),
        responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 502, 503)},
        openapi_tags=[{"name": name} for name in ("Runs", "Scalars", "Media", "Workspace", "Access")],
    )
    app.state.mcp = mcp
    read_only = read_only or share
    app.state.access_token = secrets.token_urlsafe(16) if share else None
    # McpBodyGuard is registered first so SecurityMiddleware stays outermost:
    # authentication runs before any request body is read. Tokenless servers also
    # reject foreign Host headers (DNS-rebinding protection); share mode keeps
    # serving whatever public Host the tunnel forwards, with the token as the gate.
    # JsonGzipMiddleware is registered before both and stays innermost: it finalizes
    # response bodies before outer layers add their headers.
    app.add_middleware(JsonGzipMiddleware)
    app.add_middleware(McpBodyGuard)
    app.add_middleware(SecurityMiddleware, token=app.state.access_token, read_only=read_only,
                       allowed_hosts=None if share else host_allowlist(host))

    @app.exception_handler(UnsafeLogPathError)
    async def reject_unsafe_path(request: Request, error: UnsafeLogPathError):
        return JSONResponse({"detail": "Path is not allowed"}, status_code=403)

    @app.exception_handler(SourceUnavailable)
    async def source_unavailable(request: Request, error: SourceUnavailable):
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.exception_handler(RunNotFound)
    async def run_not_found(request: Request, error: RunNotFound):
        return JSONResponse({"detail": "Run not found"}, status_code=404)

    @app.exception_handler(SourceRequestError)
    async def source_request_error(request: Request, error: SourceRequestError):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_unavailable(request: Request, error: Exception):
        logging.getLogger(__name__).warning("Experiment storage is unavailable", exc_info=error)
        return JSONResponse({"detail": "Experiment storage is unavailable"}, status_code=503)

    app.state.reader = combined_reader
    local_sources = [source for source in sources or [] if source.reader is not None]
    app.state.local_reader = SourceReader(reader, local_sources) if local_sources else reader
    app.state.workspace = WorkspaceStore(logdir, read_only)
    app.include_router(router)
    app.include_router(workspace_router)
    app.include_router(assets_router)
    app.router.routes.extend(mcp_http.routes)

    target = Path(static_dir) if static_dir is not None else _DEFAULT_STATIC_DIR
    if target.is_dir():
        app.mount("/", StaticFiles(directory=target, html=True), name="web")
    return app
