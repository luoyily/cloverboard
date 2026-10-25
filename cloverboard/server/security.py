"""Authentication and read-only enforcement for the entire HTTP service."""

import secrets
from urllib.parse import quote, urlencode

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ACCESS_COOKIE = "cloverboard_access"
READ_METHODS = frozenset({"GET", "HEAD"})


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, token: str | None, read_only: bool) -> None:
        self.app = app
        self.token = token
        self.read_only = read_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket" and (self.token or self.read_only):
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        query_token = request.query_params.get("token")
        clean_query = None
        if query_token is not None:
            clean_query = urlencode([(key, value) for key, value in request.query_params.multi_items() if key != "token"])
            # Uvicorn logs the scope's query string; credentials must not reach it.
            scope["query_string"] = clean_query.encode("ascii")

        async def send_response(message: Message) -> None:
            if message["type"] == "http.response.start" and self.token:
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        if self.token and not self._authenticated(request, query_token):
            response = JSONResponse(
                {"detail": "Access token is missing or invalid"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        elif request.url.path == "/mcp" and request.headers.get("origin") and request.headers["origin"].rstrip("/") != str(request.base_url).rstrip("/"):
            response = JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
        elif self.read_only and request.method not in READ_METHODS and not (request.url.path == "/mcp" and request.method == "POST"):
            response = JSONResponse({"detail": "Server is read-only"}, status_code=403)
        elif self.token and clean_query is not None:
            # A relative redirect keeps the externally supplied share host intact.
            path = "/" + scope["path"].replace("\\", "/").lstrip("/")
            destination = quote(path, safe="/") + (f"?{clean_query}" if clean_query else "")
            response = RedirectResponse(destination, status_code=303)
            response.set_cookie(
                self._cookie_name(request), self.token, httponly=True, samesite="lax",
                secure=request.url.scheme == "https",
            )
        else:
            await self.app(scope, receive, send_response)
            return
        await response(scope, receive, send_response)

    def _authenticated(self, request: Request, query_token: str | None) -> bool:
        if query_token is not None:
            supplied = query_token
        elif "authorization" in request.headers:
            scheme, _, supplied = request.headers["authorization"].partition(" ")
            if scheme.casefold() != "bearer":
                return False
        else:
            supplied = request.cookies.get(self._cookie_name(request), "")
        return self.token is not None and secrets.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8"))

    def _cookie_name(self, request: Request) -> str:
        # Cookies ignore ports, while separate cloverboard servers have distinct tokens.
        server = request.scope.get("server")
        port = server[1] if server is not None else 0
        return f"{ACCESS_COOKIE}_{port}"
