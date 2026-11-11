import asyncio
import json

from slide_switcher.bridge import Bridge
from slide_switcher.config import Settings
from slide_switcher.controllers import BrowserController
from slide_switcher.engine import PresentationEngine
from slide_switcher.jev import JevClient
from slide_switcher.web_server import IsolationHeaders, SessionRoutes


def request(path, method="GET", headers=None, body=b""):
    async def perform():
        async def downstream(scope, receive, send):
            await SessionRoutes.reply(send, 404, b"Not found")
        events = []
        async def send(event): events.append(event)
        async def receive(): return {"type": "http.request", "body": body}
        values = {"host": "127.0.0.1:8502", **(headers or {})}
        scope = {"type": "http", "method": method, "path": path, "scheme": "http",
                 "headers": [(k.encode(), v.encode()) for k, v in values.items()]}
        await IsolationHeaders(SessionRoutes(downstream))(scope, receive, send)
        return events[0]["status"], dict(events[0]["headers"]), b"".join(e.get("body", b"") for e in events[1:])
    return asyncio.run(perform())


def test_inline_moonshine_and_presenter_share_dashboard_origin(deck, bundle, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/check.js").write_text("export const ready = true;")
    engine = PresentationEngine(deck, bundle, JevClient(Settings()), BrowserController(3))
    bridge = Bridge(engine, tmp_path)
    prefix = "/session/" + bridge.session_id
    try:
        for page in ("/mic/embed", "/present"):
            status, headers, html = request(prefix + page)
            assert status == 200
            assert headers[b"cross-origin-opener-policy"] == b"same-origin"
            assert headers[b"cross-origin-embedder-policy"] == b"require-corp"
            assert (prefix + "/api/").encode() in html
            assert bridge.token.encode() not in html
        assert (prefix + "/wasm/dist/index.js").encode() in request(prefix + "/mic/embed")[2]
        status, headers, asset = request(prefix + "/assets/check.js")
        assert status == 200 and b"export const ready" in asset
        assert b"javascript" in headers[b"content-type"]
        assert request(prefix + "/api/state")[0] == 403
        auth = {"x-session-token": bridge.token}
        assert json.loads(request(prefix + "/api/state", headers=auth)[2])["slide"] == 1
        accepted = {**auth, "origin": "http://127.0.0.1:8502", "content-type": "application/json"}
        assert request(prefix + "/api/goto", "POST", accepted, b'{"slide":2}')[0] == 200
        assert engine.slide == 2
        blocked = {**accepted, "origin": "https://example.com"}
        assert request(prefix + "/api/goto", "POST", blocked, b'{"slide":1}')[0] == 403
        assert engine.slide == 2
        assert request(prefix + "/api/goto", "POST", accepted, b"x" * 20001)[0] == 413
    finally:
        bridge.stop()
    assert request(prefix + "/mic/embed")[0] == 404


def test_unknown_inline_session_cannot_proxy_arbitrary_ports():
    assert request("/session/127.0.0.1:8502/api/state")[0] == 404
