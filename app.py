from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import uuid

import streamlit as st

from slide_switcher.bridge import Bridge
from slide_switcher.config import GateSettings, ROOT, Settings, endpoint
from slide_switcher.controllers import BrowserController, PowerPointController, load_plugin
from slide_switcher.deck import prepare_deck
from slide_switcher.engine import PresentationEngine
from slide_switcher.jev import JevClient
from slide_switcher.history import read_activity, speech_rows, decision_rows
from slide_switcher.vision import VisionPreprocessor, validate_bundle

st.set_page_config(page_title="Jev · Slide Switcher", page_icon="🎙️", layout="wide", initial_sidebar_state="collapsed")
st.html((ROOT / "web/interface.css").read_text())
if "connections" not in st.session_state:
    st.session_state.connections = Settings.load()
if "gate_settings" not in st.session_state:
    st.session_state.gate_settings = GateSettings()
st.session_state.setdefault("controller_mode", "Browser presenter")
st.session_state.setdefault("controller_plugin", "")


def stop_session():
    if "engine" in st.session_state:
        st.session_state.engine.stop()
        st.session_state.bridge.stop()
        del st.session_state.engine
        del st.session_state.bridge


def start_session():
    deck = st.session_state.deck
    st.session_state.bundle = completion_goals(st.session_state.bundle, deck)
    mode = st.session_state.controller_mode
    if mode == "Windows PowerPoint":
        controller = PowerPointController(deck.source, len(deck.slides))
    elif mode == "Existing controller plugin":
        controller = load_plugin(st.session_state.controller_plugin, deck)
    else:
        controller = BrowserController(len(deck.slides))
    engine = PresentationEngine(deck, st.session_state.bundle, JevClient(st.session_state.connections), controller, st.session_state.gate_settings)
    bridge = Bridge(engine, st.session_state.connections.moonshine_root, st.session_state.connections.bridge_port)
    engine.start()
    st.session_state.engine, st.session_state.bridge = engine, bridge


def settings_menu():
    with st.popover("Settings", icon=":material/tune:"):
        with st.form("preferences_form"):
            connections, switching, presenter = st.tabs(["Connections", "Switching", "Presenter"])
            current, gate = st.session_state.connections, st.session_state.gate_settings
            with connections:
                db_key = st.text_input("Doubao key", type="password", placeholder="Using saved key" if current.doubao_key else "Add API key")
                db_model = st.text_input("Vision model", value=current.doubao_model)
                db_base = st.text_input("Doubao API endpoint", value=current.doubao_base)
                jev_key = st.text_input("Jev key", type="password", placeholder="Using saved key" if current.jev_key else "Add API key")
                jev_model = st.text_input("Decision model", value=current.jev_model)
            with switching:
                st.caption("A NEXT decision or a sufficiently explained slide advances automatically. Confidence and probability are for reference only.")
                cooldown = st.number_input("Navigation cooldown (seconds)", 0., 60., float(gate.cooldown_seconds))
            with presenter:
                modes = ["Browser presenter", "Windows PowerPoint", "Existing controller plugin"]
                mode = st.selectbox("Presenter", modes, index=modes.index(st.session_state.controller_mode))
                plugin = st.text_input("Controller plugin", value=st.session_state.controller_plugin, placeholder="module:factory")
                st.caption("Presenter changes take effect at the next session.")
            if st.form_submit_button("Save settings", type="primary", width="stretch"):
                try:
                    updated = replace(current, doubao_key=db_key or current.doubao_key, doubao_model=db_model,
                                      doubao_base=endpoint(db_base), jev_key=jev_key or current.jev_key, jev_model=jev_model)
                    updated_gate = replace(gate, cooldown_seconds=cooldown)
                    st.session_state.connections, st.session_state.gate_settings = updated, updated_gate
                    st.session_state.controller_mode, st.session_state.controller_plugin = mode, plugin
                    if "engine" in st.session_state:
                        engine = st.session_state.engine
                        engine.configure(JevClient(updated), updated_gate)
                    st.success("Settings saved.")
                except ValueError as error:
                    st.error(str(error))

with st.container(key="topbar"):
    brand, preferences = st.columns([6, 1], vertical_alignment="center")
    brand.markdown('<div class="brand">jev<i></i><small>Slide Switcher</small></div>', unsafe_allow_html=True)
    with preferences:
        settings_menu()


def steps(presenting=False):
    st.markdown(f'<div class="steps"><span class="{"" if presenting else "active"}"><em>{"✓" if presenting else "1"}</em>Prepare</span><span>—</span><span class="{"active" if presenting else ""}"><em>2</em>Present</span></div>', unsafe_allow_html=True)


