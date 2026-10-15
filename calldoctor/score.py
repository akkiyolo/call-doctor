"""Step 5: blind LLM-as-judge scoring, paired metrics, and the regression check."""
from __future__ import annotations

import hashlib
import random
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel
from rich.console import Console

from . import llm, prompts, replay, store
from .config import get_settings
from .schemas import Patch, ReplayRun, Score, Turn

console = Console()
ARM_WORDS = ("original", "patched", "patch", "baseline", "arm")


class _JudgeOut(BaseModel):  # reasons first: reasoning before verdict
    reasons: str
    next_step_reached: bool
    recovery: int
    human_likeness: int
    compliance_pass: bool
    compliance_issues: list[str]


def run_id(r: ReplayRun, tag: str = "") -> str:
    """Opaque id. Recomputable from (call, arm, seed) for unblinding, but leaks nothing to the judge."""
    return hashlib.sha1(f"{tag}|{r.call_id}|{r.arm}|{r.seed}".encode()).hexdigest()[:12]


def _fmt(turns: list[Turn]) -> str:
    return "\n".join(f"{t.speaker.upper()}: {t.text}" for t in turns) or "(empty)"


def judge_input(history: list[Turn], continuation: list[Turn]) -> str:
    """The ONLY run-specific text the judge sees. No arm, seed, run id or call id."""
    return f"CONTEXT\n{_fmt(history)}\n\nCONTINUATION\n{_fmt(continuation)}"


def judge(history: list[Turn], r: ReplayRun, rid: str) -> Score:
    system = prompts.render("judge.md")
    msg = [{"role": "user", "text": judge_input(history, r.turns)}]
    out = llm.complete("judge", system, msg, schema=_JudgeOut, seed=get_settings().random_seed)
    return Score(
        run_id=rid, reasons=out.reasons, next_step_reached=out.next_step_reached,
        recovery=max(0, min(2, out.recovery)), human_likeness=max(0, min(2, out.human_likeness)),
        compliance_pass=out.compliance_pass, compliance_issues=out.compliance_issues,
    )


def score_runs(history: list[Turn], runs: list[ReplayRun], have: dict[str, Score],
               tag: str = "") -> dict[str, Score]:
    """Judge every unscored run in a seeded shuffled order so arm order can't leak via position."""
    todo = [r for r in runs if run_id(r, tag) not in have]
    random.Random(get_settings().random_seed).shuffle(todo)
    out = dict(have)
    with ThreadPoolExecutor(max_workers=get_settings().max_concurrency) as pool:
        futs = [(r, pool.submit(judge, history, r, run_id(r, tag))) for r in todo]
        for r, f in futs:
            try:
                sc = f.result()
                out[sc.run_id] = sc
            except llm.BudgetExceeded:
                raise
            except Exception as e:
                console.print(f"[red]judge failed[/] {r.call_id} seed={r.seed}: {str(e)[:160]}")
    return out


# ---------- metrics ----------

def composite(s: Score) -> float:
    """Per-run value for paired comparison. A compliance failure outweighs everything else."""
    return 2 * s.next_step_reached + s.recovery + s.human_likeness - (0 if s.compliance_pass else 5)


METRIC_KEYS = ("next_step_rate", "mean_recovery", "mean_human_likeness",
               "compliance_pass_rate", "mean_composite")


def arm_metrics(scores: list[Score]) -> dict:
    n = len(scores)
    if not n:
        return {"n": 0, **{k: None for k in METRIC_KEYS}}
    return {
        "n": n,
        "next_step_rate": sum(s.next_step_reached for s in scores) / n,
        "mean_recovery": sum(s.recovery for s in scores) / n,
        "mean_human_likeness": sum(s.human_likeness for s in scores) / n,
        "compliance_pass_rate": sum(s.compliance_pass for s in scores) / n,
        "mean_composite": sum(composite(s) for s in scores) / n,
    }


def paired_win_rate(pairs: list[tuple[Score, Score]]) -> float | None:
    """pairs: (original, patched) on the same seed. Ties count half."""
    if not pairs:
        return None
    w = sum(1.0 if composite(p) > composite(o) else 0.5 if composite(p) == composite(o) else 0.0
            for o, p in pairs)
    return w / len(pairs)


