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
