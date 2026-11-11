"""Read bounded, local saved activity without depending on Streamlit session state."""
import json
from pathlib import Path


def read_activity(directory: Path, limit=300):
    path = directory / "presentation_log.jsonl"
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 2_000_000)
            stream.seek(start)
            if start:
                stream.readline()  # The first record may begin before this bounded tail.
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict) and isinstance(value.get("time"), str) and isinstance(value.get("kind"), str):
                records.append(value)
        except (ValueError, TypeError):
            continue  # Includes a record concurrently being appended.
    return records[-limit:]


def speech_rows(records):
    # Modern sessions save each accepted final phrase, including the final slide.
    # Older logs have only inference batches; recover those without inventing text.
    with_transcripts = {x.get("session_id") for x in records if x["kind"] == "transcript"}
    rows = []
    for event in records:
        if event["kind"] == "transcript":
            speech = [event.get("text")]
        elif event["kind"] == "inference" and event.get("session_id") not in with_transcripts:
            speech = event.get("new_utterances") or [event.get("utterance")]
        else:
            continue
        for text in speech:
            if isinstance(text, str) and text.strip():
                rows.append({"Time (UTC)": event["time"], "Slide": event.get("slide"), "Speech": text})
    return rows


def decision_rows(records):
    return [{"Time (UTC)": x["time"], "Slide": x.get("slide"),
             "Transition": x.get("transition", x.get("decision")), "Explanation": x.get("coverage", "—"),
             "Action": x.get("action"), "Time (ms)": x.get("latency_ms"),
             "New speech": "\n".join(x.get("new_utterances") or [x.get("utterance", "")])}
            for x in records if x["kind"] == "inference"]
