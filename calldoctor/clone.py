"""Step 2: clone the real customer into a Persona from their own turns only."""
from __future__ import annotations

from . import llm, prompts, store
from .config import get_settings
from .schemas import Persona, Transcript


def customer_lines(t: Transcript) -> str:
    """Only the customer's words; the bot's claims about the customer never reach the cloner."""
    return "\n".join(f"- {x.text}" for x in t.turns if x.speaker == "customer")


def _missing_verbatim(p: Persona, t: Transcript) -> list[str]:
    said = " ".join(x.text for x in t.turns if x.speaker == "customer")
    return [s for s in p.style_examples if s.strip().strip('"') not in said]


def clone(call_id: str, force: bool = False) -> Persona:
    if not force and (cached := store.load(call_id, "persona.json", Persona)):
        return cached
    t = store.load_transcript(call_id)
    system = prompts.render("clone.md")
    msgs = [{"role": "user", "text": "CUSTOMER LINES\n" + customer_lines(t)}]
    seed = get_settings().random_seed
    p = llm.complete("diagnose", system, msgs, schema=Persona, seed=seed)

    if bad := _missing_verbatim(p, t):  # one repair: style examples must be exact quotes
        msgs += [
            {"role": "model", "text": p.model_dump_json()},
            {"role": "user", "text": "These style_examples are not verbatim customer text: "
                                     f"{bad}. Copy exact substrings. Return the full JSON again."},
        ]
        p = llm.complete("diagnose", system, msgs, schema=Persona, seed=seed)
        p.style_examples = [s for s in p.style_examples if s not in _missing_verbatim(p, t)]

    store.save(call_id, "persona.json", p)
    return p
