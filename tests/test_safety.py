from copy import deepcopy
from dataclasses import replace
import json
import math
from types import SimpleNamespace

import pytest

from slide_switcher.config import GateSettings, Settings, endpoint
from slide_switcher.controllers import BrowserController
from slide_switcher.engine import PresentationEngine, TemporalGate
from slide_switcher.jev import Decision, JevClient, validate_response
from slide_switcher.vision import validate_bundle


def next_decision(confidence=.95, probability=.98):
    return Decision("NEXT", confidence, {"NEXT": probability, "STAY": (1-probability)/2, "UNCERTAIN": (1-probability)/2})


def raw_response():
    return {"model": "jev-test-fixture", "answers": {"slide_action": {"type": "choice", "choice": "NEXT", "confidence": .95,
            "probabilities": {"NEXT": .98, "STAY": .01, "UNCERTAIN": .01}}}}


@pytest.mark.parametrize("field,value", [("confidence", float("nan")), ("confidence", True), ("confidence", 1.1), ("choice", "JUMP"), ("type", "noul")])
def test_invalid_jev_answers_fail_closed(field, value):
    data = raw_response()
    data["answers"]["slide_action"][field] = value
    with pytest.raises(ValueError):
        validate_response(data)


@pytest.mark.parametrize("probabilities", [{"NEXT": .7, "STAY": .7, "UNCERTAIN": .7}, {"NEXT": True, "STAY": 0, "UNCERTAIN": 0},
    {"NEXT": .1, "STAY": .8, "UNCERTAIN": .1}, {"NEXT": 1}, {"NEXT": float("nan"), "STAY": 0, "UNCERTAIN": 0}])
def test_invalid_probabilities_fail_closed(probabilities):
    data = raw_response()
    data["answers"]["slide_action"]["probabilities"] = probabilities
    with pytest.raises(ValueError):
        validate_response(data)


def test_missing_confidence_remains_unavailable():
    data = raw_response()
    del data["answers"]["slide_action"]["confidence"]
    result = validate_response(data)
    assert result.confidence is None
    gate = TemporalGate(GateSettings())
    assert gate.observe(result, 1, 10, -100)[0]


def test_returned_confidence_is_display_only():
    gate = TemporalGate(GateSettings())
    assert gate.observe(next_decision(.1), 1, 10, -100)[0]


def test_one_fresh_next_is_enough_but_the_same_observation_cannot_repeat():
    gate = TemporalGate(GateSettings())
    assert gate.observe(next_decision(), 1, 10, -100)[0]
    assert not gate.observe(next_decision(), 1, 12, -100)[0]


def test_next_probability_is_display_only():
    gate = TemporalGate(GateSettings())
    assert gate.observe(next_decision(.01, .34), 1, 10, -100)[0]


@pytest.mark.parametrize("decision", [Decision("STAY"), Decision("UNCERTAIN"), Decision(error="timeout")])
def test_stay_uncertain_or_error_never_advances(decision):
    gate = TemporalGate(GateSettings())
    assert not gate.observe(decision, 1, 10, -100)[0]


def test_next_during_cooldown_is_not_queued_for_later():
    gate = TemporalGate(GateSettings())
    assert not gate.observe(next_decision(), 1, 11, 10)[0]
    assert not gate.observe(next_decision(), 2, 12, 10)[0]
    assert not gate.observe(next_decision(), 2, 16, 10)[0]
    assert gate.observe(next_decision(), 3, 17, 10)[0]


@pytest.mark.parametrize("change", ["hash", "missing_transition", "wrong_pair", "missing_slide", "bad_list"])
def test_incompatible_profiles_rejected(bundle, deck, change):
    b = deepcopy(bundle)
    if change == "hash": b["deck_sha256"] = "other"
    if change == "missing_transition": b["transitions"].pop()
    if change == "wrong_pair": b["transitions"][0]["to_slide"] = 3
    if change == "missing_slide": b["slide_understanding"].pop()
    if change == "bad_list": b["transitions"][0]["false_positive_traps"] = "word"
    with pytest.raises(ValueError): validate_bundle(b, deck)


