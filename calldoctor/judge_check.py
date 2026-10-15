"""Step 8: human spot-check of the LLM judge. Agreement % feeds the index page."""
from __future__ import annotations

import json
import random
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from . import replay, store
from .config import get_settings
from .schemas import Diagnosis, ReplayRun, Score
from .score import run_id

console = Console()
DIMS = ("next_step_reached", "recovery", "human_likeness", "compliance_pass")
CONTEXT_TURNS = 4


def _path() -> Path:
    return get_settings().runs_dir / "judge_check.json"


def load() -> dict:
    p = _path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}


def candidates() -> list[dict]:
    """Every judged replay, with the context the judge saw. Arm is kept out, so the human is blind too."""
    out = []
    for cid in store.transcript_ids():
        d = store.load(cid, "diagnosis.json", Diagnosis)
        runs = store.load_list(cid, "replays.json", ReplayRun) or []
        scores = {s.run_id: s for s in (store.load_list(cid, "scores.json", Score) or [])}
        if not d:
            continue
        history = replay.fork(store.load_transcript(cid), d.break_idx)
        for r in runs:
            if (s := scores.get(run_id(r))):
                out.append({"call_id": cid, "history": history, "run": r, "score": s})
    return out


def sample(k: int, already: set[str]) -> list[dict]:
    pool = [c for c in candidates() if c["score"].run_id not in already]
    random.Random(get_settings().random_seed).shuffle(pool)
    return pool[:k]


def agreement(items: list[dict]) -> float | None:
    marks = [v for it in items for v in it["agree"].values()]
    return sum(marks) / len(marks) if marks else None


def _ask(prompt: str) -> bool:
    while True:
        a = console.input(f"  {prompt} agree? [bold](y/n)[/] ").strip().lower()
        if a in ("y", "n"):
            return a == "y"


def run(k: int | None = None) -> dict:
    data = load()
    done = {it["run_id"] for it in data["items"]}
    todo = sample(k or get_settings().judge_check_k, done)
    if not todo:
        console.print("[yellow]No unchecked judged runs. Run replay + score first.[/]")
    for i, c in enumerate(todo, 1):
        ctx = "\n".join(f"[dim]{t.speaker.upper()}: {t.text}[/]" for t in c["history"][-CONTEXT_TURNS:])
        cont = "\n".join(f"[bold]{t.speaker.upper()}:[/] {t.text}" for t in c["run"].turns)
        console.print(Panel(f"{ctx}\n[dim]── continuation ──[/]\n{cont}", title=f"{i}/{len(todo)}"))
        s: Score = c["score"]
        console.print(f"[cyan]Judge reasons:[/] {s.reasons}")
        console.print(f"[cyan]Verdict:[/] next_step={s.next_step_reached} recovery={s.recovery}/2 "
                      f"human_likeness={s.human_likeness}/2 compliance_pass={s.compliance_pass} "
                      f"{s.compliance_issues or ''}")
        agree = {d: _ask(f"{d}={getattr(s, d)}") for d in DIMS}
        data["items"].append({"run_id": s.run_id, "call_id": c["call_id"], "agree": agree})
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")  # save after every item
    a = agreement(data["items"])
    console.print(f"[bold]Judge agreement:[/] {'n/a' if a is None else f'{a:.0%}'} "
                  f"over {len(data['items'])} items")
    return data
