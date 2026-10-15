"""Bot <-> customer conversation engine. Shared by make-calls (full calls) and replay (forks)."""
from __future__ import annotations

import re
from typing import Callable

from . import llm, prompts
from .schemas import Persona, Turn

KICKOFF = "[Call connected. The customer has picked up the phone.]"
BOT_END = "<END_CALL>"
_CUST_END = re.compile(r"<END(?:\s+reason=\"([^\"]*)\")?\s*/?>", re.IGNORECASE)


def bot_system(bot_prompt: str) -> str:
    """The bot prompt under test plus a fixed call-system note (identical for every arm)."""
    return bot_prompt + prompts.render("bot_harness.md")


def to_messages(turns: list[Turn], me: str) -> list[dict]:
    """Map turns to Gemini contents from `me`'s point of view, merging same-role neighbours.

    Gemini expects the first message to be from the user, so the bot's view starts with KICKOFF.
    """
    msgs: list[dict] = [{"role": "user", "text": KICKOFF}] if me == "bot" else []
    for t in turns:
        role = "model" if t.speaker == me else "user"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["text"] += "\n" + t.text
        else:
            msgs.append({"role": role, "text": t.text})
    if not msgs:  # customer view with no bot turn yet; never happens for outbound calls
        msgs.append({"role": "user", "text": KICKOFF})
    return msgs


def parse_bot(text: str) -> tuple[str, bool]:
    ended = BOT_END in text
    return text.replace(BOT_END, "").strip(), ended


def parse_customer(text: str) -> tuple[str, str | None]:
    m = _CUST_END.search(text)
    if not m:
        return text.strip(), None
    return _CUST_END.sub("", text).strip(), (m.group(1) or "ended")


def bot_reply(bot_prompt: str, history: list[Turn], seed: int | None) -> tuple[str, bool]:
    raw = llm.complete("bot", bot_system(bot_prompt), to_messages(history, "bot"), seed=seed)
    return parse_bot(raw)


def customer_reply(persona: Persona, history: list[Turn], seed: int | None) -> tuple[str, str | None]:
    system = prompts.render("customer_sim.md", p=persona)
    raw = llm.complete("customer", system, to_messages(history, "customer"), seed=seed)
    return parse_customer(raw)


def run(
    bot_prompt: str,
    customer: Callable[[list[Turn], int], tuple[str, str | None]],
    history: list[Turn],
    max_turns: int,
    seed: int,
) -> tuple[list[Turn], str]:
    """Continue `history` with the bot speaking next, until an end condition.

    `customer(turns, seed)` returns (text, end_reason or None). Returns (new turns only, end_reason).
    Each turn gets its own derived seed so arms with the same base seed stay paired.
    """
    turns = list(history)
    new: list[Turn] = []
    end_reason = "max_turns"
    for k in range(max_turns):
        speaker = "bot" if k % 2 == 0 else "customer"
        turn_seed = seed * 1000 + k
        if speaker == "bot":
            text, ended = bot_reply(bot_prompt, turns, turn_seed)
            stop = "bot_ended_call" if ended else None
        else:
            text, stop = customer(turns, turn_seed)
            stop = f"customer:{stop}" if stop else None
        t = Turn(idx=len(turns), speaker=speaker, text=text)
        turns.append(t)
        new.append(t)
        if stop:
            end_reason = stop
            break
    return new, end_reason