def completion_goals(bundle, deck):
    if "completion_goals" not in bundle and st.session_state.connections.doubao_key:
        with st.spinner("Defining what each slide needs to explain…"):
            return VisionPreprocessor(st.session_state.connections).ensure_completion_goals(deck, bundle)
    return bundle


def prepare(content, name, demo=False):
    with st.spinner("Preparing your presentation…"):
        cfg = st.session_state.connections
        deck = prepare_deck(content, name, cfg.data_root, cfg.soffice)
        stop_session()
        st.session_state.deck = deck
        st.session_state.pop("bundle", None)
        saved = deck.directory / "transition_profiles.json"
        if demo and not saved.exists() and (ROOT / "examples/transition_profiles.json").exists():
            sample = validate_bundle(json.loads((ROOT / "examples/transition_profiles.json").read_text()), deck)
            saved.write_text(json.dumps(sample, ensure_ascii=False, indent=2))
        if saved.exists():
            st.session_state.bundle = completion_goals(validate_bundle(json.loads(saved.read_text()), deck), deck)
        elif cfg.doubao_key:
            st.session_state.bundle = VisionPreprocessor(cfg).analyze(deck)


def details():
    deck = st.session_state.deck
    with st.expander("Presentation details"):
        slides_tab, plan_tab, goals_tab, history_tab, files_tab = st.tabs(["Slides & notes", "Transition plan", "Slide goals", "Saved activity", "Files"])
        with slides_tab:
            selected = st.selectbox("Slide", list(range(1, len(deck.slides) + 1)), format_func=lambda i: f"{i}. {deck.slides[i-1].title}")
            slide = deck.slides[selected-1]
            st.image(str(deck.directory / slide.image), width="stretch")
            if slide.notes:
                st.caption("Speaker notes")
                st.write(slide.notes)
        with plan_tab:
            if "bundle" in st.session_state:
                bundle = st.session_state.bundle
                st.write(bundle["presentation_flow"])
                if bundle["transitions"]:
                    i = st.selectbox("Transition", list(range(len(bundle["transitions"]))), format_func=lambda i: f"Slide {i+1} → {i+2}")
                    st.write(bundle["transitions"][i]["transition_intent"])
                    with st.expander("Technical profile"):
                        st.json(bundle["transitions"][i])
        with goals_tab:
            bundle = st.session_state.get("bundle", {})
            if bundle.get("completion_goals"):
                number = st.selectbox("Goals for slide", list(range(1, len(deck.slides) + 1)))
                goal = bundle["completion_goals"][number - 1]
                st.caption("These ideas define enough explanation. Different wording is fine; optional details do not delay switching.")
                with st.form(f"completion_goal_{number}"):
                    message = st.text_area("Main message", goal["main_message"])
                    essential = st.text_area("Essential ideas — one per line (1–3)", "\n".join(goal["essential_points"]))
                    optional = st.text_area("Optional details — one per line", "\n".join(goal["optional_details"]))
                    if st.form_submit_button("Save slide goals", type="primary"):
                        try:
                            goals = [dict(x) for x in bundle["completion_goals"]]
                            goals[number - 1] = {"slide_number": number, "main_message": message,
                                "essential_points": [x.strip() for x in essential.splitlines() if x.strip()],
                                "optional_details": [x.strip() for x in optional.splitlines() if x.strip()]}
                            updated = validate_bundle({**bundle, "completion_goals": goals,
                                "completion_goals_review": {"by": "Presenter", "at": datetime.now(timezone.utc).isoformat(),
                                    "reason": "Reviewed in Slide goals"}}, deck)
                            if "engine" in st.session_state:
                                st.session_state.engine.update_bundle(updated)
                            st.session_state.bundle = updated
                            (deck.directory / "transition_profiles.json").write_text(json.dumps(updated, ensure_ascii=False, indent=2))
                            st.success("Slide goals saved.")
                        except ValueError as error:
                            st.error(str(error))
            else:
                st.caption("Completion goals are unavailable. Add a Doubao key and reanalyze to enable coverage-based switching.")
        with history_tab:
            records = read_activity(deck.directory)
            if records:
                st.caption("Recent activity saved on this laptop for this deck. Reloading the page keeps this history. Times are UTC.")
                rows = speech_rows(records)
                if rows:
                    st.dataframe(rows[-80:][::-1], width="stretch", hide_index=True)
                with st.expander("Saved decisions"):
                    st.dataframe(decision_rows(records)[-80:][::-1], width="stretch", hide_index=True)
                st.download_button("Download saved activity", "\n".join(json.dumps(x, ensure_ascii=False) for x in records),
                    file_name="saved_activity.jsonl", mime="application/x-ndjson")
            else:
                st.caption("Your finalized speech and decisions will be saved here when you rehearse with this deck.")
        with files_tab:
            if "bundle" in st.session_state:
                st.download_button("Download transition profiles", json.dumps(st.session_state.bundle, ensure_ascii=False, indent=2),
                                   file_name="transition_profiles.json", mime="application/json")
            profiles = st.file_uploader("Import saved profiles", type=["json"], max_upload_size=2)
            if profiles and st.button("Load profiles"):
                try:
                    bundle = completion_goals(validate_bundle(json.loads(profiles.getvalue()), deck), deck)
                    stop_session()
                    st.session_state.bundle = bundle
                    (deck.directory / "transition_profiles.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
                    st.rerun()
                except Exception as error:
                    st.error(str(error))
            if st.button("Reanalyze presentation", disabled=not st.session_state.connections.doubao_key):
                try:
                    with st.spinner("Updating the transition plan…"):
                        bundle = VisionPreprocessor(st.session_state.connections).analyze(deck, force=True)
                    stop_session()
                    st.session_state.bundle = bundle
                    st.rerun()
                except Exception as error:
                    st.error(str(error))


if "engine" not in st.session_state:
    if "deck" not in st.session_state:
        st.markdown('<div class="eyebrow">YOUR NEXT PRESENTATION</div>', unsafe_allow_html=True)
        st.title("Present without the clicks.")
        st.markdown('<div class="hero-copy">Add your PowerPoint. Let your words set the pace.</div>', unsafe_allow_html=True)
        with st.container(key="upload-card"):
            upload = st.file_uploader("PowerPoint presentation", type=["pptx"], label_visibility="collapsed")
            actions, example = st.columns([1, 1], vertical_alignment="center")
            begin = actions.button("Prepare presentation", type="primary", disabled=upload is None, width="stretch")
            demo = example.button("Try an example", type="tertiary", width="stretch")
            st.caption("Slide images and notes are analyzed by Doubao. Microphone audio stays on your device.")
        if begin or demo:
            try:
                if demo:
                    from scripts.create_demo import create_demo
                    sample = ROOT / "examples/solar_storage.pptx"
                    if not sample.exists(): create_demo(sample)
                    prepare(sample.read_bytes(), sample.name, demo=True)
                else:
                    prepare(upload.getvalue(), upload.name)
                st.rerun()
            except Exception as error:
                st.error(f"We couldn't prepare this presentation. {error}")
    else:
        steps()
        st.title("Your presentation is ready." if "bundle" in st.session_state else "One more step.")
        deck = st.session_state.deck
        preview, action = st.columns([1.5, 1], gap="large")
        with preview, st.container(key="deck-card"):
            st.image(str(deck.directory / deck.slides[0].image), width="stretch")
        with action, st.container(key="ready-copy"):
            name = Path(deck.filename).stem.replace("_", " ")
            st.markdown(f'<div class="deck-name">{escape(name)}</div><span class="pill">{len(deck.slides)} slides · {"Ready to present" if "bundle" in st.session_state else "Analysis needed"}</span>', unsafe_allow_html=True)
            st.write("")
            st.caption("Listen and switch slides from one screen. Automatic switching starts paused.")
            if "bundle" in st.session_state:
                if st.button("Start presenting", type="primary", width="stretch"):
                    try:
                        start_session()
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))
            else:
                if not st.session_state.connections.doubao_key:
                    st.caption("Add a Doubao key in Settings, or import a saved transition plan below.")
                if st.button("Analyze presentation", type="primary", disabled=not st.session_state.connections.doubao_key, width="stretch"):
                    try:
                        with st.spinner("Understanding the presentation…"):
                            st.session_state.bundle = VisionPreprocessor(st.session_state.connections).analyze(deck)
                        st.rerun()
                    except Exception as error:
                        st.error(str(error))
            if st.button("Choose a different deck", type="tertiary", width="stretch"):
                st.session_state.pop("deck", None)
                st.session_state.pop("bundle", None)
                st.rerun()
        details()
