from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import unquote, urlsplit

WEB = Path(__file__).resolve().parents[1] / "web"
SESSIONS = {}


def integrated_moonshine(root: Path):
    """Wrap the user's working page in memory; don't modify or copy its runtime/models."""
    source = (root / "stt/index.html").read_text()
    marker = ".onText((text) => render(text))"
    line_marker = ".onLine((line) => {"
    if marker not in source or line_marker not in source:
        raise RuntimeError("Moonshine page callbacks differ from the inspected v0.1.5 integration. Update the adapter before using it.")
    source = source.replace(marker, ".onText((text) => { render(text); window.slideBridge.send(text, false); })", 1)
    source = source.replace(line_marker, line_marker + "\n window.slideBridge.send(line.text, true, line.id, line.lastTranscriptionLatencyMs);", 1)
    source = source.replace("window.__moonshineFileTranscript = text;", "window.__moonshineFileTranscript = text; window.slideBridge.send(text, true, 'file-' + crypto.randomUUID());", 1)
    source = re.sub(r'<header class="ms-hero">.*?</header>',
        '<header class="ms-hero"><h1>Listen with Moonshine</h1><p>Audio is processed on this device. Transcript text is sent to Jev for slide decisions.</p><p id="bridgeStatus">Connecting to presentation…</p></header>', source, count=1, flags=re.S)
    source = source.replace("<head>", "<head><style>#proof,section:has(#code){display:none!important}</style>", 1)
    injected = r'''<script>
const token = location.hash.slice(1);
const instance = crypto.randomUUID();
let partialAt = 0;
window.slideBridge = {send(text, final, id, latency_ms) {
  if (!text || (!final && Date.now()-partialAt < 500)) return;
  if (!final) partialAt = Date.now();
  fetch('/api/transcript', {method:'POST', headers:{'Content-Type':'application/json','X-Session-Token':token},
    body:JSON.stringify({text,final,event_id:instance+':'+id,latency_ms}), signal:AbortSignal.timeout(5000)})
    .then(r=>{if(!r.ok) throw Error('Disconnected'); document.getElementById('bridgeStatus').textContent='Connected. Finalized phrases feed the slide decision engine.';})
    .catch(()=>{document.getElementById('bridgeStatus').textContent='Presentation disconnected. Reopen the microphone from Streamlit.';});
}};
fetch('/api/state', {headers:{'X-Session-Token':token}}).then(r=>{
 document.getElementById('bridgeStatus').textContent=r.ok?'Connected. Start listening to send finalized phrases to Jev.':'Disconnected. Reopen this window from Streamlit.';
}).catch(()=>{document.getElementById('bridgeStatus').textContent='Disconnected. Reopen this window from Streamlit.';});
</script>'''
    return source.replace('<script type="module">', injected + '<script type="module">', 1).encode()


class Bridge:
    def __init__(self, engine, moonshine_root: Path, port=0):
        self.engine, self.moonshine_root = engine, moonshine_root.resolve()
        self.token = secrets.token_urlsafe(32)
        self.session_id = secrets.token_urlsafe(18)
        self.server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, bridge=self))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="slide-browser-bridge")
        self.thread.start()
        SESSIONS[self.session_id] = self

    def stop(self):
        SESSIONS.pop(self.session_id, None)
        self.server.shutdown()
        self.server.server_close()

    def link(self, route):
        return self.url + route + "#" + self.token

    def embedded_link(self, route):
        return "/session/" + self.session_id + route + "#" + self.token


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, bridge, **kwargs):
        self.bridge = bridge
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass  # Tokens and request content are never written to server logs.

    def reply(self, status, body, content_type="application/json"):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        # The dashboard and session bridge use different loopback ports.
        self.send_header("Cross-Origin-Resource-Policy", "same-site")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        supplied = self.headers.get("X-Session-Token", "")
        origin = self.headers.get("Origin")
        return secrets.compare_digest(supplied, self.bridge.token) and (not origin or origin == self.bridge.url)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/health":
            return self.reply(200, {"ok": True})
        if path.startswith("/api/"):
            if not self.authorized():
                return self.reply(403, {"error": "unauthorized"})
            if path == "/api/state":
                snap = self.bridge.engine.snapshot()
                return self.reply(200, {k: snap[k] for k in ("slide", "count", "epoch", "auto", "reason")})
            match = re.fullmatch(r"/api/image/(\d+)", path)
            if match:
                number = int(match.group(1))
                if not 1 <= number <= len(self.bridge.engine.deck.slides):
                    return self.reply(404, {})
                deck = self.bridge.engine.deck
                return self.reply(200, (deck.directory / deck.slides[number - 1].image).read_bytes(), "image/png")
            return self.reply(404, {})
        if path in ("/present", "/present/"):
            return self.reply(200, (WEB / "presenter.html").read_bytes(), "text/html; charset=utf-8")
        if path in ("/mic/embed", "/mic/embed/"):
            return self.reply(200, (WEB / "microphone.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/mic-check/":
            diagnostic = self.bridge.moonshine_root / "mic-check/index.html"
            if diagnostic.exists():
                return self.reply(200, diagnostic.read_bytes(), "text/html; charset=utf-8")
        if path in ("/mic", "/mic/", "/stt/"):
            try:
                return self.reply(200, integrated_moonshine(self.bridge.moonshine_root), "text/html; charset=utf-8")
            except (OSError, RuntimeError):
                return self.reply(503, "Moonshine integration unavailable. Set MOONSHINE_ROOT to the existing project.", "text/plain")
        mounts = {"/wasm/": "language-bindings/wasm", "/test-assets/": "test-assets", "/assets/": "assets"}
        for prefix, relative in mounts.items():
            if path.startswith(prefix):
                root = (self.bridge.moonshine_root / relative).resolve()
                target = (root / path[len(prefix):]).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    return self.reply(404, {})
                mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                return self.reply(200, target.read_bytes(), mime)
        return self.reply(404, {})

    def do_POST(self):
        if not self.authorized():
            return self.reply(403, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 20000:
                return self.reply(413, {"error": "request_too_large"})
            data = json.loads(self.rfile.read(length))
            route = urlsplit(self.path).path
            engine = self.bridge.engine
            if route == "/api/transcript":
                text = data["text"]
                event_id = data["event_id"]
                if not isinstance(text, str) or not isinstance(event_id, str) or len(event_id) > 200 or type(data.get("final", True)) is not bool:
                    raise ValueError
                accepted = engine.add_transcript(text, event_id, data.get("final", True), data.get("latency_ms"))
                return self.reply(200, {"accepted": accepted})
            if route == "/api/goto":
                engine.goto(data["slide"])
                return self.reply(200, {"slide": engine.slide})
            if route == "/api/rendered":
                engine.rendered(data["slide"])
                return self.reply(200, {"ok": True})
            return self.reply(404, {})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self.reply(400, {"error": "invalid_request"})
        except Exception:
            return self.reply(503, {"error": "controller_unavailable"})
