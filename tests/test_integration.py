import json
from pathlib import Path
import shutil

import pytest
import requests

from scripts.create_demo import create_demo
from slide_switcher.bridge import Bridge, integrated_moonshine
from slide_switcher.config import Settings
from slide_switcher.controllers import BrowserController
from slide_switcher.deck import prepare_deck, validate_pptx
from slide_switcher.engine import PresentationEngine
from slide_switcher.jev import JevClient


def test_real_powerpoint_rendering_keeps_slide_order_and_notes(tmp_path):
    s = Settings.load()
    if not (s.soffice or shutil.which("soffice") or shutil.which("libreoffice")):
        pytest.skip("LibreOffice required for renderer integration test")
    source = create_demo(tmp_path / "demo.pptx")
    deck = prepare_deck(source.read_bytes(), source.name, tmp_path / "cache", s.soffice)
    assert len(deck.slides) == 3
    assert "Solar output" in deck.slides[0].title
    assert "charging" in deck.slides[1].notes
    from PIL import Image
    for slide in deck.slides:
        with Image.open(deck.directory / slide.image) as image:
            assert image.width == 1440 and image.height > 500
            assert len(image.getcolors(2000000)) > 100
    # Cached rendering works without invoking LibreOffice again.
    cached = prepare_deck(source.read_bytes(), source.name, tmp_path / "cache", "not-a-real-program")
    assert cached.sha256 == deck.sha256


def test_damaged_upload_is_rejected():
    with pytest.raises(ValueError): validate_pptx(b"not a PowerPoint")


def test_existing_moonshine_page_is_wrapped_without_source_edits():
    root = Settings.load().moonshine_root
    if not (root / "stt/index.html").exists(): pytest.skip("Existing Moonshine project not configured")
    original = (root / "stt/index.html").read_bytes()
    output = integrated_moonshine(root)
    assert b"window.slideBridge.send(line.text, true" in output
    assert b"window.slideBridge.send(text, true, 'file-'" in output
    assert b"Transcript text is sent to Jev" in output
    assert (root / "stt/index.html").read_bytes() == original


def test_authenticated_bridge_manual_navigation_and_transcript(deck, bundle):
    engine = PresentationEngine(deck, bundle, JevClient(Settings()), BrowserController(3))
    bridge = Bridge(engine, Settings.load().moonshine_root)
    try:
        assert requests.get(bridge.url + "/api/state").status_code == 403
        headers = {"X-Session-Token": bridge.token}
        assert requests.get(bridge.url + "/api/state", headers=headers).json()["slide"] == 1
        assert requests.post(bridge.url + "/api/goto", headers=headers, json={"slide": 2}).status_code == 200
        assert engine.slide == 2
        message = {"text": "new explanation begins", "event_id": "test", "final": True}
        assert requests.post(bridge.url + "/api/transcript", headers=headers, json=message).json()["accepted"]
        assert not requests.post(bridge.url + "/api/transcript", headers=headers, json=message).json()["accepted"]
        assert requests.post(bridge.url + "/api/goto", headers=headers, json={"slide": 99}).status_code == 400
        assert requests.post(bridge.url + "/api/goto", headers={**headers, "Origin": "https://example.com"}, json={"slide": 1}).status_code == 403
        assert requests.get(bridge.url + "/assets/%2e%2e/%2e%2e/.env").status_code == 404
        response = requests.get(bridge.url + "/present")
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert bridge.token not in response.text
    finally:
        bridge.stop()


def test_missing_api_key_does_not_fabricate_decision():
    result = JevClient(Settings()).classify({})
    assert result.label == "UNCERTAIN" and result.confidence is None
    assert result.error == "missing_jev_key"


def test_streamlit_initial_screen():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    assert not app.exception
    assert any("Present without the clicks." == x.value for x in app.title)
    assert any("Try an example" == x.label for x in app.button)
    assert next(x for x in app.button if x.label == "Prepare presentation").disabled
    # Saved credentials are never inserted into browser widget values.
    assert next(x for x in app.text_input if x.label == "Doubao key").value == ""
    assert next(x for x in app.text_input if x.label == "Jev key").value == ""
    assert not any(x.label in {"Minimum confidence", "Minimum next-slide probability"} for x in app.slider)
    assert not any(x.label == "Confirming phrases" for x in app.number_input)


def test_streamlit_prepared_session_disables_duplicate_start():
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    if not (root / "examples/transition_profiles.json").exists():
        pytest.skip("Packaged example profiles required")
    s = Settings.load()
    if not (s.soffice or shutil.which("soffice") or shutil.which("libreoffice")):
        pytest.skip("LibreOffice required for preparation workflow")
    app = AppTest.from_file(str(root / "app.py")).run(timeout=30)
    next(x for x in app.button if x.label == "Try an example").click().run(timeout=30)
    assert not app.exception
    assert any(x.label == "Saved activity" for x in app.tabs)
    next(x for x in app.button if x.label == "Start presenting").click().run(timeout=30)
    try:
        assert not app.exception
        assert not any(x.label == "Start presenting" for x in app.button)
        assert "engine" in app.session_state
        assert not app.session_state.engine.snapshot()["auto"]
        assert any(x.label == "Turn on auto" for x in app.button)
    finally:
        if "engine" in app.session_state:
            app.session_state.engine.stop()
            app.session_state.bridge.stop()


def test_saved_rehearsal_is_available_after_a_fresh_frontend_session(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from slide_switcher import history
    saved = [{"time": "2026-11-04T14:37:00Z", "kind": "inference", "slide": 1,
              "new_utterances": ["A saved explanation from an earlier run"], "coverage": "PARTIAL"}]
    monkeypatch.setattr(history, "read_activity", lambda directory: saved)
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root / "app.py")).run(timeout=30)
    next(x for x in app.button if x.label == "Try an example").click().run(timeout=30)
    assert not app.exception
    assert any(x.label == "Saved activity" for x in app.tabs)
    assert any("Speech" in x.value and "A saved explanation from an earlier run" in list(x.value["Speech"]) for x in app.dataframe)
