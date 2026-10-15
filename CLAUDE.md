# Call Doctor — Build Spec

You are building **Call Doctor**: a tool for outbound Hinglish loan-sales voice-bot transcripts. It
(1) finds where a call broke, (2) proposes a minimal prompt fix, and (3) proves or disproves the fix with
**counterfactual replay**. That means forking the call at the break point, cloning the real customer
into a simulator, and replaying the rest of the call many times with the old prompt vs the patched prompt.
Then it reports the results as static HTML.

Deadline: working end-to-end in ~3 hours of build time. Simple, correct and working beats clever.
Every step must be runnable and inspectable on its own.

---

## Non-goals (do NOT build)
- No telephony, live/real-time audio or web server. The core pipeline is text-only. Audio is used in
  exactly two places, both via ElevenLabs: transcribing real call recordings in (`transcribe`), and
  rendering selected replays to audio for the report (`voice`).
- No database, no frontend framework, no auth, no Docker.
- No features beyond this spec. If something is ambiguous, pick the simpler option and log it in `NOTES.md`.

---

## Stack
- Python 3.11, `uv` or `pip` + `requirements.txt`.
- Libraries: `google-genai` (the current official Gemini SDK, NOT the deprecated
  `google-generativeai`), `elevenlabs`, `pydantic`, `python-dotenv`, `typer`, `jinja2`, `tenacity`,
  `rich`, `pydub` (stitching audio clips; needs ffmpeg).
- LLM: Gemini only. Audio: ElevenLabs only (Scribe for speech-to-text, TTS for rendering).
- All config comes from `.env` (see `.env`). Never hardcode keys, model names or voice IDs.
- On startup, `config.py` calls the Gemini models list endpoint and verifies every configured model
  ID exists. If one doesn't, fail fast and print the available model IDs, so a wrong name costs
  seconds, not a debugging session.

## Repo layout
```
calldoctor/
  config.py        # loads .env, typed settings
  llm.py           # ONE Gemini wrapper: complete(role, system, messages, schema=None) -> text | pydantic obj
                   # role in {bot, customer, diagnose, patch, judge} -> picks model + temperature from env
                   # system prompt via system_instruction; structured output via response_mime_type
                   # "application/json" + response_schema from the pydantic model
                   # retries with backoff on 429/5xx (tenacity), token + cost logging from usage_metadata,
                   # global budget guard (MAX_SPEND_USD), also treat safety-blocked/empty responses as retryable once
  audio.py         # ElevenLabs: transcribe(path) via Scribe with diarization; tts(text, voice_id) -> mp3 bytes
  schemas.py       # all pydantic models below
  diagnose.py
  clone.py
  patch.py
  replay.py
  score.py
  report.py
  judge_check.py
  cli.py           # typer app: transcribe | make-calls | diagnose | clone | patch | replay | score |
                   #            voice | report | run-all | judge-check
prompts/           # every internal prompt as a .md file, loaded at runtime (never inline long prompts in code)
templates/         # jinja2 HTML templates
transcripts/       # input calls: <call_id>.json
runs/<call_id>/    # cached outputs per step (json), so any step can be rerun alone
reports/           # generated HTML + reports/audio/*.mp3
recordings/        # input call audio (e.g. Arrowhead's sample calls) for `transcribe`
bot_prompt.md      # baseline loan-sales bot system prompt (provided)
taxonomy.yaml      # failure taxonomy (written by the human; create a stub with 3 example categories if missing)
NOTES.md
README.md
```

---

## Data contracts (`schemas.py`)
- `Turn {idx:int, speaker:"bot"|"customer", text:str}`
- `Transcript {call_id:str, source:"real"|"roleplay"|"synthetic", turns:list[Turn]}`
- `TurnLabel {idx, categories:list[str], evidence:str, severity:0-3}`
- `Diagnosis {call_id, labels:list[TurnLabel], break_idx:int, break_category:str, rationale:str}`
- `Persona {goals, objections:list[str], patience:0-3, verbosity:0-3, hindi_english_mix:float 0-1,
  interrupts:bool, trust_level:0-3, will_convert_if:str, hard_stop_if:str, style_examples:list[str]}`
