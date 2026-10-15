"""Step 4: counterfactual replay. Fork at break_idx and regenerate the rest under each arm."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from rich.console import Console

from . import dialogue, llm, store
from .config import get_settings
from .schemas import Diagnosis, Patch, Persona, ReplayRun, Transcript, Turn

console = Console()


def fork(t: Transcript, break_idx: int) -> list[Turn]:
    """History the replays start from: every turn BEFORE the break turn."""
    if not 0 <= break_idx < len(t.turns) or t.turns[break_idx].speaker != "bot":
        raise ValueError(f"{t.call_id}: break_idx {break_idx} is not a bot turn")
    return list(t.turns[:break_idx])


def seeds(n: int) -> list[int]:
    base = get_settings().random_seed
    return [base + i for i in range(n)]


def one_run(call_id: str, arm: str, bot_prompt: str, persona: Persona,
            history: list[Turn], seed: int, max_turns: int) -> ReplayRun:
    cust = lambda turns, s: dialogue.customer_reply(persona, turns, s)
    new, end = dialogue.run(bot_prompt, cust, history, max_turns, seed)
    return ReplayRun(call_id=call_id, arm=arm, seed=seed, turns=new, end_reason=end)


def run_jobs(jobs: list[tuple], max_turns: int) -> list[ReplayRun]:
    """jobs: (call_id, arm, bot_prompt, persona, history, seed). Failures are reported, not fatal."""
    out: list[ReplayRun] = []
    with ThreadPoolExecutor(max_workers=get_settings().max_concurrency) as pool:
        futs = [(j, pool.submit(one_run, *j, max_turns)) for j in jobs]
        for j, f in futs:
            try:
                out.append(f.result())
            except llm.BudgetExceeded:
                raise
            except Exception as e:
                console.print(f"[red]replay failed[/] {j[0]} {j[1]} seed={j[5]}: "
                              f"{type(e).__name__}: {str(e)[:160]}")
    return out


def _inputs(call_id: str) -> tuple[Transcript, Diagnosis, Persona, Patch]:
    t = store.load_transcript(call_id)
    d = store.load(call_id, "diagnosis.json", Diagnosis)
    p = store.load(call_id, "persona.json", Persona)
    pt = store.load(call_id, "patch.json", Patch)
    if not (d and p and pt):
        raise FileNotFoundError(f"{call_id}: run diagnose, clone and patch first")
    return t, d, p, pt


def replay(call_id: str, n: int | None = None, max_turns: int | None = None,
           force: bool = False) -> list[ReplayRun]:
    """Paired replays: the same seeds for both arms. Resumable: only missing (arm, seed) pairs run."""
    s = get_settings()
    n = n or s.replay_n
    max_turns = max_turns or s.replay_max_turns
    t, d, persona, pt = _inputs(call_id)
    history = fork(t, d.break_idx)
    prompts_by_arm = {
        "original": s.bot_prompt_path.read_text(encoding="utf-8"),
        "patched": pt.patched_prompt,
    }
    have = [] if force else (store.load_list(call_id, "replays.json", ReplayRun) or [])
    done = {(r.arm, r.seed) for r in have}
    jobs = [(call_id, arm, prompts_by_arm[arm], persona, history, sd)
            for sd in seeds(n) for arm in prompts_by_arm if (arm, sd) not in done]
    runs = have + run_jobs(jobs, max_turns)
    runs.sort(key=lambda r: (r.seed, r.arm))
    store.save(call_id, "replays.json", runs)
    return runs
