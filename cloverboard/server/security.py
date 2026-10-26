"""Authentication and read-only enforcement for the entire HTTP service."""

import json
import secrets
import socket
from urllib.parse import quote, urlencode, urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ACCESS_COOKIE = "cloverboard_access"
READ_METHODS = frozenset({"GET", "HEAD"})
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})


def _local_interface_addresses() -> set[str]:
    """Best-effort literal IPs of this machine's interfaces, IPv4 and IPv6.

    Enumeration failures degrade to an empty set: clients then fall back to
    reaching the server through --host <address> as before.
    """
    addresses: set[str] = set()
    hostname = socket.gethostname()
    for probe in (
        lambda: [info[4][0] for info in socket.getaddrinfo(hostname, None)],
        lambda: socket.gethostbyname_ex(hostname)[2],
    ):
        try:
            addresses.update(address.split("%", 1)[0].lower() for address in probe())
        except (socket.gaierror, OSError):
            continue
    return addresses


def host_allowlist(host: str) -> set[str]:
    """Loopback names plus the bound address; mirrors the MCP transport allowlist.

    A wildcard bind also admits this machine's interface addresses so LAN
    clients can reach the server by its IP. DNS rebinding stays rejected:
    a rebound request carries the attacker's domain as its Host, which never
    equals a literal interface address.
    """
    bound = host.strip("[]").lower()
    allowed = {name for name in ("127.0.0.1", "localhost", "::1", bound) if name}
    if bound in WILDCARD_HOSTS:
        try:
            allowed |= _local_interface_addresses()
        except OSError:
            pass
    return allowed


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, token: str | None, read_only: bool,
                 allowed_hosts: set[str] | None = None) -> None:
        self.app = app
        self.token = token
        self.read_only = read_only
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket" and (self.token or self.read_only):
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if self.allowed_hosts is not None and self._hostname(request) not in self.allowed_hosts:
            # Tokenless local mode has no other gate; foreign Host headers indicate
            # DNS rebinding or an unintended exposure, not a legitimate client.
            await JSONResponse({"detail": "Invalid Host header"}, status_code=421)(scope, receive, send)
            return
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

    @staticmethod
    def _hostname(request: Request) -> str:
        host = request.headers.get("host", "")
        return (urlsplit(f"https://{host}").hostname or "").lower() if host else ""

    def _cookie_name(self, request: Request) -> str:
        # Cookies ignore ports, while separate cloverboard servers have distinct tokens.
        server = request.scope.get("server")
        port = server[1] if server is not None else 0
        return f"{ACCESS_COOKIE}_{port}"


class McpBodyGuard:
    """Answer 400 instead of the SDK's 500 for undecodable /mcp request bodies."""

    MAX_BODY = 4 * 1024 * 1024

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/mcp":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.MAX_BODY:
                await JSONResponse({"detail": "Request body is too large"}, status_code=413)(scope, receive, send)
                return
            if not message.get("more_body"):
                break
        try:
            # Strict UTF-8 first: the SDK's json.loads guesses the encoding and turns
            # garbage bytes into an unhandled UnicodeDecodeError (HTTP 500).
            json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )(scope, receive, send)
            return
        fed = False

        async def replay() -> Message:
            nonlocal fed
            if not fed:
                fed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
