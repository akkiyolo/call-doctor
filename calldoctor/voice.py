"""Step 6b: render before/after continuations to audio so a reviewer can listen to the difference."""
from __future__ import annotations

import hashlib
import json
import shutil
import warnings
from pathlib import Path

from rich.console import Console

from . import audio, replay, store
from .config import get_settings
from .schemas import Diagnosis, ReplayRun, Score, Turn
from .score import composite, run_id

with warnings.catch_warnings():  # pydub warns about ffmpeg at import; we only need it for mp3 export
    warnings.simplefilter("ignore")
    from pydub import AudioSegment

console = Console()
GAP_MS = 300
LEAD_IN_TURNS = 1  # the customer line right before the fork, so the listener hears what the bot answers


def _audio_dir() -> Path:
    d = get_settings().reports_dir / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _turn_pcm(text: str, voice_id: str) -> bytes:
    """Cached per (text, voice, model): reruns cost no credits."""
    s = get_settings()
    key = hashlib.sha256(f"{text}|{voice_id}|{s.elevenlabs_tts_model}".encode()).hexdigest()[:24]
    cache = _audio_dir() / "cache" / f"{key}.pcm"
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(audio.tts(text, voice_id))
    return cache.read_bytes()


def stitch(turns: list[Turn]) -> AudioSegment:
    s = get_settings()
    voices = {"bot": s.elevenlabs_bot_voice_id, "customer": s.elevenlabs_customer_voice_id}
    out = AudioSegment.silent(duration=0, frame_rate=audio.PCM_RATE)
    for t in turns:
        seg = AudioSegment(data=_turn_pcm(t.text, voices[t.speaker]), sample_width=2,
                           frame_rate=audio.PCM_RATE, channels=1)
        out += seg + AudioSegment.silent(duration=GAP_MS, frame_rate=audio.PCM_RATE)
    return out


def export(seg: AudioSegment, stem: str) -> str:
    """mp3 when ffmpeg is available, otherwise wav (no external dependency). Returns the file name."""
    if shutil.which("ffmpeg"):
        name = f"{stem}.mp3"
        seg.export(_audio_dir() / name, format="mp3", bitrate="96k")
    else:
        name = f"{stem}.wav"
        seg.export(_audio_dir() / name, format="wav")
    return name


def improvement(call_id: str) -> float | None:
    p = get_settings().runs_dir / call_id / "summary.json"
    if not p.exists():
        return None
    s = json.loads(p.read_text(encoding="utf-8"))
    o, pt = s["original"].get("mean_composite"), s["patched"].get("mean_composite")
    return None if o is None or pt is None else pt - o


def select_calls(top: int) -> list[str]:
    env = get_settings().voice_calls
    if env:
        return env
    scored = [(improvement(c), c) for c in store.transcript_ids()]
    scored = [x for x in scored if x[0] is not None]
    return [c for _, c in sorted(scored, reverse=True)[:top]]


def best_pair(call_id: str) -> tuple[ReplayRun, ReplayRun] | None:
    runs = store.load_list(call_id, "replays.json", ReplayRun) or []
    scores = {s.run_id: s for s in (store.load_list(call_id, "scores.json", Score) or [])}
    by = {(r.arm, r.seed): r for r in runs}
    cands = [r for r in runs if r.arm == "patched" and run_id(r) in scores and ("original", r.seed) in by]
    if not cands:
        return None
    best = max(cands, key=lambda r: (composite(scores[run_id(r)]), -r.seed))
    return by[("original", best.seed)], best


def voice(call_id: str) -> dict | None:
    s = get_settings()
    if "paste" in s.elevenlabs_bot_voice_id or not s.elevenlabs_bot_voice_id:
        raise RuntimeError("set ELEVENLABS_BOT_VOICE_ID / ELEVENLABS_CUSTOMER_VOICE_ID in .env")
    pair = best_pair(call_id)
    if pair is None:
        console.print(f"[yellow]{call_id}: no scored replay pair yet, skipping[/]")
        return None
    d = store.load(call_id, "diagnosis.json", Diagnosis)
    lead = replay.fork(store.load_transcript(call_id), d.break_idx)[-LEAD_IN_TURNS:]
    orig, patched = pair
    meta = {"seed": orig.seed,
            "before": export(stitch(lead + orig.turns), f"{call_id}_before"),
            "after": export(stitch(lead + patched.turns), f"{call_id}_after")}
    (_audio_dir() / f"{call_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta
