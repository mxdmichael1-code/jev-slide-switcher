from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from slide_switcher.completion import SlideMemory, MAX_COVERAGE_CHARACTERS, validate_completion_goals
from slide_switcher.config import Settings
from slide_switcher.jev import JevClient, COVERAGE_QUESTION
from slide_switcher.vision import VisionPreprocessor, validate_bundle


def goals():
    return [{"slide_number": i, "main_message": f"Explain idea {i}",
             "essential_points": [f"Explain the cause of idea {i}", f"Explain its effect {i}"],
             "optional_details": []} for i in (1, 2, 3)]


@pytest.mark.parametrize("change", ["missing_slide", "wrong_order", "no_essentials", "too_many_essentials", "duplicate", "empty_message"])
def test_completion_goals_are_validated(bundle, deck, change):
    b = deepcopy(bundle); b["completion_goals"] = goals()
    if change == "missing_slide": b["completion_goals"].pop()
    if change == "wrong_order": b["completion_goals"][0]["slide_number"] = 2
    if change == "no_essentials": b["completion_goals"][0]["essential_points"] = []
    if change == "too_many_essentials": b["completion_goals"][0]["essential_points"] = [str(i) for i in range(4)]
    if change == "duplicate": b["completion_goals"][0]["essential_points"] = ["Cause", " cause "]
    if change == "empty_message": b["completion_goals"][0]["main_message"] = " "
    with pytest.raises(ValueError): validate_bundle(b, deck)


def test_existing_profiles_are_upgraded_once_without_repeating_image_analysis(bundle, deck, monkeypatch):
    v = VisionPreprocessor(Settings())
    calls = []
    def ask(content):
        calls.append(content)
        return {"completion_goals": goals()}
    monkeypatch.setattr(v, "ask", ask)
    upgraded = v.ensure_completion_goals(deck, bundle)
    assert upgraded["transitions"] == bundle["transitions"]
    assert validate_completion_goals(upgraded["completion_goals"], 3)
    assert (deck.directory / "transition_profiles.json").exists()
    assert v.ensure_completion_goals(deck, upgraded) is upgraded
    assert len(calls) == 1


def test_incomplete_generation_does_not_overwrite_saved_profiles(bundle, deck, monkeypatch):
    target = deck.directory / "transition_profiles.json"
    target.write_text('original')
    v = VisionPreprocessor(Settings())
    monkeypatch.setattr(v, "ask", lambda content: {"completion_goals": goals()[:1]})
    with pytest.raises(ValueError): v.ensure_completion_goals(deck, bundle)
    assert target.read_text() == 'original'


def test_slide_memory_does_not_silently_drop_earlier_explanation():
    m = SlideMemory()
    m.add("Earlier causal explanation")
    m.add("x" * MAX_COVERAGE_CHARACTERS)
    assert not m.complete
    assert m.transcript == "Earlier causal explanation"
    m.reset()
    assert m.complete and not m.transcript


def answer(label, labels):
    return {"type": "choice", "choice": label, "confidence": .3,
            "probabilities": {key: 1. if key == label else 0. for key in labels}}


def state():
    return {"completion_goal": goals()[0], "coverage_context_complete": True,
            "slide_transcript": "A complete explanation in different words.",
            "new_utterances": ["The effect follows from that cause."]}


def test_two_independent_questions_share_one_request(monkeypatch):
    requests = []
    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return SimpleNamespace(status_code=200, json=lambda: {"model": "fixture-model", "answers": {
            "slide_action": answer("STAY", ("STAY", "NEXT", "UNCERTAIN")),
            "slide_coverage": answer("COMPLETE", COVERAGE_QUESTION["criteria"])}})
    monkeypatch.setattr("slide_switcher.jev.requests.post", post)
    d = JevClient(Settings(jev_key="unit-fixture")).classify(state())
    assert len(requests) == 1 and set(requests[0]["questions"]) == {"slide_action", "slide_coverage"}
    assert d.label == "STAY" and d.coverage_label == "COMPLETE"
    assert d.effective_label == "NEXT" and d.advance_source == "coverage"


@pytest.mark.parametrize("transition,coverage,source", [
    ("NEXT", "PARTIAL", "transition"), ("STAY", "COMPLETE", "coverage"),
    ("UNCERTAIN", "COMPLETE", "coverage"), ("NEXT", "WAIT", None), ("STAY", "PARTIAL", None)])
def test_question_results_are_combined_without_confidence_thresholds(transition, coverage, source):
    from slide_switcher.jev import Decision
    assert Decision(transition, confidence=.01, coverage_label=coverage).advance_source == source


def test_malformed_coverage_cannot_trigger_an_advance(monkeypatch):
    response = {"model": "fixture-model", "answers": {
        "slide_action": answer("STAY", ("STAY", "NEXT", "UNCERTAIN")),
        "slide_coverage": answer("COMPLETE", COVERAGE_QUESTION["criteria"])}}
    response["answers"]["slide_coverage"]["probabilities"]["PARTIAL"] = float("nan")
    monkeypatch.setattr("slide_switcher.jev.requests.post", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: response))
    d = JevClient(Settings(jev_key="unit-fixture")).classify(state())
    assert d.coverage_error == "jev_invalid_coverage_response" and not d.advance_source