- `Patch {call_id, target_category, diff:str, patched_prompt:str, rationale:str}`
- `ReplayRun {call_id, arm:"original"|"patched", seed:int, turns:list[Turn], end_reason:str}`
- `Score {run_id, reasons:str, next_step_reached:bool, recovery:0-2, human_likeness:0-2,
  compliance_pass:bool, compliance_issues:list[str]}`

All LLM steps that return structure MUST use schema-constrained/JSON output validated by pydantic,
with one automatic repair retry on validation failure.

---

## Pipeline

### 0a. `transcribe` (ElevenLabs Scribe)
- `transcribe recordings/<file>` → `transcripts/<call_id>.json` with `source:"real"`.
- Use the batch Scribe model from env (`ELEVENLABS_STT_MODEL`) with speaker diarization on and the
  language left to auto-detect (code-mixed Hindi/English must not be forced to one language).
- Map the two diarized speakers to bot/customer: the speaker with the first turn is assumed to be the
  bot (outbound call), and the human can override with `--bot-speaker`.
- Merge consecutive segments from the same speaker into one turn. Keep text exactly as transcribed.

### 0b. `make-calls` (support step, build FIRST after config/llm)
- Runs the baseline bot (`bot_prompt.md`, role=bot) against a synthetic customer persona to produce
  example transcripts in `transcripts/` with `source:"synthetic"`.
- Also an interactive mode: `make-calls --interactive` lets the human type as the customer in the
  terminal to produce `source:"roleplay"` calls. This is the main way real-ish data gets in.

### 1. `diagnose`
- Input: transcript + `taxonomy.yaml`.
- Label each bot turn with zero or more taxonomy categories, a verbatim evidence snippet and a severity.
- `break_idx` = the FIRST bot turn whose failure plausibly changed the call's trajectory (not just the
  worst one). It must justify this in `rationale`.
- Prompt must include the taxonomy definitions and examples verbatim and instruct: "label only what is
  evidenced in the text; do not infer tone you cannot see."

### 2. `clone`
- Extract a `Persona` from the customer's turns ONLY (ignore the bot's claims about the customer).
- `style_examples`: 3–5 short verbatim customer lines to anchor voice and code-mixing.
- The customer simulator prompt (`prompts/customer_sim.md`) must:
  - stay in character, reproduce the language mix and verbosity, and never become suddenly cooperative;
  - never reveal it is an AI or reference "persona", "simulation" or prompts;
  - follow `will_convert_if` / `hard_stop_if` conditions strictly;
  - end the call naturally (hang up, agree to a callback, accept a link) and emit
    `<END reason="...">` on its last turn.

### 3. `patch`
- Produce a MINIMAL edit to `bot_prompt.md` that targets the diagnosed category, as a unified diff
  plus the full patched prompt.
- Anti-overfitting rules (enforce in the prompt and check in code):
  - the patch must be a general behaviour rule, not mention this customer, their name, amounts or
    specific lines;
  - the diff may change at most ~8 lines;
  - reject and retry once if the patch contains any 4+ word string copied from the transcript.

### 4. `replay`
- Fork: keep turns `0 .. break_idx-1` as history. Regenerate from the bot's turn at `break_idx` onward.
- Arms: `original` (baseline prompt) and `patched`. N runs per arm (env `REPLAY_N`, default 10),
  max turns per continuation (env `REPLAY_MAX_TURNS`, default 12).
- Use the SAME seeds/sampling settings across arms for paired comparison. Run concurrently with a
  semaphore (env `MAX_CONCURRENCY`).
- Stop on `<END ...>`, max turns, or the bot ending the call.

### 5. `score`
- LLM-as-judge with the rubric in `prompts/judge.md`. The judge must:
  - be BLIND to the arm. Strip arm labels, shuffle run order, and never mention "patch" or "original";
  - write `reasons` BEFORE the scores (reasoning first, then verdict);
  - use anchored definitions for each score level (0/1/2), written out in the prompt;
  - judge only the continuation, with the pre-fork history given as context.
