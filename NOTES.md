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
