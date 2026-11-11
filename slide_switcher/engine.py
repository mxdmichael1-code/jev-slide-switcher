from __future__ import annotations

from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
import json
import threading
import time
import uuid

from .config import GateSettings
from .completion import SlideMemory
from .jev import Decision
from .vision import validate_bundle


class TemporalGate:
    def __init__(self, settings: GateSettings):
        self.settings = settings
        self.reset()

    def reset(self):
        self.last_sequence = -1

    def observe(self, decision, sequence, now, switched_at):
        cfg = self.settings
        if sequence <= self.last_sequence:
            return False, "Waiting for fresh speech"
        self.last_sequence = sequence
        if decision.coverage_label == "WAIT":
            return False, "Holding this slide — waiting for the current explanation"
        if decision.error or not decision.advance_source:
            return False, "Slide decisions unavailable — check Settings" if decision.error else (
                "Staying on this slide" if decision.label == "STAY" else "Listening for a clearer transition")
        # A decision received during cooldown is consumed, never queued for later.
        if now - switched_at < cfg.cooldown_seconds:
            remaining = cfg.cooldown_seconds - (now - switched_at)
            return False, f"NEXT held — slide cooldown ({remaining:.0f}s remaining when checked); keep speaking"
        return True, "Essential ideas covered" if decision.advance_source == "coverage" else "NEXT detected"


