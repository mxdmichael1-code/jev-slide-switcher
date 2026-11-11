"""Reusable completion goals and speech memory for one visit to a slide."""
MAX_COVERAGE_CHARACTERS = 60000


def validate_completion_goals(goals, slide_count):
    if not isinstance(goals, list) or len(goals) != slide_count:
        raise ValueError("Completion goals must cover every slide.")
    for number, goal in enumerate(goals, 1):
        if not isinstance(goal, dict) or type(goal.get("slide_number")) is not int or goal["slide_number"] != number:
            raise ValueError("Completion goals must follow the slide order.")
        message = goal.get("main_message")
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("Each slide needs a main message.")
        goal.setdefault("optional_details", [])
        for key, minimum, maximum in (("essential_points", 1, 3), ("optional_details", 0, 6)):
            items = goal.get(key)
            if not isinstance(items, list) or not minimum <= len(items) <= maximum:
                raise ValueError(f"Invalid completion goal: {key}")
            if not all(isinstance(x, str) and x.strip() and len(x) <= 2000 for x in items):
                raise ValueError(f"Invalid completion goal: {key}")
            if len({x.strip().casefold() for x in items}) != len(items):
                raise ValueError("Completion points must be distinct.")
    return goals


class SlideMemory:
    def __init__(self):
        self.reset()

    def reset(self):
        self.utterances = []
        self.characters = 0
        self.complete = True

    def add(self, text):
        if self.characters + len(text) + 1 > MAX_COVERAGE_CHARACTERS:
            # Never silently discard earlier evidence and claim full coverage.
            self.complete = False
            return
        self.utterances.append(text)
        self.characters += len(text) + 1

    @property
    def transcript(self):
        return "\n".join(self.utterances)
