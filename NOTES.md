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
