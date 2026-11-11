import json

from slide_switcher.history import read_activity, speech_rows, decision_rows


def test_history_recovers_legacy_rehearsal_and_ignores_a_partial_record(tmp_path):
    event = {"time": "2026-11-04T14:37:00Z", "kind": "inference", "slide": 1,
             "new_utterances": ["A first fragment", "and its continuation"], "coverage": "PARTIAL"}
    (tmp_path / "presentation_log.jsonl").write_text(json.dumps(event) + '\n{"unfinished":')
    recovered = read_activity(tmp_path)
    assert recovered == [event]
    assert [x["Speech"] for x in speech_rows(recovered)] == event["new_utterances"]
    assert decision_rows(recovered)[0]["Explanation"] == "PARTIAL"
    assert not read_activity(tmp_path / "missing")


def test_new_transcripts_are_not_duplicated_by_their_inference():
    records = [
        {"time": "1", "kind": "transcript", "session_id": "visit", "slide": 1, "text": "Original speech"},
        {"time": "2", "kind": "inference", "session_id": "visit", "slide": 1, "new_utterances": ["Original speech"]},
        {"time": "3", "kind": "transcript", "session_id": "visit", "slide": 3, "text": "Final slide explanation"},
    ]
    assert [x["Speech"] for x in speech_rows(records)] == ["Original speech", "Final slide explanation"]


def test_history_has_a_bounded_tail(tmp_path):
    event = {"time": "recent", "kind": "transcript", "slide": 2, "text": "Recent speech"}
    path = tmp_path / "presentation_log.jsonl"
    path.write_text("x" * 2_100_000 + "\n" + json.dumps(event) + "\n")
    assert read_activity(tmp_path) == [event]