- Judge uses the strongest configured Gemini model (env `JUDGE_MODEL`), and the bot uses a smaller
  one, to reduce self-preference bias. Everything is Gemini, so this bias is not eliminated; say so in
  the limitations block, and treat the human judge-check agreement as the real validity evidence.
- Compliance fails if the bot: makes misleading or guaranteed rate/approval claims, keeps pushing
  after a clear refusal, ignores a do-not-call request, denies being an AI when sincerely asked, or
  asks for OTP/PIN/CVV/full card number.

### 6. `regression` (inside `score`)
- Also run the patched prompt on 2 OTHER transcripts' forks (N/2 runs each) to check the fix doesn't
  break unrelated calls. Report the delta.

### 6b. `voice` (ElevenLabs TTS)
- For each call, render to mp3: the original continuation and the best patched continuation (matched
  seed), so a reviewer can LISTEN to the before/after.
- Two voices from env: `ELEVENLABS_BOT_VOICE_ID` and `ELEVENLABS_CUSTOMER_VOICE_ID`. Model from
  `ELEVENLABS_TTS_MODEL`. Synthesize each turn, insert ~300ms silence between turns, stitch with pydub.
- Cache per turn by hash(text + voice + model) so reruns cost nothing.
- Only render calls listed in `VOICE_CALLS` (comma-separated ids) or `--top K` by improvement, to save
  credits. Default K=3.

### 7. `report`
- Per call: `reports/<call_id>.html`
  - transcript with the break turn highlighted and turn labels as chips;
  - diagnosis rationale;
  - prompt diff (rendered, red/green);
  - results table per arm: next-step rate, mean recovery, mean human-likeness, compliance pass rate,
    plus a paired win rate (patched beats original on same seed) and the regression delta;
  - best and worst patched transcript, side by side with the matched original;
  - if `voice` ran for this call: two `<audio>` players ("Before" / "After") above the transcripts.
- `reports/index.html`: all calls, aggregate table, judge agreement % (from `judge_check`), total
  API cost, and a short limitations block (simulated customers approximate real ones; text-only, so
  no audio timing/prosody; judge is an LLM with measured agreement).
- Clean, readable, no external JS. Inline CSS. Works offline. Light and dark mode.

### 8. `judge-check`
- Terminal tool: shows K random (transcript excerpt, judge verdict) pairs, the human marks
  agree/disagree per dimension, results saved to `runs/judge_check.json`, agreement % shown in index.

### `run-all`
- diagnose → clone → patch → replay → score → report for every transcript, skipping cached steps
  unless `--force`. Prints a rich summary table and total cost at the end.

---

## Engineering rules
- Every LLM call logged to `runs/llm_log.jsonl` (role, model, prompt hash, tokens, cost, latency).
- Budget guard: abort cleanly if estimated spend exceeds `MAX_SPEND_USD`.
- Deterministic where possible: fixed seeds, `temperature` per role from env.
- Hinglish: keep text exactly as written (Roman script Hindi, Devanagari, or mixed). Never translate.
- Unit tests (pytest) for: schema validation, fork logic (history slice correct), the diff
  anti-overfitting check, judge blinding (no arm label leaks into the judge input), and report rendering
  on a fixture.

## Process (follow exactly)
1. Build in this order: config/llm/schemas → make-calls → diagnose → clone → patch → replay → score →
   report → judge-check → run-all → transcribe → voice. The audio steps come last so an ElevenLabs
   issue can never block the core pipeline.
2. After EACH step: run it on `transcripts/` (generate 2 synthetic calls first if empty), show me the
   output, run tests, `git commit`.
3. Keep moving. If blocked >10 minutes on one thing, choose the simpler path and note it in `NOTES.md`.
4. Finish with README: the idea in 3 lines, ASCII pipeline diagram, how to run, results table,
   judge agreement, limitations.

## Definition of done
`python -m calldoctor.cli run-all` on 10+ transcripts produces `reports/index.html` with per-call
reports, paired before/after numbers, a regression check, judge agreement and total cost, plus
before/after audio for the top 3 calls. Tests pass.
