"""Step 3: propose a minimal, general prompt edit for the diagnosed failure, with overfitting checks."""
from __future__ import annotations

import difflib
import re

import yaml
from pydantic import BaseModel

from . import llm, prompts, store
from .config import get_settings
from .schemas import Diagnosis, Patch, Transcript

MAX_CHANGED_LINES = 8
NGRAM = 4
_WORD = re.compile(r"[\wऀ-ॿ]+")
_NUM = re.compile(r"\d[\d,.]*")


class _PatchOut(BaseModel):
    patched_prompt: str
    rationale: str


def unified_diff(old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="bot_prompt.md", tofile="bot_prompt.patched.md",
    ))


def changed_lines(diff: str) -> tuple[list[str], list[str]]:
    lines = [l for l in diff.splitlines() if not l.startswith(("+++", "---"))]
    added = [l[1:] for l in lines if l.startswith("+")]
    removed = [l[1:] for l in lines if l.startswith("-")]
    return added, removed


def _ngrams(text: str, n: int = NGRAM) -> set[tuple[str, ...]]:
    w = [x.lower() for x in _WORD.findall(text)]
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def overfit_problems(old_prompt: str, new_prompt: str, t: Transcript) -> list[str]:
    """Code-side anti-overfitting checks on the ADDED lines of the patch."""
    added, removed = changed_lines(unified_diff(old_prompt, new_prompt))
    added_text = "\n".join(added)
    probs = []
    if not added and not removed:
        probs.append("the patch makes no change")
    if max(len(added), len(removed)) > MAX_CHANGED_LINES:
        probs.append(f"too large: {len(added)} lines added / {len(removed)} removed "
                     f"(max {MAX_CHANGED_LINES})")
    convo = " ".join(x.text for x in t.turns)
    copied = (_ngrams(added_text) & _ngrams(convo)) - _ngrams(old_prompt)
    if copied:
        probs.append("copies 4+ word strings from the transcript: "
                     + "; ".join(" ".join(g) for g in sorted(copied)[:5]))
    nums = set(_NUM.findall(added_text)) & set(_NUM.findall(convo)) - set(_NUM.findall(old_prompt))
    if nums:
        probs.append(f"mentions call-specific numbers: {sorted(nums)}")
    return probs


def _normalize(new: str, old: str) -> str:
    """Strip echoed <<< >>> fences and match the original's trailing newline, so diffs stay minimal."""
    new = new.strip().removeprefix("<<<").removesuffix(">>>").strip("\n")
    return new + "\n" if old.endswith("\n") else new


def _definition(category: str) -> str:
    tax = yaml.safe_load(get_settings().taxonomy_path.read_text(encoding="utf-8"))
    for c in tax["categories"]:
        if c["id"] == category:
            return c["definition"].strip()
    return ""


def patch(call_id: str, force: bool = False) -> Patch:
    if not force and (cached := store.load(call_id, "patch.json", Patch)):
        return cached
    s = get_settings()
    d = store.load(call_id, "diagnosis.json", Diagnosis)
    if d is None:
        raise FileNotFoundError(f"{call_id}: run diagnose first")
    t = store.load_transcript(call_id)
    old = s.bot_prompt_path.read_text(encoding="utf-8")

    system = prompts.render("patch.md", category=d.break_category,
                            definition=_definition(d.break_category),
                            rationale=d.rationale, max_lines=MAX_CHANGED_LINES)
    msgs = [{"role": "user", "text": "CURRENT PROMPT\n<<<\n" + old + "\n>>>"}]
    out = llm.complete("patch", system, msgs, schema=_PatchOut, seed=s.random_seed)
    out.patched_prompt = _normalize(out.patched_prompt, old)

    if probs := overfit_problems(old, out.patched_prompt, t):  # reject and retry once
        msgs += [
            {"role": "model", "text": out.model_dump_json()},
            {"role": "user", "text": "Rejected:\n- " + "\n- ".join(probs)
                                     + "\nWrite a smaller, general rule and return the full JSON again."},
        ]
        out = llm.complete("patch", system, msgs, schema=_PatchOut, seed=s.random_seed)
        out.patched_prompt = _normalize(out.patched_prompt, old)
        if probs := overfit_problems(old, out.patched_prompt, t):
            raise ValueError(f"{call_id}: patch rejected twice: {probs}")

    p = Patch(call_id=call_id, target_category=d.break_category,
              diff=unified_diff(old, out.patched_prompt),
              patched_prompt=out.patched_prompt, rationale=out.rationale)
    store.save(call_id, "patch.json", p)
    return p
