# NOTES — decisions and ambiguities

## Step 1: config / llm / schemas (2026-10-15)
- `.env` named `gemini-3.1-pro` for diagnose/patch/judge; that ID doesn't exist for this key. The
  startup check caught it, and it was changed to `gemini-3.1-pro-preview`.
- The key is free-tier: every pro-class model (`gemini-3.1-pro-preview`, `gemini-pro-latest`,
  `gemini-4-argon`) has a quota of **0**. `gemini-2.5-pro` returns 404. `gemini-3.7/3.8-flash`
  returned 503 at the time of testing. Working: `gemini-3.5-flash`, `gemini-3.5-flash-lite`.
- 429s that report a daily or zero quota (`limit: 0` / `PerDay`) are NOT retried; backoff can't fix
  them. Other 429s and 5xx back off exponentially (6 attempts).
- Cost is estimated from a substring price table in `llm.py` (flash-lite / flash / pro). Unknown
  models are priced like pro so the budget guard errs on the safe side. Thinking tokens count as output.
- The budget guard is per process (one `run-all` = one process). It checks before every call, so a
  run can overshoot by at most the in-flight concurrent calls.
- `taxonomy.yaml` is a 3-category stub (pushy_after_refusal, ignored_question, overpromise).
- Tests set `CALLDOCTOR_SKIP_MODEL_CHECK=1` so they never touch the network.
- Observed latency of about 25s per call on this key. Replay volume (N=10 × arms × turns) will be slow.
- Switched to the free tier on the user's call: DIAGNOSE/PATCH/JUDGE_MODEL=gemini-3.5-flash,
  REPLAY_N=4, MAX_CONCURRENCY=2. To restore the spec's setup once billing is on, set those three
  back to gemini-3.1-pro-preview and REPLAY_N=10. The judge (3.5-flash) is still larger than the
  bot (3.5-flash-lite) but is the same model as the customer simulator. This goes in the report's
  limitations block.

## Step 2: make-calls
- Added `dialogue.py` (a conversation engine shared with replay) and `store.py` (run-cache helpers).
  Neither is in the spec's layout; they keep make-calls and replay from duplicating logic.
- The bot needs a way to hang up, so a fixed call-system note (`prompts/bot_harness.md`) asking for
  `<END_CALL>` is appended to the bot prompt. It's identical for every arm and never part of the patch.
- Gemini needs the first message to come from the user, so the bot's view starts with a
  "[Call connected...]" kickoff message.
- Seeds: turn k of a dialogue uses `seed*1000 + k`, so the original and patched arms get identical
  per-turn seeds.
- Synthetic personas are hand-written in `synthetic_personas.yaml` (free, deterministic), not
  LLM-generated.
- Gemini 503 "high demand" spikes killed the first batch. Retries now run up to 12 attempts / 300s,
  and one failed call no longer sinks the batch.
- The bot says "[Customer Name] ji" because bot_prompt.md gives no name. Left as-is (it's the
  baseline under test).

## Step 3: diagnose
- Free tier is ~20 requests/day PER MODEL (gemini-3.5-flash is metered as gemini-3.6-flash and hit
  20/day). Roles are now spread across separate models to multiply quota: bot=3.5-flash-lite,
  customer=3-flash-preview, diagnose/patch=2.5-flash, judge=3.8-flash. Even so, a full run-all
  (hundreds of calls) does not fit the free tier; steps are verified on tiny runs until billing is on.
- If break_idx isn't a bot turn or a category is outside the taxonomy, diagnose does one semantic
  repair round-trip and then fails loudly.