class PresentationEngine:
    def __init__(self, deck, bundle, classifier, controller, gate_settings=None, clock=time.monotonic):
        validate_bundle(bundle, deck)
        self.deck, self.bundle = deck, bundle
        self.classifier, self.controller = classifier, controller
        self.cfg = gate_settings or GateSettings()
        self.gate = TemporalGate(self.cfg)
        self.clock = clock
        self.lock = threading.RLock()
        self.session_id = uuid.uuid4().hex
        self.slide = controller.current_slide()
        self.epoch, self.sequence, self.processed = 0, 0, 0
        self.slide_since = self.clock()
        self.switched_at = float("-inf")
        self.events = deque(maxlen=250)
        self.slide_memory = SlideMemory()
        self.event_ids = deque(maxlen=500)
        self.logs = deque(maxlen=500)
        self.latest = Decision()
        self.decision_slide = None
        self.reason = "Automatic switching is off"
        self.auto = False
        self.partial = ""
        self.presented_slide = None
        self.presenter_seen = float("-inf")
        self._stopped = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._last_inference = float("-inf")
        self._last_synced = float("-inf")

    def log(self, kind, **values):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, "session_id": self.session_id, **values}
        self.logs.append(entry)
        # Explicit event log: includes transcript snippets, never audio or API keys.
        with (self.deck.directory / "presentation_log.jsonl").open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="jev-slide-decisions", daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped.set()
        self._wake.set()

    def set_auto(self, enabled):
        with self.lock:
            if self.auto != bool(enabled):
                self.auto = bool(enabled)
                self.epoch += 1  # also invalidate an in-flight decision on toggles
                self.gate.reset()
                self.processed = self.sequence
                self._clear_decision()
                self.reason = "Explain the slide naturally — waiting for fresh speech" if self.auto else "Automatic switching is off"

    def _clear_decision(self):
        self.latest = Decision()
        self.decision_slide = None

    def configure(self, classifier, settings):
        with self.lock:
            self.classifier, self.cfg = classifier, settings
            self.gate = TemporalGate(settings)
            self.epoch += 1
            self.processed = self.sequence
            self._clear_decision()
            self.reason = "Settings saved — waiting for fresh speech"

    def update_bundle(self, bundle):
        validate_bundle(bundle, self.deck)
        with self.lock:
            self.bundle = bundle
            self.epoch += 1
            self.processed = self.sequence
            self.gate.reset()
            self._clear_decision()
            self.reason = "Slide goals saved — waiting for fresh speech"

    def add_transcript(self, text: str, event_id: str, final=True, latency_ms=None):
        text = " ".join(text.strip().split())[:4000]
        if not text:
            return False
        with self.lock:
            if not final:
                self.partial = text
                return True
            self.partial = ""
            if event_id in self.event_ids:
                return False
            self.event_ids.append(event_id)
            if self.events and self.events[-1]["text"] == text and self.events[-1]["epoch"] == self.epoch:
                return False
            self.sequence += 1
            self.events.append({"sequence": self.sequence, "epoch": self.epoch, "text": text,
                                "at": self.clock(), "latency_ms": latency_ms})
            self.slide_memory.add(text)
            self.log("transcript", slide=self.slide, sequence=self.sequence, text=text, latency_ms=latency_ms)
            self._wake.set()
            return True

    def _slide_changed(self, new_slide, kind, source=None):
        old = self.slide
        self.slide = new_slide
        self.epoch += 1
        self.slide_since = self.clock()
        self.switched_at = self.clock()
        self.presented_slide = None
        self.partial = ""
        self.slide_memory.reset()
        self.processed = self.sequence
        self.gate.reset()
        self._clear_decision()
        self.reason = (f"Advanced to slide {new_slide} — listening for the next transition" if kind == "automatic_transition"
                       else "Waiting for fresh speech on the new slide")
        if source == "coverage":
            self.reason = f"Advanced to slide {new_slide} — essential ideas covered"
        self.log(kind, from_slide=old, to_slide=new_slide, **({"source": source} if source else {}))

    def sync(self):
        with self.lock:
            actual = self.controller.current_slide()
            if type(actual) is not int or not 1 <= actual <= len(self.deck.slides):
                raise RuntimeError("Controller returned an invalid slide index.")
            if actual != self.slide:
                self._slide_changed(actual, "external_navigation")

    def goto(self, target):
        with self.lock:
            actual = self.controller.goto(target)
            if actual != target:
                raise RuntimeError("Controller did not reach the requested slide.")
            if actual != self.slide:
                self._slide_changed(actual, "manual_navigation")

    def rendered(self, slide):
        with self.lock:
            if slide == self.slide:
                self.presented_slide = slide
                self.presenter_seen = self.clock()

    def evaluate_once(self):
        self.sync()
        with self.lock:
            now = self.clock()
            if self.slide >= len(self.deck.slides):
                self.reason = "Final slide — no automatic advance"
                self.processed = self.sequence
                return
            if self.sequence <= self.processed or now - self._last_inference < self.cfg.inference_interval:
                return
            fresh = [x for x in self.events if x["epoch"] == self.epoch and now - x["at"] <= self.cfg.transcript_window]
            if not fresh:
                self.processed = self.sequence
                return
            epoch, slide, sequence = self.epoch, self.slide, self.sequence
            previous_processed = self.processed
            new_speech = [x["text"] for x in fresh if x["sequence"] > self.processed]
            profile = self.bundle["transitions"][slide - 1]
            state = {"actual_slide": slide, "transition_profile": profile,
                     "current_slide": self.bundle["slide_understanding"][slide - 1],
                     "next_slide": self.bundle["slide_understanding"][slide],
                     "recent_transcript": " ".join(x["text"] for x in fresh)[-7000:],
                     "new_utterances": new_speech,
                     "newest_utterance": fresh[-1]["text"], "seconds_on_slide": round(now - self.slide_since, 1)}
            goals = self.bundle.get("completion_goals", [])
            state.update({"completion_goal": goals[slide - 1] if goals else None,
                          "slide_transcript": self.slide_memory.transcript,
                          "coverage_context_complete": self.slide_memory.complete})
            classifier = self.classifier
            payload_builder = getattr(classifier, "request_payload", None)
            payload = payload_builder(state) if callable(payload_builder) else None
            self.processed = sequence
            self._last_inference = now
        # Inference does not hold the UI/navigation lock.
        try:
            decision = classifier.classify(state)
        except Exception:
            decision = Decision(error="classifier_error")
        with self.lock:
            if self._stopped.is_set():
                return
            self.sync()
            now = self.clock()
            if epoch != self.epoch or slide != self.slide or now - self._last_inference > self.cfg.max_inference_age:
                self.gate.reset()
                self.reason = "Stale inference discarded"
                self.log("stale_inference", slide=slide, latency_ms=decision.latency_ms,
                         request=payload, response=asdict(decision), sequence=sequence)
                return
            if self.sequence > sequence:
                # Re-evaluate the whole pending batch: a correction may have
                # arrived while Jev was processing the earlier request.
                self.processed = previous_processed
                self.reason = "New speech arrived — checking the latest intent"
                self.log("superseded_inference", slide=slide, latency_ms=decision.latency_ms,
                         prompt_id=decision.prompt_id, request=payload, response=asdict(decision), sequence=sequence)
                self._wake.set()
                return
            self.latest = decision
            self.decision_slide = slide
            ready, reason = self.gate.observe(decision, sequence, now, self.switched_at)
            source = decision.advance_source
            if ready and source == "coverage" and (not goals or not self.slide_memory.complete):
                ready, reason = False, "Explanation assessment unavailable — completion goals or full speech memory are missing"
            if ready and source == "coverage" and self.partial:
                ready, reason = False, "Essential ideas covered — waiting for the current phrase to finish"
            advanced = False
            if ready and self.auto:
                if self.controller.requires_presenter and (self.presented_slide != slide or now - self.presenter_seen > 3):
                    ready, reason = False, "Open the presenter and keep this slide visible"
                    self.gate.reset()
                else:
                    if self.controller.advance_if_current(slide):
                        # Do not announce success until actual controller state confirms it.
                        actual = self.controller.current_slide()
                        if actual != slide + 1:
                            self.gate.reset()
                            raise RuntimeError("Slide advance was not confirmed by the controller.")
                        self._slide_changed(actual, "automatic_transition", source)
                        advanced = True
                    else:
                        ready, reason = False, "Slide changed before the command; action cancelled"
                        self.sync()
                        self.gate.reset()
            if not advanced:
                self.reason = reason if self.auto else "Suggestion only — automatic switching is off"
            self.log("inference", slide=slide, decision=decision.effective_label, transition=decision.label,
                     coverage=decision.coverage_label, coverage_confidence=decision.coverage_confidence,
                     coverage_error=decision.coverage_error, advance_source=source, confidence=decision.confidence,
                     p_next=decision.probabilities.get("NEXT"), latency_ms=decision.latency_ms,
                     action="NEXT" if advanced else "NONE", reason=reason if self.auto else self.reason,
                     model=decision.model, error=decision.error, prompt_id=decision.prompt_id,
                     utterance=fresh[-1]["text"], new_utterances=new_speech,
                     sequence=sequence, request=payload, response=asdict(decision))

    def _run(self):
        while not self._stopped.is_set():
            self._wake.wait(.25)
            self._wake.clear()
            try:
                self.evaluate_once()
            except Exception:
                with self.lock:
                    self.auto = False
                    self.gate.reset()
                    self.reason = "Controller/synchronization error — automatic switching paused"
                    self.log("controller_error", slide=self.slide)

    def snapshot(self):
        with self.lock:
            return {"slide": self.slide, "count": len(self.deck.slides), "epoch": self.epoch,
                    "auto": self.auto, "reason": self.reason,
                    "decision": {**asdict(self.latest), "effective_label": self.latest.effective_label,
                                 "advance_source": self.latest.advance_source},
                    "decision_slide": self.decision_slide,
                    "transcript": "\n".join(x["text"] for x in self.events), "partial": self.partial,
                    "logs": list(self.logs), "presenter_connected": self.presented_slide == self.slide and self.clock() - self.presenter_seen < 3}
