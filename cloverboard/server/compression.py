"""Response compression for JSON API payloads.

Audio and image assets travel byte-exact (HTTP Range seeking) or are already
compressed, so only complete status-200 application/json bodies are rewritten.
Streaming responses (asset proxy, MCP SSE) pass through untouched.
"""

from gzip import compress

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class JsonGzipMiddleware:
    def __init__(self, app: ASGIApp, *, minimum_size: int = 1024, compresslevel: int = 6) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or "gzip" not in Headers(scope=scope).get("Accept-Encoding", ""):
            await self.app(scope, receive, send)
            return
        start: Message | None = None
        passthrough = False

        async def send_wrapper(message: Message) -> None:
            nonlocal start, passthrough
            if message["type"] == "http.response.start" and not passthrough:
                start = message
                return
            if passthrough or message["type"] != "http.response.body" or start is None:
                await send(message)
                return
            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            headers = MutableHeaders(scope=start)
            if more_body or start["status"] != 200 or "content-encoding" in headers \
                    or not headers.get("content-type", "").startswith("application/json") \
                    or len(body) < self.minimum_size:
                passthrough = True
                await send(start)
                await send({"type": "http.response.body", "body": body, "more_body": more_body})
                return
            encoded = compress(body, compresslevel=self.compresslevel, mtime=0)
            headers["content-encoding"] = "gzip"
            headers["content-length"] = str(len(encoded))
            headers.append("vary", "Accept-Encoding")
            passthrough = True
            await send(start)
            await send({"type": "http.response.body", "body": encoded, "more_body": False})

        await self.app(scope, receive, send_wrapper)
