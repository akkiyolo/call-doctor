"""Support step: produce example transcripts by running the baseline bot against customers."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import yaml
from rich.console import Console

from . import dialogue, llm
from .config import ROOT, get_settings
from .schemas import Persona, Transcript, Turn

PERSONAS_PATH = ROOT / "synthetic_personas.yaml"
MAX_CALL_TURNS = 16
console = Console()


def load_personas() -> dict[str, Persona]:
    raw = yaml.safe_load(PERSONAS_PATH.read_text(encoding="utf-8"))
    return {k: Persona.model_validate(v) for k, v in raw.items()}


def save(t: Transcript) -> None:
    path = get_settings().transcripts_dir / f"{t.call_id}.json"
    path.write_text(t.model_dump_json(indent=2), encoding="utf-8")


def synthetic_call(name: str, persona: Persona, seed: int) -> Transcript:
    bot_prompt = get_settings().bot_prompt_path.read_text(encoding="utf-8")
    cust = lambda turns, s: dialogue.customer_reply(persona, turns, s)
    turns, end = dialogue.run(bot_prompt, cust, [], MAX_CALL_TURNS, seed)
    t = Transcript(call_id=f"syn_{name}_{seed}", source="synthetic", turns=turns)
    save(t)
    console.print(f"[green]saved[/] {t.call_id}: {len(turns)} turns, end={end}")
    return t


def make_synthetic(n: int, names: list[str] | None = None) -> list[Transcript]:
    s = get_settings()
    personas = load_personas()
    chosen = names or list(personas)[:n]
    with ThreadPoolExecutor(max_workers=s.max_concurrency) as pool:
        futs = {nm: pool.submit(synthetic_call, nm, personas[nm], s.random_seed) for nm in chosen}
    done = []
    for nm, f in futs.items():
        try:
            done.append(f.result())
        except llm.BudgetExceeded:
            raise
        except Exception as e:  # one bad call must not sink the batch
            console.print(f"[red]failed[/] {nm}: {type(e).__name__}: {str(e)[:200]}")
    return done


def interactive() -> Transcript:
    """Human types as the customer. Empty line or /end hangs up."""
    bot_prompt = get_settings().bot_prompt_path.read_text(encoding="utf-8")
    turns: list[Turn] = []

    def human(_turns, _seed):
        line = console.input("[bold cyan]you (customer)> [/]").strip()
        if line in ("", "/end"):
            return "(hangs up)", "hung_up"
        return line, None

    console.print("[dim]You are the customer. Empty line or /end to hang up.[/]")
    seed = get_settings().random_seed
    while True:
        new, end = dialogue.run(bot_prompt, human, turns, 2, seed + len(turns))
        for t in new:
            if t.speaker == "bot":
                console.print(f"[bold magenta]bot>[/] {t.text}")
        turns += new
        if end != "max_turns":
            break
    t = Transcript(call_id=f"rp_{time.strftime('%Y%m%d_%H%M%S')}", source="roleplay", turns=turns)
    save(t)
    console.print(f"[green]saved[/] {t.call_id}: {len(turns)} turns, end={end}")
    return t