@pytest.mark.parametrize("url", ["http://remote.example/api", "https://user:pass@example.com", "https://example.com?key=secret", "file:///tmp/api"])
def test_unsafe_endpoints_rejected(url):
    with pytest.raises(ValueError): endpoint(url)


class FakeClock:
    def __init__(self): self.now = 10.
    def __call__(self): return self.now
    def step(self, seconds=2): self.now += seconds


class FixedClassifier:
    def __init__(self, decision=None, callback=None):
        self.decision = decision or next_decision()
        self.calls = []
        self.callback = callback
    def classify(self, state):
        self.calls.append(state)
        if self.callback: self.callback()
        return self.decision


@pytest.fixture
def engine(deck, bundle):
    clock = FakeClock()
    model = FixedClassifier()
    e = PresentationEngine(deck, bundle, model, BrowserController(3), clock=clock)
    e.set_auto(True)
    clock.step(6)
    e.rendered(1)
    return e


def phrase(engine, number):
    engine.clock.step(1)
    engine.rendered(engine.slide)
    engine.add_transcript(f"Fresh finalized transition evidence number {number}", f"id-{number}")
    engine.evaluate_once()


def test_automatic_advance_cannot_repeat_from_same_transcript(engine):
    phrase(engine, 1)
    assert engine.slide == 2
    for _ in range(20):
        engine.clock.step()
        engine.evaluate_once()
    assert engine.slide == 2
    assert len(engine.classifier.calls) == 1


def test_success_is_reported_for_actual_advance_and_old_suggestion_is_cleared(engine):
    phrase(engine, 1)
    snap = engine.snapshot()
    assert snap["slide"] == 2
    assert snap["decision_slide"] is None
    assert snap["decision"]["confidence"] is None
    assert "Advanced to slide 2" in snap["reason"]
    assert not snap["presenter_connected"]  # New image has not acknowledged rendering yet.
    assert snap["logs"][-1]["action"] == "NEXT"
    engine.rendered(2)
    assert engine.snapshot()["presenter_connected"]


@pytest.mark.parametrize("reset", ["navigate", "toggle", "configure"])
def test_navigation_and_settings_do_not_leave_old_decision_visible(engine, reset):
    engine.classifier.decision = Decision("STAY")
    phrase(engine, 1)
    assert engine.snapshot()["decision_slide"] == 1
    if reset == "navigate":
        engine.goto(2)
    elif reset == "toggle":
        engine.set_auto(False)
    else:
        engine.configure(FixedClassifier(), replace(engine.cfg, cooldown_seconds=6))
    snap = engine.snapshot()
    assert snap["decision_slide"] is None
    assert snap["decision"]["confidence"] is None
    assert engine.gate.last_sequence == -1


def test_single_next_advances_even_with_low_confidence_and_probability(engine):
    engine.classifier.decision = next_decision(.42, .61)
    phrase(engine, 1)
    snap = engine.snapshot()
    assert snap["slide"] == 2
    assert snap["logs"][-1]["action"] == "NEXT"
    assert len(engine.classifier.calls) == 1


def test_cooldown_still_requires_new_speech_before_another_advance(engine):
    phrase(engine, 1)
    assert engine.slide == 2
    phrase(engine, 2)
    assert engine.slide == 2 and "cooldown" in engine.reason
    engine.clock.step(6)
    engine.evaluate_once()
    assert engine.slide == 2 and len(engine.classifier.calls) == 2
    phrase(engine, 3)
    assert engine.slide == 3


def test_manual_navigation_invalidates_inflight_inference(engine):
    engine.classifier.callback = lambda: engine.goto(2)
    phrase(engine, 1)
    assert engine.slide == 2
    assert engine.reason == "Stale inference discarded"
    assert engine.gate.last_sequence == -1


def test_toggling_auto_off_invalidates_inflight_result(engine):
    engine.classifier.callback = lambda: engine.set_auto(False)
    phrase(engine, 1)
    assert engine.slide == 1 and not engine.auto
    assert engine.reason == "Stale inference discarded"


def test_slow_inference_is_discarded(engine):
    engine.classifier.callback = lambda: engine.clock.step(20)
    phrase(engine, 1)
    assert engine.slide == 1
    assert engine.reason == "Stale inference discarded"


