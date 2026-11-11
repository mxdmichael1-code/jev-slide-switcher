import json
from unittest.mock import Mock

from PIL import Image
import pytest

from slide_switcher.config import Settings
from slide_switcher.vision import VisionPreprocessor


def test_json_mode_is_required_and_non_object_answers_are_rejected(monkeypatch):
    def post(url, **kwargs):
        assert kwargs["json"]["response_format"] == {"type": "json_object"}
        assert kwargs["allow_redirects"] is False
        return Mock(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": "[]"}}],
        })

    monkeypatch.setattr("slide_switcher.vision.requests.post", post)
    with pytest.raises(ValueError, match="complete valid JSON"):
        VisionPreprocessor(Settings(doubao_key="test-key")).ask([
            {"type": "text", "text": "Return a JSON object."},
        ])


def test_preparation_resumes_after_transition_failure_without_resending_images(deck, bundle):
    for slide in deck.slides:
        Image.new("RGB", (100, 80), "white").save(deck.directory / slide.image)
    settings = Settings(doubao_key="test-key", vision_batch_size=3)
    first = VisionPreprocessor(settings)
    first.ask = Mock(side_effect=[
        {"slides": bundle["slide_understanding"]}, RuntimeError("temporary API failure"),
    ])
    with pytest.raises(RuntimeError, match="temporary API failure"):
        first.analyze(deck)
    assert len(list(deck.directory.glob("slide_analysis_*.json"))) == 1
    assert not (deck.directory / "transition_profiles.json").exists()

    plan = {"presentation_flow": bundle["presentation_flow"], "transitions": bundle["transitions"],
            "completion_goals": [{"slide_number": s.number, "main_message": s.text,
                "essential_points": [s.notes], "optional_details": []} for s in deck.slides]}
    retry = VisionPreprocessor(settings)
    retry.ask = Mock(return_value=plan)
    prepared = retry.analyze(deck)
    assert retry.ask.call_count == 1
    assert all(part["type"] == "text" for part in retry.ask.call_args.args[0])
    assert prepared["slide_understanding"] == bundle["slide_understanding"]
    assert len(prepared["completion_goals"]) == len(deck.slides)
    assert json.loads((deck.directory / "transition_profiles.json").read_text())["deck_sha256"] == deck.sha256


def test_invalid_image_analysis_is_not_saved_for_retry(deck):
    for slide in deck.slides:
        Image.new("RGB", (100, 80), "white").save(deck.directory / slide.image)
    preprocessor = VisionPreprocessor(Settings(doubao_key="test-key"))
    preprocessor.ask = Mock(return_value={"slides": []})
    with pytest.raises(ValueError, match="missing or reordered"):
        preprocessor.analyze(deck)
    assert not list(deck.directory.glob("slide_analysis_*.json"))
