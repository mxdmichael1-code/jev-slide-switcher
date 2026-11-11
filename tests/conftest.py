from pathlib import Path

import pytest

from slide_switcher.deck import Deck, Slide


@pytest.fixture
def deck(tmp_path):
    return Deck("a" * 64, "fixture.pptx", tmp_path, [
        Slide(i, f"Slide {i}", f"Topic {i}", f"Notes {i}", f"{i}.png") for i in (1, 2, 3)])


@pytest.fixture
def bundle(deck):
    return {"schema_version": 1, "deck_sha256": deck.sha256, "presentation_flow": "Problem, mechanism, tradeoff",
            "slide_understanding": [{"slide_number": i, "summary": f"Explain topic {i}", "topics": [f"topic {i}"], "visual_elements": ["A diagram"]} for i in (1, 2, 3)],
            "transitions": [{"from_slide": i, "to_slide": i + 1, "current_topic": f"Topic {i}", "next_topic": f"Topic {i+1}",
                             "transition_intent": "Current explanation complete, next explanation starts",
                             "completion_signals": ["A conclusion"], "next_topic_signals": ["A new explanatory goal"],
                             "stay_signals": ["Still elaborating"], "false_positive_traps": ["Preview of later material"],
                             "visual_handoff": ["Refers to next diagram"]} for i in (1, 2)]}
