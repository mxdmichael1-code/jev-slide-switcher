"""Expose registered local sessions at the dashboard's own origin."""
import asyncio
import requests

from .bridge import SESSIONS


class IsolationHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-embedder-policy", b"require-corp"),
                ])
                message = {**message, "headers": headers}
            await send(message)
        await self.app(scope, receive, with_headers)


class SessionRoutes:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/session/"):
            return await self.app(scope, receive, send)
        parts = path.split("/", 3)
        bridge = SESSIONS.get(parts[2]) if len(parts) == 4 else None
        if bridge is None:
            return await self.reply(send, 404, b"Presentation session ended.")
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        origin = headers.get("origin")
        expected_origin = scope["scheme"] + "://" + headers.get("host", "")
        if origin and origin != expected_origin:
            return await self.reply(send, 403, b"Invalid origin.")
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if len(body) > 20000:
                return await self.reply(send, 413, b"Request too large.")
            if not event.get("more_body"):
                break
        route = "/" + parts[3]
        if scope.get("query_string"):
            route += "?" + scope["query_string"].decode()
        forwarded = {k: headers[k] for k in ("x-session-token", "content-type") if k in headers}
        if origin:
            forwarded["origin"] = bridge.url

        def fetch():
            # Only a live, registered loopback bridge can be selected; no user URL.
            with requests.Session() as client:
                client.trust_env = False
                return client.request(scope["method"], bridge.url + route, headers=forwarded,
                                      data=bytes(body), timeout=30, allow_redirects=False)
        try:
            response = await asyncio.to_thread(fetch)
        except requests.RequestException:
            return await self.reply(send, 503, b"Presentation disconnected.")
        content = response.content
        content_type = response.headers.get("content-type", "application/octet-stream")
        if content_type.startswith("text/html"):
            prefix = "/session/" + bridge.session_id
            text = content.decode()
            for root in ("/api/", "/assets/", "/wasm/", "/test-assets/"):
                text = text.replace("'" + root, "'" + prefix + root).replace('"' + root, '"' + prefix + root)
            content = text.encode()
        await self.reply(send, response.status_code, content, content_type)

    @staticmethod
    async def reply(send, status, body, content_type="text/plain; charset=utf-8"):
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