def test_partial_transcript_is_display_only(engine):
    engine.add_transcript("still being revised", "partial", final=False)
    engine.evaluate_once()
    assert engine.snapshot()["partial"] == "still being revised"
    assert not engine.classifier.calls


def test_duplicate_event_and_duplicate_text_are_not_new_votes(engine):
    assert engine.add_transcript("same phrase", "id")
    assert not engine.add_transcript("same phrase", "new-id")
    assert not engine.add_transcript("different phrase", "id")
    assert engine.sequence == 1


def test_preview_mode_never_moves_slides(engine):
    engine.set_auto(False)
    phrase(engine, 1)
    phrase(engine, 2)
    assert engine.slide == 1


def test_browser_presenter_must_actually_acknowledge_current_image(engine):
    engine.presented_slide = None
    for i in (1, 2):
        engine.clock.step(2)
        engine.add_transcript(f"fresh evidence {i}", str(i))
        engine.evaluate_once()
    assert engine.slide == 1
    assert "presenter" in engine.reason


def test_external_slide_change_synchronizes_and_drops_old_speech(engine):
    engine.classifier.decision = Decision("STAY")
    phrase(engine, 1)
    engine.controller.goto(2)
    engine.evaluate_once()
    assert engine.slide == 2
    assert len(engine.classifier.calls) == 1
    assert engine.gate.last_sequence == -1


def test_last_slide_never_calls_jev_or_advances(engine):
    engine.goto(3)
    phrase(engine, 1)
    assert engine.slide == 3 and not engine.classifier.calls
    assert any(x["kind"] == "transcript" and x["slide"] == 3 for x in engine.logs)


def test_current_profile_and_visual_summaries_are_sent(engine):
    phrase(engine, 1)
    state = engine.classifier.calls[0]
    assert state["actual_slide"] == 1
    assert state["transition_profile"]["from_slide"] == 1
    assert state["next_slide"]["slide_number"] == 2
    assert state["current_slide"]["visual_elements"]
    assert state["new_utterances"] == ["Fresh finalized transition evidence number 1"]


def test_inference_saves_exact_payload_and_answer_without_credentials(engine, monkeypatch):
    response = raw_response()
    monkeypatch.setattr("slide_switcher.jev.requests.post", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: response))
    engine.classifier = JevClient(Settings(jev_key="private-unit-fixture-key"))
    phrase(engine, 1)
    entry = engine.logs[-1]
    assert entry["request"]["state"]["newest_utterance"] == "Fresh finalized transition evidence number 1"
    assert "slide_action" in entry["request"]["questions"]
    assert entry["response"]["probabilities"] == response["answers"]["slide_action"]["probabilities"]
    assert "private-unit-fixture-key" not in json.dumps(entry)


def completion_bundle(engine):
    return {**engine.bundle, "completion_goals": [{"slide_number": i, "main_message": f"Topic {i}",
        "essential_points": [f"Explain topic {i}"], "optional_details": []} for i in (1, 2, 3)]}


def test_completed_explanation_alone_advances_without_a_handoff(engine):
    engine.update_bundle(completion_bundle(engine))
    engine.classifier.decision = Decision("STAY", coverage_label="COMPLETE")
    phrase(engine, 1)
    assert engine.slide == 2
    assert engine.logs[-1]["advance_source"] == "coverage"
    assert engine.logs[-1]["transition"] == "STAY"
    assert "essential ideas covered" in engine.reason
    assert not engine.slide_memory.transcript


def test_explicit_hold_overrides_sufficient_content(engine):
    engine.update_bundle(completion_bundle(engine))
    engine.classifier.decision = Decision("NEXT", coverage_label="WAIT")
    phrase(engine, 1)
    assert engine.slide == 1 and "Holding" in engine.reason


def test_completion_during_cooldown_is_consumed_not_queued(engine):
    engine.update_bundle(completion_bundle(engine))
    engine.classifier.decision = Decision("STAY", coverage_label="COMPLETE")
    engine.switched_at = engine.clock()
    phrase(engine, 1)
    assert engine.slide == 1 and "cooldown" in engine.reason
    engine.clock.step(6)
    engine.evaluate_once()
    assert engine.slide == 1
    phrase(engine, 2)
    assert engine.slide == 2


