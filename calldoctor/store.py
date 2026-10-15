"""Paths and JSON caching for transcripts/ and runs/<call_id>/."""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from .config import get_settings
from .schemas import Transcript

M = TypeVar("M", bound=BaseModel)


def run_dir(call_id: str) -> Path:
    d = get_settings().runs_dir / call_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcript_ids() -> list[str]:
    return sorted(p.stem for p in get_settings().transcripts_dir.glob("*.json"))


def load_transcript(call_id: str) -> Transcript:
    p = get_settings().transcripts_dir / f"{call_id}.json"
    return Transcript.model_validate_json(p.read_text(encoding="utf-8"))


def save(call_id: str, name: str, obj) -> Path:
    p = run_dir(call_id) / name
    if isinstance(obj, BaseModel):
        text = obj.model_dump_json(indent=2)
    else:
        text = TypeAdapter(type(obj)).dump_json(obj, indent=2).decode()
    p.write_text(text, encoding="utf-8")
    return p


def load(call_id: str, name: str, model: type[M]) -> M | None:
    p = get_settings().runs_dir / call_id / name
    if not p.exists():
        return None
    return model.model_validate_json(p.read_text(encoding="utf-8"))


def load_list(call_id: str, name: str, model: type[M]) -> list[M] | None:
    p = get_settings().runs_dir / call_id / name
    if not p.exists():
        return None
    return TypeAdapter(list[model]).validate_json(p.read_text(encoding="utf-8"))
