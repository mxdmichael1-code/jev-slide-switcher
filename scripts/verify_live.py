"""Opt-in real-provider smoke check using only the included synthetic example.

Makes billable API calls. Does not record microphone audio or read personal decks.
"""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slide_switcher.config import ROOT, Settings, GateSettings
from slide_switcher.controllers import BrowserController
from slide_switcher.deck import prepare_deck
from slide_switcher.engine import PresentationEngine
from slide_switcher.jev import JevClient
from slide_switcher.vision import VisionPreprocessor
from scripts.create_demo import create_demo


CASES = [
    (1, "preview_should_stay", "STAY", "We will discuss batteries later. For now, stay with this solar panel diagram. I am still explaining how passing clouds cause solar output to drop."),
    (1, "handoff_to_storage", "NEXT", "That completes why solar output changes. Let us now move to the battery diagram and explain how we store the surplus solar energy for the evening."),
    (1, "storage_explanation_begins", "NEXT", "Now the battery is the focus. In this new diagram, the arrows show excess daytime electricity charging the battery, then going to the evening load."),
    (2, "cost_preview_should_stay", "STAY", "We will compare costs later. Right now I am still explaining the charging and discharging arrows on this battery diagram."),
    (2, "handoff_to_cost", "NEXT", "We have finished how the battery stores and releases electricity. Now let us move to the final capacity versus cost tradeoff and choose a suitable battery size."),
    (2, "economics_begins", "NEXT", "Here we are comparing a smaller battery's lower upfront cost with the larger battery's greater evening coverage. This capacity-versus-cost comparison is our final decision."),
    (1, "unrelated_should_not_advance", "UNCERTAIN", "Could someone close the door? There is a lot of noise from the corridor."),
]


def main():
    settings = Settings.load()
    if not settings.jev_key or not settings.doubao_key:
        raise SystemExit("Configure Jev and Doubao keys before running the live check.")
    sample = ROOT / "examples/solar_storage.pptx"
    if not sample.exists(): create_demo(sample)
    deck = prepare_deck(sample.read_bytes(), sample.name, settings.data_root, settings.soffice)
    bundle = VisionPreprocessor(settings).analyze(deck, progress=lambda s: print(s, flush=True))
    client = JevClient(settings)
    records = []
    for slide, name, expected, text in CASES:
        state = {"actual_slide": slide, "transition_profile": bundle["transitions"][slide-1],
                 "current_slide": bundle["slide_understanding"][slide-1],
                 "next_slide": bundle["slide_understanding"][slide],
                 "recent_transcript": text, "newest_utterance": text, "seconds_on_slide": 12}
        result = client.classify(state)
        records.append({"case": name, "expected": expected, "transcript": text, **asdict(result)})
        print(json.dumps({"case": name, "expected": expected, "label": result.label,
                          "confidence": result.confidence, "latency_ms": result.latency_ms, "error": result.error}), flush=True)
    controller = BrowserController(3)
    engine = PresentationEngine(deck, bundle, client, controller,
             GateSettings(cooldown_seconds=0))
    engine.set_auto(True)
    rehearsal = [CASES[0][3], CASES[1][3], CASES[2][3],
        "During daytime, surplus solar electricity charges this battery. The middle box stores the energy and the right arrow sends it to the evening load after sunset. That explains both arrows and completes the charging and discharging mechanism.",
        CASES[4][3], CASES[5][3],
        "The battery's charging and discharging explanation is complete. We are moving on from the storage mechanism to our final slide, choosing between the smaller battery's lower investment and the larger battery's longer coverage.",
        "Please advance to the final capacity versus cost diagram now. We are finished with charging and discharging. The next explanation compares the small and large battery investment options.",
        "We now need the capacity versus cost slide on screen. My current explanation is the tradeoff between the small battery's lower cost and the large battery's longer evening coverage, rather than how a battery works."]
    for index, text in enumerate(rehearsal):
        if engine.slide == 3:
            break
        engine.rendered(engine.slide)
        engine.add_transcript(text, f"live-check-{index}")
        engine.evaluate_once()
        time.sleep(1)
    result = {"note": "Real Doubao preprocessing and Jev calls on a synthetic deck. Presenter acknowledgements in this script are simulated; browser verification is separate.",
              "doubao_model": bundle["model"], "preprocessing_calls": bundle["api_calls"],
              "cases": records, "engine_final_slide": engine.slide, "engine_log": list(engine.logs)}
    target = ROOT / "verification/live_results.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("Engine final slide:", engine.slide, "/ 3; report:", target, flush=True)
    if any(r["error"] for r in records) or engine.slide != 3:
        raise SystemExit("Live verification did not pass; inspect the report.")


if __name__ == "__main__": main()
