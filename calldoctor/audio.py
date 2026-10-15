"""ElevenLabs: Scribe speech-to-text (with diarization) and TTS. The only audio entry points."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from .config import get_settings
from .schemas import Turn

PCM_RATE = 24000  # pcm_24000: raw 16-bit mono, stitchable without ffmpeg


@lru_cache(maxsize=1)
def _client():
    from elevenlabs.client import ElevenLabs

    key = get_settings().elevenlabs_api_key
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return ElevenLabs(api_key=key)


def words_to_turns(words: list[dict], bot_speaker: str | None = None) -> tuple[list[Turn], dict]:
    """Merge consecutive same-speaker words into turns. First speaker is the bot unless overridden.

    words: [{"text", "type", "speaker_id"}] as returned by Scribe. Text is kept exactly as transcribed.
    Any speaker other than the bot is mapped to "customer".
    """
    segs: list[list] = []  # [speaker_id, text]
    for w in words:
        if w.get("type") == "audio_event":
            continue
        sid = w.get("speaker_id") or (segs[-1][0] if segs else "speaker_0")
        if segs and segs[-1][0] == sid:
            segs[-1][1] += w["text"]
        elif w.get("type") == "spacing" and segs:
            segs[-1][1] += w["text"]
        else:
            segs.append([sid, w["text"]])
    bot = bot_speaker or (segs[0][0] if segs else None)
    mapping = {sid: ("bot" if sid == bot else "customer") for sid, _ in segs}
    turns: list[Turn] = []
    for sid, text in segs:
        speaker, text = mapping[sid], text.strip()
        if not text:
            continue
        if turns and turns[-1].speaker == speaker:  # two customer ids next to each other
            turns[-1].text += " " + text
        else:
            turns.append(Turn(idx=len(turns), speaker=speaker, text=text))
    return turns, mapping


@retry(wait=wait_exponential_jitter(initial=2, max=30), stop=stop_after_attempt(4), reraise=True)
def transcribe(path: Path) -> list[dict]:
    """Batch Scribe with diarization; language auto-detected (code-mixed audio must not be forced)."""
    s = get_settings()
    with open(path, "rb") as f:
        res = _client().speech_to_text.convert(
            model_id=s.elevenlabs_stt_model, file=f, diarize=True, tag_audio_events=False,
        )
    return [{"text": w.text, "type": w.type, "speaker_id": getattr(w, "speaker_id", None)}
            for w in res.words]


@retry(wait=wait_exponential_jitter(initial=2, max=30), stop=stop_after_attempt(4), reraise=True)
def tts(text: str, voice_id: str) -> bytes:
    """Raw PCM (16-bit mono, PCM_RATE Hz) for one turn."""
    s = get_settings()
    chunks = _client().text_to_speech.convert(
        voice_id=voice_id, text=text, model_id=s.elevenlabs_tts_model,
        output_format=f"pcm_{PCM_RATE}",
    )
    return b"".join(chunks)
