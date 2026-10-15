"""All data contracts. Every JSON file under transcripts/ and runs/ is one of these."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Speaker = Literal["bot", "customer"]
Arm = Literal["original", "patched"]


class Turn(BaseModel):
    idx: int
    speaker: Speaker
    text: str


class Transcript(BaseModel):
    call_id: str
    source: Literal["real", "roleplay", "synthetic"]
    turns: list[Turn]


class TurnLabel(BaseModel):
    idx: int
    categories: list[str]
    evidence: str
    severity: int = Field(ge=0, le=3)


class Diagnosis(BaseModel):
    call_id: str
    labels: list[TurnLabel]
    break_idx: int
    break_category: str
    rationale: str


class Persona(BaseModel):
    goals: str
    objections: list[str]
    patience: int = Field(ge=0, le=3)
    verbosity: int = Field(ge=0, le=3)
    hindi_english_mix: float = Field(ge=0.0, le=1.0)
    interrupts: bool
    trust_level: int = Field(ge=0, le=3)
    will_convert_if: str
    hard_stop_if: str
    style_examples: list[str]


class Patch(BaseModel):
    call_id: str
    target_category: str
    diff: str
    patched_prompt: str
    rationale: str


class ReplayRun(BaseModel):
    call_id: str
    arm: Arm
    seed: int
    turns: list[Turn]
    end_reason: str


class Score(BaseModel):
    run_id: str
    reasons: str
    next_step_reached: bool
    recovery: int = Field(ge=0, le=2)
    human_likeness: int = Field(ge=0, le=2)
    compliance_pass: bool
    compliance_issues: list[str]
