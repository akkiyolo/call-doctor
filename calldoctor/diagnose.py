"""Step 1: label bot turns with taxonomy categories and find the break turn."""
from __future__ import annotations

import yaml
from pydantic import BaseModel
from rich.console import Console

from . import llm, prompts, store
from .config import get_settings
from .schemas import Diagnosis, Transcript, TurnLabel

console = Console()


class _DiagOut(BaseModel):
    labels: list[TurnLabel]
    break_idx: int
    break_category: str
    rationale: str


def format_transcript(t: Transcript) -> str:
    return "\n".join(f"[{x.idx}] {x.speaker.upper()}: {x.text}" for x in t.turns)


def category_ids() -> list[str]:
    tax = yaml.safe_load(get_settings().taxonomy_path.read_text(encoding="utf-8"))
    return [c["id"] for c in tax["categories"]]


def _problems(out: _DiagOut, t: Transcript, cats: list[str]) -> list[str]:
    bot_idx = {x.idx for x in t.turns if x.speaker == "bot"}
    probs = []
    if out.break_idx not in bot_idx:
        probs.append(f"break_idx {out.break_idx} is not a bot turn; bot turns are {sorted(bot_idx)}")
    if out.break_category not in cats:
        probs.append(f"break_category {out.break_category!r} not in taxonomy {cats}")
    for lab in out.labels:
        bad = [c for c in lab.categories if c not in cats]
        if bad:
            probs.append(f"label idx {lab.idx} uses unknown categories {bad}")
    return probs


def diagnose(call_id: str, force: bool = False) -> Diagnosis:
    if not force and (cached := store.load(call_id, "diagnosis.json", Diagnosis)):
        return cached
    s = get_settings()
    t = store.load_transcript(call_id)
    cats = category_ids()
    system = prompts.render(
        "diagnose.md", taxonomy=s.taxonomy_path.read_text(encoding="utf-8")
    )
    msgs = [{"role": "user", "text": "TRANSCRIPT\n" + format_transcript(t)}]
    out = llm.complete("diagnose", system, msgs, schema=_DiagOut, seed=s.random_seed)

    if probs := _problems(out, t, cats):  # one semantic repair pass
        msgs += [
            {"role": "model", "text": out.model_dump_json()},
            {"role": "user", "text": "Fix these problems and return the full JSON again:\n- "
                                     + "\n- ".join(probs)},
        ]
        out = llm.complete("diagnose", system, msgs, schema=_DiagOut, seed=s.random_seed)
        if probs := _problems(out, t, cats):
            raise ValueError(f"{call_id}: diagnosis still invalid after repair: {probs}")

    d = Diagnosis(call_id=call_id, **out.model_dump())
    store.save(call_id, "diagnosis.json", d)
    return d


def show(d: Diagnosis, t: Transcript) -> None:
    labels = {l.idx: l for l in d.labels}
    for x in t.turns:
        mark = "[bold red]>>[/]" if x.idx == d.break_idx else "  "
        line = f"{mark} [{x.idx}] {x.speaker}: {x.text}"
        if (l := labels.get(x.idx)) and l.categories:
            line += f"  [yellow]{l.categories} sev={l.severity}[/]"
        console.print(line)
    console.print(f"[bold]break_idx={d.break_idx} ({d.break_category})[/]  {d.rationale}")
