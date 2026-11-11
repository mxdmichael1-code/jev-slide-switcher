"""Opt-in real Jev checks for completion without a navigation/ending signal.

Uses the synthetic example, cached Doubao understanding, and reviewed goals. Successful
Jev decisions are cached; --refresh explicitly repeats identical requests.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slide_switcher.config import ROOT, Settings
from slide_switcher.controllers import BrowserController
from slide_switcher.deck import prepare_deck
from slide_switcher.engine import PresentationEngine
from slide_switcher.jev import COVERAGE_QUESTION, Decision, JevClient
from slide_switcher.vision import validate_bundle


SOLAR = "Clouds block some sunlight from reaching the panels, so they produce less electricity. Output rises in bright daylight, falls with cloud cover, and stops at night."
CHARGE = "During the day, when the solar panels generate more electricity than the house uses, the extra energy charges the battery."
DISCHARGE = "After sunset the panels stop generating, so the battery releases that stored electricity to supply the home's evening load."
CASES = [
    ("solar_explained_without_ending_signal", 1, [SOLAR], "COMPLETE", "coverage"),
    ("title_only", 1, ["This slide is about variable solar generation."], "PARTIAL", None),
    ("isolated_labels", 1, ["Sun, cloud, solar panel, variable power."], "PARTIAL", None),
    ("promise_is_not_explanation", 1, ["I will explain why the solar output changes with the weather and daylight."], "PARTIAL", None),
    # This is also a wrap-up intent. It may advance through the original
    # transition route, but must not falsely count as substantive coverage.
    ("wrap_up_does_not_prove_coverage", 2, ["I've covered all the essential ideas about the battery."], "PARTIAL", "transition"),
    ("contradiction", 1, ["The panels make exactly the same power in bright sunlight, under clouds, and all through the night."], "PARTIAL", None),
    ("battery_charge_only", 2, [CHARGE], "PARTIAL", None),
    ("battery_complete_without_ending_signal", 2, [CHARGE, DISCHARGE], "COMPLETE", "coverage"),
    ("complete_but_explicit_hold", 1, [SOLAR, "Keep this slide up, I still want to explain this arrow."], "WAIT", None),
    ("complete_but_unfinished", 2, [CHARGE, DISCHARGE, "And the other reason for this is because"], "WAIT", None),
    ("request_cancelled_after_completion", 1, [SOLAR, "Next slide please. Actually don't change it yet."], "WAIT", None),
    ("explicit_next_with_incomplete_coverage", 2, ["Next slide please."], None, "transition"),
    ("filler_does_not_replay_completion", 1, [SOLAR, "Um."], "UNCERTAIN", None),
    ("unrelated_does_not_replay_completion", 1, [SOLAR, "Could someone close the door?"], "UNCERTAIN", None),
    ("short_core_relationship", 1, ["Clouds change how much power the solar panel produces."], "COMPLETE", "coverage"),
    ("connected_walkthrough", 1, ["First the changing sunlight reaches the panel, then that gives us varying electrical power."], "COMPLETE", "coverage"),
    ("unfinished_sentence_before_continuation", 2, [CHARGE, "At night the stored electricity supplies the"], "WAIT", None),
    ("noun_fragment_completes_sentence", 2, [CHARGE, "At night the stored electricity supplies the", "evening load"], "COMPLETE", "coverage"),
    ("explicit_self_correction", 2, ["The panels stop during the day, sorry I mean at night. Daytime surplus charges the battery, which releases that stored electricity to power the house after sunset."], "COMPLETE", "coverage"),
    ("irrecoverable_discharge_speech", 2, [CHARGE, "The supplies but after a run of Samsung, you snow the."], ("PARTIAL", "UNCERTAIN"), None),
]


class CachedClient:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.live = client or JevClient(settings)
        self.cache = settings.data_root / "completion_eval_cache"
        self.cache.mkdir(exist_ok=True)
        self.records = []

    def classify(self, state):
        fingerprint = hashlib.sha256(json.dumps({"state": state, "question": self.live.question,
            "coverage_question": self.live.coverage_question, "model": self.settings.jev_model,
            "endpoint": self.settings.jev_url}, sort_keys=True).encode()).hexdigest()
        saved = self.cache / (fingerprint + ".json")
        if saved.exists() and "--refresh" not in sys.argv:
            decision = Decision(**json.loads(saved.read_text()))
        else:
            decision = self.live.classify(state)
            if not decision.error and not decision.coverage_error:
                saved.write_text(json.dumps(asdict(decision), ensure_ascii=False))
        self.records.append({"state": state, "answer": asdict(decision)})
        return decision


class Clock:
    def __init__(self): self.now = 10.
    def __call__(self): return self.now


def main():
    settings = Settings.load()
    if not settings.jev_key:
        raise SystemExit("Configure Jev before this opt-in live check.")
    path = ROOT / "examples/solar_storage.pptx"
    deck = prepare_deck(path.read_bytes(), path.name, settings.data_root, settings.soffice)
    bundle = validate_bundle(json.loads((deck.directory / "transition_profiles.json").read_text()), deck)
    if not bundle.get("completion_goals"):
        raise SystemExit("Prepare the example's Doubao completion goals first.")
    client, results = CachedClient(settings), []
    for name, slide, speech, coverage, source in CASES:
        # Earlier phrases provide whole-slide context. Only the last phrase is
        # new for filler/hold tests, avoiding a replay of earlier completion.
        new = speech if name not in ("filler_does_not_replay_completion", "unrelated_does_not_replay_completion") else speech[-1:]
        state = {"actual_slide": slide, "transition_profile": bundle["transitions"][slide-1],
            "current_slide": bundle["slide_understanding"][slide-1],
            "next_slide": bundle["slide_understanding"][slide],
            "completion_goal": bundle["completion_goals"][slide-1],
            "slide_transcript": "\n".join(speech), "coverage_context_complete": True,
            "recent_transcript": " ".join(speech), "newest_utterance": new[-1],
            "new_utterances": new, "seconds_on_slide": 20}
        decision = client.classify(state)
        expected_labels = (coverage,) if isinstance(coverage, str) else coverage
        passed = not decision.error and not decision.coverage_error and decision.advance_source == source and (expected_labels is None or decision.coverage_label in expected_labels)
        record = {"case": name, "expected_coverage": coverage, "expected_source": source,
            **client.records[-1], "passes": passed}
        results.append(record)
        print(json.dumps({"case": name, "transition": decision.label, "coverage": decision.coverage_label,
            "source": decision.advance_source, "passes": passed, "error": decision.error or decision.coverage_error}), flush=True)

    # Exercise the actual engine: the charge explanation expires from recent
    # context but remains in the independent current-slide memory.
    clock = Clock()
    controller = BrowserController(3)
    controller.goto(2)
    engine = PresentationEngine(deck, bundle, client, controller, clock=clock)
    engine.set_auto(True)
    engine.rendered(2)
    engine.add_transcript(CHARGE, "coverage-live-charge")
    engine.evaluate_once()
    first = controller.current_slide()
    clock.now += 60
    engine.rendered(2)
    engine.add_transcript(DISCHARGE, "coverage-live-discharge")
    engine.evaluate_once()
    second = controller.current_slide()
    state = client.records[-1]["state"]
    engine_check = {"first_slide": first, "resulting_slide": second,
        "older_charge_missing_from_recent": CHARGE not in state["recent_transcript"],
        "older_charge_retained_in_slide_memory": CHARGE in state["slide_transcript"],
        "actual_calls": client.records[-2:], "events": list(engine.logs)}
    engine_check["passes"] = first == 2 and second == 3 and engine_check["older_charge_missing_from_recent"] and engine_check["older_charge_retained_in_slide_memory"] and any(x.get("source") == "coverage" for x in engine.logs)
    summary = {"passed": sum(r["passes"] for r in results), "total": len(results),
        "failed": [r["case"] for r in results if not r["passes"]], "accumulated_memory_engine": engine_check["passes"]}
    report = {"scope": "Actual Jev two-question responses on a synthetic presentation with cached Doubao understanding and reviewed completion goals. Targeted development checks, not an accuracy benchmark. Engine presenter acknowledgements and time are simulated; browser verification is separate. No microphone capture.",
        "goal_review": bundle.get("completion_goals_review"),
        "summary": summary, "cases": results, "engine": engine_check}
    (ROOT / "verification/completion_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    if summary["failed"] or not engine_check["passes"]:
        raise SystemExit("Completion check failed; inspect verification/completion_verification.json.")


if __name__ == "__main__":
    main()
