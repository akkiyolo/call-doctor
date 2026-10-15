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