- Known quality miss on the free model: in syn_fee_questioner_42, 2.5-flash put the break at turn 8
  (hidden charges) and missed the earlier overpromise at turn 6 ("bilkul mil jayega", "rate fixed
  rahega"). Re-check with a pro model once billing is on.

## Scope change requested by the user (2026-10-15)
- The user asked for a live web app deployable on Render with a polished frontend. This overrides the
  CLAUDE.md non-goals "no web server / no frontend framework". Plan: finish the pipeline first, then a
  FastAPI app (upload transcript → run pipeline as a background job → view report) behind
  an APP_PASSWORD, plus render.yaml. The report templates double as the app's UI (still no JS framework).
- Render free plan: ephemeral disk (runs/ is lost on redeploy/restart) and the instance sleeps after
  15 min idle (in-flight jobs die). A paid plan with a persistent disk fixes both.

## Step 4: clone
- The spec has no "clone" role, so the persona extractor uses the `diagnose` role (an analysis model).
- style_examples are checked to be exact customer substrings; one repair, then non-verbatim lines are dropped.
- Observed: 2.5-flash gave hindi_english_mix=0.2 for a speaker who is clearly ~50% Hindi. The
  verbatim style_examples anchor the voice anyway.

## Step 5: patch
- The LLM returns the full patched prompt; the unified diff is computed in code (difflib), so the diff
  always matches the prompt exactly.
- "At most ~8 lines" means max(added, removed) <= 8.
- Anti-overfitting is checked in code on ADDED lines only: (a) any 4-word n-gram shared with the
  transcript, excluding n-grams already in the original prompt; (b) numbers from the transcript that
  aren't in the original prompt. Rejected once with the reasons fed back, then fail.
- The patcher sees the category definition and diagnosis rationale, not the raw transcript, which
  reduces copying.

## Step 6: replay
- Fork = turns[:break_idx]; break_idx must be a bot turn. The bot regenerates from break_idx on.
- Replays are resumable: only missing (arm, seed) pairs are run, so a quota cut-off mid-run loses nothing.
- Verified on the free tier with `--n 1 --max-turns 6` only.
- First finding (n=1): the busy_refuser patch kept "you may briefly re-explain benefits once", the
  patched bot still pitched after a clear refusal, and the customer hung up in both arms. The replay
  disproved the fix, which is exactly what it is for.

## Step 7: score
- Blinding: the judge sees only "CONTEXT" + "CONTINUATION" text. run_id is an opaque sha1 of
  (tag, call, arm, seed), judging order is a seeded shuffle, and judge.md contains none of
  original/patched/patch/baseline/arm (checked by test).
- Field order in the judge schema puts `reasons` first, so reasoning is generated before scores.
- composite = 2*next_step + recovery + human_likeness - 5 if compliance fails. The paired win rate
  compares composites on the same seed; ties count 0.5.
- Regression: this call's patched prompt is replayed on the next REGRESSION_CALLS diagnosed+cloned
  calls (both arms, max(1, N/2) seeds, reusing that call's own original-arm replays when cached).
  delta = mean composite(patched) - mean composite(original), averaged over those calls.
- Bug fixed: concurrent threads raced to build the Gemini client, and the GC'd duplicate closed its
  HTTP client mid-request. Client creation is now under the lock.
- Free tier: the judge (3.8-flash) hit 503s and then its daily quota; the original-arm score for
  busy_refuser seed 42 is still pending and fills in on the next `score` run.

## Step 8: report
- The templates (`templates/base.html`, `call.html`, `index.html`, `_macros.html`, `_style.css`) are
  shared with the web app. A `Urls` object switches links between static files and app routes.
- No external assets: system font stack, inline CSS, and a tiny inline theme-toggle script (the spec
  says "no external JS"; inline is allowed). Light/dark follows the OS, with a manual toggle.
- arm_metrics now always returns every key (None when n=0), so half-scored calls render.
- The extra limitations shown on the index are generated from config (flash judge, judge==customer, N<5).

## Step 9-10: judge-check, run-all
- judge-check keeps the human blind to the arm too. It saves after every item and skips run_ids
  already checked. Agreement = share of agreed dimensions across all items.
- The tool was demoed with piped test answers; that file was deleted so no fake agreement reaches
  the index. Real agreement needs the user to run `judge-check`.
- run-all runs diagnose+clone for ALL calls first, so regression targets exist, then
  patch/replay/score per call. Per-call failures are isolated; reports are always written, even when
  the budget guard trips.
- `pipeline.py` (not in the spec layout) holds this logic so the web app can reuse it.

## Steps 11-12: transcribe, voice
- No ffmpeg on this machine, or on Render's native Python runtime. TTS is requested as raw
  `pcm_24000`, which pydub can stitch without ffmpeg. Export is mp3 when ffmpeg is on PATH, else wav.
  The report's <audio> tag plays either.
- Per-turn TTS cache: reports/audio/cache/<sha256(text|voice|model)>.pcm.
- Audio = the customer line just before the fork + the continuation, so the listener hears what the
  bot is responding to.
- Voices: the user's ElevenLabs library has no Indian-accented voices (free plan, 10k chars/month).
  Defaults are Bella (bot) and Chris (customer); swap in Indian voices from the Voice Library via .env.
- transcribe: audio_event words are dropped, spacing is kept, and every non-bot speaker id maps to
  "customer". Verified by unit tests only (no recordings/ provided yet).
- voice is verified with a 2-turn live TTS smoke test. A full before/after needs a scored replay
  pair, which is waiting on Gemini quota.
