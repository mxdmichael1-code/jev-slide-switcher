# Exploring JEV with a PowerPoint rehearsal

When I rehearse a talk, I want the next slide to appear after I’ve explained the current one. I wanted to see whether JEV could make that judgment from the speech and the slide’s purpose.

## From a command to a judgment

The first version looked for requests to advance or signs of a topic handoff. In rehearsal, I could finish explaining a slide without giving either cue. I added a second question for JEV: has the speaker covered the essential ideas on this slide? Each slide has editable goals, and the app keeps track of finalized phrases across the slide so an idea can be explained over several sentences.

- LibreOffice renders the PowerPoint. Doubao drafts slide goals and transition profiles from the deck and speaker notes.
- Moonshine runs speech recognition in the browser. JEV receives finalized transcript text and slide context and returns structured transition and coverage judgments.
- The app checks for explicit holds, stale results, and recent navigation before advancing. Manual navigation resets the current slide’s transcript context.

The app runs locally. Deck preparation sends slide images and context to Doubao. Automatic switching sends transcript text and slide context to JEV. Decks, prepared profiles, and rehearsal activity are stored locally under `data/` by default.

## Run locally

Requires Python 3.10+, LibreOffice, and a local Moonshine v0.1.5 web project for microphone input. Set `MOONSHINE_ROOT` to the directory containing `stt/index.html`, `assets/`, `language-bindings/wasm/`, and `test-assets/`. See [.env.example](.env.example) for configuration. Use your own JEV key for automatic switching and a vision-capable Doubao model and key to prepare new decks. Set `SOFFICE_PATH` if the app can’t find LibreOffice.

```bash
./run.sh setup
# Edit .env with the paths and credentials for the features you plan to use.
./run.sh app
```

Open [127.0.0.1:8502](http://127.0.0.1:8502/). Choose **Try an example** to load the bundled synthetic deck and its prepared profiles, or upload your own PowerPoint and choose **Prepare presentation**. Review the editable goals in **Presentation details → Slide goals**. Start presenting and turn on automatic switching. You can type rehearsal phrases in **Activity & tools** to exercise switching without a microphone.

## Checks

```bash
./run.sh test
```

The automated tests cover response parsing, incomplete explanations, duplicate speech, stale judgments, manual navigation, and preparation and cache behavior. The scripts in `scripts/` offer optional live checks against the synthetic deck. The live checks use configured provider credentials and may incur API usage. They exercise selected cases from development.

## Related work

[Embedding Models & Fine-tuning](https://mxdlalala.web.app/projects/fine-tuning/) explores a related question on debate feedback: can a model pick up which side a sentence favors, and how strongly?