def _by_arm(runs: list[ReplayRun], scores: dict[str, Score], tag: str = ""):
    arms = {"original": {}, "patched": {}}
    for r in runs:
        if (s := scores.get(run_id(r, tag))):
            arms[r.arm][r.seed] = s
    pairs = [(arms["original"][sd], arms["patched"][sd])
             for sd in sorted(arms["original"]) if sd in arms["patched"]]
    return arms, pairs


# ---------- regression ----------

def regression_targets(call_id: str, k: int) -> list[str]:
    """The next k other calls (cyclic, sorted) that have been diagnosed and cloned."""
    ids = store.transcript_ids()
    ready = [c for c in ids if c != call_id
             and (store.run_dir(c) / "diagnosis.json").exists()
             and (store.run_dir(c) / "persona.json").exists()]
    if call_id in ids:
        start = ids.index(call_id)
        ready.sort(key=lambda c: (ids.index(c) - start) % len(ids))
    return ready[:k]


def regression(call_id: str, n_seeds: int) -> dict:
    """Run THIS call's patched prompt on other calls' forks (both arms, same seeds) and judge them."""
    from .schemas import Diagnosis, Persona

    s = get_settings()
    patched = store.load(call_id, "patch.json", Patch).patched_prompt
    original = s.bot_prompt_path.read_text(encoding="utf-8")
    runs = store.load_list(call_id, "regression_runs.json", ReplayRun) or []
    scores = {x.run_id: x for x in (store.load_list(call_id, "regression_scores.json", Score) or [])}
    result = {}
    for other in regression_targets(call_id, s.regression_calls):
        t = store.load_transcript(other)
        d = store.load(other, "diagnosis.json", Diagnosis)
        persona = store.load(other, "persona.json", Persona)
        history = replay.fork(t, d.break_idx)
        done = {(r.call_id, r.arm, r.seed) for r in runs}
        # reuse the other call's own original-arm replays when they exist (same seeds, same prompt)
        own = {(r.arm, r.seed): r for r in (store.load_list(other, "replays.json", ReplayRun) or [])}
        jobs = []
        for sd in replay.seeds(n_seeds):
            if (other, "original", sd) not in done:
                if ("original", sd) in own:
                    runs.append(own[("original", sd)])
                else:
                    jobs.append((other, "original", original, persona, history, sd))
            if (other, "patched", sd) not in done:
                jobs.append((other, "patched", patched, persona, history, sd))
        runs += replay.run_jobs(jobs, s.replay_max_turns)
        mine = [r for r in runs if r.call_id == other]
        scores = score_runs(history, mine, scores, tag=f"reg:{call_id}")
        arms, pairs = _by_arm(mine, scores, tag=f"reg:{call_id}")
        o, p = arm_metrics(list(arms["original"].values())), arm_metrics(list(arms["patched"].values()))
        result[other] = {
            "original": o, "patched": p, "win_rate": paired_win_rate(pairs),
            "delta_composite": (p["mean_composite"] - o["mean_composite"]) if o["n"] and p["n"] else None,
        }
    store.save(call_id, "regression_runs.json", runs)
    store.save(call_id, "regression_scores.json", list(scores.values()))
    return result


# ---------- step entry ----------

def score(call_id: str, force: bool = False, with_regression: bool = True) -> dict:
    from .schemas import Diagnosis, Transcript

    t: Transcript = store.load_transcript(call_id)
    d = store.load(call_id, "diagnosis.json", Diagnosis)
    runs = store.load_list(call_id, "replays.json", ReplayRun)
    if not (d and runs):
        raise FileNotFoundError(f"{call_id}: run diagnose and replay first")
    history = replay.fork(t, d.break_idx)
    have = {} if force else {x.run_id: x for x in (store.load_list(call_id, "scores.json", Score) or [])}
    scores = score_runs(history, runs, have)
    store.save(call_id, "scores.json", list(scores.values()))

    arms, pairs = _by_arm(runs, scores)
    summary = {
        "call_id": call_id,
        "original": arm_metrics(list(arms["original"].values())),
        "patched": arm_metrics(list(arms["patched"].values())),
        "paired_win_rate": paired_win_rate(pairs),
        "n_pairs": len(pairs),
        "regression": {},
    }
    if with_regression:
        n_seeds = max(1, len({r.seed for r in runs}) // 2)
        summary["regression"] = regression(call_id, n_seeds)
    deltas = [v["delta_composite"] for v in summary["regression"].values() if v["delta_composite"] is not None]
    summary["regression_delta"] = sum(deltas) / len(deltas) if deltas else None
    store.save(call_id, "summary.json", summary)
    return summary