@pytest.mark.parametrize("unavailable", ["goals", "memory"])
def test_coverage_cannot_advance_without_valid_goals_and_full_memory(engine, unavailable):
    if unavailable == "memory":
        engine.update_bundle(completion_bundle(engine))
        engine.slide_memory.complete = False
    engine.classifier.decision = Decision("STAY", coverage_label="COMPLETE")
    phrase(engine, 1)
    assert engine.slide == 1 and "unavailable" in engine.reason


def test_coverage_uses_earlier_speech_beyond_the_recent_window(engine):
    engine.classifier.decision = Decision("STAY")
    engine.add_transcript("The first essential idea was explained here", "earlier")
    engine.evaluate_once()
    engine.clock.step(60)
    engine.set_auto(False)
    engine.set_auto(True)
    phrase(engine, 2)
    state = engine.classifier.calls[-1]
    assert "first essential idea" not in state["recent_transcript"]
    assert "first essential idea" in state["slide_transcript"]
    engine.goto(2)
    assert not engine.slide_memory.transcript


def test_coverage_waits_for_an_in_progress_phrase(engine):
    engine.update_bundle(completion_bundle(engine))
    engine.classifier.decision = Decision("STAY", coverage_label="COMPLETE")
    engine.classifier.callback = lambda: engine.add_transcript("and another unfinished thought", "partial", final=False)
    phrase(engine, 1)
    assert engine.slide == 1 and "phrase to finish" in engine.reason
    engine.classifier.callback = None
    phrase(engine, 2)
    assert engine.slide == 2


def test_same_words_on_a_new_slide_are_new_speech(engine):
    engine.add_transcript("Next slide please", "one")
    engine.evaluate_once()
    assert engine.slide == 2
    engine.clock.step(6)
    engine.rendered(2)
    assert engine.add_transcript("Next slide please", "two")
    engine.evaluate_once()
    assert engine.slide == 3


def test_completion_goals_changed_during_inference_invalidate_the_result(engine):
    engine.classifier.callback = lambda: engine.update_bundle(completion_bundle(engine))
    phrase(engine, 1)
    assert engine.slide == 1
    assert engine.logs[-1]["kind"] == "stale_inference"


def test_all_unprocessed_phrases_reach_jev_even_when_a_filler_arrives_last(engine):
    engine.add_transcript("Next slide please", "request")
    engine.add_transcript("Um", "filler")
    engine.evaluate_once()
    assert engine.classifier.calls[0]["new_utterances"] == ["Next slide please", "Um"]
    assert engine.slide == 2


def test_new_speech_does_not_repeat_previous_context(engine):
    engine.set_auto(False)
    engine.add_transcript("Next slide please", "old-request")
    engine.evaluate_once()
    engine.clock.step(2)
    engine.add_transcript("Actually stay here and explain the cloud diagram", "correction")
    engine.evaluate_once()
    state = engine.classifier.calls[-1]
    assert state["new_utterances"] == ["Actually stay here and explain the cloud diagram"]
    assert "Next slide please" in state["recent_transcript"]


def test_cancellation_arriving_during_inference_is_not_lost(engine):
    engine.classifier.callback = lambda: engine.add_transcript("Actually stay on this slide", "cancel")
    engine.add_transcript("Next slide please", "request")
    engine.evaluate_once()
    assert engine.slide == 1 and engine.processed == 0
    assert engine.logs[-1]["kind"] == "superseded_inference"
    engine.classifier.callback = None
    engine.classifier.decision = Decision("STAY")
    engine.clock.step(1)
    engine.evaluate_once()
    assert engine.classifier.calls[-1]["new_utterances"] == ["Next slide please", "Actually stay on this slide"]
    assert engine.slide == 1


def test_browser_controller_compare_and_advance_is_atomic():
    c = BrowserController(3)
    c.goto(2)
    assert not c.advance_if_current(1)
    assert c.current_slide() == 2
    assert c.advance_if_current(2)
    assert not c.advance_if_current(3)


@pytest.mark.parametrize("target", [0, 4, -1, True, "2"])
def test_controller_rejects_invalid_indices(target):
    with pytest.raises(ValueError): BrowserController(3).goto(target)