else:
    engine, bridge = st.session_state.engine, st.session_state.bridge
    steps(presenting=True)
    st.title(Path(engine.deck.filename).stem.replace("_", " "))

    @st.fragment(run_every=1)
    def live_controls():
        snap = engine.snapshot()
        with st.container(key="live-controls"):
            status, automatic, more = st.columns([4, 2, 1], vertical_alignment="center")
            if snap["auto"] and snap["presenter_connected"]:
                text, style = "Automatic switching on", ""
            elif snap["auto"]:
                text, style = "Waiting for the slide preview", "neutral"
            else:
                text, style = "You're in control · auto is off", "neutral"
            status.markdown(f'<span class="pill {style}">{text}</span>', unsafe_allow_html=True)
            if automatic.button("Pause auto" if snap["auto"] else "Turn on auto", type="secondary" if snap["auto"] else "primary", width="stretch"):
                engine.set_auto(not snap["auto"])
                st.rerun(scope="fragment")
            with more.popover("More", width="stretch"):
                destination = st.number_input("Jump to slide", 1, snap["count"], snap["slide"])
                if st.button("Go to slide"):
                    engine.goto(int(destination))
                    st.rerun(scope="fragment")
                st.link_button("Open projector view ↗", bridge.link("/present"))
                if st.button("Back to deck", type="tertiary"):
                    stop_session()
                    st.rerun()
            st.caption(snap["reason"])

    live_controls()
    left, right = st.columns([1.85, 1], gap="large")
    # Persistent frames sit outside timed fragments, preserving microphone state.
    with left, st.container(key="deck-card"):
        st.markdown(f'<iframe class="jev-presenter" title="Slide preview" src="{escape(bridge.embedded_link("/present?embedded=1"), quote=True)}" allow="fullscreen; cross-origin-isolated" allowfullscreen></iframe>', unsafe_allow_html=True)
    with right, st.container(key="listening-card"):
        st.markdown('<div class="section-label">YOUR MICROPHONE</div>', unsafe_allow_html=True)
        st.markdown(f'<iframe class="jev-microphone" title="Moonshine microphone" src="{escape(bridge.embedded_link("/mic/embed"), quote=True)}" allow="microphone; autoplay; cross-origin-isolated"></iframe>', unsafe_allow_html=True)

        @st.fragment(run_every=1)
        def transcript_panel():
            snap = engine.snapshot()
            with st.container(key="transcript"):
                st.markdown('<div class="section-label">LIVE TRANSCRIPT</div>', unsafe_allow_html=True)
                with st.container(height=175, autoscroll=True, border=False):
                    if snap["transcript"]:
                        st.write(snap["transcript"][-1400:])
                    elif not snap["partial"]:
                        st.caption("Your words will appear here.")
                    if snap["partial"]:
                        st.caption(snap["partial"])
                if snap["decision"]["error"]:
                    st.warning("Slide decisions are unavailable. Check the connection in Settings.")

        transcript_panel()

    with st.expander("Activity & tools"):
        @st.fragment(run_every=1)
        def activity():
            snap = engine.snapshot()
            a, b, c, d = st.columns(4)
            available = snap["decision_slide"] is not None
            a.metric("Suggestion", snap["decision"]["effective_label"] if available else "—")
            b.metric("Transition", snap["decision"]["label"] if available else "—")
            c.metric("Explanation", snap["decision"]["coverage_label"] if available else "—")
            confidence = snap["decision"]["confidence"]
            probability = snap["decision"]["probabilities"].get("NEXT")
            d.metric("Response time", f"{snap['decision']['latency_ms']:.0f} ms" if snap["decision_slide"] is not None else "—")
            if snap["decision_slide"] is not None:
                st.caption(f"Suggestion for slide {snap['decision_slide']}. Only confirmed advances appear as automatic transitions in the log.")
                if confidence is not None and probability is not None:
                    st.caption(f"Transition confidence {confidence:.0%} · P(NEXT) {probability:.0%}. Informational values only.")
                if snap["decision"]["coverage_error"]:
                    st.caption("Explanation assessment unavailable for this update; transition detection remains available.")
            if snap["logs"]:
                st.dataframe(decision_rows(snap["logs"])[-60:][::-1], width="stretch", hide_index=True)
                st.download_button("Download activity log", "\n".join(json.dumps(x, ensure_ascii=False) for x in snap["logs"]), file_name="presentation_log.jsonl", mime="application/x-ndjson")
            with st.form("manual_phrase", clear_on_submit=True):
                phrase = st.text_input("Rehearsal phrase", placeholder="Try a spoken transition without a microphone")
                if st.form_submit_button("Send phrase") and phrase.strip():
                    engine.add_transcript(phrase, "manual:" + uuid.uuid4().hex)
        activity()
    details()
