"""Call Doctor CLI. Each command runs one pipeline step and caches its output under runs/."""
from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.console import Console

from . import llm

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


@app.callback()
def _main() -> None:
    """Diagnose, patch and counterfactually replay Hinglish loan-sales bot calls."""


def _done() -> None:
    console.print(f"[dim]estimated spend this run: ${llm.spend():.4f}[/]")


@app.command("make-calls")
def make_calls(
    n: int = typer.Option(2, help="Number of synthetic calls (first N personas)."),
    persona: Optional[list[str]] = typer.Option(None, help="Specific persona names (repeatable)."),
    interactive: bool = typer.Option(False, "--interactive", help="Type as the customer yourself."),
) -> None:
    """Generate example transcripts: baseline bot vs synthetic personas, or vs you."""
    from . import make_calls as mc

    if interactive:
        mc.interactive()
    else:
        mc.make_synthetic(n, persona)
    _done()


def _ids(call_id: Optional[str]) -> list[str]:
    from . import store

    return [call_id] if call_id else store.transcript_ids()


@app.command()
def diagnose(
    call_id: Optional[str] = typer.Argument(None, help="One call id; default all transcripts."),
    force: bool = typer.Option(False, "--force", help="Ignore cached diagnosis."),
) -> None:
    """Label bot turns with taxonomy categories and pick the break turn."""
    from . import diagnose as dg
    from . import store

    for cid in _ids(call_id):
        console.rule(cid)
        d = dg.diagnose(cid, force=force)
        dg.show(d, store.load_transcript(cid))
    _done()


@app.command()
def clone(
    call_id: Optional[str] = typer.Argument(None, help="One call id; default all transcripts."),
    force: bool = typer.Option(False, "--force", help="Ignore cached persona."),
) -> None:
    """Extract a customer Persona from the customer's turns only."""
    from . import clone as cl

    for cid in _ids(call_id):
        console.rule(cid)
        console.print_json(cl.clone(cid, force=force).model_dump_json())
    _done()


@app.command()
def patch(
    call_id: Optional[str] = typer.Argument(None, help="One call id; default all transcripts."),
    force: bool = typer.Option(False, "--force", help="Ignore cached patch."),
) -> None:
    """Propose a minimal, general edit to bot_prompt.md for the diagnosed failure."""
    from rich.syntax import Syntax

    from . import patch as pt

    for cid in _ids(call_id):
        console.rule(cid)
        p = pt.patch(cid, force=force)
        console.print(f"[bold]target:[/] {p.target_category}")
        console.print(f"[bold]rationale:[/] {p.rationale}")
        console.print(Syntax(p.diff, "diff", theme="ansi_dark"))
    _done()


@app.command()
def replay(
    call_id: Optional[str] = typer.Argument(None, help="One call id; default all transcripts."),
    n: Optional[int] = typer.Option(None, help="Runs per arm (default REPLAY_N)."),
    max_turns: Optional[int] = typer.Option(None, help="Max turns per continuation."),
    force: bool = typer.Option(False, "--force", help="Discard cached replays."),
) -> None:
    """Fork at the break turn and replay original vs patched prompt with paired seeds."""
    from rich.table import Table

    from . import replay as rp

    for cid in _ids(call_id):
        console.rule(cid)
        runs = rp.replay(cid, n=n, max_turns=max_turns, force=force)
        tab = Table("seed", "arm", "turns", "end_reason", "first bot reply at fork")
        for r in runs:
            tab.add_row(str(r.seed), r.arm, str(len(r.turns)), r.end_reason,
                        (r.turns[0].text[:90] + "...") if r.turns else "")
        console.print(tab)
    _done()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; keep ₹ and Devanagari
        stream.reconfigure(encoding="utf-8")
    try:
        app()
    except llm.BudgetExceeded as e:
        console.print(f"[bold red]Budget guard:[/] {e}. Aborting cleanly; cached steps are kept.")
        sys.exit(2)


if __name__ == "__main__":
    main()
